from __future__ import annotations

import re
from fractions import Fraction


def normalize_description(source_description: str) -> str:
    return re.sub(r"\s+", " ", source_description).strip()


def split_csvish(text: str) -> list[str]:
    if not text:
        return []
    text = text.replace(";", ",").replace("\n", ",")
    return [item.strip() for item in text.split(",") if item.strip()]


def detect_measurement(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _normalize_measurement_source(source: str) -> str:
    return source.strip().replace('"', " inch").replace("in.", "inch").replace(" in ", " inch ")


def parse_measurement_value(source: str) -> dict:
    normalized = _normalize_measurement_source(source)
    match = re.search(r"(?P<value>\d+(?:\.\d+)?|\d+/\d+)\s*(?P<unit>inch|in|mm|millimeter|millimeters)", normalized, re.IGNORECASE)
    if not match:
        return {"value": None, "unit": "", "source_text": source.strip(), "confidence": 0.0}
    raw_value = match.group("value")
    try:
        value = float(Fraction(raw_value)) if "/" in raw_value else float(raw_value)
    except Exception:
        value = None
    unit = match.group("unit").lower()
    if unit in {"in"}:
        unit = "inch"
    if unit in {"millimeter", "millimeters"}:
        unit = "mm"
    return {"value": value, "unit": unit, "source_text": source.strip(), "confidence": 1.0 if value is not None else 0.5}


def detect_geometry_measurement(text: str, kind: str) -> str:
    patterns = {
        "diameter": [
            r"\b\d+(?:\.\d+)?(?:-\s*|\s+)?inch\s+diameter\b",
            r"\bdiameter\s*[:=]\s*\d+(?:\.\d+)?\s*(?:inch|in|mm)\b",
            r"\b\d+(?:\.\d+)?\s*(?:inch|in|mm)\s+(?:diameter|dia\\.?|diam\\.?|wide)\b",
        ],
        "thickness": [
            r"\b\d+(?:\.\d+)?(?:-\s*|\s+)?inch\s+thickness\b",
            r"\b\d+(?:\.\d+)?\s*inch\s+thick\b",
            r"\bthickness\s*[:=]\s*\d+(?:\.\d+)?\s*(?:inch|in|mm)\b",
            r"\b\d+(?:\.\d+)?\s*(?:inch|in|mm)\s+thick\b",
        ],
        "center_hole": [
            r"\b\d+/\d+(?:-\s*|\s+)?inch\s+center\s+hole\b",
            r"\b\d+(?:\.\d+)?(?:-\s*|\s+)?inch\s+center\s+hole\b",
            r"\b\d+/\d+\s*(?:inch|in|\")\s+arbor\s+hole\b",
            r"\b\d+(?:\.\d+)?\s*(?:inch|in|mm)\s+(?:bore|center\s+hole|arbor\s+hole)\b",
            r"\bcenter\s+hole\s*[:=]\s*(?:\d+/\d+|\d+(?:\.\d+)?)\s*(?:inch|in|mm)\b",
        ],
    }
    for pattern in patterns[kind]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            source = match.group(0).strip()
            parsed = parse_measurement_value(source)
            if parsed.get("unit") == "inch" and parsed.get("value") is not None:
                raw_value = re.search(r"\d+/\d+|\d+(?:\.\d+)?", source)
                value_text = raw_value.group(0) if raw_value else str(parsed["value"]).rstrip("0").rstrip(".")
                return f"{value_text}-inch"
            if kind == "diameter":
                value = re.search(r"\d+(?:\.\d+)?", source)
                return f"{value.group(0)}-inch" if value and "inch" in source.lower() else source
            if kind == "thickness":
                value = re.search(r"\d+(?:\.\d+)?", source)
                return f"{value.group(0)}-inch" if value and "inch" in source.lower() else source
            if kind == "center_hole":
                value = re.search(r"\d+/\d+|\d+(?:\.\d+)?", source)
                return f"{value.group(0)}-inch" if value and ("inch" in source.lower() or '"' in source) else source
    return ""


def bool_hint(text: str, *keywords: str) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
