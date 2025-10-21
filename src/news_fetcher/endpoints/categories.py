"""
CoinDesk Categories Endpoint Handler
Fetches available article categories
"""

import os
import json
import requests
from datetime import datetime
from typing import Dict, List


class CoinDeskCategoriesAPI:
    def __init__(self):
        self.api_key = os.getenv("API_KEY")
        self.base_url = "https://data-api.coindesk.com"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json"
        }

    def fetch_categories(self) -> Dict:
        """
        Fetch available article categories from CoinDesk API

        Returns:
            Dictionary containing API response with categories
        """
        endpoint = f"{self.base_url}/news/v1/category/list"

        try:
            response = requests.get(endpoint, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching categories: {e}")
            return {"error": str(e), "categories": []}

    def save_to_file(self, data: Dict, filename_suffix: str = "") -> str:
        """Save data to JSON file with timestamp"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"coindesk_categories_{timestamp}{filename_suffix}.json"
        filepath = os.path.join("data", "raw", filename)

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return filepath

    def analyze_categories(self, categories_data: Dict) -> Dict:
        """Analyze categories data and extract insights"""
        categories = categories_data.get("categories", [])

        # Category classification
        trading_related = ["markets", "trading", "price", "analysis", "technical"]
        tech_related = ["tech", "technology", "blockchain", "defi", "nft", "web3"]
        policy_related = ["policy", "regulation", "legal", "government", "sec"]
        business_related = ["business", "finance", "corporate", "investment", "funding"]

        analysis = {
            "total_categories": len(categories),
            "category_list": [cat.get("name", "Unknown") for cat in categories],
            "category_ids": [cat.get("id", "unknown") for cat in categories],
            "fetched_at": datetime.now().isoformat(),
            "classification": {
                "trading_related": [],
                "tech_related": [],
                "policy_related": [],
                "business_related": [],
                "other": []
            },
            "insights": {
                "has_markets_category": False,
                "has_defi_category": False,
                "has_policy_category": False,
                "has_nft_category": False,
                "most_common_terms": []
            }
        }

        # Classify categories
        for category in categories:
            cat_name = category.get("name", "").lower()
            cat_id = category.get("id", "").lower()
            full_text = f"{cat_name} {cat_id}".lower()

            classified = False

            # Check trading related
            if any(term in full_text for term in trading_related):
                analysis["classification"]["trading_related"].append(category)
                classified = True

            # Check tech related
            if any(term in full_text for term in tech_related):
                analysis["classification"]["tech_related"].append(category)
                classified = True

            # Check policy related
            if any(term in full_text for term in policy_related):
                analysis["classification"]["policy_related"].append(category)
                classified = True

            # Check business related
            if any(term in full_text for term in business_related):
                analysis["classification"]["business_related"].append(category)
                classified = True

            if not classified:
                analysis["classification"]["other"].append(category)

        # Update insights
        analysis["insights"]["has_markets_category"] = any(
            "market" in cat.get("name", "").lower() for cat in categories
        )
        analysis["insights"]["has_defi_category"] = any(
            "defi" in cat.get("name", "").lower() for cat in categories
        )
        analysis["insights"]["has_policy_category"] = any(
            "policy" in cat.get("name", "").lower() for cat in categories
        )
        analysis["insights"]["has_nft_category"] = any(
            "nft" in cat.get("name", "").lower() for cat in categories
        )

        # Count term frequency
        all_terms = " ".join([cat.get("name", "") for cat in categories]).lower().split()
        term_freq = {}
        for term in all_terms:
            if len(term) > 2:  # Ignore very short terms
                term_freq[term] = term_freq.get(term, 0) + 1

        analysis["insights"]["most_common_terms"] = sorted(
            term_freq.items(), key=lambda x: x[1], reverse=True
        )[:5]

        return analysis


def main():
    """Main function to demonstrate categories endpoint usage"""
    api = CoinDeskCategoriesAPI()

    print("🔄 Fetching news categories...")

    # Fetch categories data
    categories_data = api.fetch_categories()

    if "error" not in categories_data:
        # Save raw data
        filepath = api.save_to_file(categories_data)
        print(f"✅ Raw categories data saved to {filepath}")

        # Analyze categories
        analysis = api.analyze_categories(categories_data)

        # Save analysis
        analysis_filepath = api.save_to_file(analysis, "_analysis")
        print(f"✅ Categories analysis saved to {analysis_filepath}")

        # Print summary
        print(f"\n📊 Categories Summary:")
        print(f"  Total categories: {analysis['total_categories']}")
        print(f"  Trading-related: {len(analysis['classification']['trading_related'])}")
        print(f"  Tech-related: {len(analysis['classification']['tech_related'])}")
        print(f"  Policy-related: {len(analysis['classification']['policy_related'])}")
        print(f"  Business-related: {len(analysis['classification']['business_related'])}")
        print(f"  Other: {len(analysis['classification']['other'])}")

        print(f"\n📂 Available Categories:")
        for category in analysis['category_list'][:10]:  # Show first 10
            print(f"  • {category}")

        if len(analysis['category_list']) > 10:
            print(f"  ... and {len(analysis['category_list']) - 10} more")

        print(f"\n🔍 Key Insights:")
        insights = analysis['insights']
        print(f"  • Has Markets category: {insights['has_markets_category']}")
        print(f"  • Has DeFi category: {insights['has_defi_category']}")
        print(f"  • Has Policy category: {insights['has_policy_category']}")
        print(f"  • Has NFT category: {insights['has_nft_category']}")

        if insights['most_common_terms']:
            print(f"  • Most common terms: {', '.join([term for term, count in insights['most_common_terms']])}")

        return {
            "raw_data": categories_data,
            "analysis": analysis,
            "filepath": filepath,
            "analysis_filepath": analysis_filepath
        }
    else:
        print(f"❌ Error fetching categories: {categories_data.get('error')}")
        return {"error": categories_data.get('error')}


if __name__ == "__main__":
    result = main()
    if "error" not in result:
        print("\n🎯 Categories fetch completed successfully!")
    else:
        print(f"\n❌ Categories fetch failed: {result['error']}")