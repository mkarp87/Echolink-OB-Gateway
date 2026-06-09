from __future__ import annotations

import json
import socket
import struct
import threading
import time
from pathlib import Path

from echolink_ob.analog.usrp import USRP_HEADER_SIZE, USRP_VOICE_FRAME_BYTES, UsrpPacket
from echolink_ob.config import load_config
from echolink_ob.echolink.gsm import GSM_PCM_BYTES, Gsm610Codec
from echolink_ob.echolink.rtp import build_gsm_rtp, build_ndata_info
from echolink_ob.echolink.service import EchoLinkUdpConferenceService
from echolink_ob.analog.usrp import pcm_sine_frames


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _write_sim_config(tmp_path: Path, *, audio_port: int, control_port: int, usrp_rx_port: int, usrp_tx_port: int) -> Path:
    port_plan = {
        "plan": {
            "host": "127.0.0.1",
            "app_usrp_rx_port": usrp_rx_port,
            "app_usrp_tx_port": usrp_tx_port,
            "app_tlv_rx_port": _free_udp_port(),
            "app_tlv_tx_port": _free_udp_port(),
        }
    }
    port_plan_path = tmp_path / "port-plan.json"
    port_plan_path.write_text(json.dumps(port_plan), encoding="utf-8")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"""
[bridge]
max_transmit_seconds = 180
tx_hang_ms = 250
enabled = true

[echolink]
callsign = "TEST-L"
password = "redacted"
max_connected_stations = 5
bind_host = "127.0.0.1"
audio_port = {audio_port}
control_port = {control_port}
directory_host = "127.0.0.1"
directory_port = 5200
location = "test"
status_text = "test"
register_with_directory = false
directory_refresh_seconds = 480

[openbridge]
host = "127.0.0.1"
port = 54096
passphrase = "redacted"
network_id = 3100000
fixed_tgid = 310001
slot = 1
call_type = "group"
local_bind_host = "127.0.0.1"
local_bind_port = 0
both_slots = false

[identity]
fallback_source_id = 1234567
strip_suffixes = ["-L", "-R", "-M"]
radioid_file = "{tmp_path / 'users.json'}"
radioid_url = ""
radioid_fallback_url = ""
auto_download_radioid = false
manual_overrides_file = "{tmp_path / 'overrides.toml'}"
positive_cache_days = 30
negative_cache_hours = 24

[vocoder]
preferred = "ambeserver"
fallback = "md380emu"
allow_fallback = true
switch_back_when_idle = true
allow_mid_stream_switch = false

[ambeserver]
host = "127.0.0.1"
port = 2460
timeout_ms = 500

[md380emu]
host = "127.0.0.1"
port = 2990
timeout_ms = 500
enabled = true

[audio]
sample_rate = 8000
channels = 1
format = "s16le"
frame_ms = 20
jitter_buffer_ms = 120
silence_gap_ms = 250

[port_manager]
enabled = true
host = "127.0.0.1"
range_start = 23000
range_end = 23199
reserved_ports = []
state_file = "{port_plan_path}"
reuse_existing_allocation = true

[analog_bridge]
enabled = true
auto_manage_ports = true
ini_path = "{tmp_path / 'Analog_Bridge.ini'}"
log_level = 0
export_metadata = true
transfer_root_dir = "{tmp_path}"
subscriber_file = "{tmp_path / 'subscriber_ids.csv'}"
decoder_fallback = true
use_emulator = false
emulator_address = "127.0.0.1:2990"
pcm_port = 2222
min_tx_time_ms = 2500
repeater_id = 3100000
tx_ts = 1
color_code = 1
usrp_audio = "AUDIO_USE_AGC"
usrp_gain = 1.10
usrp_agc = "-20,10,100"
tlv_audio = "AUDIO_BPF"
tlv_gain = 1.0

[access]
allow_echolink_callsigns = ["*"]
deny_echolink_callsigns = []
banlist_file = "{tmp_path / 'banlist.txt'}"
allowlist_file = "{tmp_path / 'allowlist.txt'}"
allow_duplicate_callsigns = false
kick_idle_stations = false
max_idle_minutes = 0
allow_echolink_users = true
allow_echolink_links = true
allow_echolink_repeaters = true
allow_echolink_conferences = false

[dashboard]
enabled = false
listen_host = "127.0.0.1"
listen_port = 8082
require_auth = false
last_heard_file = "{tmp_path / 'lastheard.json'}"
last_heard_limit = 20
control_file = "{tmp_path / 'dashboard-commands.jsonl'}"
push_interval_seconds = 1.5

[logging]
level = "DEBUG"
log_file = "{tmp_path / 'echolink-ob.log'}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return cfg_path


def test_echolink_rtp_to_usrp_socket_simulation_sends_ptt_one_and_paced_audio(tmp_path):
    audio_port = _free_udp_port()
    control_port = _free_udp_port()
    usrp_rx_port = _free_udp_port()
    usrp_tx_port = _free_udp_port()
    cfg_path = _write_sim_config(
        tmp_path,
        audio_port=audio_port,
        control_port=control_port,
        usrp_rx_port=usrp_rx_port,
        usrp_tx_port=usrp_tx_port,
    )
    cfg = load_config(cfg_path)

    capture_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    capture_sock.bind(("127.0.0.1", usrp_tx_port))
    capture_sock.settimeout(1.5)
    captured: list[tuple[float, bytes]] = []
    stop_capture = threading.Event()

    def capture_loop() -> None:
        while not stop_capture.is_set():
            try:
                data, _ = capture_sock.recvfrom(2048)
            except socket.timeout:
                break
            captured.append((time.monotonic(), data))

    cap_thread = threading.Thread(target=capture_loop, daemon=True)
    cap_thread.start()

    service = EchoLinkUdpConferenceService(cfg, status_file=tmp_path / "status.json")
    service_thread = threading.Thread(target=service.run, kwargs={"seconds": 1.4}, daemon=True)
    service_thread.start()
    time.sleep(0.15)

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.bind(("127.0.0.2", 0))
        client.sendto(build_ndata_info("Station N0CALL\nUnit Test\nLocal\niPhone"), ("127.0.0.1", audio_port))
        time.sleep(0.05)

        pcm_frames = list(pcm_sine_frames(seconds=0.48, frequency_hz=1000.0))
        assert len(pcm_frames) == 24
        with Gsm610Codec.create() as codec:
            sequence = 100
            timestamp = 0
            # Simulate a normal EchoLink client cadence: four 20 ms GSM frames
            # per RTP packet, one RTP packet every 80 ms.
            for idx in range(0, len(pcm_frames), 4):
                block = b"".join(pcm_frames[idx : idx + 4])
                assert len(block) == GSM_PCM_BYTES * 4
                payload = build_gsm_rtp(
                    codec.encode_pcm(block),
                    sequence=sequence,
                    timestamp=timestamp,
                    ssrc=0x12345678,
                    marker=(idx == 0),
                )
                client.sendto(payload, ("127.0.0.1", audio_port))
                sequence += 1
                timestamp += 640
                time.sleep(0.080)
    finally:
        client.close()

    service_thread.join(timeout=3.0)
    service.request_stop()
    stop_capture.set()
    cap_thread.join(timeout=2.0)
    capture_sock.close()

    assert not service_thread.is_alive()
    assert len(captured) >= 20

    raw_ptt_words = []
    voice_packets: list[tuple[float, UsrpPacket]] = []
    unkey_packets: list[UsrpPacket] = []
    for ts, raw in captured:
        magic, _seq, _memory, ptt, _tg, _ptype, _mpxid, _reserved = struct.unpack("!4sIIIIIII", raw[:USRP_HEADER_SIZE])
        assert magic == b"USRP"
        raw_ptt_words.append(ptt)
        pkt = UsrpPacket.from_bytes(raw)
        if pkt.keyup and len(pkt.payload) == USRP_VOICE_FRAME_BYTES:
            voice_packets.append((ts, pkt))
        if not pkt.keyup and not pkt.payload:
            unkey_packets.append(pkt)

    assert 1 in raw_ptt_words
    assert 0xFFFFFFFF not in raw_ptt_words
    assert len(voice_packets) >= 20
    assert unkey_packets
    assert service.stats.rtp_marker_packets_received == 1
    assert service.stats.rtp_gsm_frames_received == 24
    assert service.stats.pcm_frames_to_usrp >= 20

    intervals = [b[0] - a[0] for a, b in zip(voice_packets, voice_packets[1:])]
    # Allow normal scheduler jitter in CI, but reject the original bursty path.
    assert max(intervals[:8]) < 0.055
    assert sum(intervals[:10]) > 0.14
