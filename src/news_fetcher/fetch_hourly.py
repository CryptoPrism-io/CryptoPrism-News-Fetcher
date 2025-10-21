import os
import csv
import json
import requests
import time
import glob
from datetime import datetime, timedelta
from collections import Counter
from dotenv import load_dotenv
from . import db_cc_news

load_dotenv()

API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY")
URL = "https://min-api.cryptocompare.com/data/v2/news/"
HEADERS = {"Content-type": "application/json; charset=UTF-8"}

# Sources that don't provide body text (excluded from fetching)
EXCLUDED_SOURCES = [
    'investing_comcryptonews',                  # Investing.com Crypto News
    'investing_comcryptoopinionandanalysis'     # Investing.Com Crypto Opinion and Analysis
]

def filter_articles(articles):
    """
    Filter out sources that don't provide body text.

    Args:
        articles: List of article dictionaries

    Returns:
        list: Filtered articles (excluded sources removed)
    """
    filtered = [a for a in articles if a.get('source', '') not in EXCLUDED_SOURCES]
    excluded_count = len(articles) - len(filtered)

    if excluded_count > 0:
        print(f"   ⚠️  Filtered out {excluded_count} articles from Investing.com (no body text)")

    return filtered

def fetch_crypto_news_hourly(hours_back=1):
    """
    Fetch news from the last X hours using timestamp filtering

    Args:
        hours_back: Number of hours to look back (default: 1)

    Returns:
        dict: Filtered news data containing only articles from the specified time range
    """
    # Calculate timestamp for X hours ago
    current_time = datetime.now()
    time_threshold = current_time - timedelta(hours=hours_back)
    timestamp_threshold = int(time_threshold.timestamp())

    print(f"📅 Fetching articles from last {hours_back} hour(s)")
    print(f"   Time range: {time_threshold.strftime('%Y-%m-%d %H:%M:%S')} to {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Unix timestamp threshold: {timestamp_threshold}\n")

    # Fetch news with API key
    params = {
        "lang": "EN",
        "api_key": API_KEY
    }

    all_articles = []
    page = 0
    max_pages = 10  # Fetch up to 10 pages (500 articles max)

    while page < max_pages:
        # Add lTs (latest timestamp) parameter for pagination
        if all_articles:
            # Get the oldest article timestamp from previous batch
            last_timestamp = min(a.get("published_on", 0) for a in all_articles)
            params["lTs"] = last_timestamp

        resp = requests.get(URL, params=params, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()

        articles = data.get("Data", [])

        if not articles:
            print(f"   No more articles found (page {page + 1})")
            break

        # Filter articles by timestamp
        new_articles = [a for a in articles if a.get("published_on", 0) >= timestamp_threshold]

        if not new_articles:
            print(f"   Reached articles older than {hours_back} hour(s) (page {page + 1})")
            break

        # Filter out sources without body text (e.g., Investing.com)
        new_articles = filter_articles(new_articles)

        if not new_articles:
            print(f"   All articles filtered out on page {page + 1}")
            # Continue to next page in case there are valid articles later
            page += 1
            time.sleep(0.5)
            continue

        all_articles.extend(new_articles)
        print(f"   Page {page + 1}: Found {len(new_articles)} articles within time range (after filtering)")

        # If we got fewer articles than expected, we've likely reached the threshold
        if len(new_articles) < len(articles):
            break

        page += 1
        time.sleep(0.5)  # Rate limiting

    print(f"\n✅ Total articles fetched: {len(all_articles)}\n")

    return {
        "Type": 100,
        "Message": f"News from last {hours_back} hour(s)",
        "Data": all_articles,
        "fetch_params": {
            "hours_back": hours_back,
            "timestamp_threshold": timestamp_threshold,
            "time_range_start": time_threshold.strftime('%Y-%m-%d %H:%M:%S'),
            "time_range_end": current_time.strftime('%Y-%m-%d %H:%M:%S')
        }
    }

def save_to_csv_with_body(data, timestamp):
    """Save news data to CSV file with full body text"""
    os.makedirs("data/csv_exports", exist_ok=True)

    filename = f"data/csv_exports/crypto_news_hourly_{timestamp}.csv"

    articles = data.get("Data", [])

    if not articles:
        print("⚠️  No articles to save")
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

    filename = f"data/json_exports/crypto_news_hourly_{timestamp}.json"

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved full JSON to {filename}")
    return filename

def analyze_data(data, timestamp):
    """Perform comprehensive analysis on the news data"""
    articles = data.get("Data", [])

    if not articles:
        print("⚠️  No articles to analyze")
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

    # Calculate time span
    time_span_minutes = 0
    if oldest and newest:
        time_span_minutes = (newest - oldest).total_seconds() / 60

    analysis = {
        "timestamp": timestamp,
        "fetch_params": data.get("fetch_params", {}),
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
            "newest": newest.strftime("%Y-%m-%d %H:%M:%S") if newest else "",
            "time_span_minutes": round(time_span_minutes, 2)
        },
        "top_sources": dict(source_name_counts.most_common(10)),
        "top_categories": dict(category_counts.most_common(15)),
        "top_tags": dict(tag_counts.most_common(15))
    }

    # Save analysis
    os.makedirs("data/analysis", exist_ok=True)
    analysis_file = f"data/analysis/analysis_hourly_{timestamp}.json"

    with open(analysis_file, 'w', encoding='utf-8') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved analysis to {analysis_file}")

    # Print summary
    print(f"\n{'='*60}")
    print(f"📊 HOURLY ANALYSIS SUMMARY - {timestamp}")
    print(f"{'='*60}")

    fetch_params = analysis.get("fetch_params", {})
    if fetch_params:
        print(f"Time Range: {fetch_params.get('time_range_start')} to {fetch_params.get('time_range_end')}")
        print(f"Hours Back: {fetch_params.get('hours_back')}")
        print(f"-" * 60)

    print(f"Total Articles: {analysis['total_articles']}")
    print(f"Unique Sources: {analysis['unique_sources']}")
    print(f"Time Span: {analysis['date_range']['time_span_minutes']:.1f} minutes")
    print(f"\nBody Length Stats:")
    print(f"  Average: {analysis['body_stats']['avg_length']} chars")
    print(f"  Min: {analysis['body_stats']['min_length']} chars")
    print(f"  Max: {analysis['body_stats']['max_length']} chars")
    print(f"Articles with Images: {analysis['image_percentage']}%")
    print(f"\nActual Date Range:")
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

