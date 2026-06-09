from __future__ import annotations

from dataclasses import dataclass, field
import csv
import json
from pathlib import Path
from typing import Any


@dataclass
class RadioIdIndex:
    callsign_to_id: dict[str, int] = field(default_factory=dict)
    id_to_callsign: dict[int, str] = field(default_factory=dict)
    id_to_name: dict[int, str] = field(default_factory=dict)

    def lookup_callsign(self, callsign: str) -> int | None:
        return self.callsign_to_id.get(callsign.upper())

    def lookup_id(self, dmr_id: int) -> str | None:
        return self.id_to_callsign.get(int(dmr_id))

    def lookup_name(self, dmr_id: int) -> str | None:
        return self.id_to_name.get(int(dmr_id))

    @classmethod
    def from_file(cls, path: str | Path) -> "RadioIdIndex":
        p = Path(path)
        if not p.exists():
            return cls()
        if p.suffix.lower() == ".json":
            return cls.from_json(p)
        if p.suffix.lower() == ".csv":
            return cls.from_csv(p)
        raise ValueError(f"unsupported RadioID file type: {p}")

    @classmethod
    def from_json(cls, path: Path) -> "RadioIdIndex":
        data = json.loads(path.read_text())
        rows: list[dict[str, Any]]
        if isinstance(data, dict) and "users" in data:
            rows = data["users"]
        elif isinstance(data, list):
            rows = data
        else:
            rows = data.get("results", []) if isinstance(data, dict) else []
        idx = cls()
        for row in rows:
            callsign = str(row.get("callsign") or row.get("call") or "").upper().strip()
            raw_id = row.get("id") or row.get("radio_id") or row.get("dmr_id")
            name = str(row.get("name") or row.get("fname") or row.get("first_name") or "").strip()
            surname = str(row.get("surname") or row.get("lname") or row.get("last_name") or "").strip()
            full_name = " ".join(part for part in (name, surname) if part).strip()
            if callsign and raw_id:
                dmr_id = int(raw_id)
                idx.callsign_to_id[callsign] = dmr_id
                idx.id_to_callsign[dmr_id] = callsign
                if full_name:
                    idx.id_to_name[dmr_id] = full_name
        return idx

    @classmethod
    def from_csv(cls, path: Path) -> "RadioIdIndex":
        idx = cls()
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                callsign = str(row.get("callsign") or row.get("Callsign") or row.get("Call") or "").upper().strip()
                raw_id = row.get("id") or row.get("radio_id") or row.get("Radio ID") or row.get("RadioID")
                name = str(row.get("Name") or row.get("name") or row.get("First Name") or row.get("fname") or "").strip()
                surname = str(row.get("Surname") or row.get("Last Name") or row.get("lname") or "").strip()
                full_name = " ".join(part for part in (name, surname) if part).strip()
                if callsign and raw_id:
                    dmr_id = int(raw_id)
                    idx.callsign_to_id[callsign] = dmr_id
                    idx.id_to_callsign[dmr_id] = callsign
                    if full_name:
                        idx.id_to_name[dmr_id] = full_name
        return idx
