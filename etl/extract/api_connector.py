import os
import requests

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("NEWS_API_BASE_URL")

def fetch_news(endpoint: str, params: dict = None) -> dict:
    """
    Fetch news data from the API.

    :param endpoint: API endpoint path (e.g., 'v2/top-headlines')
    :param params: Query parameters dict
    :return: Parsed JSON response as dict
    """
    if params is None:
        params = {}
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    url = f"{BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()
