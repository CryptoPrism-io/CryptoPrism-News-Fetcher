"""
CoinDesk Articles Endpoint Handler
Fetches latest news articles with metadata
"""

import os
import json
import requests
from datetime import datetime
from typing import Dict, Optional, List


class CoinDeskArticlesAPI:
    def __init__(self):
        self.api_key = os.getenv("API_KEY")
        self.base_url = "https://data-api.coindesk.com"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json"
        }

    def fetch_articles(self,
                      limit: int = 20,
                      offset: int = 0,
                      from_time: Optional[str] = None,
                      to_time: Optional[str] = None,
                      category: Optional[str] = None,
                      source: Optional[str] = None) -> Dict:
        """
        Fetch articles from CoinDesk API

        Args:
            limit: Number of results to return
            offset: Pagination offset
            from_time: Fetch articles from this datetime (ISO format)
            to_time: Fetch articles up to this datetime (ISO format)
            category: Filter by category
            source: Filter by source

        Returns:
            Dictionary containing API response
        """
        endpoint = f"{self.base_url}/news/v1/search"

        params = {
            "lang": "EN",
            "source_key": source if source else "coindesk",  # Default to coindesk if no source specified
        }

        # Add search string for category or general search
        if category:
            params["search_string"] = category
        else:
            params["search_string"] = ""  # Empty for all articles

        # Add limit if different from default
        if limit != 20:
            params["limit"] = limit

        try:
            response = requests.get(endpoint, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching articles: {e}")
            return {"error": str(e), "articles": [], "count": 0, "total": 0}

    def save_to_file(self, data: Dict, filename_suffix: str = "") -> str:
        """Save data to JSON file with timestamp"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"coindesk_articles_{timestamp}{filename_suffix}.json"
        filepath = os.path.join("data", "raw", filename)

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return filepath

    def extract_article_metadata(self, articles_data: Dict) -> List[Dict]:
        """Extract and process article metadata"""
        articles = articles_data.get("articles", [])
        processed_articles = []

        for article in articles:
            processed_article = {
                "id": article.get("id"),
                "title": article.get("title"),
                "summary": article.get("summary"),
                "url": article.get("url"),
                "image_url": article.get("image_url"),
                "published_at": article.get("published_at"),
                "source": article.get("source"),
                "category": article.get("category"),
                "fetched_at": datetime.now().isoformat(),
                "word_count": len(article.get("title", "").split()) + len(article.get("summary", "").split()),
                "has_image": bool(article.get("image_url"))
            }
            processed_articles.append(processed_article)

        return processed_articles


def main():
    """Main function to demonstrate endpoint usage"""
    api = CoinDeskArticlesAPI()

    print("🔄 Fetching latest articles...")

    # Fetch different sets of articles
    test_cases = [
        {"name": "latest_20", "params": {"limit": 20}},
        {"name": "ethereum_search", "params": {"limit": 15, "category": "ethereum"}},
        {"name": "coindesk_source", "params": {"limit": 15, "source": "coindesk"}},
        {"name": "bitcoin_search", "params": {"limit": 10, "category": "bitcoin"}}
    ]

    results = {}

    for test_case in test_cases:
        print(f"📰 Fetching {test_case['name']}...")
        data = api.fetch_articles(**test_case["params"])

        if "error" not in data:
            filepath = api.save_to_file(data, f"_{test_case['name']}")
            processed = api.extract_article_metadata(data)

            results[test_case['name']] = {
                "filepath": filepath,
                "count": data.get("count", 0),
                "total_available": data.get("total", 0),
                "processed_articles": processed
            }

            print(f"✅ Saved {len(processed)} articles to {filepath}")
        else:
            print(f"❌ Error fetching {test_case['name']}: {data.get('error')}")
            results[test_case['name']] = {"error": data.get('error')}

    return results


if __name__ == "__main__":
    results = main()
    print("\n📊 Articles fetch completed!")
    for name, result in results.items():
        if "error" not in result:
            print(f"  {name}: {result['count']} articles")
        else:
            print(f"  {name}: ERROR - {result['error']}")