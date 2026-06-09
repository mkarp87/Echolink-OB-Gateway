from __future__ import annotations

import argparse
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path

from echolink_ob.config import AppConfig, load_config
from echolink_ob.logging_setup import setup_logging
from echolink_ob.openbridge.client import OpenBridgeClient
from echolink_ob.openbridge.dmrd import DMRD_BODY_LEN, DMRDPacket, MAX_3BYTE_ID, validate_3byte_id
from echolink_ob.openbridge.test_sender import make_client, resolve_source_id

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayPlan:
    input_packets: int
    output_packets: int
    source_id: int
    destination_id: int
    network_id: int
    stream_id: int
    first_input_stream_id: int | None
    packet_ms: float


def load_dmrd_capture(path: str | Path) -> list[DMRDPacket]:
    p = Path(path)
    data = p.read_bytes()
    if not data:
        raise ValueError(f"empty DMRD capture: {p}")
    if len(data) % DMRD_BODY_LEN != 0:
        raise ValueError(
            f"DMRD capture length must be a multiple of {DMRD_BODY_LEN}; got {len(data)} bytes"
        )
    packets: list[DMRDPacket] = []
    for offset in range(0, len(data), DMRD_BODY_LEN):
        packets.append(DMRDPacket.from_raw53(data[offset : offset + DMRD_BODY_LEN]))
    return packets


def select_stream_packets(packets: list[DMRDPacket], stream_id: int | None) -> list[DMRDPacket]:
    if not packets:
        return []
    selected_stream = stream_id if stream_id is not None else packets[0].stream_id
    selected = [pkt for pkt in packets if pkt.stream_id == selected_stream]
    if not selected:
        raise ValueError(f"no packets found for stream_id={selected_stream}")
    return selected


def rewrite_for_replay(
    packets: list[DMRDPacket],
    *,
    source_id: int,
    destination_id: int,
    network_id: int,
    slot: int,
    stream_id: int,
    sequence_start: int = 0,
) -> list[DMRDPacket]:
    validate_3byte_id(source_id, "source-id")
    validate_3byte_id(destination_id, "destination-id")
    output: list[DMRDPacket] = []
    seq = sequence_start % 256
    for pkt in packets:
        output.append(
            DMRDPacket(
                sequence=seq,
                rf_source_id=source_id,
                destination_id=destination_id,
                network_id=network_id,
                slot=slot,
                call_type="group",
                frame_type=pkt.frame_type,
                dtype_vseq=pkt.dtype_vseq,
                stream_id=stream_id,
                payload=pkt.payload,
            )
        )
        seq = (seq + 1) % 256
    return output


def build_replay_packets(
    capture_path: str | Path,
    cfg: AppConfig,
    *,
    source_id: int,
    stream_filter: int | None = None,
    preserve_stream_id: bool = False,
    new_stream_id: int | None = None,
    packet_ms: float = 60.0,
) -> tuple[list[DMRDPacket], ReplayPlan]:
    packets = load_dmrd_capture(capture_path)
    selected = select_stream_packets(packets, stream_filter)
    first_stream_id = selected[0].stream_id if selected else None
    if new_stream_id is not None:
        replay_stream_id = int(new_stream_id)
    elif preserve_stream_id and first_stream_id is not None:
        replay_stream_id = int(first_stream_id)
    else:
        replay_stream_id = random.randint(1, 0xFFFFFFFF)
    output = rewrite_for_replay(
        selected,
        source_id=source_id,
        destination_id=cfg.openbridge.fixed_tgid,
        network_id=cfg.openbridge.network_id,
        slot=cfg.openbridge.slot,
        stream_id=replay_stream_id,
    )
    plan = ReplayPlan(
        input_packets=len(packets),
        output_packets=len(output),
        source_id=source_id,
        destination_id=cfg.openbridge.fixed_tgid,
        network_id=cfg.openbridge.network_id,
        stream_id=replay_stream_id,
        first_input_stream_id=first_stream_id,
        packet_ms=packet_ms,
    )
    return output, plan


def send_replay(client: OpenBridgeClient, packets: list[DMRDPacket], *, packet_ms: float, repeat: int = 1) -> None:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    delay = max(0.0, packet_ms / 1000.0)
    total = 0
    for r in range(repeat):
        log.info("openbridge_replay_cycle_start cycle=%s packets=%s", r + 1, len(packets))
        for pkt in packets:
            client.send_packet(pkt)
            total += 1
            if delay:
                time.sleep(delay)
    log.info("openbridge_replay_done packets=%s cycles=%s", total, repeat)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Replay a recorded .dmrd stream as audible test traffic. "
            "This is the preferred audio test because it reuses known-good DMR voice bursts."
        )
    )
    p.add_argument("capture", help="path to stream-*.dmrd recorded by echolink-ob-record-dmr")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument(
        "--source-id",
        type=int,
        default=None,
        help=(
            "3-byte DMR RF source subscriber ID to place in replayed packets. "
            f"Valid range is 0..{MAX_3BYTE_ID}. Defaults to [identity].fallback_source_id."
        ),
    )
    p.add_argument("--stream-id", type=int, default=None, help="only replay this captured stream ID")
    p.add_argument("--new-stream-id", type=int, default=None, help="force this outgoing stream ID")
    p.add_argument("--preserve-stream-id", action="store_true", help="reuse the captured stream ID")
    p.add_argument("--packet-ms", type=float, default=60.0, help="packet pacing in milliseconds")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--dry-run", action="store_true", help="show what would be sent without transmitting")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    source_id = resolve_source_id(cfg, args.source_id, parser)

    try:
        packets, plan = build_replay_packets(
            args.capture,
            cfg,
            source_id=source_id,
            stream_filter=args.stream_id,
            preserve_stream_id=args.preserve_stream_id,
            new_stream_id=args.new_stream_id,
            packet_ms=args.packet_ms,
        )
    except Exception as exc:
        parser.error(str(exc))

    log.info(
        "openbridge_replay_plan capture=%s input_packets=%s output_packets=%s src=%s dst=%s "
        "network_id=%s input_stream=%s output_stream=%s packet_ms=%s repeat=%s dry_run=%s",
        args.capture,
        plan.input_packets,
        plan.output_packets,
        plan.source_id,
        plan.destination_id,
        plan.network_id,
        plan.first_input_stream_id,
        plan.stream_id,
        plan.packet_ms,
        args.repeat,
        args.dry_run,
    )

    if args.dry_run:
        print(
            "replay_plan "
            f"packets={plan.output_packets} source_id={plan.source_id} "
            f"tg={plan.destination_id} network_id={plan.network_id} "
            f"stream={plan.stream_id} packet_ms={plan.packet_ms} repeat={args.repeat}"
        )
        return 0

    client = make_client(cfg)
    try:
        send_replay(client, packets, packet_ms=args.packet_ms, repeat=args.repeat)
        return 0
    except KeyboardInterrupt:
        log.info("openbridge_replay_interrupted")
        return 130
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
