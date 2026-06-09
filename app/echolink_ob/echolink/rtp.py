from __future__ import annotations

import random
import struct
from dataclasses import dataclass

# EchoLink audio rides over RTP/GSM.  Earlier builds used 0xC0 as a hard-coded
# first RTP byte.  That is seen in some EchoLink traffic, but standard RTP v2
# packets commonly use 0x80, and clients may set the RTP marker bit in the
# second byte at the start of a talkspurt.  The parser accepts both observed
# first-byte variants and always masks the marker bit out of the payload type.
RTP_VERSION_V2 = 0x80
RTP_VERSION_LEGACY_MARKER = 0xC0
RTP_VERSION_MARKER = RTP_VERSION_LEGACY_MARKER  # backward-compatible export
RTP_PAYLOAD_GSM = 0x03
RTP_PAYLOAD_SPEEX = 0x96
RTP_HEADER_LEN = 12
RTCP_SR = 200
RTCP_RR = 201
RTCP_SDES = 202
RTCP_BYE = 203
SDES_CNAME = 1
SDES_NAME = 2
SDES_PRIV = 8


def is_probable_rtp_packet(data: bytes) -> bool:
    """Return True when a datagram looks like RTP rather than EchoLink text."""
    if len(data) < RTP_HEADER_LEN:
        return False
    first = data[0]
    if first in (RTP_VERSION_V2, RTP_VERSION_LEGACY_MARKER):
        return True
    # Standard RTP v2 has the high two version bits set to binary 10.  The
    # remaining first-byte bits are padding/extension/CSRC count.
    return (first >> 6) == 2


@dataclass(frozen=True)
class RtpPacket:
    sequence: int
    timestamp: int
    ssrc: int
    payload_type: int
    payload: bytes
    marker: bool = False
    first_byte: int = RTP_VERSION_V2

    def to_bytes(self) -> bytes:
        payload_type = self.payload_type & 0x7F
        second = payload_type | (0x80 if self.marker else 0)
        return struct.pack(
            "!BBHII",
            self.first_byte & 0xFF,
            second & 0xFF,
            self.sequence & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            self.ssrc & 0xFFFFFFFF,
        ) + self.payload


def parse_rtp(data: bytes) -> RtpPacket:
    if len(data) < RTP_HEADER_LEN:
        raise ValueError("RTP packet too short")
    first, second, seq, ts, ssrc = struct.unpack("!BBHII", data[:RTP_HEADER_LEN])
    if not is_probable_rtp_packet(data):
        raise ValueError(f"not RTP v2/EchoLink RTP first byte: 0x{first:02x}")
    payload_type = second & 0x7F
    marker = bool(second & 0x80)
    return RtpPacket(
        sequence=seq,
        timestamp=ts,
        ssrc=ssrc,
        payload_type=payload_type,
        payload=data[RTP_HEADER_LEN:],
        marker=marker,
        first_byte=first,
    )


def build_gsm_rtp(
    gsm_payload: bytes,
    *,
    sequence: int,
    timestamp: int = 0,
    ssrc: int = 0,
    marker: bool = False,
    first_byte: int = RTP_VERSION_LEGACY_MARKER,
) -> bytes:
    # EchoLink mobile clients are more compatible with the legacy 0xC0
    # first byte used by the original application on outbound RTP/GSM.
    return RtpPacket(
        sequence=sequence,
        timestamp=timestamp,
        ssrc=ssrc,
        payload_type=RTP_PAYLOAD_GSM,
        payload=gsm_payload,
        marker=marker,
        first_byte=first_byte,
    ).to_bytes()


def _rtcp_header(packet_type: int, count: int, body: bytes) -> bytes:
    pad = (-len(body)) % 4
    body_padded = body + (b"\x00" * pad)
    length_words_minus_one = (len(body_padded) + 4) // 4 - 1
    return struct.pack("!BBH", 0x80 | (count & 0x1F), packet_type & 0xFF, length_words_minus_one) + body_padded


