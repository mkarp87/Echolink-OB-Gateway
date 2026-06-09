from __future__ import annotations

import math
import socket
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

USRP_MAGIC = b"USRP"
USRP_HEADER_SIZE = 32
USRP_VOICE_TYPE = 0
USRP_DTMF_TYPE = 1
USRP_TEXT_TYPE = 2
USRP_VOICE_FRAME_BYTES = 320
USRP_SAMPLE_RATE = 8000
USRP_SAMPLES_PER_FRAME = 160
USRP_KEYUP_WORD = 1


@dataclass(frozen=True)
class UsrpPacket:
    sequence: int
    keyup: bool
    payload: bytes = b""
    memory: int = 0
    talkgroup: int = 0
    packet_type: int = USRP_VOICE_TYPE
    mpxid: int = 0
    reserved: int = 0

    def to_bytes(self) -> bytes:
        header = struct.pack(
            "!4sIIIIIII",
            USRP_MAGIC,
            self.sequence & 0xFFFFFFFF,
            self.memory & 0xFFFFFFFF,
            USRP_KEYUP_WORD if self.keyup else 0,
            self.talkgroup & 0xFFFFFFFF,
            self.packet_type & 0xFFFFFFFF,
            self.mpxid & 0xFFFFFFFF,
            self.reserved & 0xFFFFFFFF,
        )
        return header + self.payload

    @classmethod
    def from_bytes(cls, data: bytes) -> "UsrpPacket":
        if len(data) < USRP_HEADER_SIZE:
            raise ValueError(f"USRP packet too short: {len(data)}")
        magic, seq, memory, keyup, talkgroup, packet_type, mpxid, reserved = struct.unpack(
            "!4sIIIIIII", data[:USRP_HEADER_SIZE]
        )
        if magic != USRP_MAGIC:
            raise ValueError("invalid USRP magic")
        return cls(
            sequence=seq,
            keyup=bool(keyup),
            payload=data[USRP_HEADER_SIZE:],
            memory=memory,
            talkgroup=talkgroup,
            packet_type=packet_type,
            mpxid=mpxid,
            reserved=reserved,
        )


def pcm_sine_frames(
    *,
    seconds: float,
    frequency_hz: float = 1000.0,
    amplitude: int = 6000,
    sample_rate: int = USRP_SAMPLE_RATE,
    frame_ms: int = 20,
) -> Iterator[bytes]:
    if seconds <= 0:
        return
    samples_per_frame = int(sample_rate * frame_ms / 1000)
    total_frames = max(1, int(round(seconds * 1000 / frame_ms)))
    sample_index = 0
    for _ in range(total_frames):
        out = bytearray()
        for _sample in range(samples_per_frame):
            value = int(amplitude * math.sin(2.0 * math.pi * frequency_hz * sample_index / sample_rate))
            out += struct.pack("<h", value)
            sample_index += 1
        yield bytes(out)


def send_usrp_pcm_frames(
    *,
    target_host: str,
    target_port: int,
    frames: Iterator[bytes],
    talkgroup: int = 0,
    start_sequence: int = 1,
    frame_interval_s: float = 0.020,
    send_unkey: bool = True,
) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    count = 0
    seq = start_sequence
    try:
        for frame in frames:
            if len(frame) != USRP_VOICE_FRAME_BYTES:
                raise ValueError(f"USRP voice frame must be {USRP_VOICE_FRAME_BYTES} bytes, got {len(frame)}")
            pkt = UsrpPacket(sequence=seq, keyup=True, payload=frame, talkgroup=talkgroup)
            sock.sendto(pkt.to_bytes(), (target_host, target_port))
            count += 1
            seq = (seq + 1) & 0xFFFFFFFF
            time.sleep(frame_interval_s)
        if send_unkey:
            pkt = UsrpPacket(sequence=seq, keyup=False, payload=b"", talkgroup=talkgroup)
            sock.sendto(pkt.to_bytes(), (target_host, target_port))
    finally:
        sock.close()
    return count


def write_usrp_wav_like_pcm(path: str | Path, frames: list[bytes]) -> None:
    # Raw PCM helper for diagnostics; intentionally no WAV header.
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"".join(frames))
