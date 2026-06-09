from __future__ import annotations

from pathlib import Path

from echolink_ob.analog.tlv import RawTlvFrame, write_tlv_capture


def test_write_tlv_capture_length_prefixes_frames(tmp_path: Path):
    out = tmp_path / "capture.tlvraw"
    frames = [RawTlvFrame(data=b"abc", source=("127.0.0.1", 1), received_time=1.0), RawTlvFrame(data=b"d", source=("127.0.0.1", 2), received_time=2.0)]
    write_tlv_capture(out, frames)
    assert out.read_bytes() == b"\x00\x03abc\x00\x01d"
