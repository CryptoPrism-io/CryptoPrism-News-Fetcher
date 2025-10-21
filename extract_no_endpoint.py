"""
Extract all sources and categories with no endpoint and save to CSV
"""
import json
import csv
from pathlib import Path
from typing import List, Dict

def find_latest_files():
    """Find the latest source and category JSON files"""
    data_dir = Path("data/raw")

    # Find latest sources file
    sources_files = list(data_dir.glob("coindesk_sources_*.json"))
    latest_sources = max(sources_files, key=lambda p: p.stat().st_mtime) if sources_files else None

    # Find latest categories file
    categories_files = list(data_dir.glob("coindesk_categories_*.json"))
    latest_categories = max(categories_files, key=lambda p: p.stat().st_mtime) if categories_files else None

    return latest_sources, latest_categories

def extract_no_endpoint_sources(filepath: Path) -> List[Dict]:
    """Extract sources with no endpoint"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    no_endpoint = []
    for source in data.get('Data', []):
        # Check if ENDPOINT field is missing or null
        if 'ENDPOINT' not in source or source.get('ENDPOINT') is None:
            no_endpoint.append({
                'Type': 'SOURCE',
                'ID': source.get('ID'),
                'SOURCE_KEY': source.get('SOURCE_KEY'),
                'NAME': source.get('NAME'),
                'URL': source.get('URL'),
                'SOURCE_TYPE': source.get('SOURCE_TYPE'),
                'ENDPOINT': source.get('ENDPOINT', 'NULL'),
                'STATUS': source.get('STATUS')
            })

    return no_endpoint

def extract_no_endpoint_categories(filepath: Path) -> List[Dict]:
    """Extract categories with no endpoint"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    no_endpoint = []
    for category in data.get('Data', []):
        # Check if ENDPOINT field is missing or null
        if 'ENDPOINT' not in category or category.get('ENDPOINT') is None:
            no_endpoint.append({
                'Type': 'CATEGORY',
                'ID': category.get('ID'),
                'SOURCE_KEY': 'N/A',
                'NAME': category.get('NAME'),
                'URL': 'N/A',
                'SOURCE_TYPE': category.get('TYPE'),
                'ENDPOINT': category.get('ENDPOINT', 'NULL'),
                'STATUS': category.get('STATUS')
            })

    return no_endpoint

def save_to_csv(entries: List[Dict], output_file: str):
    """Save entries to CSV file"""
    if not entries:
        print("No entries found with missing endpoint")
        return

    # Define CSV columns
    fieldnames = ['Type', 'ID', 'SOURCE_KEY', 'NAME', 'URL', 'SOURCE_TYPE', 'ENDPOINT', 'STATUS']

    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(entries)

    print(f"✅ Saved {len(entries)} entries to {output_file}")

def main():
    """Main execution"""
    print("🔍 Searching for entries with no endpoint...")

    # Find latest files
    sources_file, categories_file = find_latest_files()

    all_entries = []

    # Extract from sources
    if sources_file:
        print(f"📄 Processing sources file: {sources_file}")
        sources_no_endpoint = extract_no_endpoint_sources(sources_file)
        all_entries.extend(sources_no_endpoint)
        print(f"   Found {len(sources_no_endpoint)} sources with no endpoint")

    # Extract from categories
    if categories_file:
        print(f"📄 Processing categories file: {categories_file}")
        categories_no_endpoint = extract_no_endpoint_categories(categories_file)
        all_entries.extend(categories_no_endpoint)
        print(f"   Found {len(categories_no_endpoint)} categories with no endpoint")

    # Save to CSV
    output_file = "data/processed/no_endpoint_entries.csv"
    Path("data/processed").mkdir(parents=True, exist_ok=True)
    save_to_csv(all_entries, output_file)

    # Print summary
    print(f"\n📊 Summary:")
    print(f"   Total entries with no endpoint: {len(all_entries)}")
    print(f"   Sources: {sum(1 for e in all_entries if e['Type'] == 'SOURCE')}")
    print(f"   Categories: {sum(1 for e in all_entries if e['Type'] == 'CATEGORY')}")

if __name__ == "__main__":
    main()
