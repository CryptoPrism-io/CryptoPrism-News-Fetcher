import csv
import json
from datetime import datetime
from collections import Counter
import os

def analyze_old_csv(csv_path):
    """Analyze the old CoinDesk CSV data from October 1"""
    articles = []

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        articles = list(reader)

    # Extract data
    sources = [a.get("SOURCE_NAME", "") for a in articles]
    categories_raw = [a.get("CATEGORIES", "") for a in articles]
    sentiments = [a.get("SENTIMENT", "") for a in articles]

    # Parse categories
    all_categories = []
    for cat_str in categories_raw:
        if cat_str:
            all_categories.extend([c.strip() for c in cat_str.split(",")])

    # Count occurrences
    source_counts = Counter(sources)
    category_counts = Counter(all_categories)
    sentiment_counts = Counter(s for s in sentiments if s)

    # Calculate stats
    body_lengths = [int(a.get("BODY_LENGTH", 0)) for a in articles]
    avg_body_length = sum(body_lengths) / len(body_lengths) if body_lengths else 0

    # Publication times
    pub_times = []
    for a in articles:
        try:
            pub_times.append(datetime.fromtimestamp(int(a.get("PUBLISHED_ON", 0))))
        except:
            pass

    oldest = min(pub_times) if pub_times else None
    newest = max(pub_times) if pub_times else None

    analysis = {
        "timestamp": "20251001_145721",
        "source": "CoinDesk API",
        "total_articles": len(articles),
        "unique_sources": len(source_counts),
        "unique_categories": len(category_counts),
        "avg_body_length": round(avg_body_length, 2),
        "date_range": {
            "oldest": oldest.strftime("%Y-%m-%d %H:%M:%S") if oldest else "",
            "newest": newest.strftime("%Y-%m-%d %H:%M:%S") if newest else ""
        },
        "top_sources": dict(source_counts.most_common(10)),
        "top_categories": dict(category_counts.most_common(15)),
        "sentiment_distribution": dict(sentiment_counts)
    }

    return analysis

def load_new_analysis(json_path):
    """Load the new CryptoCompare analysis"""
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def compare_analyses(old, new):
    """Compare two analysis results"""
    comparison = {
        "comparison_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "old_dataset": {
            "timestamp": old["timestamp"],
            "source": old.get("source", "Unknown"),
            "total_articles": old["total_articles"],
            "unique_sources": old["unique_sources"],
            "avg_body_length": old["avg_body_length"]
        },
        "new_dataset": {
            "timestamp": new["timestamp"],
            "source": "CryptoCompare API",
            "total_articles": new["total_articles"],
            "unique_sources": new["unique_sources"],
            "avg_body_length": new["avg_body_length"]
        },
        "changes": {
            "article_count_change": new["total_articles"] - old["total_articles"],
            "source_diversity_change": new["unique_sources"] - old["unique_sources"],
            "avg_length_change": round(new["avg_body_length"] - old["avg_body_length"], 2),
            "avg_length_change_pct": round((new["avg_body_length"] - old["avg_body_length"]) / old["avg_body_length"] * 100, 2) if old["avg_body_length"] else 0
        },
        "category_comparison": {},
        "source_comparison": {},
        "insights": []
    }

    # Compare categories
    old_cats = set(old["top_categories"].keys())
    new_cats = set(new["top_categories"].keys())

    comparison["category_comparison"] = {
        "old_unique_categories": old["unique_categories"],
        "new_unique_categories": new["unique_categories"],
        "common_categories": list(old_cats & new_cats),
        "new_trending_categories": list(new_cats - old_cats),
        "disappeared_categories": list(old_cats - new_cats),
        "old_top_5": dict(list(old["top_categories"].items())[:5]),
        "new_top_5": dict(list(new["top_categories"].items())[:5])
    }

    # Compare sources
    old_sources = set(old["top_sources"].keys())
    new_sources = set(new["top_sources"].keys())

    comparison["source_comparison"] = {
        "common_sources": list(old_sources & new_sources),
        "new_sources": list(new_sources - old_sources),
        "disappeared_sources": list(old_sources - new_sources),
        "old_top_5": dict(list(old["top_sources"].items())[:5]),
        "new_top_5": dict(list(new["top_sources"].items())[:5])
    }

    # Generate insights
    insights = []

    # API change insight
    insights.append({
        "type": "API_CHANGE",
        "title": "Data Source Migration",
        "description": f"Switched from {old.get('source', 'CoinDesk')} to CryptoCompare API",
        "impact": "HIGH",
        "details": f"This represents a fundamental change in data sources and may affect content coverage and categorization"
    })

    # Article count
    if comparison["changes"]["article_count_change"] > 0:
        insights.append({
            "type": "VOLUME_INCREASE",
            "title": "Higher Article Volume",
            "description": f"New dataset contains {comparison['changes']['article_count_change']} more articles",
            "impact": "MEDIUM"
        })
    elif comparison["changes"]["article_count_change"] < 0:
        insights.append({
            "type": "VOLUME_DECREASE",
            "title": "Lower Article Volume",
            "description": f"New dataset contains {abs(comparison['changes']['article_count_change'])} fewer articles",
            "impact": "MEDIUM"
        })

    # Source diversity
    if comparison["changes"]["source_diversity_change"] > 0:
        insights.append({
            "type": "DIVERSITY_INCREASE",
            "title": "More Diverse Sources",
            "description": f"New dataset includes {comparison['changes']['source_diversity_change']} more unique sources",
            "impact": "HIGH",
            "details": f"New sources include: {', '.join(comparison['source_comparison']['new_sources'][:5])}"
        })
    elif comparison["changes"]["source_diversity_change"] < 0:
        insights.append({
            "type": "DIVERSITY_DECREASE",
            "title": "Less Diverse Sources",
            "description": f"New dataset has {abs(comparison['changes']['source_diversity_change'])} fewer unique sources",
            "impact": "MEDIUM"
        })

    # Article length
    if abs(comparison["changes"]["avg_length_change_pct"]) > 10:
        direction = "longer" if comparison["changes"]["avg_length_change"] > 0 else "shorter"
        insights.append({
            "type": "CONTENT_LENGTH_CHANGE",
            "title": f"Articles are {comparison['changes']['avg_length_change_pct']}% {direction}",
            "description": f"Average article length changed from {old['avg_body_length']} to {new['avg_body_length']} characters",
            "impact": "LOW"
        })

    # Category changes
    if comparison["category_comparison"]["new_trending_categories"]:
        insights.append({
            "type": "NEW_TOPICS",
            "title": "Emerging Topics Detected",
            "description": f"New categories appearing in latest data: {', '.join(comparison['category_comparison']['new_trending_categories'][:5])}",
            "impact": "MEDIUM"
        })

    comparison["insights"] = insights

    return comparison

