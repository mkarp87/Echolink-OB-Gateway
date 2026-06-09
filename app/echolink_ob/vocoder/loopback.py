from __future__ import annotations

import hashlib

from .base import VocoderBackend


class LoopbackVocoder(VocoderBackend):
    """Deterministic test backend.

    This is not AMBE. It gives the bridge a stable encode/decode surface for routing,
    buffering, and stream tests without needing AMBE hardware.
    """

    def __init__(self) -> None:
        self._store: dict[bytes, bytes] = {}

    def encode_pcm_to_ambe(self, pcm_frame: bytes) -> bytes:
        digest = hashlib.sha1(pcm_frame).digest()[:9]
        # DMR voice bursts carry three AMBE segments, 9 bytes each, but the surrounding
        # DMR payload is handled by the OpenBridge packet layer. For tests, 9 bytes is enough.
        self._store[digest] = pcm_frame
        return digest

    def decode_ambe_to_pcm(self, ambe_frame: bytes) -> bytes:
        return self._store.get(ambe_frame, b"\x00\x00" * 160)

    def health_check(self) -> bool:
        return True

    def reset_stream(self) -> None:
        pass

    def backend_name(self) -> str:
        return "loopback"
