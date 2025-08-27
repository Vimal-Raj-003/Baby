import os
import requests
from typing import List, Dict, Optional

SERPAPI_URL = "https://serpapi.com/search.json"

class SerpAPISearcher:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("SERPAPI_API_KEY is not set. Please set it in your .env or Streamlit sidebar.")

    def search(self, query: str, location: Optional[str] = None, num: int = 10) -> List[Dict]:
        params = {
            "engine": "google",
            "q": query,
            "api_key": self.api_key,
            "num": min(max(num, 1), 100),
            "hl": "en",
        }
        if location:
            params["location"] = location

        r = requests.get(SERPAPI_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        organic = data.get("organic_results", [])
        results = []
        for item in organic:
            results.append({
                "title": item.get("title"),
                "link": item.get("link"),
                "snippet": item.get("snippet"),
                "position": item.get("position"),
            })
        return results

# Optional: Hunter.io for email enrichment
class HunterClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("HUNTER_API_KEY", "").strip()

    def domain_search(self, domain: str, limit: int = 10) -> List[str]:
        if not self.api_key:
            return []
        try:
            url = "https://api.hunter.io/v2/domain-search"
            params = {"domain": domain, "api_key": self.api_key, "limit": limit}
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            emails = [e.get("value") for e in data.get("data", {}).get("emails", []) if e.get("value")]
            return emails
        except Exception:
            return []
