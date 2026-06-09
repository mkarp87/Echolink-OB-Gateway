from echolink_ob.analog.tlv import RawTlvFrame, extract_dmr_ambe_blocks, parse_tlv_datagram


def test_parse_tlv_big_endian_length():
    data = bytes([0x42]) + (27).to_bytes(2, "big") + bytes(range(27))
    parsed = parse_tlv_datagram(data)
    assert parsed.tag == 0x42
    assert parsed.length == 27
    assert parsed.valid_length is True
    assert parsed.length_endian == "big"
    assert parsed.value == bytes(range(27))


def test_extract_dmr_blocks_from_30_byte_frames():
    payload = bytes(range(27))
    raw = RawTlvFrame(data=b"\x01\x00\x1b" + payload, source=("127.0.0.1", 1), received_time=0)
    assert extract_dmr_ambe_blocks([raw]) == [payload]


def test_ignores_metadata_and_end_frames():
    frames = [b"\x01\x00\x12" + bytes(18), b"\x09\x00\x00"]
    assert extract_dmr_ambe_blocks(frames) == []