def cleanup_old_exports(keep_latest=1):
    """
    Clean up old CSV and JSON export files, keeping only the latest N files.

    Args:
        keep_latest: Number of latest files to keep (default: 1)
    """
    print(f"\n🧹 Cleaning up old export files (keeping latest {keep_latest})...")

    # Define patterns for files to clean
    patterns = [
        "data/csv_exports/crypto_news_hourly_*.csv",
        "data/json_exports/crypto_news_hourly_*.json",
        "data/analysis/analysis_hourly_*.json"
    ]

    total_deleted = 0

    for pattern in patterns:
        files = sorted(glob.glob(pattern), reverse=True)  # Newest first

        if len(files) > keep_latest:
            files_to_delete = files[keep_latest:]

            for file in files_to_delete:
                try:
                    os.remove(file)
                    total_deleted += 1
                    print(f"   🗑️  Deleted: {os.path.basename(file)}")
                except Exception as e:
                    print(f"   ⚠️  Could not delete {file}: {e}")

    if total_deleted == 0:
        print("   ✅ No old files to clean up")
    else:
        print(f"   ✅ Cleaned up {total_deleted} old files")

def push_to_database(data):
    """
    Push fetched articles to the PostgreSQL database.

    Args:
        data: News data dictionary with 'Data' key containing articles

    Returns:
        dict: Statistics about the database operation
    """
    print("\n💾 Pushing articles to database...")

    try:
        # Ensure table exists
        db_cc_news.create_cc_news_table()

        # Insert articles
        articles = data.get("Data", [])
        stats = db_cc_news.insert_articles(articles)

        return stats
    except Exception as e:
        print(f"❌ Database operation failed: {e}")
        return {"inserted": 0, "duplicates": 0, "errors": len(data.get("Data", []))}

if __name__ == "__main__":
    print("🚀 Fetching crypto news from the last hour (timestamp-based)...\n")

    # Generate timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Fetch data from last 1 hour
    data = fetch_crypto_news_hourly(hours_back=1)

    if not data.get("Data"):
        print("❌ No articles found in the specified time range")
        exit(1)

    # Save to CSV with body text
    csv_file = save_to_csv_with_body(data, timestamp)

    # Save full JSON
    json_file = save_full_json(data, timestamp)

    # Analyze
    analysis = analyze_data(data, timestamp)

    # Push to database
    db_stats = push_to_database(data)

    # Cleanup old exports (keep only latest)
    cleanup_old_exports(keep_latest=1)

    # Final summary
    print(f"\n{'='*60}")
    print(f"✅ HOURLY RUN COMPLETED - {timestamp}")
    print(f"{'='*60}")
    print(f"Articles Fetched: {len(data.get('Data', []))}")
    print(f"New in Database: {db_stats.get('inserted', 0)}")
    print(f"Duplicates Skipped: {db_stats.get('duplicates', 0)}")
    print(f"\nExported Files:")
    print(f"  📄 CSV: {csv_file}")
    print(f"  📦 JSON: {json_file}")
    print(f"  📊 Analysis: data/analysis/analysis_hourly_{timestamp}.json")
    print(f"{'='*60}\n")
    print(f"💡 Ready for next hourly run!")
