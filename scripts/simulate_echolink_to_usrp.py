#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from echolink_ob.analog.usrp import USRP_HEADER_SIZE, USRP_VOICE_FRAME_BYTES, UsrpPacket, pcm_sine_frames
from echolink_ob.config import load_config
from echolink_ob.echolink.gsm import GSM_PCM_BYTES, Gsm610Codec
from echolink_ob.echolink.rtp import build_gsm_rtp, build_ndata_info
from echolink_ob.echolink.service import EchoLinkUdpConferenceService


def free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def write_config(tmp: Path, audio_port: int, control_port: int, usrp_rx_port: int, usrp_tx_port: int) -> Path:
    port_plan_path = tmp / "port-plan.json"
    port_plan_path.write_text(json.dumps({"plan": {
        "host": "127.0.0.1",
        "app_usrp_rx_port": usrp_rx_port,
        "app_usrp_tx_port": usrp_tx_port,
        "app_tlv_rx_port": free_udp_port(),
        "app_tlv_tx_port": free_udp_port(),
    }}), encoding="utf-8")
    cfg_path = tmp / "config.toml"
    cfg_path.write_text(f"""
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
radioid_file = "{tmp / 'users.json'}"
radioid_url = ""
radioid_fallback_url = ""
auto_download_radioid = false
manual_overrides_file = "{tmp / 'overrides.toml'}"
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
ini_path = "{tmp / 'Analog_Bridge.ini'}"
log_level = 0
export_metadata = true
transfer_root_dir = "{tmp}"
subscriber_file = "{tmp / 'subscriber_ids.csv'}"
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
banlist_file = "{tmp / 'banlist.txt'}"
allowlist_file = "{tmp / 'allowlist.txt'}"
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
last_heard_file = "{tmp / 'lastheard.json'}"
last_heard_limit = 20
control_file = "{tmp / 'dashboard-commands.jsonl'}"
push_interval_seconds = 1.5

[logging]
level = "DEBUG"
log_file = "{tmp / 'echolink-ob.log'}"
""".strip() + "\n", encoding="utf-8")
    return cfg_path


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="eob-sim-") as tmp_s:
        tmp = Path(tmp_s)
        audio_port = free_udp_port()
        control_port = free_udp_port()
        usrp_rx_port = free_udp_port()
        usrp_tx_port = free_udp_port()
        cfg = load_config(write_config(tmp, audio_port, control_port, usrp_rx_port, usrp_tx_port))

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
        service = EchoLinkUdpConferenceService(cfg, status_file=tmp / "status.json")
        service_thread = threading.Thread(target=service.run, kwargs={"seconds": 1.4}, daemon=True)
        service_thread.start()
        time.sleep(0.15)

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            client.bind(("127.0.0.2", 0))
            client.sendto(build_ndata_info("Station N0CALL\nUnit Test\nLocal\niPhone"), ("127.0.0.1", audio_port))
            time.sleep(0.05)
            pcm_frames = list(pcm_sine_frames(seconds=0.48, frequency_hz=1000.0))
            with Gsm610Codec.create() as codec:
                sequence = 100
                timestamp = 0
                for idx in range(0, len(pcm_frames), 4):
                    block = b"".join(pcm_frames[idx:idx + 4])
                    if len(block) != GSM_PCM_BYTES * 4:
                        raise RuntimeError("unexpected generated PCM length")
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

        voice_times: list[float] = []
        ptt_words: list[int] = []
        unkeys = 0
        for ts, raw in captured:
            if len(raw) < USRP_HEADER_SIZE:
                continue
            magic, _seq, _memory, ptt, _tg, _ptype, _mpxid, _reserved = struct.unpack("!4sIIIIIII", raw[:USRP_HEADER_SIZE])
            if magic != b"USRP":
                continue
            ptt_words.append(ptt)
            pkt = UsrpPacket.from_bytes(raw)
            if pkt.keyup and len(pkt.payload) == USRP_VOICE_FRAME_BYTES:
                voice_times.append(ts)
            if not pkt.keyup and not pkt.payload:
                unkeys += 1
        intervals_ms = [round((b - a) * 1000.0, 2) for a, b in zip(voice_times, voice_times[1:])]
        result = {
            "passed": bool(1 in ptt_words and 0xFFFFFFFF not in ptt_words and len(voice_times) >= 20 and unkeys >= 1),
            "captured_packets": len(captured),
            "ptt_words_seen": sorted(set(ptt_words)),
            "voice_packets": len(voice_times),
            "unkey_packets": unkeys,
            "first_10_voice_intervals_ms": intervals_ms[:10],
            "service_stats": service.stats.__dict__,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
