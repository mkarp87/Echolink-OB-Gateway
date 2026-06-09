from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Iterable

from echolink_ob.config import load_config
from echolink_ob.logging_setup import setup_logging
from echolink_ob.openbridge.analyzer import is_voice_payload, read_capture
from echolink_ob.openbridge.dmrd import DMRDPacket, DMR_PAYLOAD_LEN

log = logging.getLogger(__name__)

BITS_PER_DMR_BURST = 264
BITS_PER_SIDE_VOICE = 108
BITS_PER_CENTER_SYNC_OR_EMB = 48
BITS_PER_DMR_AMBE_WITH_FEC = 72
BYTES_PER_DMR_AMBE_WITH_FEC = 9
AMBE_FRAMES_PER_DMR_BURST = 3
VOICE_BITS_PER_DMR_BURST = BITS_PER_DMR_AMBE_WITH_FEC * AMBE_FRAMES_PER_DMR_BURST


@dataclass(frozen=True)
class AMBE72Frame:
    stream_id: int
    packet_sequence: int
    packet_frame_type: int
    packet_dtype_vseq: int
    packet_index: int
    frame_index_in_packet: int
    frame_index_in_stream: int
    data: bytes

    def to_json(self) -> dict[str, int | str]:
        return {
            "stream_id": self.stream_id,
            "packet_sequence": self.packet_sequence,
            "packet_frame_type": self.packet_frame_type,
            "packet_dtype_vseq": self.packet_dtype_vseq,
            "packet_index": self.packet_index,
            "frame_index_in_packet": self.frame_index_in_packet,
            "frame_index_in_stream": self.frame_index_in_stream,
            "bytes": len(self.data),
            "ambe72_hex": self.data.hex(),
        }


def bytes_to_bits(data: bytes) -> list[int]:
    bits: list[int] = []
    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    return bits


def bits_to_bytes(bits: Iterable[int]) -> bytes:
    bit_list = list(bits)
    if len(bit_list) % 8:
        raise ValueError("bit length must be a multiple of 8")
    out = bytearray()
    for offset in range(0, len(bit_list), 8):
        value = 0
        for bit in bit_list[offset : offset + 8]:
            value = (value << 1) | (1 if bit else 0)
        out.append(value)
    return bytes(out)


def extract_voice_bits_from_payload33(payload: bytes) -> list[int]:
    """Return the 216 voice/FEC bits from a 33-byte DMR burst payload.

    A DMR burst is 264 bits. For voice bursts, the first 108 bits and final
    108 bits are voice/FEC data. The middle 48 bits are sync or embedded
    signalling and are not passed to the AMBE decoder.
    """

    if len(payload) != DMR_PAYLOAD_LEN:
        raise ValueError(f"DMR burst payload must be {DMR_PAYLOAD_LEN} bytes")
    bits = bytes_to_bits(payload)
    if len(bits) != BITS_PER_DMR_BURST:
        raise ValueError("unexpected DMR burst bit length")
    return bits[:BITS_PER_SIDE_VOICE] + bits[BITS_PER_SIDE_VOICE + BITS_PER_CENTER_SYNC_OR_EMB :]


def extract_ambe72_from_payload33(payload: bytes) -> list[bytes]:
    """Extract three 9-byte DMR AMBE+FEC frames from a 33-byte voice burst.

    The returned 9-byte blocks are DMR AMBE+FEC channel frames. They are not
    8 kHz PCM. They still require a DMR-capable AMBE decoder or AMBEServer
    path configured for DMR AMBE+FEC frames.
    """

    voice_bits = extract_voice_bits_from_payload33(payload)
    if len(voice_bits) != VOICE_BITS_PER_DMR_BURST:
        raise ValueError("unexpected voice bit length")
    frames: list[bytes] = []
    for offset in range(0, VOICE_BITS_PER_DMR_BURST, BITS_PER_DMR_AMBE_WITH_FEC):
        frames.append(bits_to_bytes(voice_bits[offset : offset + BITS_PER_DMR_AMBE_WITH_FEC]))
    if len(frames) != AMBE_FRAMES_PER_DMR_BURST:
        raise ValueError("unexpected AMBE frame count")
    return frames


def build_payload33_from_ambe72_triplet(frames: Iterable[bytes], center48: bytes = b"\x00" * 6) -> bytes:
    """Build one 33-byte DMR voice burst payload from three 9-byte AMBE72 frames.

    This is the inverse of :func:`extract_ambe72_from_payload33` for the two
    108-bit voice/FEC regions. The caller supplies the 48-bit center sync or
    embedded-signalling region, normally copied from a known-good template
    burst. This function does not synthesize DMR link-control/FEC on its own.
    """

    frame_list = list(frames)
    if len(frame_list) != AMBE_FRAMES_PER_DMR_BURST:
        raise ValueError(f"expected {AMBE_FRAMES_PER_DMR_BURST} AMBE72 frames")
    if any(len(frame) != BYTES_PER_DMR_AMBE_WITH_FEC for frame in frame_list):
        raise ValueError(f"each AMBE72 frame must be {BYTES_PER_DMR_AMBE_WITH_FEC} bytes")
    if len(center48) != 6:
        raise ValueError("center48 must be exactly 6 bytes")
    voice_bits = bytes_to_bits(b"".join(frame_list))
    if len(voice_bits) != VOICE_BITS_PER_DMR_BURST:
        raise ValueError("unexpected voice bit length")
    center_bits = bytes_to_bits(center48)
    burst_bits = (
        voice_bits[:BITS_PER_SIDE_VOICE]
        + center_bits
        + voice_bits[BITS_PER_SIDE_VOICE:]
    )
    payload = bits_to_bytes(burst_bits)
    if len(payload) != DMR_PAYLOAD_LEN:
        raise ValueError("unexpected rebuilt DMR payload length")
    return payload


