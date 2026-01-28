from __future__ import annotations

import re


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def parse_duration_to_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        hours = float(text)
        return int(hours * 3600)
    total = 0
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)([dhms])", text):
        amount = float(number)
        if unit == "d":
            total += int(amount * 86400)
        elif unit == "h":
            total += int(amount * 3600)
        elif unit == "m":
            total += int(amount * 60)
        elif unit == "s":
            total += int(amount)
    return total if total > 0 else None
