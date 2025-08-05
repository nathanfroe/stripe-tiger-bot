import requests
import time

def get_new_tokens():
    url = "https://api.coingecko.com/api/v3/coins/list"
    try:
        response = requests.get(url)
        response.raise_for_status()
        coins = response.json()
        print(f"[Scanner] Retrieved {len(coins)} tokens.")
        return coins
    except requests.RequestException as e:
        print(f"[Scanner] Error fetching tokens: {e}")
        return []
