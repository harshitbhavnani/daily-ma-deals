import feedparser
import re
from datetime import datetime, timedelta
import html
from rapidfuzz import fuzz

def google_news_rss(query):
    q = query.replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    return feedparser.parse(url)

sector_queries = {
    "Consumer & Retail": '("acquires" OR "acquisition" OR "merger" OR "buys") AND (consumer OR retail OR fashion OR food)',
    "Healthcare": '("acquires" OR "acquisition" OR "merger") AND (healthcare OR pharma OR medtech)',
    "Technology": '("acquires" OR "acquisition" OR "merger") AND (software OR cloud OR AI OR SaaS)',
    "Business Services": '("acquires" OR "acquisition") AND ("business services" OR B2B OR outsourcing)',
}

def deduplicate_deals(deals, similarity_threshold=85):
    """
    Removes similar headlines from the deals list using fuzzy matching.
    Prefers higher-quality sources.
    """

    if not deals:
        return deals

    preferred_sources = [
        "Bloomberg", "Reuters", "CNBC", "Financial Times", "Yahoo Finance", 
        "Investopedia", "BusinessWire", "PR Newswire"
    ]

    unique_deals = []
    seen_titles = []

    for deal in sorted(deals, key=lambda x: preferred_sources.index(x['source']) if x['source'] in preferred_sources else 999):
        is_duplicate = False
        for seen in seen_titles:
            similarity = fuzz.token_set_ratio(deal['title'], seen)
            if similarity >= similarity_threshold:
                is_duplicate = True
                break

        if not is_duplicate:
            unique_deals.append(deal)
            seen_titles.append(deal['title'])

    print(f"[🧹] Deduplicated from {len(deals)} ➝ {len(unique_deals)} deals.")
    return unique_deals

TODAY = datetime.utcnow()
deals = []

for sector, query in sector_queries.items():
    print(f"[📡] Searching Google News for: {sector}")
    feed = google_news_rss(query)
    for entry in feed.entries:
        title = html.unescape(entry.title)
        link = entry.link
        summary = html.unescape(entry.summary)
        published = datetime(*entry.published_parsed[:6])

        # Filter by date (last 24h)
        if (TODAY - published).total_seconds() > 86400:
            continue

        # Filter obvious irrelevants
        if any(bad in title.lower() for bad in ["lawsuit", "fraud", "earnings", "ipo"]):
            continue

        deal_value = re.search(r"([$€£]\s?\d+[.,]?\d*\s?(million|billion|M|B)?)", summary, re.IGNORECASE)
        deal_value = deal_value.group(0) if deal_value else "Undisclosed"

        deals.append({
            "title": title,
            "link": link,
            "summary": summary,
            "date": published.strftime("%Y-%m-%d"),
            "sector": sector,
            "deal_value": deal_value,
            "source": entry.get("source", {}).get("title", "Google News")
        })

html_output = f"""<html>
<head>
  <meta charset="UTF-8">
  <title>Centerstone M&A Deal Landscape – {TODAY.strftime('%B %d, %Y')}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f9f9f9; }}
    h1 {{ color: #2c3e50; }}
    h2 {{ color: #37474f; margin-top: 40px; border-bottom: 2px solid #ccc; padding-bottom: 4px; }}
    .deal {{ padding: 15px; border-bottom: 1px solid #ddd; }}
    .meta {{ color: #555; font-size: 0.9em; }}
    a {{ color: #007acc; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>🧾 Centerstone Capital – Daily M&A Landscape<br><small>{TODAY.strftime('%A, %B %d, %Y')}</small></h1>
"""

# Deduplicate before generating the HTML
deals = deduplicate_deals(deals)

if not deals:
    html_output += "<p>No relevant deals found in the past 24 hours.</p>"
else:
    sectors_in_order = ["Consumer & Retail", "Healthcare", "Technology", "Business Services", "Other"]
    for sector in sectors_in_order:
        sector_deals = [d for d in deals if d["sector"] == sector]
        if not sector_deals:
            continue
        html_output += f"<h2>{sector}</h2>"
        for d in sector_deals:
            html_output += f"""
            <div class="deal">
                <a href="{d['link']}" target="_blank"><strong>{d['title']}</strong></a><br>
                <span class="meta">📅 {d['date']} | 💰 {d['deal_value']} | 📰 {d['source']}</span>
                <p>{d['summary']}</p>
            </div>
            """

html_output += "</body></html>"

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_output)
