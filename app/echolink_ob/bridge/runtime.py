from __future__ import annotations

import argparse
import json
import logging
import os
import random
import signal
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from echolink_ob.analog.ports import (
    build_port_plan,
    render_analog_bridge_ini,
    write_analog_bridge_ini,
    write_state_file,
)
from echolink_ob.analog.tlv import RawTlvFrame, extract_dmr_ambe_blocks, parse_tlv_datagram
from echolink_ob.config import AppConfig, load_config
from echolink_ob.dmr.ambe import (
    build_payload33_from_ambe72_triplet,
    extract_ambe72_from_payload33,
    extract_center48_from_payload33,
)
from echolink_ob.logging_setup import setup_logging
from echolink_ob.openbridge.analyzer import is_possible_stream_end, is_voice_payload, read_capture
from echolink_ob.openbridge.client import OpenBridgeClient
from echolink_ob.openbridge.dmrd import DMRDPacket, MAX_3BYTE_ID, validate_3byte_id
from echolink_ob.openbridge.test_sender import make_client, resolve_source_id
from echolink_ob.analog.tone_openbridge import resolve_template_path
from echolink_ob.analog.usrp import pcm_sine_frames, send_usrp_pcm_frames

log = logging.getLogger(__name__)

DEFAULT_TLV_AUDIO_TAG = 0x02
DEFAULT_TLV_AUDIO_HEADER = b"\x02\x00\x1b"
DEFAULT_TLV_END_HEADER = b"\x02\x00\x00"
DEFAULT_PACKET_MS = 60.0


@dataclass(frozen=True)
class RuntimeStats:
    tlv_frames_received: int = 0
    tlv_audio_blocks_forwarded: int = 0
    openbridge_packets_sent: int = 0
    openbridge_packets_received: int = 0
    openbridge_voice_packets_to_analog: int = 0
    analog_tlv_sent: int = 0


@dataclass(frozen=True)
class TemplateParts:
    prefix: list[DMRDPacket]
    voice: list[DMRDPacket]
    suffix: list[DMRDPacket]


def split_template_packets(template_packets: list[DMRDPacket]) -> TemplateParts:
    voice = [pkt for pkt in template_packets if is_voice_payload(pkt)]
    if not voice:
        raise ValueError("template capture contains no voice packets")
    first_voice = template_packets.index(voice[0])
    last_voice = max(i for i, pkt in enumerate(template_packets) if is_voice_payload(pkt))
    prefix = [pkt for pkt in template_packets[:first_voice] if not is_voice_payload(pkt)]
    suffix = [pkt for pkt in template_packets[last_voice + 1 :] if is_possible_stream_end(pkt)]
    if not suffix:
        suffix = [pkt for pkt in template_packets[last_voice + 1 :] if not is_voice_payload(pkt)][:1]
    return TemplateParts(prefix=prefix, voice=voice, suffix=suffix)


class OpenBridgeStreamBuilder:
    """Build one OpenBridge stream from live 27-byte DMR AMBE blocks.

    Analog_Bridge emits one DMR audio TLV datagram containing 27 bytes: three
    9-byte DMR AMBE+FEC frames. Each 27-byte block maps to one 33-byte DMR
    voice burst. This builder copies signalling, dtype/vseq, and the center
    48-bit sync/embedded signalling region from a known-good DMRD template.
    """

    def __init__(
        self,
        *,
        cfg: AppConfig,
        template_packets: list[DMRDPacket],
        source_id: int,
        stream_id: int | None = None,
        sequence_start: int = 0,
    ) -> None:
        validate_3byte_id(source_id, "source-id")
        self.cfg = cfg
        self.source_id = source_id
        self.parts = split_template_packets(template_packets)
        self.stream_id = int(stream_id if stream_id is not None else random.randint(1, 0xFFFFFFFF))
        self.seq = sequence_start & 0xFF
        self.voice_index = 0
        self.started = False
        self.ended = False

    def _rewrite(self, pkt: DMRDPacket, payload: bytes | None = None) -> DMRDPacket:
        out = DMRDPacket(
            sequence=self.seq,
            rf_source_id=self.source_id,
            destination_id=self.cfg.openbridge.fixed_tgid,
            network_id=self.cfg.openbridge.network_id,
            slot=self.cfg.openbridge.slot,
            call_type="group",
            frame_type=pkt.frame_type,
            dtype_vseq=pkt.dtype_vseq,
            stream_id=self.stream_id,
            payload=pkt.payload if payload is None else payload,
        )
        self.seq = (self.seq + 1) & 0xFF
        return out

    def start_packets(self) -> list[DMRDPacket]:
        if self.started:
            return []
        self.started = True
        return [self._rewrite(pkt) for pkt in self.parts.prefix]

    def voice_packet(self, ambe_block: bytes) -> DMRDPacket:
        if len(ambe_block) != 27:
            raise ValueError(f"AMBE block must be 27 bytes, got {len(ambe_block)}")
        if self.ended:
            raise RuntimeError("cannot add voice packet after stream ended")
        template = self.parts.voice[self.voice_index % len(self.parts.voice)]
        center = extract_center48_from_payload33(template.payload)
        payload = build_payload33_from_ambe72_triplet(
            [ambe_block[0:9], ambe_block[9:18], ambe_block[18:27]],
            center48=center,
        )
        self.voice_index += 1
        return self._rewrite(template, payload=payload)

    def end_packets(self) -> list[DMRDPacket]:
        if self.ended:
            return []
        self.ended = True
        return [self._rewrite(pkt) for pkt in self.parts.suffix[:1]]


