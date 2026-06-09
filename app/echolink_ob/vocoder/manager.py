from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .base import VocoderBackend, VocoderUnavailable


@dataclass
class VocoderManager:
    primary: VocoderBackend
    fallback: Optional[VocoderBackend] = None
    allow_fallback: bool = True
    allow_mid_stream_switch: bool = False
    _active: Optional[VocoderBackend] = None
    _stream_locked: bool = False

    def select_idle_backend(self) -> VocoderBackend:
        if self.primary.health_check():
            self._active = self.primary
            return self.primary
        if self.allow_fallback and self.fallback and self.fallback.health_check():
            self._active = self.fallback
            return self.fallback
        raise VocoderUnavailable("no healthy vocoder backend")

    def begin_stream(self) -> VocoderBackend:
        if self._stream_locked and self._active:
            return self._active
        backend = self._active if self._active and self._active.health_check() else self.select_idle_backend()
        backend.reset_stream()
        self._active = backend
        self._stream_locked = True
        return backend

    def end_stream(self) -> None:
        if self._active:
            self._active.reset_stream()
        self._stream_locked = False

    @property
    def active_name(self) -> str | None:
        return self._active.backend_name() if self._active else None
