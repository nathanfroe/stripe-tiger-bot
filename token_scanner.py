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
                "address": item.get("baseToken", {}).get("address", ""),
                "liquidity": float(item.get("liquidity", {}).get("usd", 0)),
                "creator_age_days": 7  # Placeholder for now
            }
            tokens.append(token)
        return tokens
    except Exception as e:
        print(f"Error in get_new_tokens: {e}")
        return []