@dataclass(frozen=True)
class TlvCodecFormat:
    """Analog_Bridge TLV datagram format observed at runtime.

    Analog_Bridge builds differ slightly in the first three TLV bytes.  For
    DMR AMBE_AUDIO, the useful voice value is still a 27-byte block, but using
    the exact three-byte header emitted by the local Analog_Bridge instance
    makes the reverse direction more reliable than assuming one hard-coded
    tag/length byte order.

    Some Analog_Bridge builds also emit a short begin/start datagram before
    audio frames.  Sending that same datagram back before inbound DMR audio is
    important for OpenBridge -> Analog_Bridge -> USRP decode.
    """

    audio_header: bytes = DEFAULT_TLV_AUDIO_HEADER
    end_datagram: bytes = DEFAULT_TLV_END_HEADER
    start_datagram: bytes | None = None
    source: str = "default"

    def __post_init__(self) -> None:
        if len(self.audio_header) != 3:
            raise ValueError("TLV audio header must be exactly 3 bytes")
        if len(self.end_datagram) < 3:
            raise ValueError("TLV end datagram must be at least 3 bytes")
        if self.start_datagram is not None and len(self.start_datagram) < 3:
            raise ValueError("TLV start datagram must be at least 3 bytes")

    def build_audio(self, ambe_block: bytes) -> bytes:
        if len(ambe_block) != 27:
            raise ValueError(f"AMBE block must be 27 bytes, got {len(ambe_block)}")
        return self.audio_header + ambe_block

    def build_start(self) -> bytes | None:
        return self.start_datagram

    def build_end(self) -> bytes:
        return self.end_datagram


def learn_tlv_codec_format(frames: Iterable[RawTlvFrame | bytes]) -> TlvCodecFormat | None:
    """Learn the outbound TLV header from frames emitted by Analog_Bridge."""

    audio_header: bytes | None = None
    end_datagram: bytes | None = None
    start_datagram: bytes | None = None
    for item in frames:
        data = item.data if isinstance(item, RawTlvFrame) else item
        if len(data) == 30:
            try:
                parsed = parse_tlv_datagram(data)
            except ValueError:
                parsed = None
            if parsed is None or len(parsed.value) == 27:
                audio_header = data[:3]
        elif len(data) == 3:
            end_datagram = data
        elif len(data) > 3 and start_datagram is None:
            # Analog_Bridge commonly emits one non-audio begin/control datagram
            # before the first 30-byte audio frame. Preserve it for reverse
            # direction decode. Do not treat metadata/control datagrams as audio.
            start_datagram = data
    if audio_header is None:
        return None
    if end_datagram is None:
        tag = audio_header[:1]
        # Preserve the length-byte style when obvious.
        be_len = int.from_bytes(audio_header[1:3], "big")
        le_len = int.from_bytes(audio_header[1:3], "little")
        if be_len == 27:
            end_datagram = tag + b"\x00\x00"
        elif le_len == 27:
            end_datagram = tag + b"\x00\x00"
        else:
            end_datagram = DEFAULT_TLV_END_HEADER
    return TlvCodecFormat(
        audio_header=audio_header,
        end_datagram=end_datagram,
        start_datagram=start_datagram,
        source="learned",
    )


