import json

from echolink_ob.openbridge.dmrd import CANNED_PAYLOAD_A, DMRDPacket, DMRD_BODY_LEN, DMRD_SIGNED_LEN
from echolink_ob.openbridge.recorder import DMRStreamRecorder


def make_packet(seq=1, frame_type=1, dtype_vseq=0):
    return DMRDPacket(
        sequence=seq,
        rf_source_id=1234567,
        destination_id=310001,
        network_id=31000189,
        slot=1,
        call_type="group",
        frame_type=frame_type,
        dtype_vseq=dtype_vseq,
        stream_id=860215535,
        payload=CANNED_PAYLOAD_A,
    )


def test_recorder_writes_stream_files(tmp_path):
    rec = DMRStreamRecorder(tmp_path, b"TESTPASS")
    rec.record_packet(make_packet(seq=14, frame_type=2, dtype_vseq=1))
    rec.record_packet(make_packet(seq=15, frame_type=0, dtype_vseq=2))
    summary = rec.write_summary()

    base = tmp_path / "stream-860215535"
    assert base.with_suffix(".dmrd").stat().st_size == DMRD_BODY_LEN * 2
    assert base.with_suffix(".signed-dmrd").stat().st_size == DMRD_SIGNED_LEN * 2
    assert base.with_suffix(".payload33").stat().st_size == 33 * 2
    lines = base.with_suffix(".jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["rf_source_id"] == 1234567
    assert first["destination_id"] == 310001
    assert first["stream_id"] == 860215535
    data = json.loads(summary.read_text())
    assert data["total_packets"] == 2
    assert data["stream_count"] == 1
    assert data["streams"][0]["packets"] == 2


def test_recorder_marks_possible_stream_end(tmp_path):
    rec = DMRStreamRecorder(tmp_path, b"TESTPASS")
    rec.record_packet(make_packet(seq=35, frame_type=2, dtype_vseq=2))
    summary = json.loads(rec.write_summary().read_text())
    assert summary["streams"][0]["saw_possible_end"] is True
    meta = json.loads((tmp_path / "stream-860215535.jsonl").read_text())
    assert meta["possible_stream_end"] is True
