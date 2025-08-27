# app.py
import os
import io
import time
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from tenacity import retry, wait_fixed, stop_after_attempt
from bs4 import BeautifulSoup
from typing import Dict, List

from search_providers import SerpAPISearcher, HunterClient
from scraper import harvest_contact_from_url, soup_text, safe_get
from utils import (
    build_queries_rule_based, compile_cert_terms, domain_from_url,
    is_likely_supplier_result, is_blacklisted_domain, AGGREGATOR_BLACKLIST
)

# Optional OpenAI (for query shaping, domain filtering, and extraction)
USE_OPENAI = False
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

load_dotenv()
st.set_page_config(page_title="Supplier Finder — LLM Filtering, 5-column Output", layout="wide")
st.title("🔎 Supplier Finder — LLM Query + Filtering (5-column Excel Output)")

# ---- Error log always visible ----
error_box = st.container()
error_log: List[str] = []
def log_error(msg: str):
    error_log.append(msg)
    with error_box:
        st.error("• " + "\n• ".join(error_log))

with st.sidebar:
    st.subheader("API Keys")
    serp_key   = st.text_input("SerpAPI Key (REQUIRED)", os.getenv("SERPAPI_API_KEY", ""), type="password")
    openai_key = st.text_input("OpenAI API Key (optional, recommended)", os.getenv("OPENAI_API_KEY", ""), type="password")
    hunter_key = st.text_input("Hunter API Key (optional)", os.getenv("HUNTER_API_KEY", ""), type="password")
    st.caption("Sidebar keys override `.env` for this session.")
    if openai_key:
        USE_OPENAI = True

with st.form("search_form"):
    c1, c2, c3 = st.columns(3)
    with c1:
        commodity = st.text_input("Commodity", placeholder="e.g., injection molding").strip()
    with c2:
        region = st.text_input("Region", placeholder="e.g., India or Coimbatore, India").strip()
    with c3:
        certification = st.text_input("Certification", placeholder="e.g., ISO 9001 / IATF 16949").strip()

    cA, cB, cC = st.columns(3)
    with cA:
        max_results = st.slider("Total Google results to scan", 10, 100, 30, step=10)
    with cB:
        use_llm_query = st.checkbox("Let OpenAI craft the search queries", value=bool(openai_key))
    with cC:
        ai_domain_filter = st.checkbox("AI filter: keep only individual company sites", value=bool(openai_key))

    use_openai_extract = st.checkbox("Use OpenAI to improve contact parsing", value=bool(openai_key))
    submitted = st.form_submit_button("Search Suppliers")

# ---------- OpenAI helpers ----------
def _get_client(api_key: str | None):
    if not OpenAI or not api_key:
        return None
    return OpenAI(api_key=api_key)

def llm_build_queries(client, commodity: str, region: str, certification: str) -> list[str]:
    """
    Ask the LLM for 5–8 diverse, *company-oriented* queries. We append negative site filters afterwards.
    """
    if not client:
        return []
    prompt = f"""
You craft expert Google queries to find individual manufacturing companies (not directories/marketplaces).
Inputs:
- Commodity: {commodity}
- Region: {region}
- Certification: {certification}

Return 6 short queries that will surface company websites, avoiding marketplaces, ads, and job boards.
Only return the queries as a JSON array of strings. Do not include explanations.
"""
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            response_format={"type":"json_object"},
            messages=[{"role":"user","content":prompt}]
        )
        import json
        obj = json.loads(r.choices[0].message.content)
        # Accept either {"queries": [...]} or raw list
        queries = obj.get("queries") if isinstance(obj, dict) else obj
        if not isinstance(queries, list):
            return []
        return [q.strip() for q in queries if isinstance(q, str) and q.strip()]
    except Exception as e:
        log_error(f"OpenAI query generation failed: {e}")
        return []

