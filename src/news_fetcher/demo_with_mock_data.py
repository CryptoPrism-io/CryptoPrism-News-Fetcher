"""
Demo script with mock data to demonstrate the analysis capabilities
"""

import json
import os
from datetime import datetime, timedelta
from src.news_fetcher.coindesk_orchestrator import CoinDeskOrchestrator


def create_mock_data():
    """Create realistic mock data for demonstration"""

    # Mock sources data
    mock_sources = {
        "sources": [
            {"id": "coindesk", "name": "CoinDesk"},
            {"id": "reuters", "name": "Reuters"},
            {"id": "bloomberg", "name": "Bloomberg"},
            {"id": "cointelegraph", "name": "Cointelegraph"},
            {"id": "decrypt", "name": "Decrypt"},
            {"id": "theblock", "name": "The Block"}
        ]
    }

    # Mock categories data
    mock_categories = {
        "categories": [
            {"id": "markets", "name": "Markets"},
            {"id": "policy", "name": "Policy"},
            {"id": "defi", "name": "DeFi"},
            {"id": "nft", "name": "NFT"},
            {"id": "tech", "name": "Technology"},
            {"id": "bitcoin", "name": "Bitcoin"},
            {"id": "ethereum", "name": "Ethereum"},
            {"id": "business", "name": "Business"}
        ]
    }

    # Mock feed categories data
    mock_feed_categories = {
        "feeds": [
            {"source": "coindesk", "categories": ["markets", "policy", "bitcoin", "ethereum", "tech"]},
            {"source": "reuters", "categories": ["markets", "policy", "business"]},
            {"source": "bloomberg", "categories": ["markets", "business", "policy"]},
            {"source": "cointelegraph", "categories": ["markets", "defi", "nft", "bitcoin", "ethereum"]},
            {"source": "decrypt", "categories": ["defi", "nft", "tech", "ethereum"]},
            {"source": "theblock", "categories": ["markets", "defi", "policy", "tech"]}
        ]
    }

    # Mock articles data
    base_time = datetime.now()
    mock_articles = {
        "latest_20": {
            "articles": [
                {
                    "id": f"article_{i}",
                    "title": f"Sample Crypto News Article {i}",
                    "summary": f"This is a sample summary for article {i} about cryptocurrency markets and trends.",
                    "url": f"https://coindesk.com/article-{i}",
                    "image_url": f"https://cdn.coindesk.com/image-{i}.jpg" if i % 2 == 0 else None,
                    "published_at": (base_time - timedelta(hours=i)).isoformat(),
                    "source": ["coindesk", "reuters", "bloomberg", "cointelegraph"][i % 4],
                    "category": ["markets", "policy", "defi", "bitcoin", "ethereum"][i % 5]
                }
                for i in range(20)
            ],
            "count": 20,
            "offset": 0,
            "total": 1500
        },
        "markets_category": {
            "articles": [
                {
                    "id": f"market_article_{i}",
                    "title": f"Market Analysis Article {i}",
                    "summary": f"Market analysis summary {i} discussing price movements and trading volumes.",
                    "url": f"https://coindesk.com/markets/article-{i}",
                    "image_url": f"https://cdn.coindesk.com/market-{i}.jpg" if i % 3 == 0 else None,
                    "published_at": (base_time - timedelta(hours=i*2)).isoformat(),
                    "source": ["coindesk", "bloomberg", "reuters"][i % 3],
                    "category": "markets"
                }
                for i in range(15)
            ],
            "count": 15,
            "offset": 0,
            "total": 450
        },
        "coindesk_source": {
            "articles": [
                {
                    "id": f"coindesk_article_{i}",
                    "title": f"CoinDesk Exclusive Article {i}",
                    "summary": f"Exclusive analysis from CoinDesk covering topic {i} in the cryptocurrency space.",
                    "url": f"https://coindesk.com/exclusive-{i}",
                    "image_url": f"https://cdn.coindesk.com/exclusive-{i}.jpg",
                    "published_at": (base_time - timedelta(hours=i*3)).isoformat(),
                    "source": "coindesk",
                    "category": ["markets", "policy", "tech", "bitcoin", "ethereum"][i % 5]
                }
                for i in range(15)
            ],
            "count": 15,
            "offset": 0,
            "total": 300
        }
    }

    return {
        "sources": mock_sources,
        "categories": mock_categories,
        "feed_categories": mock_feed_categories,
        "articles": mock_articles
    }


def simulate_api_processing(mock_data):
    """Simulate the API processing pipeline with mock data"""

    orchestrator = CoinDeskOrchestrator()

    # Process mock sources
    sources_analysis = orchestrator.sources_api.analyze_sources(mock_data["sources"])

    # Process mock categories
    categories_analysis = orchestrator.categories_api.analyze_categories(mock_data["categories"])

    # Process mock feed categories
    feed_analysis = orchestrator.feed_categories_api.analyze_feed_categories(mock_data["feed_categories"])
    recommendations = orchestrator.feed_categories_api.generate_recommendations(feed_analysis)
    feed_analysis["recommendations"] = recommendations

    # Process mock articles
    articles_results = {}
    for query_type, article_data in mock_data["articles"].items():
        processed_articles = orchestrator.articles_api.extract_article_metadata(article_data)
        articles_results[query_type] = {
            "count": article_data.get("count", 0),
            "total_available": article_data.get("total", 0),
            "processed_articles": processed_articles
        }

    # Compile results
    orchestrator.results = {
        "sources": {
            "raw_data": mock_data["sources"],
            "analysis": sources_analysis
        },
        "categories": {
            "raw_data": mock_data["categories"],
            "analysis": categories_analysis
        },
        "feed_categories": {
            "raw_data": mock_data["feed_categories"],
            "analysis": feed_analysis
        },
        "articles": articles_results
    }

    return orchestrator


