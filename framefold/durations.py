"""Validated duration preferences and evidence-centered proposal boundaries."""
def normalize(value):
    if value is None or value == {"mode": "auto"}:
        return None
    if not isinstance(value, dict):
        raise ValueError("Choose a valid clip duration.")
    mode = value.get("mode")
    fields = ("min_ms", "max_ms") if mode == "range" else ("target_ms",)
    if mode not in ("exact", "target", "range") or set(value) != {"mode", *fields}:
        raise ValueError("Choose a valid duration mode and length.")
    if any(type(value[k]) is not int or not 1000 <= value[k] <= 300000 for k in fields):
        raise ValueError("Choose durations between one second and five minutes.")
    if mode == "range" and value["min_ms"] > value["max_ms"]:
        raise ValueError("Minimum duration must be at most the maximum.")
    return dict(value)


def bounds(pref):
    if pref["mode"] == "range":
        return pref["min_ms"], pref["max_ms"]
    target = pref["target_ms"]
    slack = target // 5 if pref["mode"] == "target" else 0
    return max(1000, target-slack), min(300000, target+slack)


def windows(rows, signals, duration, pref):
    if not pref:
        return rows
    low, high = bounds(pref)
    if duration < low:
        return []
    speech = signals["speech"]
    # Prefix counts measure support throughout the proposed export, including
    # context spanning adjacent decoder windows. Each bin is half a second.
    motion = signals.get("motion_cues", signals["motion"])
    energy = signals["energy"]
    active = [int((i < len(motion) and motion[i] >= 2.2) or
                  (i < len(energy) and energy[i] >= 220)) for i in range((duration+499)//500)]
    for cue in speech:
        for i in range(max(0,cue["start"]//500), min(len(active),(cue["end"]+499)//500)):
            active[i] = 1
    prefix = [0]
    for value in active:
        prefix.append(prefix[-1]+value)
    result = []
    for a, b, score, reason in rows:
        length = min(duration, high, max(low, pref.get("target_ms", b-a)))
        start = max(0, min(duration-length, (a+b-length)//2))
        end = start+length
        if pref["mode"] != "exact":
            ends = [cue["end"] for cue in speech if start+low <= cue["end"] <= min(duration,start+high)
                    and cue["end"] >= (a+b)//2 and cue["text"].rstrip().endswith((".", "!", "?", "。", "！", "？"))]
            if ends:
                end = min(ends, key=lambda t: abs(t-end))
        first, last = start//500, (end+499)//500
        fraction = (prefix[last]-prefix[first]) / max(1,last-first)
        if end-start >= 30000 and fraction < 0.15:
            continue
        if reason.startswith("Speech"):
            reason = "Spoken passage with surrounding context."
        result.append((start, end, score + fraction, reason))
    return result
