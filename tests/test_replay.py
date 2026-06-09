from pathlib import Path

from echolink_ob.config import load_config
from echolink_ob.openbridge.dmrd import CANNED_PAYLOAD_A, DMRDPacket, DMRD_BODY_LEN
from echolink_ob.openbridge.replay import build_replay_packets, load_dmrd_capture, rewrite_for_replay


def make_packet(seq: int, stream_id: int = 1234) -> DMRDPacket:
    return DMRDPacket(
        sequence=seq,
        rf_source_id=1234567,
        destination_id=310001,
        network_id=31000189,
        slot=1,
        call_type="group",
        frame_type=1 if seq % 6 == 0 else 0,
        dtype_vseq=seq % 6,
        stream_id=stream_id,
        payload=CANNED_PAYLOAD_A,
    )


def test_load_dmrd_capture(tmp_path: Path):
    path = tmp_path / "stream.dmrd"
    packets = [make_packet(0), make_packet(1)]
    path.write_bytes(b"".join(pkt.to_raw53() for pkt in packets))
    loaded = load_dmrd_capture(path)
    assert len(loaded) == 2
    assert path.stat().st_size == DMRD_BODY_LEN * 2
    assert loaded[0].stream_id == 1234


def test_rewrite_for_replay_changes_outer_ids_but_preserves_payload_and_type():
    packet = make_packet(7, stream_id=111)
    rewritten = rewrite_for_replay(
        [packet],
        source_id=3101234,
        destination_id=310001,
        network_id=31000189,
        slot=1,
        stream_id=222,
    )
    out = rewritten[0]
    assert out.sequence == 0
    assert out.rf_source_id == 3101234
    assert out.destination_id == 310001
    assert out.network_id == 31000189
    assert out.stream_id == 222
    assert out.frame_type == packet.frame_type
    assert out.dtype_vseq == packet.dtype_vseq
    assert out.payload == packet.payload


def test_build_replay_packets_uses_config_targets(tmp_path: Path):
    cfg = load_config("config/config-sample.toml")
    path = tmp_path / "stream.dmrd"
    packets = [make_packet(0), make_packet(1)]
    path.write_bytes(b"".join(pkt.to_raw53() for pkt in packets))
    replay, plan = build_replay_packets(path, cfg, source_id=3109999, new_stream_id=555)
    assert plan.output_packets == 2
    assert plan.destination_id == cfg.openbridge.fixed_tgid
    assert plan.network_id == cfg.openbridge.network_id
    assert plan.stream_id == 555
    assert replay[0].rf_source_id == 3109999
    assert replay[0].destination_id == cfg.openbridge.fixed_tgid
    assert replay[0].network_id == cfg.openbridge.network_id