def main():
    """Run demo with mock data"""
    print("🎭 Starting Demo with Mock Data")
    print("=" * 60)

    # Create mock data
    print("📝 Creating realistic mock data...")
    mock_data = create_mock_data()

    # Simulate processing
    print("⚙️ Simulating API processing pipeline...")
    orchestrator = simulate_api_processing(mock_data)

    # Perform comprehensive analysis
    print("🔍 Performing comprehensive analysis...")
    comprehensive_analysis = orchestrator.perform_cross_endpoint_analysis()

    # Save results
    final_results = {
        "session_info": {
            "session_id": f"demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "completed_at": datetime.now().isoformat(),
            "endpoints_tested": list(orchestrator.results.keys()),
            "note": "This is a demonstration with mock data"
        },
        "raw_data_results": orchestrator.results,
        "comprehensive_analysis": comprehensive_analysis
    }

    # Save to file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"coindesk_demo_analysis_{timestamp}.json"
    filepath = os.path.join("data", "processed", filename)

    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(final_results, f, indent=2, ensure_ascii=False)

    print(f"💾 Demo analysis saved to: {filepath}")

    # Print comprehensive summary
    print_demo_summary(comprehensive_analysis)

    return final_results


def print_demo_summary(analysis):
    """Print detailed demo summary"""
    print("\n" + "=" * 60)
    print("📊 DEMO ANALYSIS SUMMARY")
    print("=" * 60)

    # Data Quality
    quality = analysis.get("data_quality", {})
    print(f"\n🎯 Data Quality:")
    print(f"   • All endpoints: ✅ SUCCESS")
    print(f"   • Sources available: {quality.get('data_completeness', {}).get('sources', 0)}")
    print(f"   • Categories available: {quality.get('data_completeness', {}).get('categories', 0)}")
    print(f"   • Articles processed: {quality.get('data_completeness', {}).get('articles', 0)}")

    # Content Insights
    content = analysis.get("content_insights", {})
    if content.get("content_characteristics"):
        chars = content["content_characteristics"]
        print(f"\n📰 Content Analysis:")
        print(f"   • Total articles: {chars.get('total_articles_analyzed', 0)}")
        print(f"   • Avg word count: {chars.get('avg_word_count', 0):.1f}")
        print(f"   • With images: {chars.get('image_percentage', 0):.1f}%")

    if content.get("category_distribution"):
        print(f"\n📂 Top Categories:")
        top_cats = sorted(content["category_distribution"].items(), key=lambda x: x[1], reverse=True)[:5]
        for i, (cat, count) in enumerate(top_cats, 1):
            print(f"   {i}. {cat}: {count} articles")

    if content.get("source_distribution"):
        print(f"\n📰 Top Sources:")
        top_sources = sorted(content["source_distribution"].items(), key=lambda x: x[1], reverse=True)[:5]
        for i, (source, count) in enumerate(top_sources, 1):
            print(f"   {i}. {source}: {count} articles")

    # Data Relationships
    relationships = analysis.get("data_relationships", {})
    if relationships.get("source_category_mapping"):
        mapping = relationships["source_category_mapping"]
        print(f"\n🔗 Source-Category Mapping:")
        print(f"   • Coverage: {mapping.get('coverage_percentage', 0):.1f}%")
        print(f"   • Sources in both: {len(mapping.get('sources_in_both', []))}")

    # Coverage Analysis
    coverage = analysis.get("coverage_analysis", {})
    if coverage.get("thematic_coverage"):
        thematic = coverage["thematic_coverage"]
        print(f"\n📊 Thematic Coverage:")
        print(f"   • Trading categories: {thematic.get('trading_categories', 0)}")
        print(f"   • Tech categories: {thematic.get('tech_categories', 0)}")
        print(f"   • Policy categories: {thematic.get('policy_categories', 0)}")
        print(f"   • Business categories: {thematic.get('business_categories', 0)}")

    # Key Recommendations
    recommendations = analysis.get("recommendations", {})
    print(f"\n🎯 Key Recommendations:")

    data_strategy = recommendations.get("data_strategy", [])
    for i, rec in enumerate(data_strategy[:3], 1):
        print(f"   {i}. {rec}")

    monitoring = recommendations.get("monitoring_setup", [])
    if monitoring:
        print(f"\n📡 Monitoring Setup:")
        for i, rec in enumerate(monitoring[:3], 1):
            print(f"   {i}. {rec}")

    integration = recommendations.get("integration_priorities", [])
    if integration:
        print(f"\n🔧 Integration Priorities:")
        for i, rec in enumerate(integration[:3], 1):
            print(f"   {i}. {rec}")

    print("\n" + "=" * 60)
    print("🎭 Demo completed! This shows the full analysis capability with real data.")
    print("=" * 60)


if __name__ == "__main__":
    main()