import os
import csv
import json
import requests
from datetime import datetime
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY")
URL = "https://min-api.cryptocompare.com/data/v2/news/"
HEADERS = {"Content-type": "application/json; charset=UTF-8"}
PARAMS = {"lang": "EN", "api_key": API_KEY}

def fetch_crypto_news():
    """Fetch latest news from CryptoCompare API"""
    resp = requests.get(URL, params=PARAMS, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()

def save_to_csv_with_body(data, timestamp):
    """Save news data to CSV file with full body text"""
    os.makedirs("data/csv_exports", exist_ok=True)

    filename = f"data/csv_exports/crypto_news_with_body_{timestamp}.csv"

    articles = data.get("Data", [])

    if not articles:
        print("No articles to save")
        return filename

    # CSV fields - now including 'body' field
    fieldnames = [
        "id", "title", "published_on", "source", "url",
        "categories", "tags", "lang", "source_name",
        "body", "body_length", "has_image", "imageurl",
        "upvotes", "downvotes"
    ]

    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for article in articles:
            body_text = article.get("body", "")
            writer.writerow({
                "id": article.get("id", ""),
                "title": article.get("title", ""),
                "published_on": datetime.fromtimestamp(article.get("published_on", 0)).strftime("%Y-%m-%d %H:%M:%S"),
                "source": article.get("source", ""),
                "url": article.get("url", ""),
                "categories": article.get("categories", ""),
                "tags": article.get("tags", ""),
                "lang": article.get("lang", ""),
                "source_name": article.get("source_info", {}).get("name", ""),
                "body": body_text,
                "body_length": len(body_text),
                "has_image": "Yes" if article.get("imageurl") else "No",
                "imageurl": article.get("imageurl", ""),
                "upvotes": article.get("upvotes", "0"),
                "downvotes": article.get("downvotes", "0")
            })

    print(f"✅ Saved {len(articles)} articles with body text to {filename}")
    return filename

def save_full_json(data, timestamp):
    """Save full JSON response"""
    os.makedirs("data/json_exports", exist_ok=True)

    filename = f"data/json_exports/crypto_news_with_body_{timestamp}.json"

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved full JSON to {filename}")
    return filename

def analyze_data(data, timestamp):
    """Perform comprehensive analysis on the news data"""
    articles = data.get("Data", [])

    if not articles:
        print("No articles to analyze")
        return {}

    # Extract analysis data
    sources = [a.get("source", "") for a in articles]
    source_names = [a.get("source_info", {}).get("name", "") for a in articles]
    categories_raw = [a.get("categories", "") for a in articles]
    tags_raw = [a.get("tags", "") for a in articles]

    # Parse categories
    all_categories = []
    for cat_str in categories_raw:
        if cat_str:
            all_categories.extend(cat_str.split("|"))

    # Parse tags
    all_tags = []
    for tag_str in tags_raw:
        if tag_str:
            all_tags.extend(tag_str.split("|"))

    # Count occurrences
    source_counts = Counter(sources)
    source_name_counts = Counter(source_names)
    category_counts = Counter(all_categories)
    tag_counts = Counter(all_tags)

    # Calculate stats
    body_lengths = [len(a.get("body", "")) for a in articles]
    avg_body_length = sum(body_lengths) / len(body_lengths) if body_lengths else 0
    min_body_length = min(body_lengths) if body_lengths else 0
    max_body_length = max(body_lengths) if body_lengths else 0

    has_image_count = sum(1 for a in articles if a.get("imageurl"))
    image_percentage = (has_image_count / len(articles) * 100) if articles else 0

    # Get publication times
    pub_times = [datetime.fromtimestamp(a.get("published_on", 0)) for a in articles]
    oldest = min(pub_times) if pub_times else None
    newest = max(pub_times) if pub_times else None

    analysis = {
        "timestamp": timestamp,
        "total_articles": len(articles),
        "unique_sources": len(source_counts),
        "unique_categories": len(category_counts),
        "unique_tags": len(tag_counts),
        "body_stats": {
            "avg_length": round(avg_body_length, 2),
            "min_length": min_body_length,
            "max_length": max_body_length
        },
        "image_percentage": round(image_percentage, 2),
        "date_range": {
            "oldest": oldest.strftime("%Y-%m-%d %H:%M:%S") if oldest else "",
            "newest": newest.strftime("%Y-%m-%d %H:%M:%S") if newest else ""
        },
        "top_sources": dict(source_name_counts.most_common(10)),
        "top_categories": dict(category_counts.most_common(15)),
        "top_tags": dict(tag_counts.most_common(15))
    }

    # Save analysis
    os.makedirs("data/analysis", exist_ok=True)
    analysis_file = f"data/analysis/analysis_with_body_{timestamp}.json"

    with open(analysis_file, 'w', encoding='utf-8') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved analysis to {analysis_file}")

    # Print summary
    print(f"\n{'='*60}")
    print(f"📊 ANALYSIS SUMMARY - {timestamp}")
    print(f"{'='*60}")
    print(f"Total Articles: {analysis['total_articles']}")
    print(f"Unique Sources: {analysis['unique_sources']}")
    print(f"Body Length Stats:")
    print(f"  Average: {analysis['body_stats']['avg_length']} chars")
    print(f"  Min: {analysis['body_stats']['min_length']} chars")
    print(f"  Max: {analysis['body_stats']['max_length']} chars")
    print(f"Articles with Images: {analysis['image_percentage']}%")
    print(f"\nDate Range:")
    print(f"  Oldest: {analysis['date_range']['oldest']}")
    print(f"  Newest: {analysis['date_range']['newest']}")
    print(f"\nTop 5 Sources:")
    for source, count in list(analysis['top_sources'].items())[:5]:
        print(f"  {source}: {count} articles")
    print(f"\nTop 10 Categories:")
    for cat, count in list(analysis['top_categories'].items())[:10]:
        print(f"  {cat}: {count}")
    print(f"{'='*60}\n")

    return analysis

if __name__ == "__main__":
    print("🚀 Fetching latest crypto news with full body text...")

    # Generate timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Fetch data
    data = fetch_crypto_news()
    print(f"✅ Fetched {len(data.get('Data', []))} articles\n")

    # Save to CSV with body text
    csv_file = save_to_csv_with_body(data, timestamp)

    # Save full JSON
    json_file = save_full_json(data, timestamp)

    # Analyze
    analysis = analyze_data(data, timestamp)

    print(f"\n✅ All operations completed successfully!")
    print(f"   CSV with Body: {csv_file}")
    print(f"   JSON: {json_file}")
    print(f"   Analysis: data/analysis/analysis_with_body_{timestamp}.json")