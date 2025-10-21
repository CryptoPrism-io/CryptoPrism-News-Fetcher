"""
CoinDesk Sources Endpoint Handler
Fetches available news sources
"""

import os
import json
import requests
from datetime import datetime
from typing import Dict, List


class CoinDeskSourcesAPI:
    def __init__(self):
        self.api_key = os.getenv("API_KEY")
        self.base_url = "https://data-api.coindesk.com"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json"
        }

    def fetch_sources(self) -> Dict:
        """
        Fetch available news sources from CoinDesk API

        Returns:
            Dictionary containing API response with sources
        """
        endpoint = f"{self.base_url}/news/v1/source/list"

        try:
            response = requests.get(endpoint, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching sources: {e}")
            return {"error": str(e), "sources": []}

    def save_to_file(self, data: Dict, filename_suffix: str = "") -> str:
        """Save data to JSON file with timestamp"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"coindesk_sources_{timestamp}{filename_suffix}.json"
        filepath = os.path.join("data", "raw", filename)

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return filepath

    def analyze_sources(self, sources_data: Dict) -> Dict:
        """Analyze sources data and extract insights"""
        sources = sources_data.get("sources", [])

        analysis = {
            "total_sources": len(sources),
            "source_list": [source.get("name", "Unknown") for source in sources],
            "source_ids": [source.get("id", "unknown") for source in sources],
            "fetched_at": datetime.now().isoformat(),
            "analysis": {
                "has_coindesk": any("coindesk" in source.get("id", "").lower() for source in sources),
                "has_reuters": any("reuters" in source.get("id", "").lower() for source in sources),
                "has_bloomberg": any("bloomberg" in source.get("id", "").lower() for source in sources),
                "crypto_specific_sources": [
                    source for source in sources
                    if any(term in source.get("name", "").lower()
                          for term in ["crypto", "coin", "bitcoin", "blockchain"])
                ],
                "mainstream_sources": [
                    source for source in sources
                    if any(term in source.get("name", "").lower()
                          for term in ["reuters", "bloomberg", "wall street", "financial times"])
                ]
            }
        }

        return analysis


def main():
    """Main function to demonstrate sources endpoint usage"""
    api = CoinDeskSourcesAPI()

    print("🔄 Fetching news sources...")

    # Fetch sources data
    sources_data = api.fetch_sources()

    if "error" not in sources_data:
        # Save raw data
        filepath = api.save_to_file(sources_data)
        print(f"✅ Raw sources data saved to {filepath}")

        # Analyze sources
        analysis = api.analyze_sources(sources_data)

        # Save analysis
        analysis_filepath = api.save_to_file(analysis, "_analysis")
        print(f"✅ Sources analysis saved to {analysis_filepath}")

        # Print summary
        print(f"\n📊 Sources Summary:")
        print(f"  Total sources: {analysis['total_sources']}")
        print(f"  Crypto-specific sources: {len(analysis['analysis']['crypto_specific_sources'])}")
        print(f"  Mainstream sources: {len(analysis['analysis']['mainstream_sources'])}")

        print(f"\n📰 Available Sources:")
        for source in analysis['source_list'][:10]:  # Show first 10
            print(f"  • {source}")

        if len(analysis['source_list']) > 10:
            print(f"  ... and {len(analysis['source_list']) - 10} more")

        return {
            "raw_data": sources_data,
            "analysis": analysis,
            "filepath": filepath,
            "analysis_filepath": analysis_filepath
        }
    else:
        print(f"❌ Error fetching sources: {sources_data.get('error')}")
        return {"error": sources_data.get('error')}


if __name__ == "__main__":
    result = main()
    if "error" not in result:
        print("\n🎯 Sources fetch completed successfully!")
    else:
        print(f"\n❌ Sources fetch failed: {result['error']}")