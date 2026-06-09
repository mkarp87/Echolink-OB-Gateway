from __future__ import annotations

from dataclasses import dataclass, field
import random

from echolink_ob.bridge.arbiter import GatewayArbiter, GatewayDirection
from echolink_ob.echolink.conference import EchoLinkConferenceManager, AudioDelivery
from echolink_ob.openbridge.dmrd import CANNED_PAYLOAD_A, DMRDPacket
from echolink_ob.vocoder.base import VocoderBackend


@dataclass
class SimulatedBridgeController:
    """Offline bridge controller used by tests.

    It verifies conference routing, gateway arbitration, vocoder locking surface, and
    OpenBridge packet construction without needing a real EchoLink or AMBE endpoint.
    """

    conference: EchoLinkConferenceManager
    arbiter: GatewayArbiter
    vocoder: VocoderBackend
    network_id: int
    fixed_tgid: int
    slot: int = 1
    sequence: int = 0
    stream_id: int = field(default_factory=lambda: random.randint(1, 0xFFFFFFFF))

    def echolink_audio_frame(self, callsign: str, pcm_frame: bytes, dmr_source_id: int) -> tuple[AudioDelivery, DMRDPacket | None]:
        gateway_allowed = self.arbiter.start(GatewayDirection.ECHOLINK_TO_DMR, callsign.upper())
        delivery = self.conference.route_speaker_audio(callsign, pcm_frame, gateway_allowed)
        if not gateway_allowed:
            return delivery, None
        # Test vocoder surface. The DMR payload assembly remains a real AMBE/DMR step for production.
        _ambe = self.vocoder.encode_pcm_to_ambe(pcm_frame)
        pkt = DMRDPacket(
            sequence=self.sequence,
            rf_source_id=dmr_source_id,
            destination_id=self.fixed_tgid,
            network_id=self.network_id,
            slot=self.slot,
            call_type="group",
            frame_type=1,
            dtype_vseq=self.sequence % 6,
            stream_id=self.stream_id,
            payload=CANNED_PAYLOAD_A,
        )
        self.sequence = (self.sequence + 1) % 256
        return delivery, pkt

    def dmr_audio_frame(self, pcm_frame: bytes, source_id: int) -> AudioDelivery | None:
        if not self.arbiter.start(GatewayDirection.DMR_TO_ECHOLINK, str(source_id)):
            return None
        return self.conference.broadcast_from_dmr(pcm_frame)

    def end_echolink_stream(self, callsign: str) -> None:
        self.conference.end_speaker(callsign)
        self.arbiter.end(callsign.upper())
        self.vocoder.reset_stream()

    def end_dmr_stream(self, source_id: int) -> None:
        self.arbiter.end(str(source_id))
        self.vocoder.reset_stream()
