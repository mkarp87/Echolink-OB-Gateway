import pytest

from echolink_ob.openbridge.dmrd import (
    CANNED_PAYLOAD_A,
    DMRD_SIGNED_LEN,
    DMRDPacket,
    pack_bits,
    unpack_bits,
    verify_signed_dmrd,
)


def test_bits_round_trip_group_slot1():
    bits = pack_bits(1, "group", 2, 5)
    assert unpack_bits(bits) == (1, "group", 2, 5)


def test_bits_round_trip_unit_slot2():
    bits = pack_bits(2, "unit", 1, 2)
    assert unpack_bits(bits) == (2, "unit", 1, 2)


def test_dmrd_signed_roundtrip():
    packet = DMRDPacket(
        sequence=7,
        rf_source_id=3101234,
        destination_id=310001,
        network_id=31000189,
        slot=1,
        call_type="group",
        frame_type=1,
        dtype_vseq=3,
        stream_id=0x12345678,
        payload=CANNED_PAYLOAD_A,
    )
    signed = packet.to_signed(b"TESTPASS")
    assert len(signed) == DMRD_SIGNED_LEN
    assert verify_signed_dmrd(signed, b"TESTPASS")
    parsed = DMRDPacket.from_signed(signed, b"TESTPASS")
    assert parsed == packet


def test_dmrd_rejects_bad_hmac():
    packet = DMRDPacket(
        sequence=1,
        rf_source_id=1,
        destination_id=2,
        network_id=3,
        slot=1,
        call_type="group",
        frame_type=1,
        dtype_vseq=0,
        stream_id=4,
        payload=CANNED_PAYLOAD_A,
    )
    signed = bytearray(packet.to_signed(b"pass"))
    signed[-1] ^= 0xFF
    assert not verify_signed_dmrd(bytes(signed), b"pass")
    with pytest.raises(ValueError):
        DMRDPacket.from_signed(bytes(signed), b"pass")


def test_payload_length_validation():
    with pytest.raises(ValueError):
        DMRDPacket(0, 1, 2, 3, 1, "group", 2, 0, 4, b"short")


def test_dmrd_rejects_4byte_network_id_as_rf_source():
    with pytest.raises(ValueError, match="Do not use the 4-byte OpenBridge network_id"):
        DMRDPacket(
            sequence=1,
            rf_source_id=31000189,
            destination_id=310001,
            network_id=31000189,
            slot=1,
            call_type="group",
            frame_type=1,
            dtype_vseq=0,
            stream_id=4,
            payload=CANNED_PAYLOAD_A,
        )
