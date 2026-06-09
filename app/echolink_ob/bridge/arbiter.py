from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time


class GatewayDirection(str, Enum):
    IDLE = "idle"
    ECHOLINK_TO_DMR = "echolink_to_dmr"
    DMR_TO_ECHOLINK = "dmr_to_echolink"


@dataclass
class GatewayArbiter:
    tx_hang_ms: int = 500
    max_transmit_seconds: int = 180
    direction: GatewayDirection = GatewayDirection.IDLE
    stream_owner: str | None = None
    started_at: float | None = None
    hang_until: float = 0.0

    def _now(self) -> float:
        return time.monotonic()

    def can_start(self, direction: GatewayDirection, owner: str) -> bool:
        now = self._now()
        if self.direction == GatewayDirection.IDLE and now >= self.hang_until:
            return True
        return self.direction == direction and self.stream_owner == owner

    def start(self, direction: GatewayDirection, owner: str) -> bool:
        if not self.can_start(direction, owner):
            return False
        if self.direction == GatewayDirection.IDLE:
            self.direction = direction
            self.stream_owner = owner
            self.started_at = self._now()
        return True

    def check_timeout(self) -> bool:
        if self.direction == GatewayDirection.IDLE or self.started_at is None:
            return False
        return (self._now() - self.started_at) > self.max_transmit_seconds

    def end(self, owner: str | None = None) -> None:
        if owner is not None and self.stream_owner != owner:
            return
        self.direction = GatewayDirection.IDLE
        self.stream_owner = None
        self.started_at = None
        self.hang_until = self._now() + (self.tx_hang_ms / 1000.0)
