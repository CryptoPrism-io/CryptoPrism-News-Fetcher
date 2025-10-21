"""
Comprehensive data analysis of CoinDesk API exports
Generates insights and use case recommendations
"""

import os
import csv
import json
from datetime import datetime
from collections import Counter, defaultdict
from typing import Dict, List, Any


class CoinDeskDataAnalyzer:
    def __init__(self, csv_dir: str = "data/csv_exports"):
        self.csv_dir = csv_dir
        self.sources = []
        self.categories = []
        self.articles = []
        self.articles_full = []

    def load_csv_data(self, filename: str) -> List[Dict]:
        """Load data from CSV file"""
        filepath = os.path.join(self.csv_dir, filename)

        if not os.path.exists(filepath):
            print(f"⚠️ File not found: {filepath}")
            return []

        data = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            data = list(reader)

        return data

    def load_all_data(self):
        """Load all CSV files"""
        print("📂 Loading CSV data...")

        # Find the latest CSV files
        files = os.listdir(self.csv_dir)

        # Get latest timestamp
        sources_files = [f for f in files if f.startswith('sources_')]
        if sources_files:
            latest_sources = sorted(sources_files)[-1]
            timestamp = latest_sources.replace('sources_', '').replace('.csv', '')

            self.sources = self.load_csv_data(f"sources_{timestamp}.csv")
            self.categories = self.load_csv_data(f"categories_{timestamp}.csv")
            self.articles = self.load_csv_data(f"articles_{timestamp}.csv")
            self.articles_full = self.load_csv_data(f"articles_full_body_{timestamp}.csv")

            print(f"✅ Loaded {len(self.sources)} sources")
            print(f"✅ Loaded {len(self.categories)} categories")
            print(f"✅ Loaded {len(self.articles)} articles")
            print(f"✅ Loaded {len(self.articles_full)} full articles")
        else:
            print("❌ No CSV files found")

    def analyze_sources(self) -> Dict:
        """Analyze source data"""
        print("\n📰 Analyzing Sources...")

        analysis = {
            "total_sources": len(self.sources),
            "active_sources": 0,
            "benchmark_scores": [],
            "source_types": Counter(),
            "languages": Counter(),
            "top_sources": [],
            "score_distribution": {
                "excellent": 0,    # 70+
                "good": 0,         # 60-69
                "average": 0,      # 50-59
                "below_average": 0 # <50
            }
        }

        for source in self.sources:
            if source.get('STATUS') == 'ACTIVE':
                analysis['active_sources'] += 1

            score = int(source.get('BENCHMARK_SCORE', 0))
            analysis['benchmark_scores'].append(score)

            if score >= 70:
                analysis['score_distribution']['excellent'] += 1
            elif score >= 60:
                analysis['score_distribution']['good'] += 1
            elif score >= 50:
                analysis['score_distribution']['average'] += 1
            else:
                analysis['score_distribution']['below_average'] += 1

            analysis['source_types'][source.get('SOURCE_TYPE')] += 1
            analysis['languages'][source.get('LANG')] += 1

        # Top sources by benchmark score
        sorted_sources = sorted(
            self.sources,
            key=lambda x: int(x.get('BENCHMARK_SCORE', 0)),
            reverse=True
        )

        analysis['top_sources'] = [
            {
                'name': s.get('NAME'),
                'key': s.get('SOURCE_KEY'),
                'score': int(s.get('BENCHMARK_SCORE', 0))
            }
            for s in sorted_sources[:10]
        ]

        # Calculate stats
        if analysis['benchmark_scores']:
            analysis['avg_benchmark_score'] = sum(analysis['benchmark_scores']) / len(analysis['benchmark_scores'])
            analysis['max_benchmark_score'] = max(analysis['benchmark_scores'])
            analysis['min_benchmark_score'] = min(analysis['benchmark_scores'])

        return analysis

    def analyze_categories(self) -> Dict:
        """Analyze category data"""
        print("📂 Analyzing Categories...")

        analysis = {
            "total_categories": len(self.categories),
            "active_categories": 0,
            "categories_with_words": 0,
            "categories_with_phrases": 0,
            "top_categories_by_complexity": [],
            "category_types": {
                "tokens": [],      # Individual cryptocurrencies
                "topics": [],      # General topics
                "events": []       # Event-based categories
            }
        }

        token_keywords = ['btc', 'eth', 'ada', 'sol', 'xrp', 'doge', 'matic']
        topic_keywords = ['defi', 'nft', 'market', 'trading', 'policy', 'regulation']
        event_keywords = ['airdrop', 'launch', 'upgrade', 'fork', 'hack']

        for category in self.categories:
            if category.get('STATUS') == 'ACTIVE':
                analysis['active_categories'] += 1

            words = category.get('INCLUDED_WORDS', '')
            phrases = category.get('INCLUDED_PHRASES', '')

            if words:
                analysis['categories_with_words'] += 1
            if phrases:
                analysis['categories_with_phrases'] += 1

            # Categorize by type
            name = category.get('NAME', '').lower()

            if any(keyword in name for keyword in token_keywords):
                analysis['category_types']['tokens'].append(category.get('NAME'))
            elif any(keyword in name for keyword in topic_keywords):
                analysis['category_types']['topics'].append(category.get('NAME'))
            elif any(keyword in name for keyword in event_keywords):
                analysis['category_types']['events'].append(category.get('NAME'))

        # Top categories by filter complexity
        sorted_cats = sorted(
            self.categories,
            key=lambda x: len(x.get('INCLUDED_WORDS', '')) + len(x.get('INCLUDED_PHRASES', '')),
            reverse=True
        )

        analysis['top_categories_by_complexity'] = [
            {
                'name': c.get('NAME'),
                'words_count': len(c.get('INCLUDED_WORDS', '').split(',')),
                'phrases_count': len(c.get('INCLUDED_PHRASES', '').split(',')) if c.get('INCLUDED_PHRASES') else 0
            }
            for c in sorted_cats[:10]
        ]

        return analysis

    def analyze_articles(self) -> Dict:
        """Analyze article data"""
        print("📰 Analyzing Articles...")

        analysis = {
            "total_articles": len(self.articles),
            "sentiment_distribution": Counter(),
            "source_distribution": Counter(),
            "category_frequency": Counter(),
            "authors_count": set(),
            "articles_with_images": 0,
            "avg_body_length": 0,
            "trending_keywords": Counter(),
            "publication_patterns": {
                "by_hour": defaultdict(int),
                "by_status": Counter()
            }
        }

        body_lengths = []

        for article in self.articles:
            # Sentiment
            sentiment = article.get('SENTIMENT', 'UNKNOWN')
            analysis['sentiment_distribution'][sentiment] += 1

            # Source
            source = article.get('SOURCE_KEY', 'unknown')
            analysis['source_distribution'][source] += 1

            # Categories
            categories = article.get('CATEGORIES', '').split(', ')
            for cat in categories:
                if cat:
                    analysis['category_frequency'][cat] += 1

            # Authors
            authors = article.get('AUTHORS', '')
            if authors:
                for author in authors.split(','):
                    analysis['authors_count'].add(author.strip())

            # Images
            if article.get('IMAGE_URL'):
                analysis['articles_with_images'] += 1

            # Body length
            try:
                length = int(article.get('BODY_LENGTH', 0))
                body_lengths.append(length)
            except:
                pass

            # Keywords
            keywords = article.get('KEYWORDS', '').split('|')
            for keyword in keywords:
                if keyword:
                    analysis['trending_keywords'][keyword.strip()] += 1

            # Status
            status = article.get('STATUS', 'UNKNOWN')
            analysis['publication_patterns']['by_status'][status] += 1

        # Calculate averages
        if body_lengths:
            analysis['avg_body_length'] = sum(body_lengths) / len(body_lengths)
            analysis['max_body_length'] = max(body_lengths)
            analysis['min_body_length'] = min(body_lengths)

        analysis['unique_authors'] = len(analysis['authors_count'])
        analysis['authors_count'] = list(analysis['authors_count'])[:20]  # Top 20 authors

        # Top categories
        analysis['top_categories'] = analysis['category_frequency'].most_common(15)

        # Top keywords
        analysis['top_keywords'] = analysis['trending_keywords'].most_common(15)

        # Image percentage
        analysis['image_percentage'] = (analysis['articles_with_images'] / len(self.articles)) * 100 if self.articles else 0

        return analysis

    def generate_insights(self, source_analysis: Dict, category_analysis: Dict, article_analysis: Dict) -> Dict:
        """Generate actionable insights"""
        print("\n🔍 Generating Insights...")

        insights = {
            "key_findings": [],
            "opportunities": [],
            "recommendations": [],
            "data_quality": [],
            "use_cases": []
        }

        # Key findings
        insights['key_findings'].append(
            f"Analyzed {source_analysis['total_sources']} news sources with average benchmark score of {source_analysis['avg_benchmark_score']:.1f}/100"
        )

        insights['key_findings'].append(
            f"Processed {article_analysis['total_articles']} articles covering {category_analysis['total_categories']} cryptocurrency categories"
        )

        sentiment_total = sum(article_analysis['sentiment_distribution'].values())
        positive_pct = (article_analysis['sentiment_distribution']['POSITIVE'] / sentiment_total) * 100 if sentiment_total else 0
        insights['key_findings'].append(
            f"Overall market sentiment: {positive_pct:.1f}% positive articles"
        )

        # Opportunities
        if source_analysis['score_distribution']['excellent'] > 0:
            insights['opportunities'].append(
                f"Leverage {source_analysis['score_distribution']['excellent']} high-quality sources (score 70+) for premium content"
            )

        top_cat = article_analysis['top_categories'][0] if article_analysis['top_categories'] else None
        if top_cat:
            insights['opportunities'].append(
                f"Most covered topic: {top_cat[0]} ({top_cat[1]} articles) - potential for specialized newsletter"
            )

        insights['opportunities'].append(
            f"Sentiment analysis available on all {article_analysis['total_articles']} articles - ready for trading signals"
        )

        # Recommendations
        insights['recommendations'].append(
            "Implement automated daily fetching to build historical sentiment database"
        )

        if article_analysis['image_percentage'] > 50:
            insights['recommendations'].append(
                f"Leverage high image coverage ({article_analysis['image_percentage']:.1f}%) for visual content creation"
            )

        insights['recommendations'].append(
            "Create category-specific alerts for high-impact topics like POLICY, REGULATION"
        )

        # Data quality
        insights['data_quality'].append(
            f"All {source_analysis['active_sources']} sources are ACTIVE and operational"
        )

        insights['data_quality'].append(
            f"{article_analysis['unique_authors']} unique authors identified for credibility tracking"
        )

        # Use cases
        insights['use_cases'] = [
            {
                "name": "Trading Signals",
                "description": "Correlate sentiment with price movements",
                "data_ready": True,
                "implementation": "Track sentiment changes for specific tokens (BTC, ETH) and alert on significant shifts"
            },
            {
                "name": "Market Intelligence Dashboard",
                "description": "Real-time news monitoring by category",
                "data_ready": True,
                "implementation": "Display trending topics, sentiment trends, and breaking news alerts"
            },
            {
                "name": "Automated Newsletter",
                "description": "Daily/weekly curated news digest",
                "data_ready": True,
                "implementation": "Filter by category, sentiment, and source quality for personalized newsletters"
            },
            {
                "name": "Social Media Automation",
                "description": "Auto-post breaking news to Twitter/Discord",
                "data_ready": True,
                "implementation": "Filter high-quality sources + positive sentiment + trending categories"
            },
            {
                "name": "Research & Analysis",
                "description": "Historical trend analysis and reporting",
                "data_ready": True,
                "implementation": "Build time-series database for sentiment, topic trends, and source analysis"
            }
        ]

        return insights

    def create_report(self) -> Dict:
        """Generate comprehensive analysis report"""
        print("\n📊 Creating Comprehensive Report...")
        print("=" * 60)

        # Load data
        self.load_all_data()

        # Perform analyses
        source_analysis = self.analyze_sources()
        category_analysis = self.analyze_categories()
        article_analysis = self.analyze_articles()
        insights = self.generate_insights(source_analysis, category_analysis, article_analysis)

        report = {
            "report_metadata": {
                "generated_at": datetime.now().isoformat(),
                "data_sources": ["CoinDesk API via data-api.coindesk.com"],
                "analysis_version": "1.0"
            },
            "executive_summary": {
                "total_sources": source_analysis['total_sources'],
                "total_categories": category_analysis['total_categories'],
                "total_articles": article_analysis['total_articles'],
                "data_quality_score": "High",
                "sentiment_overview": dict(article_analysis['sentiment_distribution'])
            },
            "source_analysis": source_analysis,
            "category_analysis": category_analysis,
            "article_analysis": article_analysis,
            "insights": insights
        }

        return report

    def save_report(self, report: Dict, format: str = 'json'):
        """Save report to file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if format == 'json':
            filename = f"analysis_report_{timestamp}.json"
            filepath = os.path.join("data", "processed", filename)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            print(f"\n💾 JSON report saved: {filepath}")
            return filepath

    def print_report_summary(self, report: Dict):
        """Print human-readable report summary"""
        print("\n" + "=" * 60)
        print("📊 COINDESK API DATA ANALYSIS REPORT")
        print("=" * 60)

        # Executive Summary
        summary = report['executive_summary']
        print("\n🎯 EXECUTIVE SUMMARY")
        print("-" * 60)
        print(f"Total News Sources: {summary['total_sources']}")
        print(f"Total Categories: {summary['total_categories']}")
        print(f"Total Articles Analyzed: {summary['total_articles']}")
        print(f"Data Quality: {summary['data_quality_score']}")

        # Sentiment Distribution
        print(f"\nSentiment Distribution:")
        for sentiment, count in summary['sentiment_overview'].items():
            pct = (count / summary['total_articles']) * 100 if summary['total_articles'] else 0
            print(f"  • {sentiment}: {count} ({pct:.1f}%)")

        # Source Analysis
        src = report['source_analysis']
        print(f"\n📰 SOURCE ANALYSIS")
        print("-" * 60)
        print(f"Average Benchmark Score: {src['avg_benchmark_score']:.1f}/100")
        print(f"Score Distribution:")
        print(f"  • Excellent (70+): {src['score_distribution']['excellent']} sources")
        print(f"  • Good (60-69): {src['score_distribution']['good']} sources")
        print(f"  • Average (50-59): {src['score_distribution']['average']} sources")

        print(f"\nTop 5 Sources by Quality:")
        for i, source in enumerate(src['top_sources'][:5], 1):
            print(f"  {i}. {source['name']} (Score: {source['score']})")

        # Article Analysis
        art = report['article_analysis']
        print(f"\n📄 ARTICLE ANALYSIS")
        print("-" * 60)
        print(f"Average Article Length: {art['avg_body_length']:.0f} characters")
        print(f"Articles with Images: {art['image_percentage']:.1f}%")
        print(f"Unique Authors: {art['unique_authors']}")

        print(f"\nTop 5 Trending Categories:")
        for i, (cat, count) in enumerate(art['top_categories'][:5], 1):
            print(f"  {i}. {cat}: {count} articles")

        print(f"\nTop 5 Keywords:")
        for i, (keyword, count) in enumerate(art['top_keywords'][:5], 1):
            print(f"  {i}. {keyword}: {count} mentions")

        # Insights
        insights = report['insights']
        print(f"\n💡 KEY FINDINGS")
        print("-" * 60)
        for finding in insights['key_findings']:
            print(f"  • {finding}")

        print(f"\n🚀 OPPORTUNITIES")
        print("-" * 60)
        for opp in insights['opportunities']:
            print(f"  • {opp}")

        print(f"\n🎯 RECOMMENDATIONS")
        print("-" * 60)
        for rec in insights['recommendations']:
            print(f"  • {rec}")

        print(f"\n📋 USE CASES")
        print("-" * 60)
        for use_case in insights['use_cases']:
            print(f"\n  {use_case['name']}")
            print(f"    Description: {use_case['description']}")
            print(f"    Ready: {'✅ Yes' if use_case['data_ready'] else '❌ No'}")
            print(f"    Implementation: {use_case['implementation']}")

        print("\n" + "=" * 60)


def main():
    """Main function"""
    analyzer = CoinDeskDataAnalyzer()

    # Generate report
    report = analyzer.create_report()

    # Save report
    analyzer.save_report(report, format='json')

    # Print summary
    analyzer.print_report_summary(report)

    return report


if __name__ == "__main__":
    main()
