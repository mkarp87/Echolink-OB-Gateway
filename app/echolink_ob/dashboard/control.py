from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DashboardCommand:
    command_id: str
    action: str
    created_at: float
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "action": self.action,
            "created_at": self.created_at,
            "payload": self.payload,
        }


def append_command(path: str | Path, action: str, **payload: Any) -> DashboardCommand:
    cmd = DashboardCommand(
        command_id=str(uuid.uuid4()),
        action=action,
        created_at=time.time(),
        payload=payload,
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(cmd.to_dict(), sort_keys=True) + "\n")
    return cmd


def read_commands(path: str | Path) -> list[DashboardCommand]:
    p = Path(path)
    if not p.exists():
        return []
    commands: list[DashboardCommand] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            commands.append(
                DashboardCommand(
                    command_id=str(row.get("command_id", "")),
                    action=str(row.get("action", "")),
                    created_at=float(row.get("created_at", 0)),
                    payload=dict(row.get("payload", {})),
                )
            )
        except Exception:
            continue
    return commands


def append_unique_line(path: str | Path, value: str) -> bool:
    callsign = value.strip().upper()
    if not callsign:
        return False
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if p.exists():
        existing = [x.strip().upper() for x in p.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip() and not x.strip().startswith("#")]
    if callsign in existing:
        return False
    with p.open("a", encoding="utf-8") as f:
        f.write(callsign + "\n")
    return True

def remove_commands(path: str | Path, command_ids: set[str]) -> int:
    """Remove processed dashboard commands from the JSONL queue.

    The dashboard command file is an append-only handoff between the web UI and
    the long-running runtime.  Restart/reload commands must be treated as
    one-shot.  If a processed reload line remains in the file, a systemd restart
    reads it again and the service exits in a restart loop.
    """
    ids = {str(command_id) for command_id in command_ids if str(command_id)}
    if not ids:
        return 0
    p = Path(path)
    if not p.exists():
        return 0

    kept: list[str] = []
    removed = 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if str(row.get("command_id", "")) in ids:
                removed += 1
                continue
        except Exception:
            # Preserve malformed lines rather than silently discarding operator
            # data. read_commands() ignores them, but this function should only
            # remove commands that were positively identified as processed.
            pass
        kept.append(line)

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(("\n".join(kept) + ("\n" if kept else "")), encoding="utf-8")
    tmp.replace(p)
    return removed