def generate_report(comparison):
    """Generate a comprehensive markdown report"""
    report = []

    report.append("# 📊 Comparative News Analysis Report")
    report.append(f"**Generated:** {comparison['comparison_timestamp']}\n")

    report.append("## 🔍 Executive Summary\n")
    report.append("This report compares two news datasets to identify trends, changes, and insights.\n")

    report.append("## 📈 Dataset Overview\n")
    report.append("### Old Dataset (CoinDesk API)")
    report.append(f"- **Timestamp:** {comparison['old_dataset']['timestamp']}")
    report.append(f"- **Total Articles:** {comparison['old_dataset']['total_articles']}")
    report.append(f"- **Unique Sources:** {comparison['old_dataset']['unique_sources']}")
    report.append(f"- **Avg Article Length:** {comparison['old_dataset']['avg_body_length']} chars\n")

    report.append("### New Dataset (CryptoCompare API)")
    report.append(f"- **Timestamp:** {comparison['new_dataset']['timestamp']}")
    report.append(f"- **Total Articles:** {comparison['new_dataset']['total_articles']}")
    report.append(f"- **Unique Sources:** {comparison['new_dataset']['unique_sources']}")
    report.append(f"- **Avg Article Length:** {comparison['new_dataset']['avg_body_length']} chars\n")

    report.append("## 📊 Key Changes\n")
    changes = comparison['changes']
    report.append(f"- **Article Count:** {changes['article_count_change']:+d} ({comparison['new_dataset']['total_articles']} vs {comparison['old_dataset']['total_articles']})")
    report.append(f"- **Source Diversity:** {changes['source_diversity_change']:+d} sources ({comparison['new_dataset']['unique_sources']} vs {comparison['old_dataset']['unique_sources']})")
    report.append(f"- **Avg Length:** {changes['avg_length_change']:+.0f} chars ({changes['avg_length_change_pct']:+.1f}%)\n")

    report.append("## 🎯 Key Insights\n")
    for i, insight in enumerate(comparison['insights'], 1):
        report.append(f"### {i}. {insight['title']} [{insight['impact']} IMPACT]")
        report.append(f"{insight['description']}\n")
        if 'details' in insight:
            report.append(f"*{insight['details']}*\n")

    report.append("## 📂 Category Analysis\n")
    cat_comp = comparison['category_comparison']

    report.append("### Old Dataset Top 5 Categories")
    for cat, count in cat_comp['old_top_5'].items():
        report.append(f"- **{cat}:** {count}")
    report.append("")

    report.append("### New Dataset Top 5 Categories")
    for cat, count in cat_comp['new_top_5'].items():
        report.append(f"- **{cat}:** {count}")
    report.append("")

    if cat_comp['new_trending_categories']:
        report.append(f"### 🆕 New Trending Categories")
        for cat in cat_comp['new_trending_categories'][:10]:
            report.append(f"- {cat}")
        report.append("")

    if cat_comp['disappeared_categories']:
        report.append(f"### ⬇️ Categories No Longer Present")
        for cat in cat_comp['disappeared_categories'][:10]:
            report.append(f"- {cat}")
        report.append("")

    report.append("## 📰 Source Analysis\n")
    src_comp = comparison['source_comparison']

    report.append("### Old Dataset Top 5 Sources")
    for src, count in src_comp['old_top_5'].items():
        report.append(f"- **{src}:** {count} articles")
    report.append("")

    report.append("### New Dataset Top 5 Sources")
    for src, count in src_comp['new_top_5'].items():
        report.append(f"- **{src}:** {count} articles")
    report.append("")

    if src_comp['new_sources']:
        report.append(f"### 🆕 New Sources ({len(src_comp['new_sources'])} total)")
        for src in src_comp['new_sources'][:15]:
            report.append(f"- {src}")
        report.append("")

    if src_comp['disappeared_sources']:
        report.append(f"### ⬇️ Sources No Longer Present ({len(src_comp['disappeared_sources'])} total)")
        for src in src_comp['disappeared_sources'][:15]:
            report.append(f"- {src}")
        report.append("")

    report.append("## 💡 Recommendations\n")
    report.append("1. **Monitor API Stability:** The switch to CryptoCompare API brings new sources and coverage patterns")
    report.append("2. **Track Emerging Topics:** Watch new categories for market trends and opportunities")
    report.append("3. **Source Diversification:** Leverage the increased source diversity for comprehensive market coverage")
    report.append("4. **Content Strategy:** Adapt to different article lengths and content styles from new sources\n")

    report.append("---")
    report.append("*Report generated by CryptoPrism News Fetcher*")

    return "\n".join(report)

