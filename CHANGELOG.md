# Changelog

## 1.0.0b9

- Fixed dashboard **Reload app** restart loops by acknowledging/removing processed dashboard commands from `dashboard-commands.jsonl`.
- Ignored and removed stale reload commands left by older builds during startup so a previously wedged service can recover after upgrade.
- Left EchoLink audio, OpenBridge audio, USRP, and vocoder behavior unchanged from the working v1.0.0b8/b7 path.

## 1.0.0b7

### Fixed

- Restored the original working DMR/HBlink3-to-EchoLink outbound RTP/GSM packetization: four GSM 06.10 frames per RTP packet using the legacy EchoLink RTP header byte. The earlier no-audio regression came from changing the EchoLink outbound packet format, while the original skipping was caused by the slow remote AMBEServer path.
- Kept the safer reverse-path USRP handling from v1.0.0b6 so full 320-byte USRP voice payloads from Analog_Bridge are accepted even when the payload packet has a zero PTT word.
- Improved EchoLink client BYE/Goodbye teardown by matching disconnect packets by exact endpoint, source IP, or the lone connected station when the client sends BYE from a different UDP source endpoint.

### Changed

- Outbound RTP/GSM to EchoLink clients now favors compatibility with the original app and mobile EchoLink clients instead of standard-only RTP packet formatting.

## 1.0.0b6

### Fixed

- DMR/HBlink3-to-EchoLink reverse audio now accepts full 320-byte USRP voice payloads even when Analog_Bridge sends a zero PTT word on the payload packet. Header-only unkey packets are still treated as stream end.
- Dashboard live updates now define the `md380` JavaScript status object correctly, preventing the EventSource update loop from stopping before the connected-stations table refreshes.
- EchoLink disconnect handling now accepts legacy-framed RTCP BYE packets and short binary-framed Goodbye/Disconnect messages from mobile clients.

### Added

- EchoLink stale-client cleanup using `[echolink].client_timeout_seconds` so clients that disappear without BYE/Goodbye are removed from the dashboard.
- Reverse-audio counters for USRP packets from Analog_Bridge, DMR audio streams to EchoLink, ignored USRP packets, and peer timeout disconnects.
- RTP marker bit on the first EchoLink packet of each DMR-to-EchoLink audio stream.

## 1.0.0b5

Current clean beta package.

### Added

- App-managed md380-emu lifecycle. When enabled, the full runtime starts a dedicated local md380-emu before Analog_Bridge and stops only the instance it started during shutdown.
- md380-emu status in the full runtime snapshot and dashboard.
- EchoLink client-side disconnect handling for RTCP BYE packets on either UDP port and short text/NDATA disconnect messages such as `Goodbye`, `BYE`, or `Disconnect`.
- Forced EchoLink status-file refresh when a client disconnects so the dashboard roster updates immediately.
- Dashboard last-heard table now shows last disconnect time and last event.
- Tests covering app-managed md380-emu start/stop, md380-emu port reuse, and client goodbye disconnect behavior.

### Changed

- The sample configuration now defaults to local md380-emu at `127.0.0.1:2990` and `analog_bridge.use_emulator = true`.
- The generated Analog_Bridge config omits `[DV3000]` when `use_emulator = true`, preventing fallback to slow remote AMBEServer paths.
- EchoLink-to-Analog_Bridge audio remains paced at 20 ms USRP frames, and DMR-to-EchoLink RTP remains one GSM frame per packet.

## 1.0.0b1

Initial beta release for the EchoLink OB Gateway project.

### Included

- EchoLink conference-style node behavior.
- EchoLink to private HBlink3 OpenBridge DMR talkgroup.
- DMR to EchoLink return audio.
- Dynamic DMR source-ID rewrite from RadioID lookup.
- Fallback DMR source ID support.
- Dynamic Analog_Bridge port planning.
- Generated Analog_Bridge configuration.
- AMBEServer/md380-emu support through Analog_Bridge.
- Web dashboard with live status, last-heard, disconnect, and block controls.
- RadioID database download command.
- systemd service support.
