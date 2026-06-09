from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from echolink_ob.config import load_config
from echolink_ob.logging_setup import setup_logging
from .dmrd import DMRDPacket, DMRD_BODY_LEN, DMRD_SIGNED_LEN

log = logging.getLogger(__name__)

VOICE_FRAME_TYPES = {0, 1}
VOICE_SYNC_FRAME_TYPE = 1
VOICE_BURST_FRAME_TYPE = 0
DATA_SYNC_FRAME_TYPE = 2
VOICE_TERMINATOR_DTYPE = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_dmrd_file(path: Path) -> list[DMRDPacket]:
    data = path.read_bytes()
    if len(data) % DMRD_BODY_LEN != 0:
        raise ValueError(f"{path} length {len(data)} is not a multiple of {DMRD_BODY_LEN}")
    packets: list[DMRDPacket] = []
    for offset in range(0, len(data), DMRD_BODY_LEN):
        packets.append(DMRDPacket.from_raw53(data[offset : offset + DMRD_BODY_LEN]))
    return packets


def read_signed_dmrd_file(path: Path, passphrase: bytes) -> list[DMRDPacket]:
    data = path.read_bytes()
    if len(data) % DMRD_SIGNED_LEN != 0:
        raise ValueError(f"{path} length {len(data)} is not a multiple of {DMRD_SIGNED_LEN}")
    packets: list[DMRDPacket] = []
    for offset in range(0, len(data), DMRD_SIGNED_LEN):
        packets.append(DMRDPacket.from_signed(data[offset : offset + DMRD_SIGNED_LEN], passphrase))
    return packets


def read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            row["_line_no"] = line_no
            rows.append(row)
    return rows


def packets_from_jsonl(path: Path) -> list[DMRDPacket]:
    packets: list[DMRDPacket] = []
    for row in read_jsonl_file(path):
        packets.append(
            DMRDPacket(
                sequence=int(row["sequence"]),
                rf_source_id=int(row["rf_source_id"]),
                destination_id=int(row["destination_id"]),
                network_id=int(row["network_id"]),
                slot=int(row["slot"]),
                call_type=str(row["call_type"]),
                frame_type=int(row["frame_type"]),
                dtype_vseq=int(row["dtype_vseq"]),
                stream_id=int(row["stream_id"]),
                payload=bytes.fromhex(str(row["payload_hex"])),
            )
        )
    return packets


def read_capture(path: Path, passphrase: bytes | None = None) -> list[DMRDPacket]:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix == ".jsonl":
        return packets_from_jsonl(path)
    if name.endswith(".signed-dmrd"):
        if passphrase is None:
            raise ValueError("signed-dmrd input requires a passphrase")
        return read_signed_dmrd_file(path, passphrase)
    if suffix == ".dmrd":
        return read_dmrd_file(path)
    if suffix == ".payload33":
        raise ValueError("payload33 files do not contain DMRD metadata; use .dmrd or .jsonl")
    raise ValueError(f"unsupported capture type: {path}")


def is_possible_stream_end(packet: DMRDPacket) -> bool:
    return packet.frame_type == DATA_SYNC_FRAME_TYPE and packet.dtype_vseq == VOICE_TERMINATOR_DTYPE


def is_voice_payload(packet: DMRDPacket) -> bool:
    return packet.frame_type in VOICE_FRAME_TYPES


def _sequence_gaps(packets: list[DMRDPacket]) -> list[dict[str, int]]:
    gaps: list[dict[str, int]] = []
    for prev, cur in zip(packets, packets[1:]):
        expected = (prev.sequence + 1) & 0xFF
        if cur.sequence != expected:
            gaps.append(
                {
                    "after_sequence": prev.sequence,
                    "next_sequence": cur.sequence,
                    "expected_sequence": expected,
                }
            )
    return gaps


def _voice_cycle_summary(packets: list[DMRDPacket]) -> dict[str, Any]:
    cycles: list[list[DMRDPacket]] = []
    current: list[DMRDPacket] = []
    for pkt in packets:
        if pkt.frame_type == VOICE_SYNC_FRAME_TYPE and pkt.dtype_vseq == 0:
            if current:
                cycles.append(current)
            current = [pkt]
        elif current and pkt.frame_type == VOICE_BURST_FRAME_TYPE and pkt.dtype_vseq in range(1, 6):
            current.append(pkt)
        elif current:
            cycles.append(current)
            current = []
    if current:
        cycles.append(current)

    complete = [c for c in cycles if len(c) == 6 and [p.dtype_vseq for p in c] == [0, 1, 2, 3, 4, 5]]
    incomplete = [c for c in cycles if c not in complete]
    return {
        "cycles_detected": len(cycles),
        "complete_cycles": len(complete),
        "incomplete_cycles": len(incomplete),
        "cycle_lengths": [len(c) for c in cycles],
        "cycle_dtype_sequences": [[p.dtype_vseq for p in c] for c in cycles],
    }


