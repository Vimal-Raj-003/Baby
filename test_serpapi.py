import os, requests
from dotenv import load_dotenv
load_dotenv()

key = os.getenv("SERPAPI_API_KEY", "")
assert key, "No SERPAPI_API_KEY found"

params = {
    "engine": "google",
    "q": "site:serpapi.com",
    "api_key": key,
    "num": 1
}
r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
print("Status:", r.status_code)
print(r.text[:500])
