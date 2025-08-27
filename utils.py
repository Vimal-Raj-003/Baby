# utils.py
import tldextract
from urllib.parse import urlparse

# ---- Marketplace / Directory blacklist (can expand anytime) ----
AGGREGATOR_BLACKLIST = [
    "indiamart.com", "dir.indiamart.com",
    "alibaba.com", "aliexpress.com", "1688.com",
    "made-in-china.com", "globalsources.com",
    "tradeindia.com", "exportersindia.com",
    "justdial.com", "yellowpages.com", "yellowpages.in", "yelp.com",
    "thomasnet.com",
    "amazon.com", "amazon.in", "amazon.co.in",
    "ebay.com", "ebay.in",
    "facebook.com", "linkedin.com", "instagram.com",
    "wikipedia.org", "wikimedia.org",
    "google.com", "maps.google.com",
]

SUPPLIER_HINT_WORDS = [
    "supplier","manufacturer","distributor","fabricator","oem","factory",
    "exporter","wholesaler","vendor","machining","stamping","molding","casting",
    "tooling","die casting","injection molding","cnc","sheet metal","foundry"
]

CERT_SYNONYMS = {
    "IATF 16949": ["IATF 16949","TS 16949"],
    "ISO 9001": ["ISO9001","ISO 9001","ISO-9001"],
    "ISO 13485": ["ISO 13485","ISO13485"],
    "ISO 14001": ["ISO 14001","ISO-14001","ISO14001"],
    "ISO 45001": ["ISO 45001","ISO45001","OHSAS 18001"],
    "RoHS": ["RoHS","Restriction of Hazardous Substances"],
    "REACH": ["REACH","Registration, Evaluation, Authorisation and Restriction of Chemicals"],
    "FDA": ["FDA","Food and Drug Administration"],
    "CE": ["CE","CE Marking"],
}

def domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return ""
        ext = tldextract.extract(url)
        if ext.registered_domain:
            return ext.registered_domain.lower()
        return parsed.netloc.lower()
    except Exception:
        return ""

def is_likely_supplier_result(title: str, snippet: str) -> bool:
    title_l = (title or "").lower()
    snip_l = (snippet or "").lower()
    return any(w in title_l or w in snip_l for w in SUPPLIER_HINT_WORDS)

def _negative_site_clause(blacklist: list[str]) -> str:
    # Append `-site:domain` for each blacklisted domain
    return " " + " ".join(f"-site:{d}" for d in blacklist)

def build_queries_rule_based(commodity: str, region: str, certification: str, blacklist: list[str] | None = None) -> list[str]:
    """Classic (non-LLM) query set with negative site filters."""
    base = f'"{commodity}" {region} "{certification}" supplier'
    alts = [
        f'"{commodity}" {region} "{certification}" manufacturer',
        f'"{commodity}" {region} "{certification}" OEM',
        f'"{commodity}" {region} factory "{certification}"',
        f'"{commodity}" {region} "{certification}" site:.co.in',
        f'"{commodity}" {region} "{certification}" site:.in',
    ]
    queries = [base] + alts
    if blacklist:
        neg = _negative_site_clause(blacklist)
        queries = [q + neg for q in queries]
    return queries

def is_blacklisted_domain(domain: str, blacklist: list[str]) -> bool:
    domain = (domain or "").lower()
    return any(domain.endswith(b) for b in blacklist)

def unique_keep_order(seq):
    seen = set(); out = []
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

def compile_cert_terms(certification: str) -> list[str]:
    terms = [certification]
    for k, v in CERT_SYNONYMS.items():
        if certification.lower() in k.lower() or certification.lower() in " ".join(v).lower():
            terms += v
    return unique_keep_order(terms)
