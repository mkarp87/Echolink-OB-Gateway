# EchoLink OB Gateway

EchoLink OB Gateway bridges an EchoLink conference-style node to a private HBlink3/OpenBridge DMR talkgroup.

The app provides:

- EchoLink UDP conference/node behavior.
- EchoLink audio into a fixed OpenBridge talkgroup.
- DMR/OpenBridge audio back to connected EchoLink clients.
- Local EchoLink-to-EchoLink repeat for connected clients.
- Per-user DMR source ID lookup from RadioID data.
- Generated Analog_Bridge configuration.
- App-managed local md380-emu support.
- A web dashboard with connected stations, last-heard history, disconnect/block controls, and live status.

This project is intended for private/interoperability systems. Do not expose private configuration files or credentials in a public repository.

---

## Audio path

```text
EchoLink client
  <-> echolink-ob Python app
  <-> Analog_Bridge USRP PCM
  <-> md380-emu or AMBEServer vocoder
  <-> echolink-ob OpenBridge adapter
  <-> HBlink3 OpenBridge
  <-> DMR talkgroup
```

For best real-time audio, the recommended vocoder path is **local md380-emu**, managed by the app.

---

## Requirements

Recommended platform:

- Ubuntu 24.04 or Debian-based Linux.
- Python 3.12 or newer.
- A valid EchoLink node callsign and password.
- HBlink3/OpenBridge access.
- DVSwitch `Analog_Bridge`.
- `md380-emu` and `qemu-arm-static` for local vocoding.

Common UDP ports:

| Purpose | Port |
|---|---:|
| EchoLink audio | UDP 5198 |
| EchoLink control | UDP 5199 |
| EchoLink directory | TCP/UDP 5200 outbound |
| OpenBridge local bind | Your configured UDP port |
| App-managed md380-emu | UDP 2990 by default |
| Dashboard | TCP 8080 by default |

---

## Download

Clone from GitHub:

```bash
git clone https://github.com/mkarp87/Echolink-OB-Gateway.git
cd Echolink-OB-Gateway
```

Or download a ZIP from GitHub and unpack it:

```bash
unzip Echolink-OB-Gateway-main.zip
cd Echolink-OB-Gateway-main
```

---

## Install

Run the installer as root or with sudo:

```bash
sudo ./scripts/install.sh --systemd
```

The installer places the live application under:

```text
/opt/echolink-ob
```

It also:

- Creates `/opt/echolink-ob/venv`.
- Installs the Python package/wrappers.
- Copies `config/config-sample.toml` to `/opt/echolink-ob/config/config.toml` if no private config exists.
- Preserves an existing `/opt/echolink-ob/config/config.toml` during upgrades.
- Installs the `echolink-ob.service` systemd unit when `--systemd` is used.

If the server already has Analog_Bridge and md380-emu installed and you do not want the installer to use apt, run:

```bash
sudo NO_APT=1 SKIP_PIP_UPGRADE=1 ./scripts/install.sh --systemd --no-apt --no-dvswitch-deps --no-dvswitch-repo
```

---

## Configure

Edit the live config:

```bash
sudo nano /opt/echolink-ob/config/config.toml
```

At minimum, configure these sections.

### 1. EchoLink

```toml
[echolink]
callsign = "YOURCALL-L"
password = "YOUR_ECHOLINK_PASSWORD"
bind_host = "0.0.0.0"
audio_port = 5198
control_port = 5199
location = "Your Location"
status_text = "EchoLink OpenBridge Gateway"
register_with_directory = true
```

Use the public IP address in `bind_host` only when your host requires binding to that specific address. Otherwise, `0.0.0.0` is usually simpler.

### 2. HBlink3/OpenBridge

```toml
[openbridge]
host = "127.0.0.1"
port = 54096
passphrase = "CHANGE_ME"
network_id = 123456
fixed_tgid = 123456
slot = 1
call_type = "group"
local_bind_host = "0.0.0.0"
local_bind_port = 54095
both_slots = false
```

Use the values from your HBlink3 OpenBridge stanza.

### 3. Identity / source ID handling

