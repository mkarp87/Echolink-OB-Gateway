from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Iterable


@dataclass(frozen=True)
class RawTlvFrame:
    data: bytes
    source: tuple[str, int]
    received_time: float


@dataclass(frozen=True)
class ParsedTlvFrame:
    tag: int
    length: int
    value: bytes
    raw: bytes
    length_endian: str
    valid_length: bool


def listen_raw_tlv(
    host: str,
    port: int,
    *,
    seconds: float | None = None,
    max_frames: int | None = None,
) -> Iterator[RawTlvFrame]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(0.25)
    start = time.monotonic()
    count = 0
    try:
        while True:
            if seconds is not None and time.monotonic() - start >= seconds:
                return
            if max_frames is not None and count >= max_frames:
                return
            try:
                data, src = sock.recvfrom(4096)
            except socket.timeout:
                continue
            count += 1
            yield RawTlvFrame(data=data, source=(src[0], int(src[1])), received_time=time.time())
    finally:
        sock.close()


def write_tlv_capture(path: str | Path, frames: list[RawTlvFrame]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as f:
        for frame in frames:
            f.write(len(frame.data).to_bytes(2, "big"))
            f.write(frame.data)


def parse_tlv_datagram(data: bytes) -> ParsedTlvFrame:
    """Parse a DVSwitch generic TLV UDP datagram.

    DVSwitch partner traffic is sent as tag/length/value records. In current
    Analog_Bridge DMR tests, voice datagrams are typically 30 bytes total:
    a three-byte TLV header followed by a 27-byte AMBE72 block containing
    three 9-byte DMR AMBE+FEC frames. This parser validates a 16-bit length
    in either endian order and falls back to treating everything after the
    first three bytes as the value when the length does not match exactly.
    """

    if len(data) < 3:
        raise ValueError(f"TLV datagram too short: {len(data)}")
    tag = data[0]
    payload_len = len(data) - 3
    be_len = int.from_bytes(data[1:3], "big")
    if be_len == payload_len:
        return ParsedTlvFrame(
            tag=tag,
            length=be_len,
            value=data[3:],
            raw=data,
            length_endian="big",
            valid_length=True,
        )
    le_len = int.from_bytes(data[1:3], "little")
    if le_len == payload_len:
        return ParsedTlvFrame(
            tag=tag,
            length=le_len,
            value=data[3:],
            raw=data,
            length_endian="little",
            valid_length=True,
        )
    return ParsedTlvFrame(
        tag=tag,
        length=payload_len,
        value=data[3:],
        raw=data,
        length_endian="fallback",
        valid_length=False,
    )


def extract_dmr_ambe_blocks(frames: Iterable[RawTlvFrame | bytes]) -> list[bytes]:
    """Return 27-byte DMR AMBE blocks from raw Analog_Bridge TLV datagrams.

    Each returned block should contain three 9-byte DMR AMBE+FEC frames.
    Non-audio TLV records such as begin/end metadata are ignored.
    """

    blocks: list[bytes] = []
    for item in frames:
        data = item.data if isinstance(item, RawTlvFrame) else item
        if len(data) == 30:
            # Fast path used by Analog_Bridge in DMR mode: 3-byte TLV header + 27 bytes audio.
            blocks.append(data[3:])
            continue
        try:
            parsed = parse_tlv_datagram(data)
        except ValueError:
            continue
        if len(parsed.value) == 27:
            blocks.append(parsed.value)
        elif len(data) > 27 and len(data[-27:]) == 27:
            # Conservative fallback for non-standard header size.
            blocks.append(data[-27:])
    return blocks
