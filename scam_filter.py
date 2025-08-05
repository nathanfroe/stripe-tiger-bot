def is_scam_token(token):
    try:
        name = token.get("name", "").lower()
        liquidity = token.get("liquidity", 0)
        creator_age_days = token.get("creator_age_days", 0)

        blacklisted = ["baby", "elon", "pepe", "rug", "moon", "pump", "scam", "shit"]
        if any(bad in name for bad in blacklisted):
            return True

        if liquidity < 5000:
            return True

        if creator_age_days < 1:
            return True

        return False
    except Exception as e:
        print(f"Error filtering token: {e}")
        return True# scam_filter.py