def ai_is_company_domain(client, domain: str, title: str, snippet: str, commodity: str, region: str, certification: str) -> bool:
    """
    Lightweight classifier: 'company' vs 'marketplace/directory/aggregator'.
    Short prompt, temperature 0, returns True if 'company'.
    """
    if not client:
        return True  # if no LLM, allow (we still have hard blacklist)
    text = f"title: {title}\nsnippet: {snippet}\ndomain: {domain}"
    prompt = f"""
Classify the website as either "company" or "marketplace".
- Treat directories, listings, B2B marketplaces, comparison portals, social networks, job boards as "marketplace".
- We want manufacturer/company sites related to commodity '{commodity}', region '{region}', certification '{certification}'.

Given:
{text}

Answer with a single word: company or marketplace
"""
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            messages=[{"role":"user","content":prompt}]
        )
        ans = (r.choices[0].message.content or "").strip().lower()
        return "company" in ans and "marketplace" not in ans
    except Exception as e:
        log_error(f"OpenAI domain filter failed for {domain}: {e}")
        return True  # don't over-filter on failure

def openai_structured_extract(client, html_text: str, base_url: str, commodity: str, region: str) -> Dict:
    """
    Ask OpenAI to pick the best single address & phone for the requested region,
    dedupe emails, and ignore obvious generic/worldwide call center numbers when a regional phone exists.
    """
    if not client:
        return {}
    snippet = html_text[:12000]
    prompt = f"""
You extract **precise contact data** from messy HTML/text for a supplier in region "{region}".

Return JSON with:
- company_name: string
- address_best: single string (prefer HQ/factory address in the specified region)
- phones_best: single string (best phone for the specified region; else main switchboard)
- emails: list of unique emails (max 5), drop images/obfuscated text

HTML/TEXT START
{snippet}
HTML/TEXT END
"""
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            response_format={"type":"json_object"},
            messages=[{"role":"user","content":prompt}]
        )
        import json
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        log_error(f"OpenAI contact extraction failed for {base_url}: {e}")
        return {}

@retry(wait=wait_fixed(1), stop=stop_after_attempt(3))
def _fetch(url: str):
    return safe_get(url)

