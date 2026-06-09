from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterable

from .station import StationType


def normalize_pattern(value: str) -> str:
    return value.strip().upper()


def load_pattern_file(path: str | Path | None) -> list[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    patterns: list[str] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        patterns.append(normalize_pattern(item))
    return patterns


def detect_station_type(callsign: str) -> StationType:
    cs = callsign.strip().upper()
    if cs.endswith("-L"):
        return "link"
    if cs.endswith("-R"):
        return "repeater"
    if cs.endswith("-M"):
        return "user"
    # EchoLink conferences are commonly displayed with leading or trailing asterisks.
    if cs.startswith("*") or cs.endswith("*"):
        return "conference"
    return "user"


def match_any(callsign: str, patterns: Iterable[str]) -> bool:
    cs = callsign.strip().upper()
    for pattern in patterns:
        p = normalize_pattern(pattern)
        if p == "*" or fnmatchcase(cs, p):
            return True
    return False


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = "allowed"
    station_type: StationType = "unknown"


@dataclass
class EchoLinkAccessRules:
    allow_patterns: list[str] = field(default_factory=lambda: ["*"])
    deny_patterns: list[str] = field(default_factory=list)
    ban_patterns: list[str] = field(default_factory=list)
    allow_users: bool = True
    allow_links: bool = True
    allow_repeaters: bool = True
    allow_conferences: bool = False

    @classmethod
    def from_files(
        cls,
        *,
        allowlist_file: str | Path | None = None,
        banlist_file: str | Path | None = None,
        deny_patterns: Iterable[str] | None = None,
        allow_users: bool = True,
        allow_links: bool = True,
        allow_repeaters: bool = True,
        allow_conferences: bool = False,
    ) -> "EchoLinkAccessRules":
        allow_patterns = load_pattern_file(allowlist_file) or ["*"]
        return cls(
            allow_patterns=allow_patterns,
            deny_patterns=[normalize_pattern(x) for x in (deny_patterns or [])],
            ban_patterns=load_pattern_file(banlist_file),
            allow_users=allow_users,
            allow_links=allow_links,
            allow_repeaters=allow_repeaters,
            allow_conferences=allow_conferences,
        )

    def check(self, callsign: str, station_type: StationType | None = None) -> AccessDecision:
        stype = station_type or detect_station_type(callsign)
        if match_any(callsign, self.ban_patterns):
            return AccessDecision(False, "banned", stype)
        if match_any(callsign, self.deny_patterns):
            return AccessDecision(False, "denied", stype)
        if not match_any(callsign, self.allow_patterns):
            return AccessDecision(False, "not_allowlisted", stype)
        type_allowed = {
            "user": self.allow_users,
            "link": self.allow_links,
            "repeater": self.allow_repeaters,
            "conference": self.allow_conferences,
            "unknown": self.allow_users,
        }.get(stype, False)
        if not type_allowed:
            return AccessDecision(False, f"station_type_blocked:{stype}", stype)
        return AccessDecision(True, "allowed", stype)
