from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from echolink_ob.config import load_config
from echolink_ob.logging_setup import setup_logging
from .dmrd import DMRDPacket
from .test_sender import make_client

log = logging.getLogger(__name__)


@dataclass
class StreamStats:
    stream_id: int
    rf_source_id: int
    destination_id: int
    network_id: int
    slot: int
    call_type: str
    first_sequence: int
    first_seen_utc: str
    last_sequence: int
    last_seen_utc: str
    packets: int = 0
    payload_bytes: int = 0
    saw_possible_end: bool = False

    def update(self, packet: DMRDPacket, now: str) -> None:
        self.last_sequence = packet.sequence
        self.last_seen_utc = now
        self.packets += 1
        self.payload_bytes += len(packet.payload)
        # HBlink3 logs commonly show OpenBridge end packets as frame=2, dtype=2.
        # Treat this only as a stream-end hint; the recorder still keeps listening.
        if packet.frame_type == 2 and packet.dtype_vseq == 2:
            self.saw_possible_end = True

    def to_json(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "rf_source_id": self.rf_source_id,
            "destination_id": self.destination_id,
            "network_id": self.network_id,
            "slot": self.slot,
            "call_type": self.call_type,
            "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence,
            "first_seen_utc": self.first_seen_utc,
            "last_seen_utc": self.last_seen_utc,
            "packets": self.packets,
            "payload_bytes": self.payload_bytes,
            "saw_possible_end": self.saw_possible_end,
        }


@dataclass
class DMRStreamRecorder:
    output_dir: Path
    passphrase: bytes
    streams: dict[int, StreamStats] = field(default_factory=dict)
    total_packets: int = 0

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _base(self, stream_id: int) -> Path:
        return self.output_dir / f"stream-{stream_id}"

    def _append_bytes(self, path: Path, data: bytes) -> None:
        with path.open("ab") as fh:
            fh.write(data)

    def _append_jsonl(self, path: Path, obj: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, sort_keys=True) + "\n")

    def record_packet(self, packet: DMRDPacket) -> None:
        now = self._now()
        base = self._base(packet.stream_id)
        if packet.stream_id not in self.streams:
            self.streams[packet.stream_id] = StreamStats(
                stream_id=packet.stream_id,
                rf_source_id=packet.rf_source_id,
                destination_id=packet.destination_id,
                network_id=packet.network_id,
                slot=packet.slot,
                call_type=packet.call_type,
                first_sequence=packet.sequence,
                last_sequence=packet.sequence,
                first_seen_utc=now,
                last_seen_utc=now,
            )
            log.info(
                "dmr_record_stream_start stream=%s src=%s dst=%s slot=%s call=%s",
                packet.stream_id,
                packet.rf_source_id,
                packet.destination_id,
                packet.slot,
                packet.call_type,
            )

        stats = self.streams[packet.stream_id]
        stats.update(packet, now)
        self.total_packets += 1

        raw53 = packet.to_raw53()
        signed = packet.to_signed(self.passphrase)
        self._append_bytes(base.with_suffix(".dmrd"), raw53)
        self._append_bytes(base.with_suffix(".signed-dmrd"), signed)
        self._append_bytes(base.with_suffix(".payload33"), packet.payload)

        meta = {
            "time_utc": now,
            "sequence": packet.sequence,
            "rf_source_id": packet.rf_source_id,
            "destination_id": packet.destination_id,
            "network_id": packet.network_id,
            "slot": packet.slot,
            "call_type": packet.call_type,
            "frame_type": packet.frame_type,
            "dtype_vseq": packet.dtype_vseq,
            "stream_id": packet.stream_id,
            "payload_len": len(packet.payload),
            "payload_hex": packet.payload.hex(),
            "possible_stream_end": packet.frame_type == 2 and packet.dtype_vseq == 2,
        }
        self._append_jsonl(base.with_suffix(".jsonl"), meta)

        if meta["possible_stream_end"]:
            log.info(
                "dmr_record_stream_possible_end stream=%s packets=%s src=%s dst=%s",
                packet.stream_id,
                stats.packets,
                packet.rf_source_id,
                packet.destination_id,
            )

    def write_summary(self) -> Path:
        summary = {
            "created_utc": self._now(),
            "total_packets": self.total_packets,
            "stream_count": len(self.streams),
            "streams": [stream.to_json() for stream in self.streams.values()],
            "files": {
                "dmrd": "stream-<stream_id>.dmrd contains concatenated 53-byte DMRD bodies",
                "signed_dmrd": "stream-<stream_id>.signed-dmrd contains concatenated 73-byte signed OpenBridge packets",
                "payload33": "stream-<stream_id>.payload33 contains concatenated 33-byte DMR payloads",
                "jsonl": "stream-<stream_id>.jsonl contains packet metadata and payload hex",
            },
        }
        path = self.output_dir / "summary.json"
        path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Record validated inbound OpenBridge DMRD streams")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--output-dir", default="/opt/echolink-ob/diagnostics/dmr-captures")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    recorder = DMRStreamRecorder(Path(args.output_dir), cfg.openbridge.passphrase)
    client = make_client(cfg)
    try:
        log.info(
            "dmr_record_start seconds=%s output_dir=%s target=%s:%s tgid=%s slot=%s",
            args.seconds,
            args.output_dir,
            cfg.openbridge.host,
            cfg.openbridge.port,
            cfg.openbridge.fixed_tgid,
            cfg.openbridge.slot,
        )
        for packet in client.listen(args.seconds):
            recorder.record_packet(packet)
        summary = recorder.write_summary()
        log.info(
            "dmr_record_done packets=%s streams=%s summary=%s",
            recorder.total_packets,
            len(recorder.streams),
            summary,
        )
        return 0
    except KeyboardInterrupt:
        summary = recorder.write_summary()
        log.info(
            "dmr_record_interrupted packets=%s streams=%s summary=%s",
            recorder.total_packets,
            len(recorder.streams),
            summary,
        )
        return 130
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
