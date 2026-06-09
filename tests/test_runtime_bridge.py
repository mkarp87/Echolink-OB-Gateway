from echolink_ob.bridge.runtime import (
    OpenBridgeStreamBuilder,
    TlvCodecFormat,
    build_tlv_audio_datagram,
    build_tlv_end_datagram,
    build_tlv_start_datagram,
    dmr_packet_to_ambe_block,
    learn_tlv_codec_format,
    split_template_packets,
)
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


def template_packets():
    return [
        make_packet(1, 2, 1, CANNED_PAYLOAD_SILENCE),
        make_packet(2, 1, 0, CANNED_PAYLOAD_A),
        make_packet(3, 0, 1, CANNED_PAYLOAD_B),
        make_packet(4, 2, 2, CANNED_PAYLOAD_SILENCE),
    ]


def test_split_template_packets():
    parts = split_template_packets(template_packets())
    assert len(parts.prefix) == 1
    assert len(parts.voice) == 2
    assert len(parts.suffix) == 1


def test_openbridge_stream_builder_uses_fallback_source_id():
    cfg = load_config("config/config-sample.toml")
    block = bytes([7]) * 27
    builder = OpenBridgeStreamBuilder(
        cfg=cfg,
        template_packets=template_packets(),
        source_id=1234567,
        stream_id=999,
    )
    packets = builder.start_packets() + [builder.voice_packet(block)] + builder.end_packets()
    assert [p.sequence for p in packets] == [0, 1, 2]
    assert {p.rf_source_id for p in packets} == {1234567}
    assert {p.stream_id for p in packets} == {999}
    assert packets[1].frame_type == 1


def test_tlv_audio_datagram_helpers():
    block = bytes(range(27))
    assert build_tlv_audio_datagram(block) == b"\x02\x00\x1b" + block
    assert build_tlv_end_datagram() == b"\x02\x00\x00"


def test_tlv_format_uses_learned_header():
    block = bytes(range(27))
    fmt = TlvCodecFormat(
        audio_header=b"\x09\x1b\x00",
        end_datagram=b"\x09\x00\x00",
        start_datagram=b"\x09start",
        source="test",
    )
    assert build_tlv_audio_datagram(block, tlv_format=fmt) == b"\x09\x1b\x00" + block
    assert build_tlv_end_datagram(tlv_format=fmt) == b"\x09\x00\x00"
    assert build_tlv_start_datagram(tlv_format=fmt) == b"\x09start"


def test_learn_tlv_codec_format_from_analog_bridge_frames():
    block = bytes([5]) * 27
    start = b"\x09begin-control-frame"
    fmt = learn_tlv_codec_format([start, b"\x09\x1b\x00" + block, b"\x09\x00\x00"])
    assert fmt is not None
    assert fmt.source == "learned"
    assert fmt.audio_header == b"\x09\x1b\x00"
    assert fmt.end_datagram == b"\x09\x00\x00"
    assert fmt.start_datagram == start


def test_dmr_packet_to_ambe_block_returns_27_bytes():
    pkt = make_packet(2, 1, 0, CANNED_PAYLOAD_A)
    block = dmr_packet_to_ambe_block(pkt)
    assert len(block) == 27

from echolink_ob.analog.ports import build_port_plan
from echolink_ob.bridge.runtime import AnalogOpenBridgeRuntime


def test_runtime_status_snapshot_contains_ports():
    cfg = load_config("config/config-sample.toml")
    plan = build_port_plan(cfg, allow_in_use=True, reuse_state=False).plan
    rt = AnalogOpenBridgeRuntime(
        cfg=cfg,
        plan=plan,
        template_packets=template_packets(),
        source_id=1234567,
    )
    snap = rt.status_snapshot()
    assert snap["source_id"] == 1234567
    assert "openbridge_target" in snap
    assert "tlv_rx" in snap
    assert snap["stats"]["openbridge_packets_sent"] == 0


def test_runtime_parser_defaults_do_not_inject_startup_audio():
    from echolink_ob.bridge.runtime import build_parser

    args = build_parser().parse_args([])
    assert args.tlv_calibration_ms == 0
    assert args.startup_mute_ms >= 2000


def test_runtime_status_snapshot_reports_startup_mute():
    cfg = load_config("config/config-sample.toml")
    plan = build_port_plan(cfg, allow_in_use=True, reuse_state=False).plan
    rt = AnalogOpenBridgeRuntime(
        cfg=cfg,
        plan=plan,
        template_packets=template_packets(),
        source_id=1234567,
        startup_mute_ms=2500,
    )
    snap = rt.status_snapshot()
    assert snap["startup_mute_seconds"] == 2.5
