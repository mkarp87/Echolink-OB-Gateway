import json

from echolink_ob.openbridge.analyzer import CaptureAnalysis, read_dmrd_file, write_voice33_outputs
from echolink_ob.openbridge.dmrd import CANNED_PAYLOAD_A, CANNED_PAYLOAD_B, DMRDPacket, DMRD_BODY_LEN


def pkt(seq, frame_type, dtype_vseq):
    return DMRDPacket(
        sequence=seq,
        rf_source_id=1234567,
        destination_id=310001,
        network_id=31000189,
        slot=1,
        call_type="group",
        frame_type=frame_type,
        dtype_vseq=dtype_vseq,
        stream_id=618503740,
        payload=CANNED_PAYLOAD_A if dtype_vseq % 2 else CANNED_PAYLOAD_B,
    )


def test_capture_analysis_detects_voice_cycle_and_end():
    packets = [pkt(36, 2, 1)] + [pkt(37 + i, 1 if i == 0 else 0, i) for i in range(6)] + [pkt(43, 2, 2)]
    report = CaptureAnalysis(packets).to_dict()
    stream = report["streams"][0]
    assert stream["stream_id"] == 618503740
    assert stream["packet_count"] == 8
    assert stream["voice_payload_packets"] == 6
    assert stream["saw_possible_end"] is True
    assert stream["sequence_gaps"] == []
    assert stream["voice_cycle_summary"]["complete_cycles"] == 1
    assert stream["voice_cycle_summary"]["cycle_dtype_sequences"] == [[0, 1, 2, 3, 4, 5]]


def test_write_voice33_outputs(tmp_path):
    packets = [pkt(1, 2, 1), pkt(2, 1, 0), pkt(3, 0, 1), pkt(4, 2, 2)]
    outputs = write_voice33_outputs(packets, tmp_path)
    out = outputs[618503740]
    assert out["packets"] == 2
    assert out["bytes"] == 66
    assert (tmp_path / "stream-618503740.voice33").stat().st_size == 66
    lines = (tmp_path / "stream-618503740.voice33.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["frame_type"] == 1


def test_read_dmrd_file(tmp_path):
    packets = [pkt(10, 1, 0), pkt(11, 0, 1)]
    path = tmp_path / "stream.dmrd"
    path.write_bytes(b"".join(p.to_raw53() for p in packets))
    parsed = read_dmrd_file(path)
    assert len(parsed) == 2
    assert parsed[0].sequence == 10
    assert path.stat().st_size == DMRD_BODY_LEN * 2
