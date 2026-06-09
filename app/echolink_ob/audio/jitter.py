from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .pcm import silence_frame


@dataclass
class JitterBuffer:
    max_frames: int
    frame_ms: int = 20
    _queue: deque[bytes] = field(default_factory=deque)
    underruns: int = 0
    drops: int = 0

    def push(self, frame: bytes) -> None:
        if len(self._queue) >= self.max_frames:
            self._queue.popleft()
            self.drops += 1
        self._queue.append(frame)

    def pop(self) -> bytes:
        if self._queue:
            return self._queue.popleft()
        self.underruns += 1
        return silence_frame(self.frame_ms)

    @property
    def depth(self) -> int:
        return len(self._queue)

    def clear(self) -> None:
        self._queue.clear()
