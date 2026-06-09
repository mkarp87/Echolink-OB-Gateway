import json

from echolink_ob.dmr.ambe import (
    AMBE_FRAMES_PER_DMR_BURST,
    BITS_PER_DMR_AMBE_WITH_FEC,
    BYTES_PER_DMR_AMBE_WITH_FEC,
    bits_to_bytes,
    bytes_to_bits,
    extract_ambe72_from_packets,
    extract_ambe72_from_payload33,
    extract_voice_bits_from_payload33,
    write_ambe72_outputs,
)
from echolink_ob.openbridge.dmrd import DMRDPacket


def test_bit_roundtrip():
    data = bytes.fromhex("00112233445566778899aabbccddeeff")
    assert bits_to_bytes(bytes_to_bits(data)) == data


def test_extract_voice_bits_removes_center_48_bits():
    # Create a synthetic burst with sequential bits. The extractor should keep
    # bits 0..107 and 156..263, dropping the center 48 sync/embedded bits.
    bits = [(i % 2) for i in range(264)]
    payload = bits_to_bytes(bits)
    voice_bits = extract_voice_bits_from_payload33(payload)
    assert len(voice_bits) == 216
    assert voice_bits[:108] == bits[:108]
    assert voice_bits[108:] == bits[156:]


def test_extract_ambe72_from_payload33_returns_three_9_byte_frames():
    payload = bytes(range(33))
    frames = extract_ambe72_from_payload33(payload)
    assert len(frames) == AMBE_FRAMES_PER_DMR_BURST
    assert all(len(frame) == BYTES_PER_DMR_AMBE_WITH_FEC for frame in frames)
    assert sum(len(frame) * 8 for frame in frames) == AMBE_FRAMES_PER_DMR_BURST * BITS_PER_DMR_AMBE_WITH_FEC


def make_pkt(seq, frame_type, dtype_vseq, stream_id=1234):
    return DMRDPacket(
        sequence=seq,
        rf_source_id=1234567,
        destination_id=310001,
        network_id=31000189,
        slot=1,
        call_type="group",
        frame_type=frame_type,
        dtype_vseq=dtype_vseq,
        stream_id=stream_id,
        payload=bytes(range(33)),
    )


def test_extract_ambe72_from_packets_skips_non_voice_packets():
    packets = [make_pkt(1, 2, 1), make_pkt(2, 1, 0), make_pkt(3, 0, 1), make_pkt(4, 2, 2)]
    frames = extract_ambe72_from_packets(packets)
    assert len(frames) == 6
    assert [f.frame_index_in_stream for f in frames] == list(range(6))
    assert frames[0].packet_sequence == 2
    assert frames[-1].packet_sequence == 3


def test_write_ambe72_outputs(tmp_path):
    packets = [make_pkt(10, 1, 0), make_pkt(11, 0, 1)]
    outputs = write_ambe72_outputs(packets, tmp_path)
    out = outputs[1234]
    assert out["frames"] == 6
    assert out["bytes"] == 54
    assert out["duration_ms_estimate"] == 120
    assert (tmp_path / "stream-1234.ambe72").stat().st_size == 54
    lines = (tmp_path / "stream-1234.ambe72.jsonl").read_text().strip().splitlines()
    assert len(lines) == 6
    first = json.loads(lines[0])
    assert first["bytes"] == 9
    assert first["packet_dtype_vseq"] == 0