def extract_center48_from_payload33(payload: bytes) -> bytes:
    """Return the 48-bit center sync/embedded-signalling area from a DMR burst."""

    if len(payload) != DMR_PAYLOAD_LEN:
        raise ValueError(f"DMR burst payload must be {DMR_PAYLOAD_LEN} bytes")
    bits = bytes_to_bits(payload)
    center = bits[BITS_PER_SIDE_VOICE : BITS_PER_SIDE_VOICE + BITS_PER_CENTER_SYNC_OR_EMB]
    return bits_to_bytes(center)


def extract_ambe72_from_packets(packets: Iterable[DMRDPacket]) -> list[AMBE72Frame]:
    frames: list[AMBE72Frame] = []
    stream_counters: dict[int, int] = {}
    for packet_index, pkt in enumerate(packets):
        if not is_voice_payload(pkt):
            continue
        next_index = stream_counters.get(pkt.stream_id, 0)
        for frame_index_in_packet, frame_bytes in enumerate(extract_ambe72_from_payload33(pkt.payload)):
            frames.append(
                AMBE72Frame(
                    stream_id=pkt.stream_id,
                    packet_sequence=pkt.sequence,
                    packet_frame_type=pkt.frame_type,
                    packet_dtype_vseq=pkt.dtype_vseq,
                    packet_index=packet_index,
                    frame_index_in_packet=frame_index_in_packet,
                    frame_index_in_stream=next_index,
                    data=frame_bytes,
                )
            )
            next_index += 1
        stream_counters[pkt.stream_id] = next_index
    return frames


def write_ambe72_outputs(packets: Iterable[DMRDPacket], output_dir: Path) -> dict[int, dict[str, int | str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = extract_ambe72_from_packets(packets)
    by_stream: dict[int, list[AMBE72Frame]] = {}
    for frame in frames:
        by_stream.setdefault(frame.stream_id, []).append(frame)

    outputs: dict[int, dict[str, int | str]] = {}
    for stream_id, stream_frames in sorted(by_stream.items()):
        base = output_dir / f"stream-{stream_id}"
        ambe_path = base.with_suffix(".ambe72")
        meta_path = base.with_suffix(".ambe72.jsonl")
        with ambe_path.open("wb") as ambe_fh, meta_path.open("w", encoding="utf-8") as meta_fh:
            for frame in stream_frames:
                ambe_fh.write(frame.data)
                meta_fh.write(json.dumps(frame.to_json(), sort_keys=True) + "\n")
        outputs[stream_id] = {
            "ambe72": str(ambe_path),
            "ambe72_jsonl": str(meta_path),
            "frames": len(stream_frames),
            "bytes": ambe_path.stat().st_size,
            "duration_ms_estimate": len(stream_frames) * 20,
        }
    return outputs


def build_extract_report(packets: list[DMRDPacket], outputs: dict[int, dict[str, int | str]]) -> dict[str, object]:
    voice_packets = [p for p in packets if is_voice_payload(p)]
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "packet_count": len(packets),
        "voice_payload_packets": len(voice_packets),
        "ambe72_frame_count": len(voice_packets) * AMBE_FRAMES_PER_DMR_BURST,
        "estimated_audio_ms": len(voice_packets) * AMBE_FRAMES_PER_DMR_BURST * 20,
        "streams": outputs,
        "notes": [
            "Each DMR 33-byte voice burst produced three 9-byte AMBE+FEC frames.",
            "The .ambe72 output is compressed DMR AMBE+FEC data, not PCM audio.",
            "Decoding requires a DMR-capable AMBE/DVSI backend or validated md380/mbelib path.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract DMR AMBE+FEC frames from recorded OpenBridge DMRD captures")
    p.add_argument("capture", help="Input .dmrd, .signed-dmrd, or .jsonl capture file")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--output-dir", default="/opt/echolink-ob/diagnostics/ambe-extract")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)

    capture_path = Path(args.capture)
    output_dir = Path(args.output_dir)
    packets = read_capture(capture_path, cfg.openbridge.passphrase)
    outputs = write_ambe72_outputs(packets, output_dir)
    report = build_extract_report(packets, outputs)
    report_path = output_dir / f"{capture_path.stem}-ambe72-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info(
        "ambe_extract_done capture=%s packets=%s frames=%s report=%s",
        capture_path,
        report["packet_count"],
        report["ambe72_frame_count"],
        report_path,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"ambe72_report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