@dataclass
class CaptureAnalysis:
    packets: list[DMRDPacket]
    created_utc: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        if not self.packets:
            return {
                "created_utc": self.created_utc,
                "packet_count": 0,
                "streams": [],
            }

        by_stream: dict[int, list[DMRDPacket]] = defaultdict(list)
        for pkt in self.packets:
            by_stream[pkt.stream_id].append(pkt)

        streams: list[dict[str, Any]] = []
        for stream_id, packets in sorted(by_stream.items()):
            frame_counts = Counter((p.frame_type, p.dtype_vseq) for p in packets)
            voice_packets = [p for p in packets if is_voice_payload(p)]
            first = packets[0]
            streams.append(
                {
                    "stream_id": stream_id,
                    "rf_source_id": first.rf_source_id,
                    "destination_id": first.destination_id,
                    "network_id": first.network_id,
                    "slot": first.slot,
                    "call_type": first.call_type,
                    "packet_count": len(packets),
                    "first_sequence": first.sequence,
                    "last_sequence": packets[-1].sequence,
                    "sequence_gaps": _sequence_gaps(packets),
                    "saw_possible_end": any(is_possible_stream_end(p) for p in packets),
                    "payload_bytes": sum(len(p.payload) for p in packets),
                    "voice_payload_packets": len(voice_packets),
                    "voice_payload_bytes": sum(len(p.payload) for p in voice_packets),
                    "frame_type_dtype_counts": [
                        {"frame_type": k[0], "dtype_vseq": k[1], "count": v}
                        for k, v in sorted(frame_counts.items())
                    ],
                    "voice_cycle_summary": _voice_cycle_summary(packets),
                }
            )

        return {
            "created_utc": self.created_utc,
            "packet_count": len(self.packets),
            "stream_count": len(by_stream),
            "streams": streams,
            "notes": [
                "voice33 files contain raw 33-byte DMR voice burst payloads, not decoded PCM",
                "AMBE extraction/deinterleaving requires a DMR AMBE/FEC implementation before PCM decode",
            ],
        }


def write_voice33_outputs(packets: Iterable[DMRDPacket], output_dir: Path) -> dict[int, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    by_stream: dict[int, list[DMRDPacket]] = defaultdict(list)
    for pkt in packets:
        if is_voice_payload(pkt):
            by_stream[pkt.stream_id].append(pkt)

    outputs: dict[int, dict[str, Any]] = {}
    for stream_id, stream_packets in sorted(by_stream.items()):
        base = output_dir / f"stream-{stream_id}"
        voice33_path = base.with_suffix(".voice33")
        meta_path = base.with_suffix(".voice33.jsonl")
        with voice33_path.open("wb") as voice_fh, meta_path.open("w", encoding="utf-8") as meta_fh:
            for pkt in stream_packets:
                voice_fh.write(pkt.payload)
                meta_fh.write(
                    json.dumps(
                        {
                            "sequence": pkt.sequence,
                            "frame_type": pkt.frame_type,
                            "dtype_vseq": pkt.dtype_vseq,
                            "stream_id": pkt.stream_id,
                            "payload_hex": pkt.payload.hex(),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
        outputs[stream_id] = {
            "voice33": str(voice33_path),
            "voice33_jsonl": str(meta_path),
            "packets": len(stream_packets),
            "bytes": voice33_path.stat().st_size,
        }
    return outputs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Analyze recorded OpenBridge DMRD captures")
    p.add_argument("capture", help="Input .dmrd, .signed-dmrd, or .jsonl capture file")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--output-dir", default="/opt/echolink-ob/diagnostics/dmr-analysis")
    p.add_argument("--write-voice33", action="store_true", help="write raw 33-byte voice burst files")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    capture_path = Path(args.capture)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    packets = read_capture(capture_path, cfg.openbridge.passphrase)
    analysis = CaptureAnalysis(packets)
    report = analysis.to_dict()
    if args.write_voice33:
        report["voice33_outputs"] = write_voice33_outputs(packets, output_dir)

    report_path = output_dir / f"{capture_path.stem}-analysis.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info(
        "dmr_analyze_done capture=%s packets=%s streams=%s report=%s",
        capture_path,
        report.get("packet_count"),
        report.get("stream_count"),
        report_path,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"analysis_report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
