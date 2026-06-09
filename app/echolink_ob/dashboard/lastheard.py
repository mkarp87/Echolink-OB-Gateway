from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class LastHeardRecord:
    """Deduplicated last-heard/last-connect record keyed by callsign."""

    callsign: str
    dmr_id: int | None = None
    name: str = ""
    source: str = "unknown"
    first_seen_utc: str = ""
    last_connect_utc: str = ""
    last_tx_utc: str = ""
    last_disconnect_utc: str = ""
    last_seen_utc: str = ""
    last_event: str = "heard"
    connect_count: int = 0
    tx_count: int = 0

    @property
    def date(self) -> str:
        value = self.last_connect_utc or self.last_seen_utc
        return value[:10]

    @property
    def time(self) -> str:
        value = self.last_connect_utc or self.last_seen_utc
        return value[11:16]

    @property
    def last_tx_time(self) -> str:
        return self.last_tx_utc[11:19] if self.last_tx_utc else ""


class LastHeardStore:
    """Small JSON last-heard store used by the dashboard/status page.

    Stored records are deduplicated by callsign.  Repeated connects update the
    same row, while transmit/heard events update last_tx_utc and tx_count.
    """

    def __init__(self, path: str | Path, *, max_records: int = 250) -> None:
        self.path = Path(path)
        self.max_records = int(max_records)
        self._lock = threading.Lock()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _from_legacy(self, row: dict) -> LastHeardRecord:
        """Read both legacy append-only records and current deduplicated records."""
        callsign = str(row.get("callsign", "") or "UNKNOWN").upper().strip()
        dmr_id = int(row["dmr_id"]) if row.get("dmr_id") not in (None, "") else None
        if "last_seen_utc" in row or "last_connect_utc" in row:
            return LastHeardRecord(
                callsign=callsign,
                dmr_id=dmr_id,
                name=str(row.get("name", "")),
                source=str(row.get("source", "unknown")),
                first_seen_utc=str(row.get("first_seen_utc", "")),
                last_connect_utc=str(row.get("last_connect_utc", "")),
                last_tx_utc=str(row.get("last_tx_utc", "")),
                last_disconnect_utc=str(row.get("last_disconnect_utc", "")),
                last_seen_utc=str(row.get("last_seen_utc", "")),
                last_event=str(row.get("last_event", row.get("event", "heard"))),
                connect_count=int(row.get("connect_count", 0) or 0),
                tx_count=int(row.get("tx_count", 0) or 0),
            )
        when = str(row.get("when_utc", ""))
        event = str(row.get("event", "heard"))
        return LastHeardRecord(
            callsign=callsign,
            dmr_id=dmr_id,
            name=str(row.get("name", "")),
            source=str(row.get("source", "unknown")),
            first_seen_utc=when,
            last_connect_utc=when if event == "connected" else "",
            last_tx_utc=when if event in ("heard", "tx", "transmit") else "",
            last_disconnect_utc=when if event == "disconnected" else "",
            last_seen_utc=when,
            last_event=event,
            connect_count=1 if event == "connected" else 0,
            tx_count=1 if event in ("heard", "tx", "transmit") else 0,
        )

    def read(self) -> list[LastHeardRecord]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        rows = data.get("records", data if isinstance(data, list) else [])
        by_call: dict[str, LastHeardRecord] = {}
        for row in rows:
            try:
                rec = self._from_legacy(row)
            except Exception:
                continue
            if not rec.callsign:
                continue
            existing = by_call.get(rec.callsign)
            if existing is None:
                by_call[rec.callsign] = rec
                continue
            by_call[rec.callsign] = self._merge(existing, rec)
        return sorted(
            by_call.values(),
            key=lambda r: r.last_connect_utc or r.last_seen_utc or r.first_seen_utc,
            reverse=True,
        )

    def _merge(self, a: LastHeardRecord, b: LastHeardRecord) -> LastHeardRecord:
        def newest(*values: str) -> str:
            return max((v for v in values if v), default="")

        def oldest(*values: str) -> str:
            return min((v for v in values if v), default="")

        return LastHeardRecord(
            callsign=a.callsign,
            dmr_id=b.dmr_id if b.dmr_id is not None else a.dmr_id,
            name=b.name or a.name,
            source=b.source or a.source,
            first_seen_utc=oldest(a.first_seen_utc, b.first_seen_utc),
            last_connect_utc=newest(a.last_connect_utc, b.last_connect_utc),
            last_tx_utc=newest(a.last_tx_utc, b.last_tx_utc),
            last_disconnect_utc=newest(a.last_disconnect_utc, b.last_disconnect_utc),
            last_seen_utc=newest(a.last_seen_utc, b.last_seen_utc),
            last_event=b.last_event if (b.last_seen_utc >= a.last_seen_utc) else a.last_event,
            connect_count=a.connect_count + b.connect_count,
            tx_count=a.tx_count + b.tx_count,
        )

    def write(self, records: list[LastHeardRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        dedup: dict[str, LastHeardRecord] = {}
        for rec in records:
            if rec.callsign in dedup:
                dedup[rec.callsign] = self._merge(dedup[rec.callsign], rec)
            else:
                dedup[rec.callsign] = rec
        sorted_records = sorted(
            dedup.values(),
            key=lambda r: r.last_connect_utc or r.last_seen_utc or r.first_seen_utc,
            reverse=True,
        )[: self.max_records]
        payload = {"records": [asdict(r) for r in sorted_records]}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def record(
        self,
        *,
        callsign: str,
        dmr_id: int | None = None,
        name: str = "",
        source: str = "unknown",
        event: str = "heard",
    ) -> LastHeardRecord:
        callsign = (callsign or "UNKNOWN").upper().strip()
        now = self._now()
        with self._lock:
            records = {r.callsign: r for r in self.read()}
            prev = records.get(callsign)
            if prev is None:
                prev = LastHeardRecord(
                    callsign=callsign,
                    dmr_id=int(dmr_id) if dmr_id is not None else None,
                    name=name or "",
                    source=source,
                    first_seen_utc=now,
                )
            rec = LastHeardRecord(
                callsign=callsign,
                dmr_id=int(dmr_id) if dmr_id is not None else prev.dmr_id,
                name=name or prev.name,
                source=source or prev.source,
                first_seen_utc=prev.first_seen_utc or now,
                last_connect_utc=now if event == "connected" else prev.last_connect_utc,
                last_tx_utc=now if event in ("heard", "tx", "transmit") else prev.last_tx_utc,
                last_disconnect_utc=now if event == "disconnected" else prev.last_disconnect_utc,
                last_seen_utc=now,
                last_event=event,
                connect_count=prev.connect_count + (1 if event == "connected" else 0),
                tx_count=prev.tx_count + (1 if event in ("heard", "tx", "transmit") else 0),
            )
            records[callsign] = rec
            self.write(list(records.values()))
        return rec

    def recent_connections(self, limit: int = 20) -> list[LastHeardRecord]:
        return [r for r in self.read() if r.last_connect_utc][: int(limit)]
