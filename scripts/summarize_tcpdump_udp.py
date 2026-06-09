#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict

LINE_RE = re.compile(
    r"^(?P<ts>\d+\.\d+)\s+IP6?\s+(?P<src>[^\s]+)\s+>\s+(?P<dst>[^:]+):.*UDP, length (?P<len>\d+)"
)

PORT_LABELS = {
    5198: "echolink_audio",
    5199: "echolink_control",
    54095: "openbridge_or_local_bind_54095",
    54096: "openbridge_or_local_bind_54096",
    2460: "ambeserver",
    2990: "md380emu",
}


def split_host_port(value: str) -> tuple[str, int | None]:
    value = value.rstrip(":")
    if "." not in value:
        return value, None
    host, port_text = value.rsplit(".", 1)
    try:
        return host, int(port_text)
    except ValueError:
        return value, None


def port_label(port: int | None) -> str:
    if port is None:
        return "unknown"
    if port in PORT_LABELS:
        return PORT_LABELS[port]
    if 23000 <= port <= 23199:
        return "analog_bridge_dynamic_range"
    return f"udp_{port}"


def main() -> int:
    flows: dict[str, dict[str, object]] = {}
    total = 0
    unmatched = 0
    for line in sys.stdin:
        m = LINE_RE.search(line)
        if not m:
            unmatched += 1
            continue
        total += 1
        ts = float(m.group("ts"))
        length = int(m.group("len"))
        src_host, src_port = split_host_port(m.group("src"))
        dst_host, dst_port = split_host_port(m.group("dst"))
        key = f"{src_host}:{src_port}->{dst_host}:{dst_port}"
        flow = flows.setdefault(
            key,
            {
                "src": src_host,
                "src_port": src_port,
                "src_label": port_label(src_port),
                "dst": dst_host,
                "dst_port": dst_port,
                "dst_label": port_label(dst_port),
                "count": 0,
                "lengths": [],
                "timestamps": [],
            },
        )
        flow["count"] = int(flow["count"]) + 1
        flow["lengths"].append(length)
        flow["timestamps"].append(ts)

    rendered = []
    for key, flow in flows.items():
        lengths = flow.pop("lengths")
        timestamps = flow.pop("timestamps")
        gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
        flow.update(
            {
                "flow": key,
                "first_ts": timestamps[0] if timestamps else None,
                "last_ts": timestamps[-1] if timestamps else None,
                "length_min": min(lengths) if lengths else None,
                "length_max": max(lengths) if lengths else None,
                "length_counts": {str(v): lengths.count(v) for v in sorted(set(lengths))},
                "gap_ms_avg": round(statistics.mean(gaps) * 1000, 3) if gaps else None,
                "gap_ms_max": round(max(gaps) * 1000, 3) if gaps else None,
            }
        )
        rendered.append(flow)
    rendered.sort(key=lambda f: (-int(f["count"]), str(f["flow"])))
    print(json.dumps({"udp_packets": total, "unmatched_lines": unmatched, "flows": rendered}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
