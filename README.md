# Supplier Finder (Commodity × Region × Certification)

A Streamlit web app that takes three inputs — **Commodity**, **Region**, and **Certification** —
then performs a structured web search (via SerpAPI) and scrapes likely supplier websites to extract:
- Supplier Name
- Website Link
- Contact Address
- Contact Email
- Contact Phone
- Certification Evidence (snippets/keywords detected)

Optionally, it can use the **OpenAI API** to improve contact/address extraction from messy pages
and **Hunter.io** to enrich email discovery (if you provide a Hunter API key).

---

## 1) Prerequisites
- Python 3.10+
- A SerpAPI API key (https://serpapi.com/)
- (Optional) An OpenAI API key for smarter parsing (https://platform.openai.com/)
- (Optional) A Hunter.io API key for email enrichment (https://hunter.io/)

## 2) Setup in VS Code (Windows-friendly)
```bash
# 1) Open a Terminal in VS Code (Ctrl+`)
# 2) Navigate to the project folder
cd supplier_finder

# 3) Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\activate

# 4) Install dependencies
pip install -r requirements.txt

# 5) Create your .env file from template
copy .env.example .env
# Edit .env and paste your keys

# 6) Run the app
streamlit run app.py
```

If you're on macOS/Linux, activation is:
```bash
source .venv/bin/activate
```

## 3) How to Use
1. Enter **Commodity**, **Region**, and **Certification**.
2. Choose how many Google results to scan (defaults to 20).
3. Toggle **Use OpenAI extraction** if you have an OpenAI key (helps with addresses).
4. Click **Search** to fetch and parse supplier info.
5. Review results and **Download CSV/Excel** from the app.

## 4) Notes
- Be mindful of API usage and quotas (SerpAPI, OpenAI, Hunter).
- Websites vary a lot; contact extraction is heuristic and may need review.
- Consider refining queries or increasing result count for broader coverage.