def build_rtcp_sdes(*, callsign: str, name: str = "", ssrc: int | None = None, priv: str | None = None) -> bytes:
    if ssrc is None:
        ssrc = random.randrange(1, 0xFFFFFFFF)
    display = f"{callsign.upper()} {name}".strip()
    items = bytearray()
    for item_type, text in ((SDES_CNAME, callsign.upper()), (SDES_NAME, display)):
        encoded = text.encode("utf-8")[:255]
        items.extend(bytes([item_type, len(encoded)]))
        items.extend(encoded)
    if priv:
        encoded = priv.encode("utf-8")[:255]
        items.extend(bytes([SDES_PRIV, len(encoded)]))
        items.extend(encoded)
    items.append(0)  # END item
    body = struct.pack("!I", ssrc & 0xFFFFFFFF) + bytes(items)
    return _rtcp_header(RTCP_SDES, 1, body)


def build_rtcp_bye(*, ssrc: int = 0) -> bytes:
    return _rtcp_header(RTCP_BYE, 1, struct.pack("!I", ssrc & 0xFFFFFFFF))


def iter_rtcp_packet_types(data: bytes) -> list[int]:
    """Return RTCP packet-type bytes found in a possibly compound packet.

    EchoLink mobile clients can send compound RTCP, for example an RR packet
    followed by a BYE packet in the same UDP datagram.  Older code only
    checked data[1], so a valid compound ``RR + BYE`` disconnect was ignored.

    The parser is deliberately tolerant of EchoLink's legacy first byte 0xC0
    while still using the standard RTCP length field to walk packet boundaries.
    """
    types: list[int] = []
    pos = 0
    while pos + 4 <= len(data):
        first = data[pos]
        version = first >> 6
        if version not in (2, 3):
            break
        packet_type = data[pos + 1]
        length_words = struct.unpack("!H", data[pos + 2:pos + 4])[0]
        packet_len = (length_words + 1) * 4
        if packet_len < 4 or pos + packet_len > len(data):
            # Keep direct single-packet detection working for short or legacy
            # control datagrams with imperfect length fields.
            types.append(packet_type)
            break
        types.append(packet_type)
        pos += packet_len
        # Padding at the end of the datagram is common; do not require another
        # full RTCP header after trailing zero bytes.
        if pos < len(data) and not data[pos:].strip(b"\x00"):
            break
    return types


def is_rtcp_bye(data: bytes) -> bool:
    if len(data) < 4:
        return False
    return RTCP_BYE in iter_rtcp_packet_types(data)


def packet_contains_disconnect_command(data: bytes) -> bool:
    """Detect short binary/text EchoLink disconnect commands.

    This is separate from ``parse_echolink_text_packet`` because iPhone clients
    can place human-readable BYE/GOODBYE/DISCONNECT strings inside binary
    control datagrams whose first byte looks RTP/RTCP-like.  Those datagrams
    were skipped by the general text parser.
    """
    if not data or len(data) > 256:
        return False
    parsed = parse_ndata(data)
    if is_disconnect_text(parsed):
        return True
    raw = data.replace(b"\x00", b" ").replace(b"\r", b" ").replace(b"\n", b" ")
    for encoding in ("utf-8", "latin-1"):
        try:
            text = raw.decode(encoding, errors="ignore")
        except Exception:
            continue
        if is_disconnect_text(text):
            return True
    printable = "".join(chr(b) if 32 <= b <= 126 else " " for b in data)
    printable = " ".join(printable.split())
    upper = printable.upper()
    if is_disconnect_text(printable):
        return True
    # Conservative embedded-token check for binary control datagrams.  Require
    # command-like words and a short datagram to avoid false positives in audio.
    for token in ("GOODBYE", "DISCONNECT", "DISCONNECTED", "LOG OFF", "LOGOFF"):
        if token in upper:
            return True
    if upper.strip() in {"BYE", "BYE BYE"}:
        return True
    return False


def parse_rtcp_sdes(data: bytes) -> dict[str, str]:
    if len(data) < 8 or (data[0] >> 6) != 2 or data[1] != RTCP_SDES:
        raise ValueError("not RTCP SDES")
    count = data[0] & 0x1F
    pos = 4
    result: dict[str, str] = {}
    for _ in range(count):
        if pos + 4 > len(data):
            break
        pos += 4  # SSRC
        while pos < len(data):
            item_type = data[pos]
            pos += 1
            if item_type == 0:
                while pos % 4 != 0 and pos < len(data):
                    pos += 1
                break
            if pos >= len(data):
                break
            length = data[pos]
            pos += 1
            value = data[pos:pos + length].decode("utf-8", errors="replace")
            pos += length
            if item_type == SDES_CNAME:
                result["cname"] = value
            elif item_type == SDES_NAME:
                result["name"] = value
            elif item_type == SDES_PRIV:
                result["priv"] = value
    callsign = result.get("name") or result.get("cname") or ""
    if callsign:
        result["callsign"] = callsign.split()[0].upper()
    return result