def build_tlv_audio_datagram(
    ambe_block: bytes,
    *,
    tag: int = DEFAULT_TLV_AUDIO_TAG,
    tlv_format: TlvCodecFormat | None = None,
) -> bytes:
    if tlv_format is not None:
        return tlv_format.build_audio(ambe_block)
    if len(ambe_block) != 27:
        raise ValueError(f"AMBE block must be 27 bytes, got {len(ambe_block)}")
    return bytes([tag & 0xFF]) + len(ambe_block).to_bytes(2, "big") + ambe_block


def build_tlv_start_datagram(
    *,
    tlv_format: TlvCodecFormat | None = None,
) -> bytes | None:
    if tlv_format is None:
        return None
    return tlv_format.build_start()


def build_tlv_end_datagram(
    *,
    tag: int = DEFAULT_TLV_AUDIO_TAG,
    tlv_format: TlvCodecFormat | None = None,
) -> bytes:
    if tlv_format is not None:
        return tlv_format.build_end()
    return bytes([tag & 0xFF]) + b"\x00\x00"


def dmr_packet_to_ambe_block(packet: DMRDPacket) -> bytes:
    if not is_voice_payload(packet):
        raise ValueError("packet is not a DMR voice payload")
    return b"".join(extract_ambe72_from_payload33(packet.payload))


def _port_in_use(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def find_analog_bridge_binary(explicit: str | None = None) -> str | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    for name in ("Analog_Bridge", "analog_bridge"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates.extend([
        "/opt/Analog_Bridge/Analog_Bridge",
        "/usr/bin/Analog_Bridge",
        "/usr/local/bin/Analog_Bridge",
        "/usr/bin/analog_bridge",
    ])
    for candidate in candidates:
        p = Path(candidate)
        if p.exists() and p.is_file():
            return str(p)
    return None


def maybe_launch_analog_bridge(
    *,
    binary: str | None,
    ini_path: str,
    should_start: bool,
) -> subprocess.Popen[bytes] | None:
    if not should_start:
        return None
    resolved = find_analog_bridge_binary(binary)
    if not resolved:
        raise FileNotFoundError("Analog_Bridge binary not found; install analog-bridge or pass --analog-bridge-bin")
    log.info("analog_bridge_launch binary=%s ini=%s", resolved, ini_path)
    return subprocess.Popen([resolved, ini_path])


def calibrate_tlv_codec_format(
    *,
    host: str,
    tlv_rx_port: int,
    usrp_tx_port: int,
    duration_ms: int = 700,
    frequency_hz: float = 1000.0,
) -> TlvCodecFormat | None:
    """Ask Analog_Bridge to encode a brief local tone and learn its TLV header.

    This is run before the bridge loops start, so the calibration TLV frames are
    consumed locally and are not forwarded to OpenBridge.
    """

    if duration_ms <= 0:
        return None

    captured: list[RawTlvFrame] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, tlv_rx_port))
    sock.settimeout(0.05)

    def sender() -> None:
        # Low level, short probe: enough to learn AB's TLV framing without
        # being useful as a transmitted test.
        frames = pcm_sine_frames(seconds=duration_ms / 1000.0, frequency_hz=frequency_hz, amplitude=1000)
        send_usrp_pcm_frames(target_host=host, target_port=usrp_tx_port, frames=frames)

    thread = threading.Thread(target=sender, daemon=True)
    start = time.monotonic()
    try:
        thread.start()
        while time.monotonic() - start < max(1.0, duration_ms / 1000.0 + 0.8):
            try:
                data, src = sock.recvfrom(4096)
            except socket.timeout:
                continue
            captured.append(RawTlvFrame(data=data, source=(src[0], int(src[1])), received_time=time.time()))
    finally:
        sock.close()
        thread.join(timeout=1.0)

    learned = learn_tlv_codec_format(captured)
    if learned is None:
        log.warning("bridge_tlv_calibration_failed frames=%s", len(captured))
        return None
    log.info(
        "bridge_tlv_calibration_ok frames=%s audio_header=%s end=%s",
        len(captured),
        learned.audio_header.hex(),
        learned.end_datagram.hex(),
    )
    if learned.start_datagram:
        log.info("bridge_tlv_calibration_start_datagram bytes=%s hex=%s", len(learned.start_datagram), learned.start_datagram.hex())
    return learned


