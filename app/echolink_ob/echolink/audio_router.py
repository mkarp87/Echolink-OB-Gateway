from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Callable

from .conference import EchoLinkConferenceManager

log = logging.getLogger(__name__)

AudioSink = Callable[[str, bytes], None]
GatewaySink = Callable[[bytes, str, int | None], None]


@dataclass
class RouterStats:
    echolink_frames_in: int = 0
    echolink_frames_repeated: int = 0
    echolink_frames_to_gateway: int = 0
    dmr_frames_in: int = 0
    dmr_frames_to_stations: int = 0
    blocked_speaker_frames: int = 0


@dataclass
class EchoLinkConferenceAudioRouter:
    """Routes conference PCM frames between EchoLink stations and the DMR gateway.

    This class is intentionally protocol-neutral.  A future EchoLink network
    session implementation only needs to call `speaker_pcm()` when one station
    sends audio, and `dmr_pcm()` when decoded DMR audio arrives from
    Analog_Bridge.  The router enforces the conference rule that EchoLink
    speaker audio is always repeated to the other connected EchoLink users.
    """

    conference: EchoLinkConferenceManager
    station_sink: AudioSink | None = None
    gateway_sink: GatewaySink | None = None
    stats: RouterStats = field(default_factory=RouterStats)
    current_gateway_source_id: int | None = None
    current_gateway_source_callsign: str | None = None
    last_audio_at: float | None = None

    def speaker_pcm(self, callsign: str, frame: bytes, *, gateway_allowed: bool = True) -> list[str]:
        self.stats.echolink_frames_in += 1
        delivery = self.conference.route_speaker_audio(callsign, frame, gateway_allowed=gateway_allowed)
        if not delivery.recipients and self.conference.active_speaker != callsign.upper():
            self.stats.blocked_speaker_frames += 1
            return []
        for recipient in delivery.recipients:
            if self.station_sink is not None:
                self.station_sink(recipient, frame)
            self.stats.echolink_frames_repeated += 1
        if delivery.gateway_allowed and self.gateway_sink is not None:
            st = self.conference.stations.get(delivery.speaker)
            source_id = st.resolved_dmr_id if st and st.resolved_dmr_id is not None else None
            self.gateway_sink(frame, delivery.speaker, source_id)
            self.current_gateway_source_id = source_id
            self.current_gateway_source_callsign = delivery.speaker
            self.stats.echolink_frames_to_gateway += 1
        self.last_audio_at = time.monotonic()
        return delivery.recipients

    def dmr_pcm(self, frame: bytes, *, source_id: int | None = None, source_alias: str | None = None) -> list[str]:
        self.stats.dmr_frames_in += 1
        delivery = self.conference.broadcast_from_dmr(frame)
        for recipient in delivery.recipients:
            if self.station_sink is not None:
                self.station_sink(recipient, frame)
            self.stats.dmr_frames_to_stations += 1
        self.current_gateway_source_id = source_id
        self.current_gateway_source_callsign = source_alias
        self.last_audio_at = time.monotonic()
        log.debug("dmr_pcm_broadcast recipients=%s source_id=%s", len(delivery.recipients), source_id)
        return delivery.recipients

    def end_speaker(self, callsign: str) -> None:
        self.conference.end_speaker(callsign)
        self.current_gateway_source_id = None
        self.current_gateway_source_callsign = None

    def snapshot(self) -> dict[str, object]:
        return {
            "connected_stations": len(self.conference.stations),
            "active_speaker": self.conference.active_speaker,
            "current_gateway_source_id": self.current_gateway_source_id,
            "current_gateway_source_callsign": self.current_gateway_source_callsign,
            "stats": self.stats.__dict__.copy(),
        }
