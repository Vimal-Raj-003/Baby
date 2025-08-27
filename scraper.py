# scraper.py
import re, time, json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from typing import Dict, List, Optional, Tuple
import phonenumbers

# ---- Tunables ----
REQUEST_TIMEOUT = 12           # seconds per HTTP GET
MAX_CONTACT_PAGES = 3          # follow at most N contact/about links
TOTAL_PER_SITE_BUDGET = 25     # hard cap seconds per site (homepage + contact pages)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
CONTACT_HREF_HINTS = ["contact", "contact-us", "contacts", "impressum", "about", "company", "reach-us"]

EMAIL_RE = re.compile(r"mailto:([^\?\"'>\s]+)|([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:tel:|\+?\d[\d\s().-]{6,}\d)")

def safe_get(url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[requests.Response]:
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except Exception:
        return None

def soup_text(soup: BeautifulSoup) -> str:
    for s in soup(["script","style","noscript"]):
        s.decompose()
    return " ".join(soup.get_text(separator=" ", strip=True).split())

def find_contact_links(base_url: str, soup: BeautifulSoup) -> List[str]:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if any(h in href.lower() for h in CONTACT_HREF_HINTS):
            links.append(urljoin(base_url, href))
    # de-dup while preserving order
    seen, out = set(), []
    for u in links:
        if u not in seen:
            seen.add(u); out.append(u)
    return out[:MAX_CONTACT_PAGES]

def extract_emails(text: str, soup: Optional[BeautifulSoup] = None) -> List[str]:
    found = set()
    if soup:
        for a in soup.find_all("a", href=True):
            if a["href"].lower().startswith("mailto:"):
                m = EMAIL_RE.search(a["href"])
                if m:
                    email = m.group(1) or m.group(2)
                    if email:
                        found.add(email)
    for m in EMAIL_RE.finditer(text):
        email = m.group(1) or m.group(2)
        if email:
            found.add(email)
    return sorted(found)

def extract_phones(text: str, default_region: str = "IN") -> List[str]:
    candidates = {m.group(0) for m in PHONE_RE.finditer(text)}
    parsed = set()
    for c in candidates:
        c_clean = c.replace("tel:", "").strip()
        try:
            for match in phonenumbers.PhoneNumberMatcher(c_clean, default_region):
                if phonenumbers.is_possible_number(match.number):
                    parsed.add(phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
        except Exception:
            pass
    return sorted(parsed)

def extract_jsonld_address(soup: BeautifulSoup) -> Optional[str]:
    try:
        for tag in soup.find_all("script", type="application/ld+json"):
            data = json.loads(tag.get_text(strip=True))
            if isinstance(data, list):
                for d in data:
                    addr = _pick_address(d)
                    if addr: return addr
            else:
                addr = _pick_address(data)
                if addr: return addr
    except Exception:
        pass
    return None

def _pick_address(data: dict) -> Optional[str]:
    try:
        if isinstance(data, dict):
            if any(t in str(data.get("@type","")).lower() for t in
                   ["organization","localbusiness","person","store","corporation"]):
                addr = data.get("address")
                if isinstance(addr, dict):
                    parts = [addr.get("streetAddress"), addr.get("addressLocality"),
                             addr.get("addressRegion"), addr.get("postalCode"), addr.get("addressCountry")]
                    return ", ".join([p for p in parts if p])
                if isinstance(addr, str):
                    return addr
    except Exception:
        pass
    return None

def extract_company_name(soup: BeautifulSoup) -> Optional[str]:
    og = soup.find("meta", attrs={"property": "og:site_name"})
    if og and og.get("content"):
        return og["content"].strip()
    title = (soup.title.string if soup.title else "").strip()
    if title:
        return title.split("|")[0].split("–")[0].strip()
    logo = soup.find("img", alt=True)
    if logo and len(logo["alt"].strip()) > 2:
        return logo["alt"].strip()
    return None

def _harvest_once(url: str, region_hint: str) -> dict:
    out = {"source_page": url, "company_name": None, "emails": [], "phones": [], "address": None}
    resp = safe_get(url)
    if not resp or not resp.ok or not resp.text:
        return out
    soup = BeautifulSoup(resp.text, "lxml")
    text = soup_text(soup)
    out["company_name"] = extract_company_name(soup)
    out["emails"] = extract_emails(text, soup)
    out["phones"] = extract_phones(text, region_hint)
    out["address"] = extract_jsonld_address(soup)
    return out

def harvest_contact_from_url(url: str, region_hint: str = "IN") -> dict:
    """
    Hard caps time spent per site to avoid hanging. Follows at most MAX_CONTACT_PAGES.
    """
    start = time.monotonic()
    result = _harvest_once(url, region_hint)

    # Stop early if we already got everything
    if result["emails"] and result["phones"] and result["address"] and result["company_name"]:
        return result

    # Respect total budget per site
    def remaining():
        return TOTAL_PER_SITE_BUDGET - (time.monotonic() - start)
    if remaining() <= 0:
        return result

    # Try contact/about pages
    resp = safe_get(url)
    if not resp or not resp.ok or not resp.text:
        return result
    soup = BeautifulSoup(resp.text, "lxml")
    links = find_contact_links(resp.url, soup)
    for link in links:
        if remaining() <= 0:
            break
        sub = _harvest_once(link, region_hint)
        result["emails"] = sorted(set(result["emails"] + sub.get("emails", [])))
        result["phones"] = sorted(set(result["phones"] + sub.get("phones", [])))
        if not result["address"] and sub.get("address"):
            result["address"] = sub["address"]
        if not result["company_name"] and sub.get("company_name"):
            result["company_name"] = sub["company_name"]

        if result["emails"] and result["phones"] and result["address"] and result["company_name"]:
            break

    return result

def find_cert_mentions(html_text: str, terms: List[str]) -> Tuple[bool, Optional[str]]:
    low = html_text.lower()
    for t in terms:
        if t.lower() in low:
            idx = low.find(t.lower())
            start = max(0, idx - 60)
            end = min(len(html_text), idx + 60)
            return True, html_text[start:end].strip()
    return False, None
