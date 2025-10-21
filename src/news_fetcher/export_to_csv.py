"""
Export CoinDesk API data to CSV files for manual analysis
"""

import os
import csv
import json
from datetime import datetime
from typing import Dict, List, Any
from dotenv import load_dotenv

from .endpoints import (
    CoinDeskArticlesAPI,
    CoinDeskSourcesAPI,
    CoinDeskCategoriesAPI,
    CoinDeskFeedCategoriesAPI
)

load_dotenv()


class CoinDeskCSVExporter:
    def __init__(self):
        self.articles_api = CoinDeskArticlesAPI()
        self.sources_api = CoinDeskSourcesAPI()
        self.categories_api = CoinDeskCategoriesAPI()
        self.feed_categories_api = CoinDeskFeedCategoriesAPI()

        self.export_dir = os.path.join("data", "csv_exports")
        os.makedirs(self.export_dir, exist_ok=True)

        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def export_sources_to_csv(self) -> str:
        """Export sources data to CSV"""
        print("📰 Fetching sources data...")

        sources_data = self.sources_api.fetch_sources()

        if "error" in sources_data:
            print(f"❌ Error fetching sources: {sources_data['error']}")
            return None

        sources = sources_data.get("Data", [])

        if not sources:
            print("⚠️ No sources data returned")
            return None

        filename = f"sources_{self.timestamp}.csv"
        filepath = os.path.join(self.export_dir, filename)

        # Define CSV columns
        fieldnames = [
            'ID',
            'SOURCE_KEY',
            'NAME',
            'IMAGE_URL',
            'URL',
            'LANG',
            'SOURCE_TYPE',
            'LAUNCH_DATE',
            'SORT_ORDER',
            'BENCHMARK_SCORE',
            'STATUS',
            'LAST_UPDATED_TS',
            'CREATED_ON',
            'UPDATED_ON'
        ]

        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()

            for source in sources:
                writer.writerow(source)

        print(f"✅ Exported {len(sources)} sources to {filepath}")
        return filepath

    def export_categories_to_csv(self) -> str:
        """Export categories data to CSV"""
        print("📂 Fetching categories data...")

        categories_data = self.categories_api.fetch_categories()

        if "error" in categories_data:
            print(f"❌ Error fetching categories: {categories_data['error']}")
            return None

        categories = categories_data.get("Data", [])

        if not categories:
            print("⚠️ No categories data returned")
            return None

        filename = f"categories_{self.timestamp}.csv"
        filepath = os.path.join(self.export_dir, filename)

        # Flatten the categories data for CSV
        rows = []
        for category in categories:
            row = {
                'ID': category.get('ID'),
                'NAME': category.get('NAME'),
                'STATUS': category.get('STATUS'),
                'CREATED_ON': category.get('CREATED_ON'),
                'UPDATED_ON': category.get('UPDATED_ON'),
                'INCLUDED_WORDS': ', '.join(category.get('FILTER', {}).get('INCLUDED_WORDS', [])),
                'INCLUDED_PHRASES': ', '.join(category.get('FILTER', {}).get('INCLUDED_PHRASES', []))
            }
            rows.append(row)

        fieldnames = ['ID', 'NAME', 'STATUS', 'CREATED_ON', 'UPDATED_ON', 'INCLUDED_WORDS', 'INCLUDED_PHRASES']

        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"✅ Exported {len(categories)} categories to {filepath}")
        return filepath

    def export_articles_to_csv(self, search_terms: List[str] = None, limit: int = 50) -> str:
        """Export articles data to CSV"""
        print("📰 Fetching articles data...")

        if search_terms is None:
            search_terms = ["bitcoin", "ethereum", "cryptocurrency", "markets"]

        all_articles = []

        for term in search_terms:
            print(f"  🔍 Searching for: {term}")
            articles_data = self.articles_api.fetch_articles(
                limit=limit,
                category=term,
                source="coindesk"
            )

            if "error" in articles_data:
                print(f"    ⚠️ Error: {articles_data['error']}")
                continue

            articles = articles_data.get("Data", [])
            print(f"    ✓ Found {len(articles)} articles")
            all_articles.extend(articles)

        if not all_articles:
            print("⚠️ No articles data returned")
            return None

        filename = f"articles_{self.timestamp}.csv"
        filepath = os.path.join(self.export_dir, filename)

        # Flatten articles for CSV
        rows = []
        for article in all_articles:
            # Get categories as comma-separated string
            categories = article.get('CATEGORY_DATA', [])
            category_names = ', '.join([cat.get('NAME', '') for cat in categories])

            row = {
                'ID': article.get('ID'),
                'GUID': article.get('GUID'),
                'TITLE': article.get('TITLE'),
                'SUBTITLE': article.get('SUBTITLE'),
                'AUTHORS': article.get('AUTHORS'),
                'PUBLISHED_ON': article.get('PUBLISHED_ON'),
                'URL': article.get('URL'),
                'IMAGE_URL': article.get('IMAGE_URL'),
                'KEYWORDS': article.get('KEYWORDS'),
                'LANG': article.get('LANG'),
                'SENTIMENT': article.get('SENTIMENT'),
                'UPVOTES': article.get('UPVOTES'),
                'DOWNVOTES': article.get('DOWNVOTES'),
                'SCORE': article.get('SCORE'),
                'STATUS': article.get('STATUS'),
                'SOURCE_KEY': article.get('SOURCE_DATA', {}).get('SOURCE_KEY'),
                'SOURCE_NAME': article.get('SOURCE_DATA', {}).get('NAME'),
                'CATEGORIES': category_names,
                'BODY_LENGTH': len(article.get('BODY', '')),
                'CREATED_ON': article.get('CREATED_ON'),
                'UPDATED_ON': article.get('UPDATED_ON')
            }
            rows.append(row)

        fieldnames = [
            'ID', 'GUID', 'TITLE', 'SUBTITLE', 'AUTHORS', 'PUBLISHED_ON',
            'URL', 'IMAGE_URL', 'KEYWORDS', 'LANG', 'SENTIMENT',
            'UPVOTES', 'DOWNVOTES', 'SCORE', 'STATUS', 'SOURCE_KEY',
            'SOURCE_NAME', 'CATEGORIES', 'BODY_LENGTH', 'CREATED_ON', 'UPDATED_ON'
        ]

        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"✅ Exported {len(all_articles)} articles to {filepath}")
        return filepath

    def export_articles_with_full_body(self, search_terms: List[str] = None, limit: int = 20) -> str:
        """Export articles with full body text to CSV (for detailed analysis)"""
        print("📰 Fetching articles with full body text...")

        if search_terms is None:
            search_terms = ["bitcoin", "ethereum"]

        all_articles = []

        for term in search_terms:
            print(f"  🔍 Searching for: {term}")
            articles_data = self.articles_api.fetch_articles(
                limit=limit,
                category=term,
                source="coindesk"
            )

            if "error" in articles_data:
                print(f"    ⚠️ Error: {articles_data['error']}")
                continue

            articles = articles_data.get("Data", [])
            print(f"    ✓ Found {len(articles)} articles")
            all_articles.extend(articles)

        if not all_articles:
            print("⚠️ No articles data returned")
            return None

        filename = f"articles_full_body_{self.timestamp}.csv"
        filepath = os.path.join(self.export_dir, filename)

        # Full article data including body
        rows = []
        for article in all_articles:
            categories = article.get('CATEGORY_DATA', [])
            category_names = ', '.join([cat.get('NAME', '') for cat in categories])

            row = {
                'ID': article.get('ID'),
                'TITLE': article.get('TITLE'),
                'SUBTITLE': article.get('SUBTITLE'),
                'AUTHORS': article.get('AUTHORS'),
                'PUBLISHED_ON': article.get('PUBLISHED_ON'),
                'URL': article.get('URL'),
                'SENTIMENT': article.get('SENTIMENT'),
                'SOURCE_NAME': article.get('SOURCE_DATA', {}).get('NAME'),
                'CATEGORIES': category_names,
                'KEYWORDS': article.get('KEYWORDS'),
                'BODY': article.get('BODY', '')[:5000]  # Limit body to 5000 chars for CSV
            }
            rows.append(row)

        fieldnames = [
            'ID', 'TITLE', 'SUBTITLE', 'AUTHORS', 'PUBLISHED_ON',
            'URL', 'SENTIMENT', 'SOURCE_NAME', 'CATEGORIES', 'KEYWORDS', 'BODY'
        ]

        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"✅ Exported {len(all_articles)} articles with body to {filepath}")
        return filepath

    def export_feed_categories_to_csv(self) -> str:
        """Export feed categories mapping to CSV"""
        print("🔗 Fetching feed categories data...")

        feed_data = self.feed_categories_api.fetch_feed_categories()

        if "error" in feed_data:
            print(f"❌ Error fetching feed categories: {feed_data['error']}")
            return None

        feeds = feed_data.get("feeds", [])

        if not feeds:
            print("⚠️ No feed categories data returned")
            return None

        filename = f"feed_categories_{self.timestamp}.csv"
        filepath = os.path.join(self.export_dir, filename)

        # Flatten feed categories
        rows = []
        for feed in feeds:
            row = {
                'SOURCE': feed.get('source'),
                'CATEGORIES': ', '.join(feed.get('categories', []))
            }
            rows.append(row)

        fieldnames = ['SOURCE', 'CATEGORIES']

        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"✅ Exported {len(feeds)} feed mappings to {filepath}")
        return filepath

    def export_all(self, article_limit: int = 100) -> Dict[str, str]:
        """Export all endpoints to CSV files"""
        print("🚀 Starting complete CSV export...")
        print("=" * 60)

        results = {
            'sources': None,
            'categories': None,
            'articles': None,
            'articles_full': None,
            'feed_categories': None
        }

        # Export sources
        results['sources'] = self.export_sources_to_csv()
        print()

        # Export categories
        results['categories'] = self.export_categories_to_csv()
        print()

        # Export articles (summary)
        results['articles'] = self.export_articles_to_csv(
            search_terms=["bitcoin", "ethereum", "defi", "markets", "policy"],
            limit=article_limit
        )
        print()

        # Export articles with full body (smaller set)
        results['articles_full'] = self.export_articles_with_full_body(
            search_terms=["bitcoin", "ethereum"],
            limit=20
        )
        print()

        # Export feed categories
        results['feed_categories'] = self.export_feed_categories_to_csv()
        print()

        print("=" * 60)
        print("📊 Export Summary:")
        for endpoint, filepath in results.items():
            if filepath:
                print(f"  ✅ {endpoint}: {filepath}")
            else:
                print(f"  ❌ {endpoint}: Failed")

        print(f"\n📁 All exports saved to: {self.export_dir}")
        return results


def main():
    """Main function to export all data to CSV"""
    exporter = CoinDeskCSVExporter()

    print("🎯 CoinDesk API Data → CSV Exporter")
    print("=" * 60)
    print()

    # Export all data
    results = exporter.export_all(article_limit=100)

    print("\n✅ Export complete!")
    print(f"\n📂 Open this folder to view your CSV files:")
    print(f"   {os.path.abspath(exporter.export_dir)}")

    return results


if __name__ == "__main__":
    main()
