import feedparser
import re
from datetime import datetime, timedelta
import html
from rapidfuzz import fuzz
from collections import defaultdict
import hashlib
import string
import os

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

def create_deal_signature(title, source):
    """Create a unique signature for exact duplicate detection"""
    # Clean title for signature
    clean_title = re.sub(r'\s*-\s*[A-Z][a-zA-Z\s&.]+$', '', title)  # Remove source suffix
    clean_title = re.sub(r'\s+', ' ', clean_title).strip().lower()
    
    # Create signature from title + source
    signature = f"{clean_title}|{source.lower()}"
    return hashlib.md5(signature.encode()).hexdigest()

def extract_deal_entities(title):
    """Extract company names and deal type from title"""
    # Remove source attribution and common prefixes
    title = re.sub(r'\s*-\s*[A-Z][a-zA-Z\s&.]+$', '', title)
    title = re.sub(r'^(reports?:?\s*|report\s+says:?\s*)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'(,?\s*report\s+says?|,?\s*reports?)$', '', title, flags=re.IGNORECASE)
    
    # Patterns to extract acquirer, target, and deal type
    patterns = [
        # Standard: "Company A acquires Company B"
        (r'([A-Z][a-zA-Z\s&.\'\-]+?)\s+(acquires?|buys?)\s+([A-Z][a-zA-Z\s&.\'\-]+?)(?:\s+(?:for|to|from|in|$))', 'acquisition'),
        
        # Board approval: "Company board approves acquisition of Target"
        (r'([A-Z][a-zA-Z\s&.\'\-]+?)\s+board\s+approves\s+acquisition\s+of\s+([A-Z][a-zA-Z\s&.\'\-]+?)(?:\s+(?:for|to|from|in|$))', 'acquisition'),
        
        # Board approval reversed: "Board of Company approves acquisition of Target"
        (r'board\s+of\s+([A-Z][a-zA-Z\s&.\'\-]+?)\s+approves\s+acquisition\s+of\s+([A-Z][a-zA-Z\s&.\'\-]+?)(?:\s+(?:for|to|from|in|$))', 'acquisition'),
        
        # Talks: "Company A in talks to acquire Company B"
        (r'([A-Z][a-zA-Z\s&.\'\-]+?)\s+in\s+talks\s+to\s+acquire\s+([A-Z][a-zA-Z\s&.\'\-]+?)(?:\s+(?:for|to|from|in|$))', 'acquisition'),
        
        # Mulling: "Company A mulling acquisition of Company B"
        (r'([A-Z][a-zA-Z\s&.\'\-]+?)\s+mulling\s+acquisition\s+of\s+([A-Z][a-zA-Z\s&.\'\-]+?)(?:\s+(?:for|to|from|in|$))', 'acquisition'),
        
        # Takeover interest: "Company B draws takeover interest from Company A"
        (r'([A-Z][a-zA-Z\s&.\'\-]+?)\s+draws\s+takeover\s+interest\s+from\s+([A-Z][a-zA-Z\s&.\'\-]+?)(?:\s+(?:for|to|from|in|$))', 'acquisition'),
        
        # Completion: "Company A completes acquisition of Company B"
        (r'([A-Z][a-zA-Z\s&.\'\-]+?)\s+completes?\s+(?:\d+\w+\s+)?acquisition\s+(?:of\s+|with\s+)?([A-Z][a-zA-Z\s&.\'\-]+?)(?:\s+(?:for|to|from|in|$))', 'acquisition'),
        
        # Expands through: "Company A expands portfolio through acquisition of Company B"
        (r'([A-Z][a-zA-Z\s&.\'\-]+?)\s+expands?.*?through.*?acquisition\s+of\s+([A-Z][a-zA-Z\s&.\'\-]+?)(?:\s+(?:for|to|from|in|$))', 'acquisition'),
    ]
    
    for pattern, deal_type in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            entity1 = match.group(1).strip()
            entity2 = match.group(2).strip() if len(match.groups()) > 1 else None
            
            # Clean up entities
            entity1 = re.sub(r'\s+(Inc|Corp|Ltd|LLC|Group|Holdings?|Co)\.?$', '', entity1, flags=re.IGNORECASE)
            if entity2:
                entity2 = re.sub(r'\s+(Inc|Corp|Ltd|LLC|Group|Holdings?|Co)\.?$', '', entity2, flags=re.IGNORECASE)
            
            # Handle special cases where target and acquirer are swapped
            if 'draws takeover interest from' in title.lower():
                return entity2.lower(), entity1.lower(), deal_type  # Swap for takeover interest
            else:
                return entity1.lower(), entity2.lower() if entity2 else None, deal_type
    
    return None, None, None

def calculate_deal_similarity(deal1, deal2):
    """Calculate similarity between two deals using multiple methods"""
    
    # Extract entities from both deals
    acquirer1, target1, type1 = extract_deal_entities(deal1['title'])
    acquirer2, target2, type2 = extract_deal_entities(deal2['title'])
    
    # Method 1: Exact entity match
    if acquirer1 and target1 and acquirer2 and target2:
        if acquirer1 == acquirer2 and target1 == target2:
            return 100  # Perfect match
        
        # Check for partial matches (same acquirer, different target names for same company)
        if acquirer1 == acquirer2:
            target_similarity = fuzz.ratio(target1, target2)
            if target_similarity >= 80:
                return 95  # Very high confidence same deal
    
    # Method 2: Fuzzy title matching after normalization
    def normalize_title(title):
        # Remove source attribution
        title = re.sub(r'\s*-\s*[A-Z][a-zA-Z\s&.]+$', '', title)
        # Remove report says, etc.
        title = re.sub(r'^(reports?:?\s*|report\s+says:?\s*)', '', title, flags=re.IGNORECASE)
        title = re.sub(r'(,?\s*report\s+says?|,?\s*reports?)$', '', title, flags=re.IGNORECASE)
        
        # Normalize acquisition language
        replacements = [
            ('mulling acquisition of', 'acquires'),
            ('in talks to acquire', 'acquires'),
            ('draws takeover interest from', 'target of'),
            ('board approves acquisition of', 'acquires'),
            ('completes acquisition of', 'acquires'),
            ('to acquire', 'acquires'),
            ('buys', 'acquires'),
        ]
        
        for old, new in replacements:
            title = re.sub(r'\b' + re.escape(old) + r'\b', new, title, flags=re.IGNORECASE)
        
        return re.sub(r'\s+', ' ', title).strip().lower()
    
    norm1 = normalize_title(deal1['title'])
    norm2 = normalize_title(deal2['title'])
    
    title_similarity = fuzz.ratio(norm1, norm2)
    
    # Method 3: Token-based similarity
    tokens1 = set(re.findall(r'\b[a-zA-Z]{3,}\b', norm1))
    tokens2 = set(re.findall(r'\b[a-zA-Z]{3,}\b', norm2))
    
    if tokens1 and tokens2:
        intersection = len(tokens1 & tokens2)
        union = len(tokens1 | tokens2)
        jaccard = (intersection / union) * 100 if union > 0 else 0
        
        # Combine title similarity and token similarity
        combined_similarity = max(title_similarity, jaccard)
        return combined_similarity
    
    return title_similarity

STOPWORDS = {
    'the', 'and', 'of', 'to', 'in', 'a', 'on', 'for', 'at', 'with', 'as',
    'by', 'an', 'from', 'is', 'this', 'that', 'be', 'after', 'its', 'via',
    'are', 'has', 'have', 'into', 'over', 'will', 'deal', 'buys', 'buying',
    'acquires', 'acquisition', 'takeover', 'merger', 'company', 'firm', 'group'
}

def normalize(title):
    title = title.lower().translate(str.maketrans('', '', string.punctuation))
    words = set(w for w in title.split() if w not in STOPWORDS)
    org_words = set(w for w in words if w in {
        'us', 'foods', 'performance', 'dodla', 'dairy', 'nvidia', 'becu',
        'openai', 'symplr', 'amn', 'healthcare', 'clarity', 'ecolytiq',
        'xealth', 'samsung', 'intuit', 'relevvo', 'pet', 'nutrition',
        'sopral', 'pupil', 'morliny'
    })
    return words, org_words

def deduplicate_deals(deals):
    print(f"[🧹] Starting deduplication of {len(deals)} deals...")

    # 1. Remove exact duplicates
    seen_signatures = set()
    unique_deals = []
    for deal in deals:
        sig = deal['title'].strip().lower() + deal['source'].strip().lower()
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            unique_deals.append(deal)

    print(f"[🧹] After removing exact duplicates: {len(unique_deals)} deals")

    # 2. Filter noisy sources
    preferred_sources = {
        'Bloomberg': 10, 'Reuters': 9, 'Financial Times': 9, 'Wall Street Journal': 9,
        'CNBC': 8, 'MarketWatch': 8, 'Yahoo Finance': 8, 'Forbes': 8,
        'TechCrunch': 7, 'Business Standard': 7, 'CNBC TV18': 7
    }

    noise_sources = {
        'Stocktwits', 'MSN', 'Seeking Alpha', 'The Motley Fool', 'Benzinga',
        'InvestorPlace', 'Zacks', 'TipRanks', 'Finbold', 'CoinCentral'
    }

    filtered = []
    for deal in unique_deals:
        if any(ns.lower() in deal['source'].lower() for ns in noise_sources):
            continue

        title = deal['title'].lower()
        if any(skip in title for skip in [
            'stock jumps', 'stock soars', 'shares surge', 'ipo', 'lawsuit', 'dividend',
            'price target', 'earnings', 'fraud', 'downgrade', 'upgrade',
            'luxury item', 'auction', 'resident buys', 'retail space'
        ]):
            continue

        filtered.append(deal)

    print(f"[🧹] After filtering noisy entries: {len(filtered)} deals")

    # 3. Deduplicate by fuzzy + org-word overlap
    deduped = []
    used = set()
    titles_normalized = [normalize(d['title']) for d in filtered]

    for i, base in enumerate(filtered):
        if i in used:
            continue

        base_words, base_org = titles_normalized[i]
        similar = [base]
        used.add(i)

        for j in range(i + 1, len(filtered)):
            if j in used:
                continue

            other_words, other_org = titles_normalized[j]
            word_overlap = base_words & other_words
            org_overlap = base_org & other_org

            if len(word_overlap) >= 3 or len(org_overlap) >= 2:
                similar.append(filtered[j])
                used.add(j)

        # Choose best source
        def score(d):
            return -preferred_sources.get(d['source'], 0), -len(d['title'])

        best = sorted(similar, key=score)[0]

        if len(similar) > 1:
            best['related_sources'] = [
                {
                    'title': d['title'],
                    'source': d['source'],
                    'link': d['link']
                }
                for d in similar if d != best
            ]
            print(f"[🔗] Grouped {len(similar)} deals into one: {best['title'][:80]}...")

        deduped.append(best)

    print(f"[✅] Final unique deals: {len(deduped)}")
    return deduped

# Main execution
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

        # Get source name
        source = entry.get("source", {}).get("title", "Google News")
        
        deals.append({
            "title": title,
            "link": link,
            "summary": summary,
            "date": published.strftime("%Y-%m-%d"),
            "sector": sector,
            "source": source
        })

print(f"[📊] Collected {len(deals)} raw articles")

# Apply production-level deduplication
deals = deduplicate_deals(deals)

# Extra duplicate removal based on entity pair
seen_entity_pairs = set()
final_deals = []

for deal in deals:
    acquirer, target, _ = extract_deal_entities(deal["title"])
    if not acquirer or not target:
        final_deals.append(deal)  # Include if we can't extract properly
        continue

    # Normalize names
    key1 = (acquirer.lower(), target.lower())
    key2 = (target.lower(), acquirer.lower())

    if key1 in seen_entity_pairs or key2 in seen_entity_pairs:
        continue  # Duplicate, skip

    seen_entity_pairs.add(key1)
    final_deals.append(deal)

deals = final_deals

# Generate HTML output
html_output = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Centerstone M&A Deal Landscape – {TODAY.strftime('%B %d, %Y')}</title>
    <style>
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 40px;
            background-color: #f8f9fa;
            color: #333;
            line-height: 1.6;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ 
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
            margin-bottom: 30px;
        }}
        .subtitle {{ color: #7f8c8d; font-size: 0.9em; margin-top: 5px; }}
        h2 {{ 
            color: #37474f;
            margin-top: 40px;
            margin-bottom: 20px;
            border-left: 4px solid #3498db;
            padding-left: 15px;
            background: white;
            padding: 15px;
            border-radius: 5px;
        }}
        .deal {{
            background: white;
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            border-left: 4px solid #3498db;
        }}
        .deal-title {{
            font-size: 1.1em;
            font-weight: 600;
            margin-bottom: 10px;
        }}
        .deal-title a {{
            color: #2c3e50;
            text-decoration: none;
        }}
        .deal-title a:hover {{
            color: #3498db;
            text-decoration: underline;
        }}
        .meta {{
            color: #7f8c8d;
            font-size: 0.9em;
            margin-bottom: 10px;
        }}
        .summary {{
            color: #555;
            margin-bottom: 10px;
        }}
        .related {{
            margin-top: 15px;
            padding-top: 15px;
            border-top: 1px solid #ecf0f1;
        }}
        .related-title {{
            font-weight: 600;
            color: #7f8c8d;
            margin-bottom: 8px;
            font-size: 0.9em;
        }}
        .related-item {{
            margin: 8px 0;
            padding-left: 15px;
            font-size: 0.9em;
            color: #666;
        }}
        .related-item a {{
            color: #3498db;
            text-decoration: none;
        }}
        .related-item a:hover {{
            text-decoration: underline;
        }}
        .stats {{
            background: white;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .no-deals {{
            text-align: center;
            color: #7f8c8d;
            font-style: italic;
            padding: 40px;
            background: white;
            border-radius: 8px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div style="display: flex; justify-content: space-between; align-items: baseline;">
    <h1>
        🧾 Centerstone Capital – Daily M&A Landscape
        <div class="subtitle">{TODAY.strftime('%A, %B %d, %Y')}</div>
    </h1>
    <a href="archives/index.html" style="font-size: 0.9em; color: #3498db; text-decoration: none;">📚 Archives</a>
</div>
        
        <div class="stats">
            <strong>📊 Daily Summary:</strong> {len(deals)} unique M&A deals identified and curated
        </div>
"""

if not deals:
    html_output += '<div class="no-deals">No relevant M&A deals found in the past 24 hours.</div>'
else:
    # Group by sector and display
    sectors_in_order = ["Consumer & Retail", "Healthcare", "Technology", "Business Services"]
    
    for sector in sectors_in_order:
        sector_deals = [d for d in deals if d["sector"] == sector]
        if not sector_deals:
            continue
            
        html_output += f'<h2>{sector} ({len(sector_deals)} deals)</h2>'
        
        for deal in sector_deals:
            html_output += f'''
            <div class="deal">
                <div class="deal-title">
                    <a href="{deal['link']}" target="_blank">{deal['title']}</a>
                </div>
                <div class="meta">
                    📅 {deal['date']} | 📰 {deal['source']}
                </div>
            '''
            
            # Add related sources if any
            if deal.get('related_sources'):
                html_output += '<div class="related">'
                html_output += '<div class="related-title">📄 Related Coverage:</div>'
                for related in deal['related_sources']:
                    html_output += f'''
                    <div class="related-item">
                        • <a href="{related['link']}" target="_blank">{related['title']}</a>
                        <em>({related['source']})</em>
                    </div>
                    '''
                html_output += '</div>'
            
            html_output += '</div>'

html_output += '''
    </div>
</body>
</html>
'''

# Archive yesterday's main index.html if it exists
yesterday = TODAY - timedelta(days=1)
yesterday_archive_path = f"archives/{yesterday.strftime('%Y-%m')}/{yesterday.strftime('%d')}.html"

if os.path.exists("index.html") and not os.path.exists(yesterday_archive_path):
    os.makedirs(os.path.dirname(yesterday_archive_path), exist_ok=True)
    os.rename("index.html", yesterday_archive_path)
    print(f"[📦] Archived yesterday's report to {yesterday_archive_path}")

# Write output
with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_output)

print(f"[✅] Generated report with {len(deals)} deals saved to index.html")

archive_dir = f"archives/{TODAY.strftime('%Y-%m')}"
archive_path = f"{archive_dir}/{TODAY.strftime('%d')}.html"

os.makedirs(archive_dir, exist_ok=True)
with open(archive_path, "w", encoding="utf-8") as f:
    f.write(html_output)
print(f"[🗂️] Saved archive to {archive_path}")

archive_index_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Archives – Centerstone M&A Landscape</title>
    <style>
        body { font-family: sans-serif; padding: 40px; background-color: #f8f9fa; }
        h1 { color: #2c3e50; margin-bottom: 30px; }
        ul { list-style: none; padding: 0; }
        li { margin: 10px 0; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <h1>📚 M&A Archives</h1>
"""

from glob import glob

archive_index_html += "<ul>\n"
for path in sorted(glob("archives/*/*.html"), reverse=True):
    if "index.html" in path:
        continue
    date_str = path.replace("archives/", "").replace(".html", "").replace("/", "-")
    rel_path = path.replace("archives/", "")
    archive_index_html += f'<li><a href="{rel_path}">{date_str}</a></li>\n'
archive_index_html += "</ul>\n</body></html>"

with open("archives/index.html", "w", encoding="utf-8") as f:
    f.write(archive_index_html)
print("[🧾] Updated archives index")
