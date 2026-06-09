from __future__ import annotations

import argparse
import json
from pathlib import Path

from echolink_ob.audio.pcm import generate_sine_pcm, split_frames
from echolink_ob.config import load_config
from echolink_ob.echolink.audio_router import EchoLinkConferenceAudioRouter
from echolink_ob.echolink.conference import EchoLinkConferenceManager
from echolink_ob.echolink.station import EchoLinkStation
from echolink_ob.identity.callsign import normalize_echolink_callsign
from echolink_ob.logging_setup import setup_logging


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run an offline EchoLink conference routing self-test")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--output-dir", default="/opt/echolink-ob/diagnostics/echolink-selftest")
    p.add_argument("--seconds", type=float, default=1.0)
    p.add_argument("--frequency", type=float, default=1000.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    conference = EchoLinkConferenceManager(max_stations=cfg.conference.max_stations)
    for callsign, dmr_id in (("K1ABC-L", 3101234), ("N2XYZ", 3105678), ("W3AAA-R", None)):
        conference.add_station(
            EchoLinkStation(
                callsign=callsign,
                normalized_callsign=normalize_echolink_callsign(callsign, cfg.identity.strip_suffixes),
                resolved_dmr_id=dmr_id,
                fallback_source_id_in_use=dmr_id is None,
            )
        )

    station_frames: dict[str, int] = {}
    gateway_frames: list[tuple[str, int | None, int]] = []

    def station_sink(recipient: str, frame: bytes) -> None:
        station_frames[recipient] = station_frames.get(recipient, 0) + 1

    def gateway_sink(frame: bytes, speaker: str, source_id: int | None) -> None:
        gateway_frames.append((speaker, source_id, len(frame)))

    router = EchoLinkConferenceAudioRouter(
        conference=conference,
        station_sink=station_sink,
        gateway_sink=gateway_sink,
    )

    pcm = generate_sine_pcm(seconds=args.seconds, frequency_hz=args.frequency, sample_rate=8000)
    frames = split_frames(pcm, sample_rate=8000, frame_ms=20)
    for frame in frames:
        router.speaker_pcm("K1ABC-L", frame, gateway_allowed=True)
    router.end_speaker("K1ABC-L")

    # Simulate decoded DMR audio from Analog_Bridge returning to EchoLink users.
    for frame in frames[:10]:
        router.dmr_pcm(frame, source_id=1234567, source_alias="1234567")

    report = {
        "conference_roster": conference.roster_text(),
        "speaker": "K1ABC-L",
        "speaker_frame_count": len(frames),
        "station_frames": station_frames,
        "gateway_frames": len(gateway_frames),
        "gateway_first_source_id": gateway_frames[0][1] if gateway_frames else None,
        "dmr_broadcast_frames": 10,
        "router_snapshot": router.snapshot(),
        "status": "ok",
        "note": "Offline protocol-neutral EchoLink conference routing test; this does not connect to the public EchoLink network.",
    }
    report_path = output_dir / "echolink-conference-selftest.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
