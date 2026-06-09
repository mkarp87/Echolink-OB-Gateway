from __future__ import annotations

import ctypes
import ctypes.util
import struct
from dataclasses import dataclass

GSM_PCM_SAMPLES = 160
GSM_PCM_BYTES = GSM_PCM_SAMPLES * 2
GSM_FRAME_BYTES = 33


class GsmUnavailable(RuntimeError):
    pass


@dataclass
class Gsm610Codec:
    """Small ctypes wrapper around libgsm 06.10.

    EchoLink's normal narrowband audio payload is RTP payload type 3,
    containing four GSM 06.10 frames per packet.  Each GSM frame represents
    160 samples at 8 kHz and is 33 bytes long.
    """

    _lib: object
    _state: ctypes.c_void_p

    @classmethod
    def create(cls) -> "Gsm610Codec":
        libname = ctypes.util.find_library("gsm")
        if not libname:
            raise GsmUnavailable("libgsm was not found; install libgsm1/libgsm1-dev")
        lib = ctypes.CDLL(libname)
        lib.gsm_create.restype = ctypes.c_void_p
        lib.gsm_destroy.argtypes = [ctypes.c_void_p]
        lib.gsm_encode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_ubyte)]
        lib.gsm_encode.restype = ctypes.c_int
        lib.gsm_decode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_short)]
        lib.gsm_decode.restype = ctypes.c_int
        state = lib.gsm_create()
        if not state:
            raise GsmUnavailable("gsm_create failed")
        return cls(_lib=lib, _state=ctypes.c_void_p(state))

    def close(self) -> None:
        if self._state:
            self._lib.gsm_destroy(self._state)
            self._state = ctypes.c_void_p()

    def __enter__(self) -> "Gsm610Codec":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def encode_frame(self, pcm_s16le: bytes) -> bytes:
        if len(pcm_s16le) != GSM_PCM_BYTES:
            raise ValueError(f"GSM input must be {GSM_PCM_BYTES} bytes/160 samples")
        samples = (ctypes.c_short * GSM_PCM_SAMPLES)(*struct.unpack("<160h", pcm_s16le))
        out = (ctypes.c_ubyte * GSM_FRAME_BYTES)()
        rc = self._lib.gsm_encode(self._state, samples, out)
        if rc not in (0, None):
            raise RuntimeError(f"gsm_encode failed rc={rc}")
        return bytes(out)

    def decode_frame(self, gsm_frame: bytes) -> bytes:
        if len(gsm_frame) != GSM_FRAME_BYTES:
            raise ValueError(f"GSM frame must be {GSM_FRAME_BYTES} bytes")
        inp = (ctypes.c_ubyte * GSM_FRAME_BYTES).from_buffer_copy(gsm_frame)
        out = (ctypes.c_short * GSM_PCM_SAMPLES)()
        rc = self._lib.gsm_decode(self._state, inp, out)
        if rc != 0:
            raise RuntimeError(f"gsm_decode failed rc={rc}")
        return struct.pack("<160h", *out)

    def encode_pcm(self, pcm_s16le: bytes) -> bytes:
        if len(pcm_s16le) % GSM_PCM_BYTES:
            raise ValueError("PCM length must be a multiple of 320 bytes")
        return b"".join(self.encode_frame(pcm_s16le[i:i+GSM_PCM_BYTES]) for i in range(0, len(pcm_s16le), GSM_PCM_BYTES))

    def decode_gsm(self, gsm_bytes: bytes) -> bytes:
        if len(gsm_bytes) % GSM_FRAME_BYTES:
            raise ValueError("GSM payload length must be a multiple of 33 bytes")
        return b"".join(self.decode_frame(gsm_bytes[i:i+GSM_FRAME_BYTES]) for i in range(0, len(gsm_bytes), GSM_FRAME_BYTES))


def libgsm_available() -> bool:
    try:
        codec = Gsm610Codec.create()
        codec.close()
        return True
    except Exception:
        return False
