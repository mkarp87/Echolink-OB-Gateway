# Testing

The source tree includes unit and protocol-level tests that can be run without a live EchoLink or DMR network.

Run from the source checkout:

```bash
PYTHONPATH=app python3 -m pytest -q
```

Run from an installed `/opt/echolink-ob` deployment:

```bash
/opt/echolink-ob/scripts/run-tests.sh
```

Current package validation:

```text
96 tests collected
96 passed
python compileall completed without syntax errors
```

Covered areas include:

- EchoLink RTP/GSM packet parsing and generation.
- EchoLink text identity parsing.
- EchoLink RTCP BYE and short goodbye disconnect handling.
- EchoLink conference routing and access-control logic.
- USRP packet parsing, key-up, voice, and unkey behavior.
- OpenBridge DMRD packet parsing, signing, replay, and record helpers.
- DMR AMBE payload extraction/rebuild helpers.
- Analog_Bridge TLV parsing and stream conversion.
- Dynamic Analog_Bridge port planning and generated INI rendering.
- App-managed md380-emu start/stop and external-listener reuse logic.
- Full runtime status snapshots and dashboard command queue handling.

Live deployment validation still requires your target environment:

- EchoLink directory authentication.
- HBlink3/OpenBridge endpoint.
- Analog_Bridge binary.
- md380-emu or AMBEServer vocoder.
- Actual EchoLink and DMR client audio tests.