```toml
[identity]
use_dynamic_source_id = true
fallback_source_id = 123456
strip_suffixes = ["-L", "-R", "-M"]
radioid_file = "/opt/echolink-ob/data/users.json"
auto_download_radioid = true
```

The app tries to map each EchoLink callsign to a RadioID DMR ID. If lookup fails, it uses `fallback_source_id`.

Do not use your OpenBridge network ID as the fallback subscriber/source ID.

### 4. Recommended vocoder settings

Use local app-managed md380-emu:

```toml
[vocoder]
preferred = "md380emu"
fallback = "ambeserver"
allow_fallback = true
switch_back_when_idle = true
allow_mid_stream_switch = false

[md380emu]
host = "127.0.0.1"
port = 2990
timeout_ms = 500
enabled = true
auto_start = true
reuse_existing = true
qemu_path = "/opt/md380-emu/qemu-arm-static"
binary_path = "/opt/md380-emu/md380-emu"
startup_wait_seconds = 2.0

[analog_bridge]
use_emulator = true
emulator_address = "127.0.0.1:2990"
```

When `auto_start = true`, the full runtime starts md380-emu before Analog_Bridge starts. When the gateway stops, it stops only the md380-emu process it started.

If another service is already listening on the configured md380-emu port and `reuse_existing = true`, the app uses that existing listener and does not stop it during shutdown.

### 5. Dashboard

```toml
[dashboard]
enabled = true
listen_host = "127.0.0.1"
listen_port = 8080
require_auth = false
last_heard_file = "/opt/echolink-ob/data/lastheard.json"
```

For LAN access, set:

```toml
listen_host = "0.0.0.0"
```

Use firewall rules or a reverse proxy if exposing the dashboard outside a trusted network.

---

## Start and stop

Start the service:

```bash
sudo systemctl start echolink-ob
```

Enable at boot:

```bash
sudo systemctl enable echolink-ob
```

Restart after config changes:

```bash
sudo systemctl restart echolink-ob
```

Stop:

```bash
sudo systemctl stop echolink-ob
```

Watch logs:

```bash
sudo journalctl -u echolink-ob -f
```

---

## Verify md380-emu and Analog_Bridge

Check service status:

```bash
systemctl status echolink-ob --no-pager
```

Check the full runtime status file:

```bash
cat /opt/echolink-ob/logs/full-status.json
```

Look for:

```json
"md380emu": {
  "auto_start": true,
  "running": true,
  "started_by_app": true
}
```

Check that the generated Analog_Bridge config uses the emulator and does not contain a `[DV3000]` section:

```bash
grep -E 'useEmulator|emulatorAddress|^\[DV3000\]' /opt/echolink-ob/generated/Analog_Bridge.ini
```

Expected:

```text
useEmulator = true
emulatorAddress = 127.0.0.1:2990
```

There should be no `[DV3000]` section when `use_emulator = true`.

---

## Dashboard

Default dashboard:

```text
http://127.0.0.1:8080/
```