# ---------- Main ----------
if submitted:
    t_start = time.perf_counter()

    if not serp_key:
        log_error("Please paste a valid SerpAPI key.")
        st.stop()
    if not commodity or not region or not certification:
        log_error("Fill **Commodity**, **Region**, **Certification**.")
        st.stop()

    client = _get_client(openai_key)

    # Build queries
    if use_llm_query and client:
        base_queries = llm_build_queries(client, commodity, region, certification)
        if not base_queries:
            base_queries = build_queries_rule_based(commodity, region, certification, AGGREGATOR_BLACKLIST)
        else:
            # Always append negative site filters to the LLM queries
            neg = " " + " ".join(f"-site:{d}" for d in AGGREGATOR_BLACKLIST)
            base_queries = [q + neg for q in base_queries]
    else:
        base_queries = build_queries_rule_based(commodity, region, certification, AGGREGATOR_BLACKLIST)

    # Search
    try:
        searcher = SerpAPISearcher(api_key=serp_key)
    except Exception as e:
        log_error(f"SerpAPI init error: {e}")
        st.stop()

    st.info("Searching via SerpAPI (ads ignored; filtering marketplaces)…")
    all_results = []
    per_query = max(1, max_results // max(1, len(base_queries)))

    prog = st.progress(0.0)
    for i, q in enumerate(base_queries, start=1):
        try:
            res = searcher.search(q, location=region, num=per_query)
            all_results.extend(res)  # we only read organic_results in search_providers.py
        except Exception as e:
            log_error(f"Search failed for query: {q} — {e}")
        finally:
            prog.progress(i / len(base_queries))

    # Filter to likely supplier + remove blacklisted + (optional) AI domain filter
    st.info("Filtering to individual supplier sites…")
    pruned, seen = [], set()
    domain_decision_cache = {}

    for item in all_results:
        title, snippet, link = item.get("title",""), item.get("snippet",""), item.get("link","")
        dom = domain_from_url(link)
        if not link or not dom:
            continue
        if dom in seen:
            continue
        # hard blacklist first
        if is_blacklisted_domain(dom, AGGREGATOR_BLACKLIST):
            continue
        # quick supplier hint check
        if not is_likely_supplier_result(title, snippet):
            continue
        # optional AI classifier
        if ai_domain_filter:
            key = (dom, title, snippet)
            if key in domain_decision_cache:
                ok = domain_decision_cache[key]
            else:
                ok = ai_is_company_domain(client, dom, title, snippet, commodity, region, certification)
                domain_decision_cache[key] = ok
            if not ok:
                continue
        seen.add(dom)
        pruned.append(item)

    if not pruned:
        log_error("No eligible supplier websites after filtering. Try broadening commodity/region/certification.")
        st.stop()

    st.success(f"Scraping {len(pruned)} supplier sites (sequential)…")

    # Scrape loop (timed)
    status = st.empty()
    bar = st.progress(0.0)
    rows, timings = [], []
    hunter = HunterClient(api_key=hunter_key)

    for i, item in enumerate(pruned, start=1):
        url = item["link"]
        status.info(f"⏳ Scraping: {url}")
        t0 = time.perf_counter()
        result_label = "Success"
        try:
            resp = _fetch(url)
            if not (resp and resp.ok and resp.text):
                result_label = "HTTP error"
                raise RuntimeError(f"Bad response for {url}")

            soup = BeautifulSoup(resp.text, "lxml")
            text = soup_text(soup)

            # Heuristic scrape (has time budget inside)
            contact = harvest_contact_from_url(resp.url, region_hint="IN")

            # OpenAI contact normalization (pick best for region)
            if use_openai_extract and client and text:
                llm = openai_structured_extract(client, text, resp.url, commodity, region)
                if llm:
                    contact["company_name"] = contact.get("company_name") or llm.get("company_name")
                    # prefer LLM's region-specific best picks if available
                    if llm.get("address_best"):
                        contact["address"] = llm["address_best"]
                    if llm.get("phones_best"):
                        contact["phones"] = list({llm["phones_best"]})
                    if llm.get("emails"):
                        contact["emails"] = sorted(set((contact.get("emails") or []) + llm["emails"]))

            # Optional Hunter enrichment
            dom = domain_from_url(resp.url)
            hunter_emails = hunter.domain_search(dom, limit=5) if hunter_key else []
            emails = sorted(set((contact.get("emails") or []) + hunter_emails))

            rows.append({
                "Supplier Name": contact.get("company_name") or item.get("title") or dom,
                "Website link": resp.url,
                "Contact Address": contact.get("address") or "",
                "Contact Email": ", ".join(emails[:5]),
                "Contact Phone Number": ", ".join((contact.get("phones") or [])[:3]),
            })

        except Exception as e:
            log_error(f"Scrape failed for {url}: {e}")
            result_label = f"Failed: {e}"
        finally:
            dt = time.perf_counter() - t0
            timings.append({"Website": url, "Seconds": round(dt, 2), "Result": result_label})
            try:
                st.toast(("✅ " if "Success" in result_label else "❌ ") + f"{url} — {dt:.2f}s")
            except Exception:
                pass
            bar.progress(i / len(pruned))

    status.empty()

    if not rows:
        log_error("No contacts extracted from any site.")
        st.stop()

    # Final 5-column output
    df = pd.DataFrame(rows, columns=[
        "Supplier Name", "Website link", "Contact Address", "Contact Email", "Contact Phone Number"
    ])

    st.subheader("Results (5 columns)")
    st.dataframe(df, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "⬇️ Download CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="suppliers.csv",
            mime="text/csv",
        )
    with c2:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Suppliers")
        st.download_button(
            "⬇️ Download Excel (XLSX)",
            data=buf.getvalue(),
            file_name="suppliers.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.subheader("⏱️ Timing Summary (per website)")
    tdf = pd.DataFrame(timings, columns=["Website","Seconds","Result"])
    st.dataframe(tdf, use_container_width=True)

    total = time.perf_counter() - t_start
    st.info(f"Total run time: **{total:.2f} seconds** — Sites scraped: **{len(pruned)}**")
