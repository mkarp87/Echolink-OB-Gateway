from __future__ import annotations

import argparse
import html
import json
import logging
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from echolink_ob.config import AppConfig, load_config
from echolink_ob.dashboard.control import append_command, append_unique_line
from echolink_ob.dashboard.lastheard import LastHeardStore
from echolink_ob.logging_setup import setup_logging

log = logging.getLogger(__name__)


def _read_json(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_patterns(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[str] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        item = line.strip()
        if item and not item.startswith("#"):
            out.append(item.upper())
    return out


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


class DashboardRenderer:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.last_heard = LastHeardStore(cfg.dashboard.last_heard_file, max_records=max(250, cfg.dashboard.last_heard_limit))

    def status_payload(self) -> dict[str, Any]:
        full = _read_json("/opt/echolink-ob/logs/full-status.json") or {}
        bridge = _read_json("/opt/echolink-ob/logs/bridge-status.json") or {}
        echolink = _read_json("/opt/echolink-ob/logs/echolink-status.json") or {}
        last_connected = [r.__dict__ for r in self.last_heard.recent_connections(20)]
        banlist = _read_patterns(self.cfg.access.banlist_file)
        return {
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "full": full,
            "bridge": bridge,
            "echolink": echolink,
            "last_connected": last_connected,
            "banlist": banlist,
        }

    def render(self) -> str:
        payload = self.status_payload()
        full = payload.get("full") or {}
        bridge = payload.get("bridge") or full.get("bridge") or {}
        echolink = payload.get("echolink") or full.get("echolink") or {}
        directory = full.get("directory") or {}
        analog = full.get("analog_bridge") or {}
        md380emu = full.get("md380emu") or {}
        records = self.last_heard.recent_connections(20)

        full_echolink = full.get("echolink") or {}
        active = echolink.get("active_speaker") if echolink.get("active_speaker") is not None else full_echolink.get("active_speaker")
        stations = echolink.get("connected_stations")
        if stations is None:
            stations = full_echolink.get("connected_stations", 0)
        station_rows = echolink.get("stations")
        if station_rows is None:
            station_rows = full_echolink.get("stations", [])
        bridge_stats = bridge.get("stats") or {}
        echolink_stats = echolink.get("stats") or (full.get("echolink") or {}).get("stats") or {}

        last_rows = []
        for rec in records:
            cs = _esc(rec.callsign)
            if rec.dmr_id is not None:
                cs = f'{cs} <small>({rec.dmr_id})</small>'
            last_rows.append(
                "<tr>"
                f"<td>{_esc(rec.date)}</td>"
                f"<td>{_esc(rec.time)}</td>"
                f"<td class='call'>{cs}</td>"
                f"<td>{_esc(rec.name)}</td>"
                f"<td>{_esc(rec.last_tx_utc.replace('T', ' ').replace('Z', '') if rec.last_tx_utc else '')}</td>"
                f"<td>{_esc(rec.last_disconnect_utc.replace('T', ' ').replace('Z', '') if rec.last_disconnect_utc else '')}</td>"
                f"<td>{_esc(rec.last_event)}</td>"
                f"<td>{_esc(rec.connect_count)}</td>"
                "</tr>"
            )
        if not last_rows:
            last_rows.append("<tr><td colspan='8'>No EchoLink connections logged yet.</td></tr>")

        connected_rows = []
        for st in station_rows:
            callsign = str(st.get("callsign", ""))
            connected_rows.append(
                "<tr>"
                f"<td class='call'>{_esc(callsign)}</td>"
                f"<td>{_esc(st.get('name', ''))}</td>"
                f"<td>{_esc(st.get('client', ''))}</td>"
                f"<td>{_esc(st.get('dmr_id', 'fallback'))}</td>"
                f"<td>{_esc(st.get('connected_seconds', ''))}</td>"
                f"<td>{_esc(st.get('idle_seconds', ''))}</td>"
                "<td>"
                f"<button onclick=\"disconnectStation('{_esc(callsign)}')\">Disconnect</button> "
                f"<button onclick=\"blockStation('{_esc(callsign)}')\">Block</button>"
                "</td>"
                "</tr>"
            )
        if not connected_rows:
            connected_rows.append("<tr><td colspan='7'>No connected EchoLink stations.</td></tr>")

        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        return f"""<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{_esc(self.cfg.echolink.callsign)} Status</title>
<style>
body {{ font-family: Arial, sans-serif; background:#d0d0d0; margin:0; padding:18px; color:#111; }}
.panel {{ background:#f7f7f7; border:1px solid #aaa; border-radius:8px; padding:12px; margin:0 0 14px; box-shadow:0 1px 2px #999; }}
h1 {{ margin:0 0 10px; font-size:22px; }}
h2 {{ margin:0 0 8px; font-size:16px; }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:10px; }}
.metric {{ background:white; border:1px solid #bbb; border-radius:6px; padding:8px; }}
.metric b {{ display:block; color:#164e7a; font-size:13px; }}
table {{ border-collapse:collapse; width:100%; background:white; }}
th {{ background:#2f6da5; color:white; padding:6px; font-size:14px; }}
td {{ border:1px solid #ccc; padding:5px 7px; font-size:14px; }}
tr:nth-child(even) {{ background:#eee; }}
.call {{ color:#004cc9; font-weight:bold; }}
small {{ color:#333; font-size:11px; }}
button {{ padding:4px 8px; }}
.footer {{ font-size:12px; color:#555; }}
#notice {{ margin:8px 0; color:#063; font-weight:bold; }}
</style>
</head>
<body>
<div class='panel'>
<h1>{_esc(self.cfg.echolink.callsign)} EchoLink/OpenBridge Status</h1>
<div id='notice'></div>
<div class='grid'>
<div class='metric'><b>EchoLink stations</b><span id='m-stations'>{_esc(stations)}</span></div>
<div class='metric'><b>Active speaker</b><span id='m-active'>{_esc(active or 'None')}</span></div>
<div class='metric'><b>Directory online</b><span id='m-directory'>{_esc(directory.get('online_ok', False))}</span></div>
<div class='metric'><b>Analog_Bridge</b><span id='m-analog'>{_esc('running' if analog.get('running') else 'unknown/stopped')}</span></div>
<div class='metric'><b>md380-emu</b><span id='m-md380'>{_esc('running' if md380emu.get('running') else 'disabled/stopped')}</span></div>
<div class='metric'><b>OpenBridge sent / received</b><span id='m-ob'>{_esc(bridge_stats.get('openbridge_packets_sent', 0))} / {_esc(bridge_stats.get('openbridge_packets_received', 0))}</span></div>
<div class='metric'><b>EchoLink GSM rx / tx</b><span id='m-gsm'>{_esc(echolink_stats.get('gsm_packets_decoded', 0))} / {_esc(echolink_stats.get('gsm_packets_sent', 0))}</span></div>
</div>
</div>
<div class='panel'>
<h2>Connected EchoLink Stations</h2>
<table>
<thead><tr><th>Callsign</th><th>Name</th><th>Client</th><th>DMR ID</th><th>Connected Seconds</th><th>Idle Seconds</th><th>Actions</th></tr></thead>
<tbody id='connected-body'>{''.join(connected_rows)}</tbody>
</table>
</div>
<div class='panel'>
<h2>Last 20 Connected Stations</h2>
<table>
<thead><tr><th>Date</th><th>Time</th><th>Callsign (DMR-Id)</th><th>Name</th><th>Last TX UTC</th><th>Last Disconnect UTC</th><th>Last Event</th><th>Connects</th></tr></thead>
<tbody id='lastheard-body'>{''.join(last_rows)}</tbody>
</table>
</div>
<div class='panel'>
<h2>Admin Controls</h2>
<p>
<input id='block-callsign' placeholder='Callsign to block'>
<button onclick='blockStation(document.getElementById("block-callsign").value)'>Block callsign</button>
<button onclick='reloadApp()'>Reload app</button>
</p>
<p>Banlist file: <code>{_esc(self.cfg.access.banlist_file)}</code></p>
</div>
<div class='footer'>Generated {generated}. Live updates via <code>/api/events</code>. JSON: <a href='/api/status'>/api/status</a></div>
<script>
function esc(v) {{ return String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
async function post(path, data) {{
  const r = await fetch(path, {{method:'POST', headers:{{'Content-Type':'application/x-www-form-urlencoded'}}, body:new URLSearchParams(data)}});
  const j = await r.json();
  document.getElementById('notice').textContent = j.message || j.status || 'ok';
}}
function disconnectStation(callsign) {{ if (callsign) post('/api/disconnect', {{callsign}}); }}
function blockStation(callsign) {{ if (callsign) post('/api/block', {{callsign}}); }}
function reloadApp() {{ if (confirm('Reload/restart echolink-ob?')) post('/api/reload', {{}}); }}
function update(payload) {{
  const full = payload.full || {{}};
  const echolink = payload.echolink || full.echolink || {{}};
  const bridge = payload.bridge || full.bridge || {{}};
  const directory = full.directory || {{}};
  const analog = full.analog_bridge || {{}};
  const md380 = full.md380emu || {{}};
  const bs = bridge.stats || {{}};
  const es = echolink.stats || {{}};
  document.getElementById('m-stations').textContent = echolink.connected_stations ?? 0;
  document.getElementById('m-active').textContent = echolink.active_speaker || 'None';
  document.getElementById('m-directory').textContent = directory.online_ok ?? false;
  document.getElementById('m-analog').textContent = analog.running ? 'running' : 'unknown/stopped';
  document.getElementById('m-md380').textContent = md380.running ? (md380.started_by_app ? 'app-managed' : 'running/reused') : 'disabled/stopped';
  document.getElementById('m-ob').textContent = `${{bs.openbridge_packets_sent || 0}} / ${{bs.openbridge_packets_received || 0}}`;
  document.getElementById('m-gsm').textContent = `${{es.gsm_packets_decoded || 0}} / ${{es.gsm_packets_sent || 0}}`;
  const stations = echolink.stations || [];
  document.getElementById('connected-body').innerHTML = stations.length ? stations.map(st => `
    <tr><td class='call'>${{esc(st.callsign)}}</td><td>${{esc(st.name)}}</td><td>${{esc(st.client)}}</td><td>${{esc(st.dmr_id ?? 'fallback')}}</td><td>${{esc(st.connected_seconds)}}</td><td>${{esc(st.idle_seconds ?? '')}}</td><td><button onclick="disconnectStation('${{esc(st.callsign)}}')">Disconnect</button> <button onclick="blockStation('${{esc(st.callsign)}}')">Block</button></td></tr>`).join('') : '<tr><td colspan="7">No connected EchoLink stations.</td></tr>';
  const lh = payload.last_connected || [];
  document.getElementById('lastheard-body').innerHTML = lh.length ? lh.map(r => {{
    const when = r.last_connect_utc || r.last_seen_utc || '';
    const date = when.slice(0,10); const time = when.slice(11,16);
    const cs = `${{esc(r.callsign)}} ${{r.dmr_id ? '<small>(' + esc(r.dmr_id) + ')</small>' : ''}}`;
    return `<tr><td>${{esc(date)}}</td><td>${{esc(time)}}</td><td class='call'>${{cs}}</td><td>${{esc(r.name)}}</td><td>${{esc((r.last_tx_utc || '').replace('T',' ').replace('Z',''))}}</td><td>${{esc((r.last_disconnect_utc || '').replace('T',' ').replace('Z',''))}}</td><td>${{esc(r.last_event || '')}}</td><td>${{esc(r.connect_count || 0)}}</td></tr>`;
  }}).join('') : '<tr><td colspan="8">No EchoLink connections logged yet.</td></tr>';
}}
try {{
  const es = new EventSource('/api/events');
  es.onmessage = ev => update(JSON.parse(ev.data));
  es.onerror = () => {{ document.getElementById('notice').textContent = 'Live update disconnected; retrying...'; }};
}} catch(e) {{ setInterval(() => fetch('/api/status').then(r => r.json()).then(update), 2000); }}
</script>
</body>
</html>"""


def make_handler(renderer: DashboardRenderer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            log.debug("dashboard_http " + fmt, *args)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            return {k: v[-1] for k, v in parse_qs(raw).items()}

        def do_POST(self) -> None:
            form = self._read_form()
            if self.path.startswith("/api/disconnect"):
                callsign = (form.get("callsign") or "").strip().upper()
                if not callsign:
                    self._send_json(400, {"ok": False, "error": "callsign required"})
                    return
                cmd = append_command(renderer.cfg.dashboard.control_file, "disconnect", callsign=callsign)
                self._send_json(200, {"ok": True, "status": "queued", "message": f"Disconnect queued for {callsign}", "command_id": cmd.command_id})
                return
            if self.path.startswith("/api/block"):
                callsign = (form.get("callsign") or "").strip().upper()
                if not callsign:
                    self._send_json(400, {"ok": False, "error": "callsign required"})
                    return
                added = append_unique_line(renderer.cfg.access.banlist_file, callsign)
                cmd = append_command(renderer.cfg.dashboard.control_file, "block", callsign=callsign)
                msg = f"Blocked {callsign}" if added else f"{callsign} was already blocked"
                self._send_json(200, {"ok": True, "status": "queued", "message": msg, "command_id": cmd.command_id})
                return
            if self.path.startswith("/api/reload"):
                cmd = append_command(renderer.cfg.dashboard.control_file, "reload")
                self._send_json(200, {"ok": True, "status": "queued", "message": "Reload queued", "command_id": cmd.command_id})
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def do_GET(self) -> None:
            if self.path.startswith("/api/status"):
                self._send_json(200, renderer.status_payload())
                return
            if self.path.startswith("/api/events"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                while True:
                    try:
                        payload = json.dumps(renderer.status_payload(), sort_keys=True)
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        time.sleep(max(0.5, float(renderer.cfg.dashboard.push_interval_seconds)))
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                return
            body = renderer.render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return Handler


class DashboardServerThread:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        renderer = DashboardRenderer(self.cfg)
        self.httpd = ThreadingHTTPServer((self.cfg.dashboard.listen_host, self.cfg.dashboard.listen_port), make_handler(renderer))
        log.info("dashboard_started host=%s port=%s", self.cfg.dashboard.listen_host, self.cfg.dashboard.listen_port)
        self.httpd.serve_forever(poll_interval=0.5)

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the echolink-ob local dashboard")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    renderer = DashboardRenderer(cfg)
    httpd = ThreadingHTTPServer((cfg.dashboard.listen_host, cfg.dashboard.listen_port), make_handler(renderer))
    print(json.dumps({"status": "dashboard_ready", "url": f"http://{cfg.dashboard.listen_host}:{cfg.dashboard.listen_port}/"}, indent=2))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