From another computer, use an SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 root@YOUR_SERVER
```

Then open:

```text
http://127.0.0.1:8080/
```

The dashboard shows:

- Connected EchoLink stations.
- Active EchoLink speaker.
- OpenBridge packet counters.
- EchoLink GSM packet counters.
- Analog_Bridge state.
- md380-emu state.
- Directory registration state.
- Last connected stations.
- Last TX time.
- Last disconnect time.
- Disconnect and block buttons.

Client-side disconnects are handled using RTCP BYE, short EchoLink goodbye text packets, exact endpoint/IP matching, lone-station BYE fallback, and stale-client timeout cleanup. The app refreshes the EchoLink status file immediately after a disconnect so the dashboard roster updates without waiting for the normal status interval.

---

## Update an existing install

From a new checkout or unpacked ZIP:

```bash
cd Echolink-OB-Gateway
sudo ./scripts/install.sh --systemd
sudo systemctl restart echolink-ob
```

The installer preserves:

```text
/opt/echolink-ob/config/config.toml
/opt/echolink-ob/data/
/opt/echolink-ob/logs/
```

After updating, review your config and set the md380-emu values shown above.

---

## Common troubleshooting

### EchoLink connects but DMR audio is delayed or choppy

Check whether Analog_Bridge is using a slow remote AMBEServer instead of local md380-emu:

```bash
grep -E 'useEmulator|emulatorAddress|^\[DV3000\]' /opt/echolink-ob/generated/Analog_Bridge.ini
```

For local md380-emu, use:

```toml
[analog_bridge]
use_emulator = true
emulator_address = "127.0.0.1:2990"
```

and:

```toml
[md380emu]
auto_start = true
port = 2990
```

### DMR/HBlink3 keys but EchoLink client has no audio

Version 1.0.0b9 keeps the original EchoLink-client-compatible RTP/GSM packetization for this direction. Check the reverse-audio counters after a DMR-side test:

```bash
cat /opt/echolink-ob/logs/full-status.json | grep -E 'openbridge_packets_received|openbridge_voice_packets_to_analog|usrp_frames_from_dmr|gsm_packets_sent|rtp_packets_sent'
```

Expected during a DMR-to-EchoLink transmission:

```text
openbridge_packets_received        increases
openbridge_voice_packets_to_analog increases
usrp_frames_from_dmr               increases
gsm_packets_sent                   increases
rtp_packets_sent                   increases
```

If OpenBridge counters increase but `usrp_frames_from_dmr` does not, focus on Analog_Bridge TLV-to-USRP decode and md380-emu. If `usrp_frames_from_dmr` increases but `gsm_packets_sent` does not, the app has no active EchoLink recipients or the station session has expired.

### EchoLink client disconnects but still shows on dashboard

Check the EchoLink status file:

```bash
cat /opt/echolink-ob/logs/echolink-status.json
```

Then check logs for disconnect handling:

```bash
sudo journalctl -u echolink-ob | grep -i 'station_disconnected\|goodbye\|rtcp_bye\|peer_timeout'
```

If a mobile client does not send a clean disconnect packet, the station is removed after `[echolink].client_timeout_seconds`.

### OpenBridge does not key

Check the OpenBridge local bind and HBlink3 port values:

```toml
[openbridge]
host = "..."
port = 54096
local_bind_host = "0.0.0.0"
local_bind_port = 54095
```

Then confirm the service owns the local UDP port:

```bash
ss -lunp | grep -E '54095|54096'
```

### EchoLink cannot connect

Verify firewall/NAT rules for UDP 5198 and 5199. Also check directory registration:

```bash
cat /opt/echolink-ob/logs/full-status.json
```

---

## Useful commands

Run tests from the source tree:

```bash
PYTHONPATH=app python3 -m pytest -q
```

Regenerate the Analog_Bridge config and port plan:

```bash
/opt/echolink-ob/venv/bin/echolink-ob-analog-plan \
  --config /opt/echolink-ob/config/config.toml \
  --write \
  --allow-in-use \
  --print-ini
```

Update RadioID data:

```bash
/opt/echolink-ob/venv/bin/echolink-ob-radioid-update \
  --config /opt/echolink-ob/config/config.toml \
  --force
```

Collect logs/status for debugging:

```bash
sudo /opt/echolink-ob/scripts/collect_audio_debug.sh 45 echolink-ob
```

---

## Repository safety

Never commit:

- `config/config.toml`
- EchoLink passwords
- OpenBridge passphrases
- RadioID cache/data files
- capture PCAPs
- logs
- diagnostics

The public example config is:

```text
config/config-sample.toml
```

---

## License

GPL-3.0-or-later.


## Dashboard reload note

The web dashboard **Reload app** button queues a one-shot restart command for the running service. Version 1.0.0b9 removes processed dashboard commands from `data/dashboard-commands.jsonl` so an old reload command cannot replay after systemd restarts the service. If an older build entered a reload loop, run this once before or after upgrading:

```bash
sudo truncate -s 0 /opt/echolink-ob/data/dashboard-commands.jsonl
sudo systemctl reset-failed echolink-ob
sudo systemctl restart echolink-ob
```

