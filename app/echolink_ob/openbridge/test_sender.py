from __future__ import annotations

import argparse
import logging
import random
import time

from echolink_ob.config import AppConfig, load_config
from echolink_ob.logging_setup import setup_logging
from .client import OpenBridgeClient
from .dmrd import CANNED_PAYLOAD_A, CANNED_PAYLOAD_B, DMRDPacket, MAX_3BYTE_ID, validate_3byte_id

log = logging.getLogger(__name__)


def make_client(cfg: AppConfig) -> OpenBridgeClient:
    return OpenBridgeClient(
        host=cfg.openbridge.host,
        port=cfg.openbridge.port,
        passphrase=cfg.openbridge.passphrase,
        network_id=cfg.openbridge.network_id,
        fixed_tgid=cfg.openbridge.fixed_tgid,
        slot=cfg.openbridge.slot,
        call_type=cfg.openbridge.call_type,
        local_bind_host=cfg.openbridge.local_bind_host,
        local_bind_port=cfg.openbridge.local_bind_port,
        both_slots=cfg.openbridge.both_slots,
    )


def build_client(config_path: str) -> OpenBridgeClient:
    cfg = load_config(config_path)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    return make_client(cfg)


def resolve_source_id(cfg: AppConfig, explicit_source_id: int | None, parser: argparse.ArgumentParser) -> int:
    source_id = cfg.identity.fallback_source_id if explicit_source_id is None else explicit_source_id
    try:
        validate_3byte_id(source_id, "source-id")
    except ValueError as exc:
        parser.error(
            f"{exc} Use a real DMR subscriber/source ID, not OpenBridge network_id "
            f"{cfg.openbridge.network_id}. Edit [identity].fallback_source_id or pass --source-id."
        )
    return source_id


def send_silence(client: OpenBridgeClient, source_id: int, seconds: float) -> None:
    stream_id = random.randint(1, 0xFFFFFFFF)
    seq = 0
    frame_count = max(1, int(seconds / 0.06))
    log.info(
        "openbridge_send_test_start source_id=%s dst_tgid=%s network_id=%s stream=%s packets=%s note=transport_only_not_audible",
        source_id,
        client.fixed_tgid,
        client.network_id,
        stream_id,
        frame_count,
    )
    for i in range(frame_count):
        payload = CANNED_PAYLOAD_A if i % 2 == 0 else CANNED_PAYLOAD_B
        pkt = DMRDPacket(
            sequence=seq,
            rf_source_id=source_id,
            destination_id=client.fixed_tgid,
            network_id=client.network_id,
            slot=client.slot,
            call_type="group",
            frame_type=1,
            dtype_vseq=i % 6,
            stream_id=stream_id,
            payload=payload,
        )
        client.send_packet(pkt)
        seq = (seq + 1) % 256
        time.sleep(0.06)
    log.info("openbridge_send_test_done packets=%s stream=%s", client.counters.packets_sent, stream_id)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenBridge DMRD send/listen test. send-silence is transport-only; use echolink-ob-replay-dmr for audible replay.")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--mode", choices=["listen", "send-silence"], required=True)
    p.add_argument(
        "--source-id",
        type=int,
        default=None,
        help=(
            "3-byte DMR RF source subscriber ID to place in DMRD bytes 5:8. "
            f"Valid range is 0..{MAX_3BYTE_ID}. Defaults to [identity].fallback_source_id."
        ),
    )
    p.add_argument("--seconds", type=float, default=30.0)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    source_id = None
    if args.mode == "send-silence":
        source_id = resolve_source_id(cfg, args.source_id, parser)

    client = make_client(cfg)
    try:
        if args.mode == "listen":
            for _pkt in client.listen(args.seconds):
                pass
        elif args.mode == "send-silence":
            assert source_id is not None
            send_silence(client, source_id, args.seconds)
        return 0
    except KeyboardInterrupt:
        log.info("openbridge_interrupted")
        return 130
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
