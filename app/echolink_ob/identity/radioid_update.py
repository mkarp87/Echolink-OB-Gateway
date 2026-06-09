from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request

from echolink_ob.config import load_config
from echolink_ob.identity.radioid import RadioIdIndex

DEFAULT_USERS_JSON_URL = "https://radioid.net/static/users.json"
DEFAULT_USER_CSV_URL = "https://radioid.net/static/user.csv"


@dataclass(frozen=True)
class DownloadResult:
    ok: bool
    path: str
    url: str
    bytes: int
    records: int
    error: str = ""
    timestamp_utc: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "path": self.path,
            "url": self.url,
            "bytes": self.bytes,
            "records": self.records,
            "error": self.error,
            "timestamp_utc": self.timestamp_utc,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _download_url(url: str, target: Path, timeout: int = 120) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "echolink-ob RadioID updater",
                "Accept": "application/json,text/csv,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
        size = tmp.stat().st_size
        if size < 1024:
            sample = tmp.read_bytes()[:200]
            raise RuntimeError(f"downloaded file too small ({size} bytes): {sample!r}")
        tmp.replace(target)
        return size
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def count_radioid_records(path: Path) -> int:
    idx = RadioIdIndex.from_file(path)
    return len(idx.callsign_to_id)


def download_radioid_database(
    path: str | Path,
    url: str | None = None,
    fallback_url: str | None = None,
    timeout: int = 120,
    force: bool = False,
) -> DownloadResult:
    target = Path(path)
    if target.exists() and not force:
        try:
            return DownloadResult(
                ok=True,
                path=str(target),
                url="existing-file",
                bytes=target.stat().st_size,
                records=count_radioid_records(target),
                timestamp_utc=_utc_now(),
            )
        except Exception as exc:
            return DownloadResult(False, str(target), "existing-file", target.stat().st_size, 0, str(exc), _utc_now())

    primary = url or (DEFAULT_USERS_JSON_URL if target.suffix.lower() == ".json" else DEFAULT_USER_CSV_URL)
    backup = fallback_url
    last_error = ""
    for candidate in [primary, backup]:
        if not candidate:
            continue
        try:
            size = _download_url(candidate, target, timeout=timeout)
            records = count_radioid_records(target)
            if records <= 0:
                raise RuntimeError("downloaded RadioID file parsed successfully but contained zero records")
            meta = {
                "downloaded_utc": _utc_now(),
                "url": candidate,
                "path": str(target),
                "bytes": size,
                "records": records,
            }
            target.with_suffix(target.suffix + ".meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
            return DownloadResult(True, str(target), candidate, size, records, timestamp_utc=meta["downloaded_utc"])
        except Exception as exc:  # try fallback if configured
            last_error = str(exc)
    return DownloadResult(False, str(target), primary, 0, 0, last_error, _utc_now())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download/update local RadioID database file")
    parser.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    parser.add_argument("--output", default=None, help="Override output path. Defaults to [identity].radioid_file")
    parser.add_argument("--url", default=None, help="Override primary download URL")
    parser.add_argument("--fallback-url", default=None, help="Optional fallback download URL")
    parser.add_argument("--force", action="store_true", help="Replace existing file")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--status", action="store_true", help="Only report current local file status")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    path = Path(args.output or cfg.identity.radioid_file)

    if args.status:
        if not path.exists():
            result = DownloadResult(False, str(path), "local", 0, 0, "file does not exist", _utc_now())
        else:
            try:
                result = DownloadResult(True, str(path), "local", path.stat().st_size, count_radioid_records(path), timestamp_utc=_utc_now())
            except Exception as exc:
                result = DownloadResult(False, str(path), "local", path.stat().st_size, 0, str(exc), _utc_now())
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return 0 if result.ok else 1

    url = args.url or getattr(cfg.identity, "radioid_url", DEFAULT_USERS_JSON_URL)
    fallback_url = args.fallback_url or getattr(cfg.identity, "radioid_fallback_url", None)
    result = download_radioid_database(path, url=url, fallback_url=fallback_url, timeout=args.timeout, force=args.force)
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