class AnalogOpenBridgeRuntime:
    def __init__(
        self,
        *,
        cfg: AppConfig,
        plan,
        template_packets: list[DMRDPacket],
        source_id: int,
        source_id_provider: Callable[[], int] | None = None,
        last_heard_callback: Callable[[int], None] | None = None,
        prebuffer_ms: int = 180,
        tx_hang_ms: int | None = None,
        packet_ms: float = DEFAULT_PACKET_MS,
        tlv_format: TlvCodecFormat | None = None,
        startup_mute_ms: int = 2500,
    ) -> None:
        self.cfg = cfg
        self.plan = plan
        self.template_packets = template_packets
        self.source_id = int(source_id)
        self.source_id_provider = source_id_provider
        self.last_heard_callback = last_heard_callback
        self.prebuffer_blocks = max(1, int(round(prebuffer_ms / packet_ms)))
        self.tx_hang_s = (tx_hang_ms if tx_hang_ms is not None else cfg.bridge.tx_hang_ms) / 1000.0
        self.packet_ms = packet_ms
        self.tlv_format = tlv_format or TlvCodecFormat()
        self.startup_mute_s = max(0.0, startup_mute_ms / 1000.0)
        self.stop_event = threading.Event()
        self.send_lock = threading.Lock()
        self._next_openbridge_voice_send_at = 0.0
        self._next_tlv_audio_send_at = 0.0
        self.stats = {
            "tlv_frames_received": 0,
            "tlv_audio_blocks_forwarded": 0,
            "openbridge_packets_sent": 0,
            "openbridge_packets_received": 0,
            "openbridge_voice_packets_to_analog": 0,
            "analog_tlv_sent": 0,
            "streams_from_tlv": 0,
            "streams_from_openbridge": 0,
            "tlv_format_learned_passively": 0,
            "openbridge_to_tlv_start_datagrams": 0,
            "openbridge_voice_paced_sleeps": 0,
            "tlv_audio_paced_sleeps": 0,
        }
        self.active_tlv_stream_id: int | None = None
        self.active_openbridge_stream_id: int | None = None
        self.started_at = time.time()

    def status_snapshot(self) -> dict:
        return {
            "uptime_seconds": int(time.time() - self.started_at),
            "source_id": self.source_id,
            "openbridge_target": f"{self.cfg.openbridge.host}:{self.cfg.openbridge.port}",
            "tlv_rx": f"{self.plan.host}:{self.plan.app_tlv_rx_port}",
            "tlv_tx": f"{self.plan.host}:{self.plan.app_tlv_tx_port}",
            "usrp_rx": f"{self.plan.host}:{self.plan.app_usrp_rx_port}",
            "usrp_tx": f"{self.plan.host}:{self.plan.app_usrp_tx_port}",
            "active_tlv_stream_id": self.active_tlv_stream_id,
            "active_openbridge_stream_id": self.active_openbridge_stream_id,
            "tlv_format": {
                "source": self.tlv_format.source,
                "audio_header_hex": self.tlv_format.audio_header.hex(),
                "end_datagram_hex": self.tlv_format.end_datagram.hex(),
                "start_datagram_hex": self.tlv_format.start_datagram.hex() if self.tlv_format.start_datagram else None,
            },
            "startup_mute_seconds": self.startup_mute_s,
            "stats": dict(self.stats),
        }

    def write_status_file(self, path: str | Path | None) -> None:
        if not path:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.status_snapshot(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    def run(
        self,
        *,
        duration: float | None = None,
        status_interval_s: float = 15.0,
        status_file: str | Path | None = None,
    ) -> dict[str, int]:
        client = make_client(self.cfg)
        analog_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tlv_thread = threading.Thread(target=self._tlv_to_openbridge_loop, args=(client,), daemon=True)
        ob_thread = threading.Thread(target=self._openbridge_to_tlv_loop, args=(client, analog_sock), daemon=True)
        next_status = time.monotonic() + max(1.0, status_interval_s)
        try:
            tlv_thread.start()
            ob_thread.start()
            self.write_status_file(status_file)
            start = time.monotonic()
            while not self.stop_event.is_set():
                now = time.monotonic()
                if duration is not None and now - start >= duration:
                    log.info("bridge_runtime_duration_elapsed seconds=%s", duration)
                    self.stop_event.set()
                    break
                if now >= next_status:
                    snap = self.status_snapshot()
                    log.info(
                        "bridge_status uptime=%s tlv_frames=%s ob_sent=%s ob_recv=%s ob_voice_to_analog=%s active_tlv_stream=%s active_ob_stream=%s",
                        snap["uptime_seconds"],
                        self.stats["tlv_frames_received"],
                        self.stats["openbridge_packets_sent"],
                        self.stats["openbridge_packets_received"],
                        self.stats["openbridge_voice_packets_to_analog"],
                        self.active_tlv_stream_id,
                        self.active_openbridge_stream_id,
                    )
                    self.write_status_file(status_file)
                    next_status = now + max(1.0, status_interval_s)
                time.sleep(0.2)
        except KeyboardInterrupt:
            log.info("bridge_runtime_interrupted")
            self.stop_event.set()
        finally:
            self.stop_event.set()
            tlv_thread.join(timeout=2.0)
            ob_thread.join(timeout=2.0)
            client.close()
            analog_sock.close()
            self.active_tlv_stream_id = None
            self.active_openbridge_stream_id = None
            self.write_status_file(status_file)
        return dict(self.stats)

    def _pace_interval(self, attr: str, stat_name: str) -> None:
        """Keep voice datagrams close to real-time packet cadence.

        Analog_Bridge emits one DMR audio TLV block per DMR voice burst.  Each
        OpenBridge voice payload should therefore leave at roughly packet_ms
        intervals instead of being flushed as a prebuffer burst.  The same
        cadence is used in the reverse direction when OpenBridge packets are
        injected back into Analog_Bridge as TLV audio.
        """
        interval_s = max(0.005, float(self.packet_ms) / 1000.0)
        now = time.monotonic()
        next_at = float(getattr(self, attr))
        if next_at > now:
            time.sleep(next_at - now)
            self.stats[stat_name] += 1
            now = time.monotonic()
        setattr(self, attr, max(next_at + interval_s, now + interval_s))

    def _reset_openbridge_voice_pacer(self) -> None:
        self._next_openbridge_voice_send_at = time.monotonic()

    def _reset_tlv_audio_pacer(self) -> None:
        self._next_tlv_audio_send_at = time.monotonic()

    def _send_ob(self, client: OpenBridgeClient, packets: Iterable[DMRDPacket]) -> None:
        with self.send_lock:
            for pkt in packets:
                if is_voice_payload(pkt):
                    self._pace_interval("_next_openbridge_voice_send_at", "openbridge_voice_paced_sleeps")
                client.send_packet(pkt)
                self.stats["openbridge_packets_sent"] += 1

    def _learn_tlv_format_from_datagram(self, data: bytes, start_candidate: bytes | None) -> bytes | None:
        """Passively learn Analog_Bridge's TLV framing from real outbound audio.

        This avoids startup calibration/keying.  Once a connected EchoLink user
        speaks, Analog_Bridge emits its real start/control, audio, and end TLV
        datagrams.  Capture that framing and reuse it when sending inbound DMR
        audio back into Analog_Bridge.
        """
        if len(data) == 30:
            if self.tlv_format.source == "default" or self.tlv_format.audio_header != data[:3]:
                self.tlv_format = TlvCodecFormat(
                    audio_header=data[:3],
                    end_datagram=self.tlv_format.end_datagram,
                    start_datagram=start_candidate or self.tlv_format.start_datagram,
                    source="passive-learned",
                )
                self.stats["tlv_format_learned_passively"] += 1
                log.info(
                    "bridge_tlv_format_passive_learn audio_header=%s start_datagram=%s",
                    self.tlv_format.audio_header.hex(),
                    self.tlv_format.start_datagram.hex() if self.tlv_format.start_datagram else None,
                )
        elif len(data) == 3 and self.tlv_format.source != "default" and self.tlv_format.end_datagram != data:
            self.tlv_format = TlvCodecFormat(
                audio_header=self.tlv_format.audio_header,
                end_datagram=data,
                start_datagram=self.tlv_format.start_datagram,
                source=self.tlv_format.source,
            )
            log.info("bridge_tlv_format_end_learn end=%s", data.hex())
        elif len(data) > 3 and len(data) != 30:
            start_candidate = data
            if self.tlv_format.source != "default" and self.tlv_format.start_datagram is None:
                self.tlv_format = TlvCodecFormat(
                    audio_header=self.tlv_format.audio_header,
                    end_datagram=self.tlv_format.end_datagram,
                    start_datagram=data,
                    source=self.tlv_format.source,
                )
                log.info("bridge_tlv_format_start_learn bytes=%s hex=%s", len(data), data.hex())
        return start_candidate

    def _tlv_to_openbridge_loop(self, client: OpenBridgeClient) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.plan.host, self.plan.app_tlv_rx_port))
        sock.settimeout(0.1)
        log.info("bridge_tlv_listener_bound host=%s port=%s", self.plan.host, self.plan.app_tlv_rx_port)
        builder: OpenBridgeStreamBuilder | None = None
        buffered: list[bytes] = []
        last_audio_time: float | None = None
        listener_started_at = time.monotonic()
        startup_mute_logged = False
        start_candidate: bytes | None = None
        try:
            while not self.stop_event.is_set():
                try:
                    data, src = sock.recvfrom(4096)
                except socket.timeout:
                    if builder is not None and last_audio_time is not None and time.monotonic() - last_audio_time >= self.tx_hang_s:
                        self._send_ob(client, builder.end_packets())
                        log.info(
                            "bridge_tlv_to_openbridge_stream_end stream=%s voice_blocks=%s",
                            builder.stream_id,
                            builder.voice_index,
                        )
                        self.active_tlv_stream_id = None
                        builder = None
                        buffered.clear()
                        last_audio_time = None
                    continue
                start_candidate = self._learn_tlv_format_from_datagram(data, start_candidate)
                if self.startup_mute_s and time.monotonic() - listener_started_at < self.startup_mute_s:
                    if not startup_mute_logged:
                        log.info("bridge_tlv_startup_mute_active seconds=%s", self.startup_mute_s)
                        startup_mute_logged = True
                    log.debug("bridge_tlv_startup_mute_drop bytes=%s source=%s:%s", len(data), src[0], src[1])
                    continue
                self.stats["tlv_frames_received"] += 1
                blocks = extract_dmr_ambe_blocks([data])
                if not blocks:
                    log.debug("bridge_tlv_non_audio bytes=%s source=%s:%s", len(data), src[0], src[1])
                    continue
                last_audio_time = time.monotonic()
                if builder is None:
                    active_source_id = self.source_id
                    if self.source_id_provider is not None:
                        try:
                            active_source_id = int(self.source_id_provider())
                        except Exception:
                            active_source_id = self.source_id
                    builder = OpenBridgeStreamBuilder(
                        cfg=self.cfg,
                        template_packets=self.template_packets,
                        source_id=active_source_id,
                    )
                    buffered.clear()
                    self.active_tlv_stream_id = builder.stream_id
                    self.stats["streams_from_tlv"] += 1
                    self._reset_openbridge_voice_pacer()
                    log.info(
                        "bridge_tlv_to_openbridge_stream_start stream=%s source_id=%s prebuffer_blocks=%s",
                        builder.stream_id,
                        active_source_id,
                        self.prebuffer_blocks,
                    )
                for block in blocks:
                    if not builder.started and len(buffered) < self.prebuffer_blocks:
                        buffered.append(block)
                        if len(buffered) < self.prebuffer_blocks:
                            continue
                        self._send_ob(client, builder.start_packets())
                        for pre in buffered:
                            self._send_ob(client, [builder.voice_packet(pre)])
                            self.stats["tlv_audio_blocks_forwarded"] += 1
                        buffered.clear()
                    else:
                        if not builder.started:
                            self._send_ob(client, builder.start_packets())
                        self._send_ob(client, [builder.voice_packet(block)])
                        self.stats["tlv_audio_blocks_forwarded"] += 1
        finally:
            if builder is not None and builder.started and not builder.ended:
                self._send_ob(client, builder.end_packets())
            self.active_tlv_stream_id = None
            sock.close()

    def _openbridge_to_tlv_loop(self, client: OpenBridgeClient, analog_sock: socket.socket) -> None:
        target = (self.plan.host, self.plan.app_tlv_tx_port)
        log.info("bridge_openbridge_to_tlv_target host=%s port=%s", target[0], target[1])
        while not self.stop_event.is_set():
            pkt = client.recv_packet()
            if pkt is None:
                continue
            self.stats["openbridge_packets_received"] += 1
            if is_voice_payload(pkt):
                if self.active_openbridge_stream_id != pkt.stream_id:
                    self.active_openbridge_stream_id = pkt.stream_id
                    self.stats["streams_from_openbridge"] += 1
                    self._reset_tlv_audio_pacer()
                    if self.last_heard_callback is not None:
                        try:
                            self.last_heard_callback(int(pkt.rf_source_id))
                        except Exception:
                            pass
                    start_datagram = build_tlv_start_datagram(tlv_format=self.tlv_format)
                    if start_datagram:
                        analog_sock.sendto(start_datagram, target)
                        self.stats["analog_tlv_sent"] += 1
                        self.stats["openbridge_to_tlv_start_datagrams"] += 1
                        log.info(
                            "bridge_openbridge_to_tlv_start_datagram stream=%s bytes=%s",
                            pkt.stream_id,
                            len(start_datagram),
                        )
                    else:
                        log.debug(
                            "bridge_openbridge_to_tlv_no_start_datagram stream=%s tlv_format_source=%s",
                            pkt.stream_id,
                            self.tlv_format.source,
                        )
                    log.info("bridge_openbridge_to_tlv_stream_start stream=%s src=%s dst=%s", pkt.stream_id, pkt.rf_source_id, pkt.destination_id)
                try:
                    block = dmr_packet_to_ambe_block(pkt)
                except Exception as exc:
                    log.warning("bridge_openbridge_to_tlv_extract_failed stream=%s error=%s", pkt.stream_id, exc)
                    continue
                self._pace_interval("_next_tlv_audio_send_at", "tlv_audio_paced_sleeps")
                analog_sock.sendto(build_tlv_audio_datagram(block, tlv_format=self.tlv_format), target)
                self.stats["openbridge_voice_packets_to_analog"] += 1
                self.stats["analog_tlv_sent"] += 1
            elif is_possible_stream_end(pkt):
                analog_sock.sendto(build_tlv_end_datagram(tlv_format=self.tlv_format), target)
                self.stats["analog_tlv_sent"] += 1
                log.info("bridge_openbridge_to_tlv_stream_end stream=%s", pkt.stream_id)
                if self.active_openbridge_stream_id == pkt.stream_id:
                    self.active_openbridge_stream_id = None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the live Analog_Bridge <-> OpenBridge bridge runtime")
    p.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    p.add_argument("--template", default=None, help="known-good stream-*.dmrd template for OpenBridge voice wrapping")
    p.add_argument("--template-mode", choices=["auto", "capture", "builtin"], default="auto")
    p.add_argument("--source-id", type=int, default=None, help=f"fallback/static DMR source ID, 0..{MAX_3BYTE_ID}")
    p.add_argument("--seconds", type=float, default=None, help="run duration; default is until Ctrl+C")
    p.add_argument("--prebuffer-ms", type=int, default=180)
    p.add_argument("--packet-ms", type=float, default=60.0)
    p.add_argument("--start-analog-bridge", action="store_true", help="launch Analog_Bridge with the generated config")
    p.add_argument("--analog-bridge-bin", default=None, help="path to Analog_Bridge binary")
    p.add_argument("--no-write-analog-config", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="validate setup and print plan, but do not start sockets")
    p.add_argument("--status-seconds", type=float, default=15.0, help="interval for bridge_status log lines")
    p.add_argument("--status-file", default="/opt/echolink-ob/logs/bridge-status.json")
    p.add_argument("--pid-file", default=None, help="optional pid file to write while running")
    p.add_argument("--tlv-calibration-ms", type=int, default=0, help="learn Analog_Bridge TLV header before bridge loops start; default 0 disables startup audio injection")
    p.add_argument("--startup-mute-ms", type=int, default=2500, help="drop Analog_Bridge TLV seen immediately after startup so the bridge never keys OpenBridge by itself")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)

    def _raise_keyboard_interrupt(_signum, _frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
        signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    except ValueError:
        # Signal handlers can only be installed in the main thread.
        pass

    source_id = resolve_source_id(cfg, args.source_id, parser)

    try:
        template_path, template_source = resolve_template_path(args.template, mode=args.template_mode)
        template_packets = read_capture(template_path, cfg.openbridge.passphrase)
    except Exception as exc:
        parser.error(str(exc))

    result = build_port_plan(cfg, allow_in_use=True, reuse_state=True)
    if not args.no_write_analog_config:
        write_state_file(cfg.port_manager.state_file, result)
        write_analog_bridge_ini(result.plan.analog_bridge_ini_path, render_analog_bridge_ini(cfg, result.plan))

    analog_rx_ports_in_use = {
        "usrp_rxPort": _port_in_use(result.plan.host, result.plan.app_usrp_tx_port),
        "ambe_audio_rxPort": _port_in_use(result.plan.host, result.plan.app_tlv_tx_port),
    }
    setup_report = {
        "status": "bridge_runtime_ready",
        "source_id": source_id,
        "template": str(template_path),
        "template_source": template_source,
        "analog_bridge_ini": result.plan.analog_bridge_ini_path,
        "port_plan": result.plan.as_dict(),
        "analog_bridge_rx_ports_in_use": analog_rx_ports_in_use,
        "start_analog_bridge": bool(args.start_analog_bridge),
        "dry_run": bool(args.dry_run),
        "status_file": args.status_file,
        "pid_file": args.pid_file,
        "startup_mute_ms": args.startup_mute_ms,
        "tlv_calibration_ms": args.tlv_calibration_ms,
        "note": "EchoLink network session layer is not included yet. This runtime bridges Analog_Bridge TLV and OpenBridge. Startup TLV is muted by default so it cannot key OpenBridge by itself.",
    }
    print(json.dumps(setup_report, indent=2, sort_keys=True))
    if args.dry_run:
        return 0

    analog_proc: subprocess.Popen[bytes] | None = None
    try:
        analog_proc = maybe_launch_analog_bridge(
            binary=args.analog_bridge_bin,
            ini_path=result.plan.analog_bridge_ini_path,
            should_start=args.start_analog_bridge,
        )
        if args.pid_file:
            Path(args.pid_file).parent.mkdir(parents=True, exist_ok=True)
            Path(args.pid_file).write_text(str(os.getpid()) + "\n", encoding="utf-8")
        if analog_proc is not None:
            # Give Analog_Bridge a short window to bind its rx ports.
            time.sleep(1.0)
            if analog_proc.poll() is not None:
                raise RuntimeError(f"Analog_Bridge exited early with status {analog_proc.returncode}")

        tlv_format = None
        if args.tlv_calibration_ms > 0:
            tlv_format = calibrate_tlv_codec_format(
                host=result.plan.host,
                tlv_rx_port=result.plan.app_tlv_rx_port,
                usrp_tx_port=result.plan.app_usrp_tx_port,
                duration_ms=args.tlv_calibration_ms,
            )

        runtime = AnalogOpenBridgeRuntime(
            cfg=cfg,
            plan=result.plan,
            template_packets=template_packets,
            source_id=source_id,
            prebuffer_ms=args.prebuffer_ms,
            packet_ms=args.packet_ms,
            tlv_format=tlv_format,
            startup_mute_ms=args.startup_mute_ms,
        )
        stats = runtime.run(duration=args.seconds, status_interval_s=args.status_seconds, status_file=args.status_file)
        print(json.dumps({"status": "bridge_runtime_stopped", "stats": stats}, indent=2, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.exception("bridge_runtime_failed error=%s", exc)
        print(f"ERROR: {exc}")
        return 2
    finally:
        if args.pid_file:
            try:
                Path(args.pid_file).unlink(missing_ok=True)
            except Exception:
                pass
        if analog_proc is not None and analog_proc.poll() is None:
            log.info("analog_bridge_terminate pid=%s", analog_proc.pid)
            analog_proc.terminate()
            try:
                analog_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                analog_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
