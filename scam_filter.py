# scam_filter.py

def is_legit_token(token):
    name = token.get("name", "").lower()
    if any(bad_word in name for bad_word in ["rug", "elon", "shit", "moon", "baby", "inu", "fuck", "scam"]):
        return False
    if token.get("liquidity", 0) < 1:
        return False
    if token.get("creator_age_days", 0) < 3:
        return False
    return True
