from __future__ import annotations

import socket
import threading

from echolink_ob.analog.usrp import (
    USRP_HEADER_SIZE,
    USRP_VOICE_FRAME_BYTES,
    UsrpPacket,
    pcm_sine_frames,
    send_usrp_pcm_frames,
)


def test_usrp_packet_round_trip_voice():
    payload = b"\x01\x02" * 160
    pkt = UsrpPacket(sequence=123, keyup=True, talkgroup=310001, payload=payload)
    raw = pkt.to_bytes()
    assert len(raw) == USRP_HEADER_SIZE + USRP_VOICE_FRAME_BYTES
    assert raw[12:16] == b"\x00\x00\x00\x01"
    parsed = UsrpPacket.from_bytes(raw)
    assert parsed.sequence == 123
    assert parsed.keyup is True
    assert parsed.talkgroup == 310001
    assert parsed.payload == payload


def test_usrp_unkey_packet_has_header_only():
    pkt = UsrpPacket(sequence=124, keyup=False)
    raw = pkt.to_bytes()
    assert len(raw) == USRP_HEADER_SIZE
    parsed = UsrpPacket.from_bytes(raw)
    assert parsed.keyup is False
    assert parsed.payload == b""


def test_pcm_sine_frames_are_20ms_slin():
    frames = list(pcm_sine_frames(seconds=0.1, frequency_hz=1000.0))
    assert len(frames) == 5
    assert all(len(frame) == USRP_VOICE_FRAME_BYTES for frame in frames)
    assert any(frame != b"\x00" * USRP_VOICE_FRAME_BYTES for frame in frames)


def test_send_usrp_pcm_frames_to_udp_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    received: list[bytes] = []

    def rx():
        while len(received) < 3:
            data, _ = sock.recvfrom(2048)
            received.append(data)

    th = threading.Thread(target=rx, daemon=True)
    th.start()
    frames = iter([b"\x00\x00" * 160, b"\x01\x00" * 160])
    sent = send_usrp_pcm_frames(target_host="127.0.0.1", target_port=port, frames=frames, frame_interval_s=0)
    th.join(timeout=2)
    sock.close()
    assert sent == 2
    assert len(received) == 3
    assert UsrpPacket.from_bytes(received[0]).keyup is True
    assert UsrpPacket.from_bytes(received[1]).keyup is True
    assert UsrpPacket.from_bytes(received[2]).keyup is False
