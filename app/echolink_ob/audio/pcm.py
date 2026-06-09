from __future__ import annotations

import math
import struct
from typing import Iterable

SAMPLE_RATE = 8000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
FRAME_MS = 20
SAMPLES_PER_20MS = SAMPLE_RATE * FRAME_MS // 1000
BYTES_PER_20MS = SAMPLES_PER_20MS * SAMPLE_WIDTH_BYTES


def silence_frame(frame_ms: int = FRAME_MS, sample_rate: int = SAMPLE_RATE) -> bytes:
    samples = sample_rate * frame_ms // 1000
    return b"\x00\x00" * samples


def split_frames(pcm: bytes, frame_ms: int = FRAME_MS, sample_rate: int = SAMPLE_RATE) -> list[bytes]:
    frame_bytes = sample_rate * frame_ms // 1000 * SAMPLE_WIDTH_BYTES
    if frame_bytes <= 0:
        raise ValueError("invalid frame size")
    frames: list[bytes] = []
    for start in range(0, len(pcm), frame_bytes):
        chunk = pcm[start : start + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk += b"\x00" * (frame_bytes - len(chunk))
        frames.append(chunk)
    return frames


def generate_sine_pcm(
    seconds: float = 1.0,
    frequency_hz: float = 1000.0,
    sample_rate: int = SAMPLE_RATE,
    amplitude: int = 8000,
) -> bytes:
    total = int(seconds * sample_rate)
    out = bytearray()
    for n in range(total):
        sample = int(amplitude * math.sin(2.0 * math.pi * frequency_hz * n / sample_rate))
        out.extend(struct.pack("<h", sample))
    return bytes(out)


def rms(pcm: bytes) -> float:
    if len(pcm) % 2:
        raise ValueError("s16le PCM length must be even")
    if not pcm:
        return 0.0
    count = len(pcm) // 2
    total = 0
    for (sample,) in struct.iter_unpack("<h", pcm):
        total += sample * sample
    return math.sqrt(total / count)


def join_frames(frames: Iterable[bytes]) -> bytes:
    return b"".join(frames)