if __name__ == "__main__":
    print("🔍 Starting Comparative Analysis (500 vs 500)...\n")

    # Analyze old data
    print("📄 Analyzing old dataset (CoinDesk - 500 articles)...")
    old_csv = "data/csv_exports/articles_20251001_145721.csv"
    old_analysis = analyze_old_csv(old_csv)
    print(f"✅ Old dataset: {old_analysis['total_articles']} articles from {old_analysis['unique_sources']} sources\n")

    # Load new analysis
    print("📄 Loading new dataset analysis (CryptoCompare - 500 articles)...")
    new_json = "data/analysis/analysis_500_20251005_013651.json"
    new_analysis = load_new_analysis(new_json)
    print(f"✅ New dataset: {new_analysis['total_articles']} articles from {new_analysis['unique_sources']} sources\n")

    # Compare
    print("🔄 Performing comparative analysis...")
    comparison = compare_analyses(old_analysis, new_analysis)

    # Save comparison JSON
    os.makedirs("data/comparisons", exist_ok=True)
    comparison_file = "data/comparisons/comparative_analysis_500vs500.json"
    with open(comparison_file, 'w', encoding='utf-8') as f:
        json.dump(comparison, f, indent=2, ensure_ascii=False)
    print(f"✅ Saved comparison JSON: {comparison_file}\n")

    # Generate and save report
    report = generate_report(comparison)
    report_file = "data/comparisons/COMPARATIVE_ANALYSIS_REPORT_500vs500.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"✅ Saved report: {report_file}\n")

    # Print summary
    print("="*60)
    print("📊 COMPARATIVE ANALYSIS SUMMARY")
    print("="*60)
    print(f"\n🔍 Key Insights ({len(comparison['insights'])} total):\n")
    for i, insight in enumerate(comparison['insights'][:5], 1):
        print(f"{i}. [{insight['impact']}] {insight['title']}")
        print(f"   {insight['description']}\n")

    print("="*60)
    print(f"\n✅ Full report available at: {report_file}")
    print(f"✅ JSON data available at: {comparison_file}\n")
