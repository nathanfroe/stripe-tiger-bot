# scam_filter.py

def is_legit_token(token_data):
    # Placeholder: Simple rule-based check
    if token_data.get("liquidity_locked", False) and not token_data.get("honeypot", True):
        return True
    return False
