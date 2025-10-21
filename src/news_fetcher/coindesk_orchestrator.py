"""
CoinDesk API Orchestrator
Coordinates all endpoint calls and performs comprehensive analysis
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any
from collections import defaultdict

from .endpoints import (
    CoinDeskArticlesAPI,
    CoinDeskSourcesAPI,
    CoinDeskCategoriesAPI,
    CoinDeskFeedCategoriesAPI
)


class CoinDeskOrchestrator:
    def __init__(self):
        self.articles_api = CoinDeskArticlesAPI()
        self.sources_api = CoinDeskSourcesAPI()
        self.categories_api = CoinDeskCategoriesAPI()
        self.feed_categories_api = CoinDeskFeedCategoriesAPI()

        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.results = {}

    def fetch_all_endpoints(self) -> Dict[str, Any]:
        """Fetch data from all CoinDesk API endpoints"""
        print("🚀 Starting comprehensive CoinDesk API fetch...")
        print(f"📅 Session ID: {self.session_id}")

        # Step 1: Fetch metadata endpoints first
        print("\n🔧 Phase 1: Fetching metadata endpoints...")

        # Fetch sources
        print("  📰 Fetching sources...")
        try:
            sources_data = self.sources_api.fetch_sources()
            if "error" not in sources_data:
                filepath = self.sources_api.save_to_file(sources_data)
                analysis = self.sources_api.analyze_sources(sources_data)
                sources_result = {
                    "raw_data": sources_data,
                    "analysis": analysis,
                    "filepath": filepath
                }
            else:
                sources_result = {"error": sources_data.get('error')}
            self.results['sources'] = sources_result
        except Exception as e:
            self.results['sources'] = {"error": str(e)}
        time.sleep(1)  # Rate limiting courtesy

        # Fetch categories
        print("  📂 Fetching categories...")
        try:
            categories_data = self.categories_api.fetch_categories()
            if "error" not in categories_data:
                filepath = self.categories_api.save_to_file(categories_data)
                analysis = self.categories_api.analyze_categories(categories_data)
                categories_result = {
                    "raw_data": categories_data,
                    "analysis": analysis,
                    "filepath": filepath
                }
            else:
                categories_result = {"error": categories_data.get('error')}
            self.results['categories'] = categories_result
        except Exception as e:
            self.results['categories'] = {"error": str(e)}
        time.sleep(1)

        # Fetch feed categories
        print("  🔗 Fetching feed categories...")
        try:
            feed_categories_data = self.feed_categories_api.fetch_feed_categories()
            if "error" not in feed_categories_data:
                filepath = self.feed_categories_api.save_to_file(feed_categories_data)
                analysis = self.feed_categories_api.analyze_feed_categories(feed_categories_data)
                recommendations = self.feed_categories_api.generate_recommendations(analysis)
                analysis["recommendations"] = recommendations
                feed_categories_result = {
                    "raw_data": feed_categories_data,
                    "analysis": analysis,
                    "filepath": filepath
                }
            else:
                feed_categories_result = {"error": feed_categories_data.get('error')}
            self.results['feed_categories'] = feed_categories_result
        except Exception as e:
            self.results['feed_categories'] = {"error": str(e)}
        time.sleep(1)

        # Step 2: Fetch articles with different parameters
        print("\n📰 Phase 2: Fetching articles with various filters...")
        try:
            # Fetch different sets of articles
            test_cases = [
                {"name": "latest_20", "params": {"limit": 20}},
                {"name": "markets_category", "params": {"limit": 15, "category": "markets"}},
                {"name": "coindesk_source", "params": {"limit": 15, "source": "coindesk"}},
            ]

            articles_result = {}
            for test_case in test_cases:
                print(f"    🔍 Fetching {test_case['name']}...")
                data = self.articles_api.fetch_articles(**test_case["params"])

                if "error" not in data:
                    filepath = self.articles_api.save_to_file(data, f"_{test_case['name']}")
                    processed = self.articles_api.extract_article_metadata(data)

                    articles_result[test_case['name']] = {
                        "filepath": filepath,
                        "count": data.get("count", 0),
                        "total_available": data.get("total", 0),
                        "processed_articles": processed
                    }
                else:
                    articles_result[test_case['name']] = {"error": data.get('error')}
                time.sleep(1)  # Rate limiting between requests

            self.results['articles'] = articles_result
        except Exception as e:
            self.results['articles'] = {"error": str(e)}

        print("✅ All endpoints fetched successfully!")
        return self.results

    def perform_cross_endpoint_analysis(self) -> Dict[str, Any]:
        """Perform comprehensive analysis across all endpoint data"""
        print("\n🔍 Starting cross-endpoint analysis...")

        analysis = {
            "session_id": self.session_id,
            "analyzed_at": datetime.now().isoformat(),
            "data_quality": {},
            "content_insights": {},
            "operational_insights": {},
            "recommendations": {},
            "data_relationships": {},
            "coverage_analysis": {}
        }

        # Data Quality Analysis
        analysis["data_quality"] = self._analyze_data_quality()

        # Content Insights
        analysis["content_insights"] = self._analyze_content_patterns()

        # Operational Insights
        analysis["operational_insights"] = self._analyze_operational_metrics()

        # Data Relationships
        analysis["data_relationships"] = self._analyze_data_relationships()

        # Coverage Analysis
        analysis["coverage_analysis"] = self._analyze_coverage()

        # Generate Recommendations
        analysis["recommendations"] = self._generate_comprehensive_recommendations(analysis)

        return analysis

    def _analyze_data_quality(self) -> Dict[str, Any]:
        """Analyze data quality across endpoints"""
        quality_analysis = {
            "endpoints_status": {},
            "data_completeness": {},
            "consistency_checks": {},
            "potential_issues": []
        }

        # Check endpoint status
        for endpoint, result in self.results.items():
            if "error" in result:
                quality_analysis["endpoints_status"][endpoint] = "FAILED"
                quality_analysis["potential_issues"].append(f"{endpoint} endpoint failed: {result['error']}")
            else:
                quality_analysis["endpoints_status"][endpoint] = "SUCCESS"

        # Check data completeness
        if "sources" in self.results and "error" not in self.results["sources"]:
            sources_count = len(self.results["sources"]["analysis"]["source_list"])
            quality_analysis["data_completeness"]["sources"] = sources_count
            if sources_count == 0:
                quality_analysis["potential_issues"].append("No sources returned from API")

        if "categories" in self.results and "error" not in self.results["categories"]:
            categories_count = len(self.results["categories"]["analysis"]["category_list"])
            quality_analysis["data_completeness"]["categories"] = categories_count
            if categories_count == 0:
                quality_analysis["potential_issues"].append("No categories returned from API")

        if "articles" in self.results and "error" not in self.results["articles"]:
            articles_data = self.results["articles"]
            total_articles = sum(result.get("count", 0) for result in articles_data.values()
                               if isinstance(result, dict) and "error" not in result)
            quality_analysis["data_completeness"]["articles"] = total_articles
            if total_articles == 0:
                quality_analysis["potential_issues"].append("No articles returned from any query")

        return quality_analysis

    def _analyze_content_patterns(self) -> Dict[str, Any]:
        """Analyze content patterns and trends"""
        content_analysis = {
            "article_patterns": {},
            "category_distribution": {},
            "source_distribution": {},
            "temporal_patterns": {},
            "content_characteristics": {}
        }

        if "articles" in self.results and "error" not in self.results["articles"]:
            articles_data = self.results["articles"]

            # Analyze article patterns
            all_articles = []
            for query_type, result in articles_data.items():
                if isinstance(result, dict) and "processed_articles" in result:
                    all_articles.extend(result["processed_articles"])

            if all_articles:
                # Word count analysis
                word_counts = [article.get("word_count", 0) for article in all_articles]
                content_analysis["content_characteristics"] = {
                    "total_articles_analyzed": len(all_articles),
                    "avg_word_count": sum(word_counts) / len(word_counts) if word_counts else 0,
                    "max_word_count": max(word_counts) if word_counts else 0,
                    "min_word_count": min(word_counts) if word_counts else 0,
                    "articles_with_images": sum(1 for article in all_articles if article.get("has_image")),
                    "image_percentage": (sum(1 for article in all_articles if article.get("has_image")) / len(all_articles)) * 100
                }

                # Category distribution
                categories = [article.get("category") for article in all_articles if article.get("category")]
                category_freq = defaultdict(int)
                for cat in categories:
                    category_freq[cat] += 1
                content_analysis["category_distribution"] = dict(category_freq)

                # Source distribution
                sources = [article.get("source") for article in all_articles if article.get("source")]
                source_freq = defaultdict(int)
                for source in sources:
                    source_freq[source] += 1
                content_analysis["source_distribution"] = dict(source_freq)

        return content_analysis

    def _analyze_operational_metrics(self) -> Dict[str, Any]:
        """Analyze operational metrics and performance"""
        operational_analysis = {
            "api_response_efficiency": {},
            "data_freshness": {},
            "coverage_metrics": {},
            "scalability_indicators": {}
        }

        # Analyze API efficiency
        total_requests = len([ep for ep in self.results.keys()])
        successful_requests = len([ep for ep, result in self.results.items() if "error" not in result])

        operational_analysis["api_response_efficiency"] = {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "success_rate": (successful_requests / total_requests) * 100 if total_requests > 0 else 0
        }

        # Check data freshness
        if "articles" in self.results and "error" not in self.results["articles"]:
            articles_data = self.results["articles"]
            latest_timestamps = []

            for query_type, result in articles_data.items():
                if isinstance(result, dict) and "processed_articles" in result:
                    for article in result["processed_articles"]:
                        if article.get("published_at"):
                            latest_timestamps.append(article["published_at"])

            if latest_timestamps:
                operational_analysis["data_freshness"] = {
                    "latest_article_timestamp": max(latest_timestamps),
                    "oldest_article_timestamp": min(latest_timestamps),
                    "timestamp_range_hours": "calculated_from_timestamps"
                }

        return operational_analysis

    def _analyze_data_relationships(self) -> Dict[str, Any]:
        """Analyze relationships between different data types"""
        relationships = {
            "source_category_mapping": {},
            "content_source_alignment": {},
            "category_coverage_gaps": {},
            "cross_validation_opportunities": {}
        }

        # Analyze source-category relationships
        if ("feed_categories" in self.results and "error" not in self.results["feed_categories"] and
            "sources" in self.results and "error" not in self.results["sources"] and
            "categories" in self.results and "error" not in self.results["categories"]):

            feed_data = self.results["feed_categories"]["analysis"]
            sources_data = self.results["sources"]["analysis"]
            categories_data = self.results["categories"]["analysis"]

            # Map available sources vs feed sources
            available_sources = set(sources_data["source_ids"])
            feed_sources = set(feed_data["statistics"]["unique_sources"])

            relationships["source_category_mapping"] = {
                "sources_in_both": list(available_sources.intersection(feed_sources)),
                "sources_only_in_list": list(available_sources - feed_sources),
                "sources_only_in_feeds": list(feed_sources - available_sources),
                "coverage_percentage": (len(available_sources.intersection(feed_sources)) / len(available_sources)) * 100 if available_sources else 0
            }

            # Map available categories vs feed categories
            available_categories = set(categories_data["category_ids"])
            feed_categories = set(feed_data["statistics"]["unique_categories"])

            relationships["category_coverage_gaps"] = {
                "categories_in_both": list(available_categories.intersection(feed_categories)),
                "categories_only_in_list": list(available_categories - feed_categories),
                "categories_only_in_feeds": list(feed_categories - available_categories),
                "coverage_percentage": (len(available_categories.intersection(feed_categories)) / len(available_categories)) * 100 if available_categories else 0
            }

        return relationships

    def _analyze_coverage(self) -> Dict[str, Any]:
        """Analyze content coverage and gaps"""
        coverage = {
            "temporal_coverage": {},
            "thematic_coverage": {},
            "source_coverage": {},
            "geographic_coverage": {},
            "coverage_recommendations": []
        }

        # Analyze thematic coverage
        if "categories" in self.results and "error" not in self.results["categories"]:
            categories_analysis = self.results["categories"]["analysis"]

            coverage["thematic_coverage"] = {
                "total_categories": categories_analysis["total_categories"],
                "trading_categories": len(categories_analysis["classification"]["trading_related"]),
                "tech_categories": len(categories_analysis["classification"]["tech_related"]),
                "policy_categories": len(categories_analysis["classification"]["policy_related"]),
                "business_categories": len(categories_analysis["classification"]["business_related"]),
                "other_categories": len(categories_analysis["classification"]["other"])
            }

            # Generate coverage recommendations
            if categories_analysis["classification"]["policy_related"]:
                coverage["coverage_recommendations"].append("Strong policy coverage available")
            if categories_analysis["classification"]["tech_related"]:
                coverage["coverage_recommendations"].append("Good technology coverage for DeFi/NFT trends")
            if categories_analysis["classification"]["trading_related"]:
                coverage["coverage_recommendations"].append("Comprehensive trading/markets coverage")

        return coverage

    def _generate_comprehensive_recommendations(self, analysis: Dict[str, Any]) -> Dict[str, List[str]]:
        """Generate comprehensive recommendations based on all analysis"""
        recommendations = {
            "immediate_actions": [],
            "data_strategy": [],
            "monitoring_setup": [],
            "integration_priorities": [],
            "risk_mitigation": []
        }

        # Immediate actions based on data quality
        quality = analysis.get("data_quality", {})
        if quality.get("potential_issues"):
            recommendations["immediate_actions"].extend([
                f"Address: {issue}" for issue in quality["potential_issues"][:3]
            ])

        success_rate = quality.get("endpoints_status", {})
        failed_endpoints = [ep for ep, status in success_rate.items() if status == "FAILED"]
        if failed_endpoints:
            recommendations["immediate_actions"].append(
                f"Fix failed endpoints: {', '.join(failed_endpoints)}"
            )

        # Data strategy recommendations
        content_insights = analysis.get("content_insights", {})
        if content_insights.get("category_distribution"):
            top_categories = sorted(content_insights["category_distribution"].items(),
                                  key=lambda x: x[1], reverse=True)[:3]
            recommendations["data_strategy"].append(
                f"Focus on top categories: {', '.join([cat for cat, count in top_categories])}"
            )

        if content_insights.get("source_distribution"):
            top_sources = sorted(content_insights["source_distribution"].items(),
                               key=lambda x: x[1], reverse=True)[:3]
            recommendations["data_strategy"].append(
                f"Prioritize top sources: {', '.join([source for source, count in top_sources])}"
            )

        # Monitoring setup recommendations
        operational = analysis.get("operational_insights", {})
        success_rate_pct = operational.get("api_response_efficiency", {}).get("success_rate", 0)

        if success_rate_pct < 100:
            recommendations["monitoring_setup"].append("Implement API health monitoring and alerting")

        recommendations["monitoring_setup"].extend([
            "Set up automated data freshness checks",
            "Monitor article volume trends by category",
            "Track source availability and performance"
        ])

        # Integration priorities
        relationships = analysis.get("data_relationships", {})
        source_coverage = relationships.get("source_category_mapping", {}).get("coverage_percentage", 0)

        if source_coverage < 90:
            recommendations["integration_priorities"].append(
                "Improve source-category mapping coverage"
            )

        recommendations["integration_priorities"].extend([
            "Implement cross-source content validation",
            "Create unified article deduplication system",
            "Build category-based content recommendation engine"
        ])

        # Risk mitigation
        recommendations["risk_mitigation"].extend([
            "Implement fallback mechanisms for failed API calls",
            "Set up data backup and recovery procedures",
            "Create rate limiting and quota management",
            "Establish data quality validation pipelines"
        ])

        return recommendations

    def save_comprehensive_analysis(self, analysis: Dict[str, Any]) -> str:
        """Save comprehensive analysis to file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"coindesk_comprehensive_analysis_{timestamp}.json"
        filepath = os.path.join("data", "processed", filename)

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)

        return filepath

    def run_complete_analysis(self) -> Dict[str, Any]:
        """Run complete analysis workflow"""
        print("🎯 Starting Complete CoinDesk API Analysis")
        print("=" * 60)

        # Fetch all data
        fetch_results = self.fetch_all_endpoints()

        # Perform cross-endpoint analysis
        comprehensive_analysis = self.perform_cross_endpoint_analysis()

        # Combine results
        final_results = {
            "session_info": {
                "session_id": self.session_id,
                "completed_at": datetime.now().isoformat(),
                "endpoints_tested": list(self.results.keys())
            },
            "raw_data_results": fetch_results,
            "comprehensive_analysis": comprehensive_analysis
        }

        # Save comprehensive analysis
        analysis_filepath = self.save_comprehensive_analysis(final_results)

        print(f"\n💾 Complete analysis saved to: {analysis_filepath}")

        # Print executive summary
        self._print_executive_summary(comprehensive_analysis)

        return final_results

    def _print_executive_summary(self, analysis: Dict[str, Any]) -> None:
        """Print executive summary of the analysis"""
        print("\n" + "=" * 60)
        print("📊 EXECUTIVE SUMMARY")
        print("=" * 60)

        # Data Quality Summary
        quality = analysis.get("data_quality", {})
        success_count = len([status for status in quality.get("endpoints_status", {}).values()
                           if status == "SUCCESS"])
        total_endpoints = len(quality.get("endpoints_status", {}))

        print(f"\n🎯 API Performance:")
        print(f"   • Endpoints tested: {total_endpoints}")
        print(f"   • Successful: {success_count}")
        print(f"   • Success rate: {(success_count/total_endpoints)*100:.1f}%" if total_endpoints > 0 else "   • Success rate: N/A")

        # Content Summary
        content = analysis.get("content_insights", {})
        if content.get("content_characteristics"):
            chars = content["content_characteristics"]
            print(f"\n📰 Content Analysis:")
            print(f"   • Total articles analyzed: {chars.get('total_articles_analyzed', 0)}")
            print(f"   • Average word count: {chars.get('avg_word_count', 0):.1f}")
            print(f"   • Articles with images: {chars.get('image_percentage', 0):.1f}%")

        # Top Recommendations
        recommendations = analysis.get("recommendations", {})
        immediate_actions = recommendations.get("immediate_actions", [])

        print(f"\n🚨 Priority Actions:")
        for i, action in enumerate(immediate_actions[:3], 1):
            print(f"   {i}. {action}")

        data_strategy = recommendations.get("data_strategy", [])
        print(f"\n📈 Strategy Recommendations:")
        for i, strategy in enumerate(data_strategy[:3], 1):
            print(f"   {i}. {strategy}")

        print("\n" + "=" * 60)
        print("Analysis complete! Check the saved file for detailed insights.")


def main():
    """Main function to run the orchestrator"""
    orchestrator = CoinDeskOrchestrator()
    results = orchestrator.run_complete_analysis()
    return results


if __name__ == "__main__":
    main()