def build_ndata_info(text: str) -> bytes:
    return ("oNDATA\r" + text.replace("\n", "\r") + "\x00").encode("utf-8")


def parse_ndata(data: bytes) -> str | None:
    if not data or is_probable_rtp_packet(data):
        return None
    if len(data) >= 7 and data[1:6] == b"NDATA":
        end = data.find(b"\x00")
        if end < 0:
            end = len(data)
        return data[7:end].decode("utf-8", errors="replace").replace("\r", "\n")
    return None


def is_disconnect_text(text: str | None) -> bool:
    """Return True for EchoLink text/control messages that mean client disconnect.

    Official and mobile EchoLink clients vary: some send RTCP BYE, some send
    short text payloads such as GOODBYE/BYE on the audio or control port.
    Treat only short command-like messages as disconnects so ordinary chat or
    roster text containing the word goodbye does not disconnect a station.
    """
    if text is None:
        return False
    normalized = " ".join(text.replace("\r", "\n").split()).strip().upper()
    if not normalized:
        return False
    compact = normalized.rstrip(".!")
    if compact in {"BYE", "GOODBYE", "DISCONNECT", "DISCONNECTED", "LEAVE", "QUIT", "LOGOFF", "LOG OFF"}:
        return True
    if len(compact) <= 64 and (
        compact.startswith("GOODBYE")
        or compact.startswith("DISCONNECT")
        or compact.startswith("BYE ")
    ):
        return True
    return False


def parse_station_identity_text(text: str) -> dict[str, str]:
    """Extract EchoLink station identity from text-style connect packets.

    Some EchoLink clients send NDATA-style/plain station information during
    connect rather than RTCP SDES.  Typical payload text begins with a line
    like ``Station KN4KCW`` followed by name/location/client lines.
    """
    normalized = text.replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    result: dict[str, str] = {}
    if not lines:
        return result
    first = lines[0]
    upper = first.upper()
    if upper.startswith("STATION "):
        callsign = first.split(None, 1)[1].strip().split()[0].upper()
        result["callsign"] = callsign
        result["station_line"] = first
        if len(lines) > 1:
            result["name"] = lines[1]
        if len(lines) > 2:
            result["location"] = lines[2]
        if len(lines) > 3:
            result["client"] = lines[3]
    return result


def parse_echolink_text_packet(data: bytes) -> str | None:
    """Return textual EchoLink packet content when possible.

    Accepts standard oNDATA packets and also plain/loosely-framed text
    datagrams used by some mobile EchoLink clients during connect attempts.

    The official EchoLink clients are not perfectly consistent here.  Some
    mobile builds send station identity as a loosely-framed binary datagram
    on both the audio and control ports, with the printable ``Station`` block
    embedded after a short binary prefix.  Treat that as identity text instead
    of logging a bad-control warning.
    """
    parsed = parse_ndata(data)
    if parsed is not None:
        return parsed
    if not data or is_probable_rtp_packet(data):
        return None

    # Fast path for binary-framed mobile identity packets.
    for marker in (b"Station ", b"station "):
        idx = data.find(marker)
        if idx >= 0:
            raw_embedded = data[idx:].strip(b"\x00\r\n ")
            text = raw_embedded.decode("utf-8", errors="ignore")
            return text.replace("\x00", "")

    # Strip common NUL padding and try printable UTF-8/ASCII text.
    raw = data.strip(b"\x00\r\n ")
    if not raw:
        return None
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="ignore")
    text = text.replace("\x00", "")
    if is_disconnect_text(text):
        return text

    # Some mobile clients send short binary-framed disconnect commands with a
    # few non-printable prefix/suffix bytes.  Build a conservative printable
    # view so packets like b"\x03GOODBYE\x00" are handled without treating
    # arbitrary binary RTP/control as text.
    printable = "".join(chr(b) if 32 <= b <= 126 else " " for b in data)
    printable = " ".join(printable.split())
    if is_disconnect_text(printable):
        return printable
    for token in ("GOODBYE", "DISCONNECT", "DISCONNECTED", "LOG OFF", "LOGOFF"):
        if len(printable) <= 96 and token in printable.upper():
            return printable

    if "Station " in text or text.startswith("Station"):
        return text
    return None
