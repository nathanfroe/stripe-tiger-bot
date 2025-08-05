import requests

def get_new_tokens():
    url = "https://api.dexscreener.com/latest/dex/pairs/bsc"
    try:
        response = requests.get(url)
        data = response.json()
        tokens = []
        for item in data.get("pairs", []):
            token = {
                "name": item.get("baseToken", {}).get("name", "Unknown"),
                "address": item
