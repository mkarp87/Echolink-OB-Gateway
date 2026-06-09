from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time

from .station import EchoLinkStation

log = logging.getLogger(__name__)


@dataclass
class AudioDelivery:
    speaker: str
    recipients: list[str]
    frame: bytes
    gateway_allowed: bool


@dataclass
class EchoLinkConferenceManager:
    max_stations: int = 50
    allow_duplicate_callsigns: bool = False
    stations: dict[str, EchoLinkStation] = field(default_factory=dict)
    active_speaker: str | None = None
    last_blocked_log_at: float = 0.0

    def add_station(self, station: EchoLinkStation) -> None:
        key = station.callsign.upper()
        if len(self.stations) >= self.max_stations:
            raise RuntimeError("conference is full")
        if not self.allow_duplicate_callsigns and key in self.stations:
            raise RuntimeError(f"duplicate callsign connected: {station.callsign}")
        self.stations[key] = station
        log.info("station_connected callsign=%s total=%s", station.callsign, len(self.stations))

    def remove_station(self, callsign: str) -> None:
        key = callsign.upper()
        if self.active_speaker == key:
            self.end_speaker(key)
        if key in self.stations:
            del self.stations[key]
            log.info("station_disconnected callsign=%s total=%s", callsign, len(self.stations))

    def begin_speaker(self, callsign: str) -> bool:
        key = callsign.upper()
        if key not in self.stations:
            raise KeyError(callsign)
        if self.active_speaker is None:
            self.active_speaker = key
            self.stations[key].is_speaking = True
            self.stations[key].last_heard_at = time.monotonic()
            log.info("echolink_speaker_selected callsign=%s", callsign)
            return True
        return self.active_speaker == key

    def end_speaker(self, callsign: str) -> None:
        key = callsign.upper()
        if self.active_speaker == key:
            self.stations[key].is_speaking = False
            self.active_speaker = None
            log.info("echolink_speaker_ended callsign=%s", callsign)

    def route_speaker_audio(self, callsign: str, frame: bytes, gateway_allowed: bool) -> AudioDelivery:
        key = callsign.upper()
        if not self.begin_speaker(key):
            now = time.monotonic()
            # A blocked speaker can generate dozens of GSM frames per second.
            # Rate-limit this log so a stuck active speaker does not flood logs.
            if now - self.last_blocked_log_at >= 1.0:
                self.last_blocked_log_at = now
                log.info("echolink_speaker_blocked callsign=%s active=%s", callsign, self.active_speaker)
            return AudioDelivery(speaker=key, recipients=[], frame=frame, gateway_allowed=False)
        self.stations[key].last_heard_at = time.monotonic()
        recipients = [cs for cs in self.stations if cs != key and not self.stations[cs].muted]
        log.debug("echolink_audio_repeated speaker=%s recipients=%s", key, len(recipients))
        return AudioDelivery(speaker=key, recipients=recipients, frame=frame, gateway_allowed=gateway_allowed)

    def release_inactive_speaker(self, timeout_s: float) -> bool:
        """Release the active EchoLink speaker after an audio-idle timeout.

        EchoLink RTP does not always send a clean explicit unkey event.  The
        active speaker is therefore released when no audio frame has arrived
        from that station for the configured hang/idle period.
        """
        if self.active_speaker is None:
            return False
        st = self.stations.get(self.active_speaker)
        if st is None:
            self.active_speaker = None
            return True
        last = st.last_heard_at
        if last is None:
            return False
        if time.monotonic() - last >= timeout_s:
            self.end_speaker(st.callsign)
            log.info("echolink_speaker_released_idle callsign=%s idle_ms=%s", st.callsign, int(timeout_s * 1000))
            return True
        return False

    def broadcast_from_dmr(self, frame: bytes) -> AudioDelivery:
        recipients = [cs for cs, st in self.stations.items() if not st.muted]
        return AudioDelivery(speaker="DMR", recipients=recipients, frame=frame, gateway_allowed=False)

    def roster_text(self) -> str:
        lines = ["Connected stations:"]
        for cs in sorted(self.stations):
            marker = "->" if cs == self.active_speaker else "  "
            st = self.stations[cs]
            dmr = st.resolved_dmr_id if st.resolved_dmr_id is not None else "fallback"
            lines.append(f"{marker} {st.callsign:<12} {st.station_type:<10} DMR={dmr}")
        return "\n".join(lines)
