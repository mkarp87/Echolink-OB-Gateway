from __future__ import annotations

from dataclasses import dataclass

from .callsign import normalize_echolink_callsign
from .radioid import RadioIdIndex


@dataclass
class ResolveResult:
    normalized_callsign: str
    dmr_id: int
    fallback_used: bool


@dataclass
class IdentityResolver:
    radioid: RadioIdIndex
    fallback_source_id: int
    suffixes: tuple[str, ...] = ("-L", "-R", "-M")
    overrides: dict[str, int] | None = None

    def resolve_echolink(self, callsign: str) -> ResolveResult:
        normalized = normalize_echolink_callsign(callsign, self.suffixes)
        if self.overrides and normalized in self.overrides:
            return ResolveResult(normalized, int(self.overrides[normalized]), False)
        found = self.radioid.lookup_callsign(normalized)
        if found is not None:
            return ResolveResult(normalized, found, False)
        return ResolveResult(normalized, self.fallback_source_id, True)
