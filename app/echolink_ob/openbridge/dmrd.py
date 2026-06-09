from __future__ import annotations

from dataclasses import dataclass
import hmac
from hashlib import sha1

DMRD_MAGIC = b"DMRD"
DMRD_BODY_LEN = 53
DMRD_HMAC_LEN = 20
DMRD_SIGNED_LEN = DMRD_BODY_LEN + DMRD_HMAC_LEN
DMR_PAYLOAD_LEN = 33
MAX_3BYTE_ID = 0xFFFFFF

CALL_TYPE_GROUP = "group"
CALL_TYPE_UNIT = "unit"
CALL_TYPE_VCSBK = "vcsbk"


def validate_3byte_id(value: int, field_name: str = "value") -> None:
    if not 0 <= value <= MAX_3BYTE_ID:
        raise ValueError(
            f"{field_name} must fit the 3-byte DMR ID field "
            f"(0..{MAX_3BYTE_ID}); got {value}. "
            "Do not use the 4-byte OpenBridge network_id as an RF source subscriber ID."
        )


def int_to_3(value: int, field_name: str = "value") -> bytes:
    validate_3byte_id(value, field_name)
    return value.to_bytes(3, "big")


def int_to_4(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"value does not fit 4 bytes: {value}")
    return value.to_bytes(4, "big")


def int_from_bytes(value: bytes) -> int:
    return int.from_bytes(value, "big")


def sign_dmrd(raw53: bytes, passphrase: bytes) -> bytes:
    if len(raw53) != DMRD_BODY_LEN:
        raise ValueError(f"DMRD body must be {DMRD_BODY_LEN} bytes, got {len(raw53)}")
    return raw53 + hmac.new(passphrase, raw53, sha1).digest()


def verify_signed_dmrd(packet: bytes, passphrase: bytes) -> bool:
    if len(packet) != DMRD_SIGNED_LEN:
        return False
    if packet[:4] != DMRD_MAGIC:
        return False
    raw = packet[:DMRD_BODY_LEN]
    supplied = packet[DMRD_BODY_LEN:]
    expected = hmac.new(passphrase, raw, sha1).digest()
    return hmac.compare_digest(supplied, expected)


def pack_bits(slot: int, call_type: str, frame_type: int, dtype_vseq: int) -> int:
    if slot not in (1, 2):
        raise ValueError("slot must be 1 or 2")
    if not 0 <= frame_type <= 3:
        raise ValueError("frame_type must fit 2 bits")
    if not 0 <= dtype_vseq <= 15:
        raise ValueError("dtype_vseq must fit 4 bits")
    bits = ((frame_type & 0x03) << 4) | (dtype_vseq & 0x0F)
    if slot == 2:
        bits |= 0x80
    if call_type == CALL_TYPE_UNIT:
        bits |= 0x40
    elif call_type == CALL_TYPE_VCSBK:
        # HBlink3 detects VCSBK when (bits & 0x23) == 0x23.
        bits = (bits & ~0x0F) | 0x03
        bits |= 0x20
    elif call_type != CALL_TYPE_GROUP:
        raise ValueError(f"unknown call_type: {call_type}")
    return bits


def unpack_bits(bits: int) -> tuple[int, str, int, int]:
    slot = 2 if (bits & 0x80) else 1
    if bits & 0x40:
        call_type = CALL_TYPE_UNIT
    elif (bits & 0x23) == 0x23:
        call_type = CALL_TYPE_VCSBK
    else:
        call_type = CALL_TYPE_GROUP
    frame_type = (bits & 0x30) >> 4
    dtype_vseq = bits & 0x0F
    return slot, call_type, frame_type, dtype_vseq


@dataclass(frozen=True)
class DMRDPacket:
    sequence: int
    rf_source_id: int
    destination_id: int
    network_id: int
    slot: int
    call_type: str
    frame_type: int
    dtype_vseq: int
    stream_id: int
    payload: bytes

    def __post_init__(self) -> None:
        if not 0 <= self.sequence <= 255:
            raise ValueError("sequence must be 0..255")
        if len(self.payload) != DMR_PAYLOAD_LEN:
            raise ValueError(f"payload must be {DMR_PAYLOAD_LEN} bytes")
        # Validate ranges early.
        int_to_3(self.rf_source_id, "rf_source_id")
        int_to_3(self.destination_id, "destination_id")
        int_to_4(self.network_id)
        int_to_4(self.stream_id)
        pack_bits(self.slot, self.call_type, self.frame_type, self.dtype_vseq)

    def to_raw53(self) -> bytes:
        return b"".join(
            [
                DMRD_MAGIC,
                bytes([self.sequence]),
                int_to_3(self.rf_source_id, "rf_source_id"),
                int_to_3(self.destination_id, "destination_id"),
                int_to_4(self.network_id),
                bytes([pack_bits(self.slot, self.call_type, self.frame_type, self.dtype_vseq)]),
                int_to_4(self.stream_id),
                self.payload,
            ]
        )

    def to_signed(self, passphrase: bytes) -> bytes:
        return sign_dmrd(self.to_raw53(), passphrase)

    @classmethod
    def from_raw53(cls, data: bytes) -> "DMRDPacket":
        if len(data) != DMRD_BODY_LEN:
            raise ValueError(f"raw DMRD must be {DMRD_BODY_LEN} bytes")
        if data[:4] != DMRD_MAGIC:
            raise ValueError("not a DMRD packet")
        slot, call_type, frame_type, dtype_vseq = unpack_bits(data[15])
        return cls(
            sequence=data[4],
            rf_source_id=int_from_bytes(data[5:8]),
            destination_id=int_from_bytes(data[8:11]),
            network_id=int_from_bytes(data[11:15]),
            slot=slot,
            call_type=call_type,
            frame_type=frame_type,
            dtype_vseq=dtype_vseq,
            stream_id=int_from_bytes(data[16:20]),
            payload=data[20:53],
        )

    @classmethod
    def from_signed(cls, data: bytes, passphrase: bytes) -> "DMRDPacket":
        if not verify_signed_dmrd(data, passphrase):
            raise ValueError("DMRD HMAC verification failed")
        return cls.from_raw53(data[:DMRD_BODY_LEN])


# 33-byte transport test payloads. These are opaque DMR payload bytes used to exercise
# OpenBridge framing and pacing. They are not a promise of intelligible voice.
CANNED_PAYLOAD_A = bytes.fromhex(
    "4f2e00b501ae3a001c40a0c1cc7dff57d75df5d5065026f82880bd616f13f185890000"
)[:DMR_PAYLOAD_LEN]
CANNED_PAYLOAD_B = bytes.fromhex(
    "4f410061011e3a781c30a061ccbdff57d75df5d2534425c02fe0b1216713e885ba0000"
)[:DMR_PAYLOAD_LEN]
CANNED_PAYLOAD_SILENCE = b"\x00" * DMR_PAYLOAD_LEN
