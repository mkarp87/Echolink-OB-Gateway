from __future__ import annotations

import socket
from pathlib import Path

from echolink_ob.config import load_config
from echolink_ob.echolink.rtp import parse_rtp
from echolink_ob.echolink.service import EchoLinkPeer, EchoLinkUdpConferenceService


class DummyGsmCodec:
    def encode_pcm(self, pcm: bytes) -> bytes:
        assert len(pcm) % 320 == 0
        return b"x" * (33 * (len(pcm) // 320))

    def close(self) -> None:
        pass


def test_service_outbound_rtp_uses_legacy_echolink_packetization(tmp_path: Path):
    cfg = load_config("config/config-sample.toml")
    svc = EchoLinkUdpConferenceService(cfg, status_file=tmp_path / "status.json")
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(1.0)
    svc._audio_sock = tx
    peer = EchoLinkPeer(callsign="K1ABC-L", audio_addr=rx.getsockname())
    svc.peers_by_call[peer.callsign] = peer
    svc.encode_codec = DummyGsmCodec()
    try:
        frame = b"\x00\x00" * 160
        for _ in range(4):
            svc._send_pcm_to_station(peer.callsign, frame)
        first = parse_rtp(rx.recvfrom(2048)[0])
        for _ in range(4):
            svc._send_pcm_to_station(peer.callsign, frame)
        second = parse_rtp(rx.recvfrom(2048)[0])
    finally:
        tx.close()
        rx.close()
        svc.close()

    assert first.sequence == 0
    assert first.timestamp == 0
    assert first.ssrc == 0
    assert first.first_byte == 0xC0
    assert len(first.payload) == 33 * 4
    assert second.sequence == 1
    assert second.timestamp == 0
    assert second.ssrc == 0
    assert second.first_byte == 0xC0
