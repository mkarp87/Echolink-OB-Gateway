from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Literal

StationType = Literal["user", "link", "repeater", "conference", "unknown"]


@dataclass
class EchoLinkStation:
    callsign: str
    normalized_callsign: str
    station_type: StationType = "unknown"
    node_number: int | None = None
    ip_address: str | None = None
    name: str = ""
    location: str = ""
    client: str = ""
    resolved_dmr_id: int | None = None
    fallback_source_id_in_use: bool = False
    connected_at: float = field(default_factory=time.monotonic)
    last_heard_at: float | None = None
    is_speaking: bool = False
    muted: bool = False

    @property
    def connected_seconds(self) -> float:
        return time.monotonic() - self.connected_at

    @property
    def idle_seconds(self) -> float | None:
        if self.last_heard_at is None:
            return None
        return time.monotonic() - self.last_heard_at
