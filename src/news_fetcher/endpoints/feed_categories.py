"""
CoinDesk Feed Categories Endpoint Handler
Fetches combined mapping of sources and their categories
"""

import os
import json
import requests
from datetime import datetime
from typing import Dict, List
from collections import defaultdict


class CoinDeskFeedCategoriesAPI:
    def __init__(self):
        self.api_key = os.getenv("API_KEY")
        self.base_url = "https://data-api.coindesk.com"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json"
        }

    def fetch_feed_categories(self) -> Dict:
        """
        Fetch combined feed categories mapping from CoinDesk API

        Returns:
            Dictionary containing API response with feed categories
        """
        endpoint = f"{self.base_url}/news/v1/feed_category/list"

        try:
            response = requests.get(endpoint, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching feed categories: {e}")
            return {"error": str(e), "feeds": []}

    def save_to_file(self, data: Dict, filename_suffix: str = "") -> str:
        """Save data to JSON file with timestamp"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"coindesk_feed_categories_{timestamp}{filename_suffix}.json"
        filepath = os.path.join("data", "raw", filename)

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return filepath

    def analyze_feed_categories(self, feed_categories_data: Dict) -> Dict:
        """Analyze feed categories data and extract insights"""
        feeds = feed_categories_data.get("feeds", [])

        # Initialize analysis structure
        analysis = {
            "total_feeds": len(feeds),
            "fetched_at": datetime.now().isoformat(),
            "source_to_categories": {},
            "category_to_sources": defaultdict(list),
            "statistics": {
                "avg_categories_per_source": 0,
                "max_categories_per_source": 0,
                "min_categories_per_source": float('inf'),
                "most_versatile_source": "",
                "most_popular_category": "",
                "unique_categories": set(),
                "unique_sources": set()
            },
            "relationships": {
                "sources_covering_markets": [],
                "sources_covering_policy": [],
                "sources_covering_tech": [],
                "multi_category_sources": [],
                "single_category_sources": []
            },
            "coverage_matrix": {}
        }

        if not feeds:
            return analysis

        # Process each feed
        for feed in feeds:
            source = feed.get("source", "unknown")
            categories = feed.get("categories", [])

            analysis["source_to_categories"][source] = categories
            analysis["statistics"]["unique_sources"].add(source)

            # Track categories for each source
            for category in categories:
                analysis["category_to_sources"][category].append(source)
                analysis["statistics"]["unique_categories"].add(category)

            # Update statistics
            cat_count = len(categories)
            if cat_count > analysis["statistics"]["max_categories_per_source"]:
                analysis["statistics"]["max_categories_per_source"] = cat_count
                analysis["statistics"]["most_versatile_source"] = source

            if cat_count < analysis["statistics"]["min_categories_per_source"]:
                analysis["statistics"]["min_categories_per_source"] = cat_count

            # Categorize sources
            if cat_count > 1:
                analysis["relationships"]["multi_category_sources"].append(source)
            elif cat_count == 1:
                analysis["relationships"]["single_category_sources"].append(source)

            # Check for specific category coverage
            for category in categories:
                if "market" in category.lower():
                    analysis["relationships"]["sources_covering_markets"].append(source)
                if "policy" in category.lower() or "regulation" in category.lower():
                    analysis["relationships"]["sources_covering_policy"].append(source)
                if "tech" in category.lower() or "defi" in category.lower() or "nft" in category.lower():
                    analysis["relationships"]["sources_covering_tech"].append(source)

        # Calculate averages and most popular
        if feeds:
            total_categories = sum(len(feed.get("categories", [])) for feed in feeds)
            analysis["statistics"]["avg_categories_per_source"] = total_categories / len(feeds)

        # Find most popular category
        if analysis["category_to_sources"]:
            most_popular = max(analysis["category_to_sources"].items(), key=lambda x: len(x[1]))
            analysis["statistics"]["most_popular_category"] = most_popular[0]

        # Convert sets to lists for JSON serialization
        analysis["statistics"]["unique_categories"] = list(analysis["statistics"]["unique_categories"])
        analysis["statistics"]["unique_sources"] = list(analysis["statistics"]["unique_sources"])

        # Create coverage matrix
        for source in analysis["statistics"]["unique_sources"]:
            analysis["coverage_matrix"][source] = {}
            for category in analysis["statistics"]["unique_categories"]:
                analysis["coverage_matrix"][source][category] = (
                    category in analysis["source_to_categories"].get(source, [])
                )

        # Remove duplicates from relationship lists
        for key in analysis["relationships"]:
            analysis["relationships"][key] = list(set(analysis["relationships"][key]))

        return analysis

    def generate_recommendations(self, analysis: Dict) -> Dict:
        """Generate actionable recommendations based on analysis"""
        recommendations = {
            "content_strategy": [],
            "filtering_strategy": [],
            "monitoring_priorities": [],
            "integration_opportunities": []
        }

        stats = analysis.get("statistics", {})
        relationships = analysis.get("relationships", {})

        # Content strategy recommendations
        if stats.get("most_versatile_source"):
            recommendations["content_strategy"].append(
                f"Prioritize {stats['most_versatile_source']} as it covers the most categories "
                f"({stats['max_categories_per_source']} categories)"
            )

        if stats.get("most_popular_category"):
            recommendations["content_strategy"].append(
                f"Focus on '{stats['most_popular_category']}' category as it has the most source coverage "
                f"({len(analysis['category_to_sources'].get(stats['most_popular_category'], []))} sources)"
            )

        # Filtering strategy recommendations
        multi_cat_sources = relationships.get("multi_category_sources", [])
        single_cat_sources = relationships.get("single_category_sources", [])

        if multi_cat_sources:
            recommendations["filtering_strategy"].append(
                f"Use sources like {', '.join(multi_cat_sources[:3])} for broad coverage filtering"
            )

        if single_cat_sources:
            recommendations["filtering_strategy"].append(
                f"Use sources like {', '.join(single_cat_sources[:3])} for specialized category filtering"
            )

        # Monitoring priorities
        market_sources = relationships.get("sources_covering_markets", [])
        policy_sources = relationships.get("sources_covering_policy", [])
        tech_sources = relationships.get("sources_covering_tech", [])

        if market_sources:
            recommendations["monitoring_priorities"].append(
                f"Monitor {', '.join(market_sources[:3])} for market-related news"
            )

        if policy_sources:
            recommendations["monitoring_priorities"].append(
                f"Track {', '.join(policy_sources[:3])} for regulatory updates"
            )

        if tech_sources:
            recommendations["monitoring_priorities"].append(
                f"Follow {', '.join(tech_sources[:3])} for technology developments"
            )

        # Integration opportunities
        recommendations["integration_opportunities"].append(
            "Create category-specific dashboards based on source-category relationships"
        )

        if len(stats.get("unique_categories", [])) > 5:
            recommendations["integration_opportunities"].append(
                "Implement hierarchical filtering UI with primary and secondary categories"
            )

        recommendations["integration_opportunities"].append(
            "Set up cross-source validation for important news categories"
        )

        return recommendations


def main():
    """Main function to demonstrate feed categories endpoint usage"""
    api = CoinDeskFeedCategoriesAPI()

    print("🔄 Fetching feed categories mapping...")

    # Fetch feed categories data
    feed_categories_data = api.fetch_feed_categories()

    if "error" not in feed_categories_data:
        # Save raw data
        filepath = api.save_to_file(feed_categories_data)
        print(f"✅ Raw feed categories data saved to {filepath}")

        # Analyze feed categories
        analysis = api.analyze_feed_categories(feed_categories_data)

        # Generate recommendations
        recommendations = api.generate_recommendations(analysis)
        analysis["recommendations"] = recommendations

        # Save analysis
        analysis_filepath = api.save_to_file(analysis, "_analysis")
        print(f"✅ Feed categories analysis saved to {analysis_filepath}")

        # Print summary
        stats = analysis['statistics']
        print(f"\n📊 Feed Categories Summary:")
        print(f"  Total feeds: {analysis['total_feeds']}")
        print(f"  Unique sources: {len(stats['unique_sources'])}")
        print(f"  Unique categories: {len(stats['unique_categories'])}")
        print(f"  Avg categories per source: {stats['avg_categories_per_source']:.1f}")
        print(f"  Most versatile source: {stats['most_versatile_source']}")
        print(f"  Most popular category: {stats['most_popular_category']}")

        print(f"\n📈 Source Distribution:")
        print(f"  Multi-category sources: {len(analysis['relationships']['multi_category_sources'])}")
        print(f"  Single-category sources: {len(analysis['relationships']['single_category_sources'])}")

        print(f"\n🎯 Key Recommendations:")
        for i, rec in enumerate(recommendations["content_strategy"][:3], 1):
            print(f"  {i}. {rec}")

        return {
            "raw_data": feed_categories_data,
            "analysis": analysis,
            "recommendations": recommendations,
            "filepath": filepath,
            "analysis_filepath": analysis_filepath
        }
    else:
        print(f"❌ Error fetching feed categories: {feed_categories_data.get('error')}")
        return {"error": feed_categories_data.get('error')}


if __name__ == "__main__":
    result = main()
    if "error" not in result:
        print("\n🎯 Feed categories fetch completed successfully!")
    else:
        print(f"\n❌ Feed categories fetch failed: {result['error']}")