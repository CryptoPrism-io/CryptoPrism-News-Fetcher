import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY")
URL = "https://min-api.cryptocompare.com/data/v2/news/"
HEADERS = {"Content-type": "application/json; charset=UTF-8"}
PARAMS = {"lang": "EN", "api_key": API_KEY}

def fetch_crypto_news() -> requests.Response:
    resp = requests.get(URL, params=PARAMS, headers=HEADERS)
    resp.raise_for_status()
    return resp

if __name__ == "__main__":
    response = fetch_crypto_news()
    print(response.status_code)
    print(response.json())
