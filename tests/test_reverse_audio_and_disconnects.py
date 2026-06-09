from __future__ import annotations

import socket
import time
from pathlib import Path

from echolink_ob.analog.usrp import UsrpPacket
from echolink_ob.config import load_config
from echolink_ob.dashboard.server import DashboardRenderer
from echolink_ob.echolink.rtp import build_ndata_info, is_rtcp_bye, parse_rtp
from echolink_ob.echolink.service import EchoLinkUdpConferenceService


class DummyGsmCodec:
    def encode_pcm(self, pcm: bytes) -> bytes:
        assert len(pcm) % 320 == 0
        return b"g" * (33 * (len(pcm) // 320))

    def close(self) -> None:
        pass


def _tmp_config(tmp_path: Path, *, client_timeout_seconds: int = 120):
    src = Path("config/config-sample.toml")
    cfg_path = tmp_path / "config.toml"
    text = src.read_text()
    text = text.replace('state_file = "/opt/echolink-ob/data/port-plan.json"', f'state_file = "{tmp_path}/port-plan.json"')
    text = text.replace('ini_path = "/opt/echolink-ob/generated/Analog_Bridge.ini"', f'ini_path = "{tmp_path}/Analog_Bridge.ini"')
    text = text.replace('last_heard_file = "/opt/echolink-ob/data/lastheard.json"', f'last_heard_file = "{tmp_path}/lastheard.json"')
    text = text.replace('control_file = "/opt/echolink-ob/data/dashboard-commands.jsonl"', f'control_file = "{tmp_path}/dashboard-commands.jsonl"')
    text = text.replace('client_timeout_seconds = 120', f'client_timeout_seconds = {client_timeout_seconds}')
    cfg_path.write_text(text)
    return load_config(cfg_path)


def test_usrp_payload_with_keyup_false_is_forwarded_to_echolink(tmp_path: Path):
    cfg = _tmp_config(tmp_path)
    svc = EchoLinkUdpConferenceService(cfg, status_file=tmp_path / "status.json")
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(1.0)
    svc._audio_sock = tx
    svc.encode_codec = DummyGsmCodec()
    try:
        svc._connect_or_update_peer(callsign="K1ABC", audio_addr=rx.getsockname(), name="One")
        payload = b"\x34\x12" * 160
        # Some Analog_Bridge builds put a zero PTT word on decoded PCM payloads.
        # A full 320-byte USRP voice payload is still valid audio and must not be dropped.
        for seq in range(1, 5):
            svc._handle_usrp(UsrpPacket(sequence=seq, keyup=False, payload=payload).to_bytes(), ("127.0.0.1", 23001))
        rtp = parse_rtp(rx.recvfrom(2048)[0])
    finally:
        tx.close()
        rx.close()
        svc.close()

    assert rtp.payload == b"g" * (33 * 4)
    assert rtp.first_byte == 0xC0
    assert svc.stats.usrp_frames_from_dmr == 4
    assert svc.stats.usrp_payload_unkey_frames_from_dmr == 4
    assert svc.stats.gsm_packets_sent == 1


def test_stale_echolink_peer_times_out_and_updates_status(tmp_path: Path):
    cfg = _tmp_config(tmp_path, client_timeout_seconds=1)
    status_file = tmp_path / "status.json"
    svc = EchoLinkUdpConferenceService(cfg, status_file=status_file)
    try:
        peer = svc._connect_or_update_peer(callsign="K1ABC", audio_addr=("127.0.0.1", 5198), name="One")
        peer.last_rx = time.monotonic() - 5
        svc._expire_stale_peers()
        assert "K1ABC" not in svc.conference.stations
        assert svc.stats.peer_timeouts == 1
        assert svc.stats.stations_disconnected == 1
        assert status_file.exists()
    finally:
        svc.close()


def test_binary_framed_goodbye_disconnects_station(tmp_path: Path):
    cfg = _tmp_config(tmp_path)
    svc = EchoLinkUdpConferenceService(cfg, status_file=tmp_path / "status.json")
    try:
        addr = ("127.0.0.1", 5198)
        svc._handle_audio(build_ndata_info("Station K1ABC\n\nOne\n\nHere\n\niPhone"), addr)
        assert "K1ABC" in svc.conference.stations
        svc._handle_audio(b"\x02\x00GOODBYE\x00\x00", addr)
        assert "K1ABC" not in svc.conference.stations
    finally:
        svc.close()


def test_legacy_framed_rtcp_bye_is_detected():
    assert is_rtcp_bye(bytes([0x81, 203, 0, 1, 0, 0, 0, 1])) is True
    assert is_rtcp_bye(bytes([0xC1, 203, 0, 1, 0, 0, 0, 1])) is True


def test_dashboard_live_update_defines_md380_variable(tmp_path: Path):
    cfg = _tmp_config(tmp_path)
    html = DashboardRenderer(cfg).render()
    assert "const md380 = full.md380emu || {};" in html


def test_bye_from_unmatched_port_disconnects_lone_station(tmp_path: Path):
    cfg = _tmp_config(tmp_path)
    svc = EchoLinkUdpConferenceService(cfg, status_file=tmp_path / "status.json")
    try:
        svc._connect_or_update_peer(callsign="K1ABC", audio_addr=("198.51.100.10", 5198), name="One")
        assert "K1ABC" in svc.conference.stations
        # Some EchoLink clients send BYE from a different UDP source endpoint.
        # If only one station is connected, it is safe to remove that station.
        svc._handle_control(b"bye", ("203.0.113.50", 62000))
        assert "K1ABC" not in svc.conference.stations
        assert svc.stats.stations_disconnected == 1
    finally:
        svc.close()


def test_compound_rtcp_rr_plus_bye_disconnects_station(tmp_path: Path):
    cfg = _tmp_config(tmp_path)
    svc = EchoLinkUdpConferenceService(cfg, status_file=tmp_path / "status.json")
    try:
        addr = ("198.51.100.20", 5199)
        svc._connect_or_update_peer(callsign="K1ABC", control_addr=addr, name="One")
        rr = bytes([0x80, 201, 0, 1, 0, 0, 0, 1])
        bye = bytes([0x81, 203, 0, 1, 0, 0, 0, 1])
        assert is_rtcp_bye(rr + bye) is True
        svc._handle_control(rr + bye, addr)
        assert "K1ABC" not in svc.conference.stations
        assert svc.stats.stations_disconnected == 1
    finally:
        svc.close()


def test_binary_control_goodbye_inside_rtcp_like_packet_disconnects(tmp_path: Path):
    cfg = _tmp_config(tmp_path)
    svc = EchoLinkUdpConferenceService(cfg, status_file=tmp_path / "status.json")
    try:
        addr = ("198.51.100.21", 5199)
        svc._connect_or_update_peer(callsign="K1ABC", control_addr=addr, name="One")
        # First byte looks like legacy RTCP, but the disconnect command is text
        # embedded inside a binary-framed iPhone control datagram.
        svc._handle_control(b"\xc0\xc9\x00\x04\x00\x00GOODBYE\x00", addr)
        assert "K1ABC" not in svc.conference.stations
        assert svc.stats.stations_disconnected == 1
    finally:
        svc.close()


def test_control_keepalive_refreshes_stale_timeout_clock(tmp_path: Path):
    cfg = _tmp_config(tmp_path, client_timeout_seconds=1)
    svc = EchoLinkUdpConferenceService(cfg, status_file=tmp_path / "status.json")
    try:
        addr = ("198.51.100.22", 5199)
        peer = svc._connect_or_update_peer(callsign="K1ABC", control_addr=addr, name="One")
        peer.last_rx = time.monotonic() - 5
        # This resembles the iPhone CALLSIGN/RR keepalive seen in captures.
        svc._handle_control(b"\xc0\xc9\x00\x01\x00\x00\x00\x00CALLSIGN\x00K1ABC", addr)
        svc._expire_stale_peers()
        assert "K1ABC" in svc.conference.stations
    finally:
        svc.close()
