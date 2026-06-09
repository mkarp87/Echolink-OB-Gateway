from __future__ import annotations

import socket

from .base import VocoderUnavailable


class AMBEServerBackend:
    """AMBEServer placeholder adapter.

    Health checks are conservative. Actual AMBE encode/decode packet semantics vary by
    server implementation and must be validated against the target endpoint before enabling
    production audio.
    """

    def __init__(self, host: str, port: int, timeout_ms: int = 500) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout_ms / 1000.0

    def health_check(self) -> bool:
        # UDP has no connection handshake. This verifies local socket creation and address
        # resolution only; live media validation must use the external integration test.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect((self.host, self.port))
            return True
        except OSError:
            return False

    def encode_pcm_to_ambe(self, pcm_frame: bytes) -> bytes:
        raise VocoderUnavailable("AMBEServer encode protocol is not implemented in this direct Python backend")

    def decode_ambe_to_pcm(self, ambe_frame: bytes) -> bytes:
        raise VocoderUnavailable("AMBEServer decode protocol is not implemented in this direct Python backend")

    def reset_stream(self) -> None:
        pass

    def backend_name(self) -> str:
        return "ambeserver"
