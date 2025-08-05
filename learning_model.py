from ai_brain import load_brain

def adjust_threshold():
    brain = load_brain()
    if not brain:
        return 60  # Default threshold if no data

    total_tokens = 0
    total_win_rate = 0

    for data in brain.values():
        total = data["success"] + data["failure"]
        if total == 0:
            continue
        win_rate = data["success"] / total
        total_tokens += 1
        total_win_rate += win_rate

    average_win_rate = (total_win_rate / total_tokens) if total_tokens > 0 else 0
    if average_win_rate > 0.8:
        return 70  # Be more selective
    elif average_win_rate < 0.5:
        return 50  # Be more aggressive
    else:
        return 60  # Default

def dynamic_legit_check(score):
    threshold = adjust_threshold()
    return score >= threshold
