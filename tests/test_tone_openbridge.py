from echolink_ob.analog.tone_openbridge import build_openbridge_packets_from_ambe_blocks
from echolink_ob.config import load_config
from echolink_ob.openbridge.dmrd import CANNED_PAYLOAD_A, CANNED_PAYLOAD_B, CANNED_PAYLOAD_SILENCE, DMRDPacket


def make_packet(seq, frame_type, dtype, payload):
    return DMRDPacket(
        sequence=seq,
        rf_source_id=1234567,
        destination_id=310001,
        network_id=31000189,
        slot=1,
        call_type="group",
        frame_type=frame_type,
        dtype_vseq=dtype,
        stream_id=1234,
        payload=payload,
    )


def test_build_openbridge_packets_from_ambe_blocks():
    cfg = load_config("config/config-sample.toml")
    template = [
        make_packet(1, 2, 1, CANNED_PAYLOAD_SILENCE),
        make_packet(2, 1, 0, CANNED_PAYLOAD_A),
        make_packet(3, 0, 1, CANNED_PAYLOAD_B),
        make_packet(4, 2, 2, CANNED_PAYLOAD_SILENCE),
    ]
    blocks = [bytes([i]) * 27 for i in range(1, 4)]
    packets = build_openbridge_packets_from_ambe_blocks(blocks, template, cfg, source_id=310001, stream_id=999)
    assert len(packets) == 5
    assert packets[0].frame_type == 2
    assert [p.frame_type for p in packets[1:4]] == [1, 0, 1]
    assert packets[-1].frame_type == 2
    assert {p.rf_source_id for p in packets} == {310001}
    assert {p.destination_id for p in packets} == {cfg.openbridge.fixed_tgid}
    assert {p.stream_id for p in packets} == {999}

from pathlib import Path
import pytest
import echolink_ob.analog.tone_openbridge as tone_ob


def test_resolve_template_uses_builtin_when_no_capture(monkeypatch, tmp_path):
    builtin = tmp_path / "builtin.dmrd"
    builtin.write_bytes(b"template")
    monkeypatch.setattr(tone_ob, "DEFAULT_CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(tone_ob, "DEFAULT_BUILTIN_TEMPLATE", builtin)
    path, source = tone_ob.resolve_template_path(None, mode="auto")
    assert path == builtin
    assert source == "builtin"


def test_resolve_template_capture_mode_fails_before_tone(monkeypatch, tmp_path):
    monkeypatch.setattr(tone_ob, "DEFAULT_CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(tone_ob, "DEFAULT_BUILTIN_TEMPLATE", tmp_path / "missing.dmrd")
    with pytest.raises(FileNotFoundError):
        tone_ob.resolve_template_path(None, mode="capture")


def test_resolve_template_prefers_explicit(tmp_path):
    explicit = tmp_path / "explicit.dmrd"
    explicit.write_bytes(b"template")
    path, source = tone_ob.resolve_template_path(str(explicit), mode="builtin")
    assert path == explicit
    assert source == "explicit"
