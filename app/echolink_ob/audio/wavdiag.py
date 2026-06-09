from __future__ import annotations

from pathlib import Path
import wave


def write_wav(path: str | Path, pcm: bytes, sample_rate: int = 8000) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def read_wav(path: str | Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1:
            raise ValueError("expected mono WAV")
        if wf.getsampwidth() != 2:
            raise ValueError("expected signed 16-bit WAV")
        sample_rate = wf.getframerate()
        return wf.readframes(wf.getnframes()), sample_rate
