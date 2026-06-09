from __future__ import annotations


def normalize_echolink_callsign(callsign: str, suffixes: tuple[str, ...] = ("-L", "-R", "-M")) -> str:
    value = callsign.strip().upper()
    for suffix in suffixes:
        if value.endswith(suffix.upper()):
            return value[: -len(suffix)]
    return value
