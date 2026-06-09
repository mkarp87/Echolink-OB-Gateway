from __future__ import annotations

import json
import logging
import queue
import random
import select
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from echolink_ob.analog.usrp import USRP_VOICE_FRAME_BYTES, USRP_VOICE_TYPE, UsrpPacket
from echolink_ob.config import AppConfig
from echolink_ob.identity.callsign import normalize_echolink_callsign
from echolink_ob.identity.radioid import RadioIdIndex
from echolink_ob.identity.resolver import IdentityResolver
from echolink_ob.dashboard.lastheard import LastHeardStore

from .audio_router import EchoLinkConferenceAudioRouter
from .conference import EchoLinkConferenceManager
from .gsm import GSM_FRAME_BYTES, GSM_PCM_BYTES, Gsm610Codec, GsmUnavailable
from .rtp import (
    RTP_PAYLOAD_GSM,
    build_gsm_rtp,
    build_ndata_info,
    build_rtcp_bye,
    build_rtcp_sdes,
    is_rtcp_bye,
    packet_contains_disconnect_command,
    parse_ndata,
    parse_echolink_text_packet,
    parse_station_identity_text,
    parse_rtcp_sdes,
    parse_rtp,
    is_disconnect_text,
)
from .access import EchoLinkAccessRules, detect_station_type
from .station import EchoLinkStation

log = logging.getLogger(__name__)


@dataclass
class EchoLinkPeer:
    callsign: str
    control_addr: tuple[str, int] | None = None
    audio_addr: tuple[str, int] | None = None
    ssrc: int = field(default_factory=lambda: random.randrange(1, 0xFFFFFFFF))
    rtp_sequence: int = 0
    rtp_timestamp: int = 0
    next_rtp_marker: bool = False
    pending_pcm: bytearray = field(default_factory=bytearray)
    name: str = ""
    location: str = ""
    client: str = ""
    connected_at: float = field(default_factory=time.monotonic)
    last_rx: float = field(default_factory=time.monotonic)


@dataclass
class EchoLinkServiceStats:
    control_packets: int = 0
    audio_packets: int = 0
    stations_connected: int = 0
    stations_disconnected: int = 0
    gsm_packets_decoded: int = 0
    gsm_packets_sent: int = 0
    pcm_frames_to_usrp: int = 0
    pcm_frames_queued_to_usrp: int = 0
    usrp_frames_dropped: int = 0
    usrp_unkey_packets_sent: int = 0
    usrp_keyup_packets_sent: int = 0
    usrp_header_keyups_sent: int = 0
    usrp_frames_from_dmr: int = 0
    usrp_packets_from_dmr: int = 0
    usrp_payload_unkey_frames_from_dmr: int = 0
    usrp_unkey_packets_from_dmr: int = 0
    usrp_ignored_packets_from_dmr: int = 0
    dmr_streams_to_echolink: int = 0
    peer_timeouts: int = 0
    rtp_packets_received: int = 0
    rtp_marker_packets_received: int = 0
    rtp_gsm_frames_received: int = 0
    rtp_packets_sent: int = 0
    roster_packets_sent: int = 0
    bad_packets: int = 0


class EchoLinkUdpConferenceService:
    """Minimal EchoLink UDP conference layer.

    This implements the station-to-station UDP side used by EchoLink-like
    clients: RTCP SDES/BYE on the control port and RTP/GSM on the audio port.
    Directory-server registration is still handled separately.
    """

    def __init__(self, cfg: AppConfig, *, status_file: str | Path | None = None) -> None:
        self.cfg = cfg
        self.status_file = Path(status_file or "/opt/echolink-ob/logs/echolink-status.json")
        self.conference = EchoLinkConferenceManager(max_stations=cfg.echolink.max_connected_stations, allow_duplicate_callsigns=cfg.access.allow_duplicate_callsigns)
        self.radioid = RadioIdIndex.from_file(cfg.identity.radioid_file)
        self.identity_resolver = IdentityResolver(self.radioid, cfg.identity.fallback_source_id, cfg.identity.strip_suffixes)
        self.access_rules = EchoLinkAccessRules.from_files(
            allowlist_file=cfg.access.allowlist_file,
            banlist_file=cfg.access.banlist_file,
            deny_patterns=cfg.access.deny_echolink_callsigns,
            allow_users=cfg.access.allow_echolink_users,
            allow_links=cfg.access.allow_echolink_links,
            allow_repeaters=cfg.access.allow_echolink_repeaters,
            allow_conferences=cfg.access.allow_echolink_conferences,
        )
        self.last_heard = LastHeardStore(cfg.dashboard.last_heard_file, max_records=cfg.dashboard.last_heard_limit)
        # libgsm keeps codec state. Use separate states for encode and decode;
        # sharing one state in both directions produces warbled audio.
        self.decode_codec = Gsm610Codec.create()
        self.encode_codec = Gsm610Codec.create()
        self.peers_by_call: dict[str, EchoLinkPeer] = {}
        self.peers_by_ip: dict[str, EchoLinkPeer] = {}
        self.stats = EchoLinkServiceStats()
        self._audio_sock: socket.socket | None = None
        self._control_sock: socket.socket | None = None
        self._usrp_sock: socket.socket | None = None
        self._usrp_tx_sock: socket.socket | None = None
        self._usrp_target: tuple[str, int] | None = None
        self._usrp_seq = 1
        self._usrp_seq_lock = threading.Lock()
        self._usrp_tx_queue: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=250)
        self._usrp_tx_stop = threading.Event()
        self._usrp_tx_thread: threading.Thread | None = None
        self._usrp_keyed = False
        self._usrp_key_requested = False
        self._usrp_state_lock = threading.Lock()
        self._dmr_audio_active = False
        self._last_dmr_usrp_at = 0.0
        self._last_peer_expire_check = 0.0
        self._stop = False
        self._last_status = 0.0
        self._last_roster = 0.0
        self.router = EchoLinkConferenceAudioRouter(
            self.conference,
            station_sink=self._send_pcm_to_station,
            gateway_sink=self._send_pcm_to_usrp,
        )

    def request_stop(self) -> None:
        """Ask the service loop to exit without closing sockets under select()."""
        self._stop = True

    def close(self) -> None:
        self._stop = True
        # Send the queued unkey while the USRP transmit socket is still open.
        # Closing sockets first made the final unkey a no-op and could leave
        # Analog_Bridge/HBLink waiting for timeout-based stream teardown.
        self._queue_usrp_unkey(clear_pending=True)
        self._usrp_tx_stop.set()
        if self._usrp_tx_thread is not None and self._usrp_tx_thread.is_alive():
            self._usrp_tx_thread.join(timeout=1.0)
        for attr in ("_audio_sock", "_control_sock", "_usrp_sock", "_usrp_tx_sock"):
            sock = getattr(self, attr)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, attr, None)
        self.decode_codec.close()
        self.encode_codec.close()

    def _bind_udp(self, host: str, port: int) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.setblocking(False)
        return sock

    def start(self) -> None:
        self._audio_sock = self._bind_udp(self.cfg.echolink.bind_host, self.cfg.echolink.audio_port)
        self._control_sock = self._bind_udp(self.cfg.echolink.bind_host, self.cfg.echolink.control_port)
        plan = self._port_plan()
        self._usrp_sock = self._bind_udp(self.cfg.port_manager.host, plan["app_usrp_rx_port"])
        self._usrp_tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._usrp_target = (plan["host"], plan["app_usrp_tx_port"])
        self._usrp_tx_stop.clear()
        self._usrp_tx_thread = threading.Thread(target=self._usrp_tx_loop, name="echolink-usrp-pacer", daemon=True)
        self._usrp_tx_thread.start()
        log.info("echolink_audio_bound host=%s port=%s", self.cfg.echolink.bind_host, self.cfg.echolink.audio_port)
        log.info("echolink_control_bound host=%s port=%s", self.cfg.echolink.bind_host, self.cfg.echolink.control_port)
        log.info("echolink_usrp_rx_bound host=%s port=%s", self.cfg.port_manager.host, plan["app_usrp_rx_port"])
        log.info("echolink_usrp_tx_target host=%s port=%s", self._usrp_target[0], self._usrp_target[1])

    def _port_plan(self) -> dict[str, int]:
        path = Path(self.cfg.port_manager.state_file)
        if not path.exists():
            raise FileNotFoundError(f"port plan not found: {path}; run echolink-ob-run first")
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("plan", data)

    def reload_access_rules(self) -> None:
        self.access_rules = EchoLinkAccessRules.from_files(
            allowlist_file=self.cfg.access.allowlist_file,
            banlist_file=self.cfg.access.banlist_file,
            deny_patterns=self.cfg.access.deny_echolink_callsigns,
            allow_users=self.cfg.access.allow_echolink_users,
            allow_links=self.cfg.access.allow_echolink_links,
            allow_repeaters=self.cfg.access.allow_echolink_repeaters,
            allow_conferences=self.cfg.access.allow_echolink_conferences,
        )
        log.info("echolink_access_reloaded banlist=%s allowlist=%s", self.cfg.access.banlist_file, self.cfg.access.allowlist_file)

    def _send_reject_notice(self, addr: tuple[str, int], reason: str) -> None:
        data = build_ndata_info(f"Connection rejected by {self.cfg.echolink.callsign}: {reason}")
        if self._audio_sock is not None:
            try:
                self._audio_sock.sendto(data, (addr[0], self.cfg.echolink.audio_port))
            except OSError:
                pass
        if self._control_sock is not None:
            try:
                self._control_sock.sendto(build_rtcp_bye(ssrc=0), (addr[0], self.cfg.echolink.control_port))
            except OSError:
                pass

    def _connect_or_update_peer(
        self,
        *,
        callsign: str,
        control_addr: tuple[str, int] | None = None,
        audio_addr: tuple[str, int] | None = None,
        name: str = "",
        location: str = "",
        client: str = "",
    ) -> EchoLinkPeer:
        callsign = callsign.upper()
        peer = self.peers_by_call.get(callsign)
        if peer is None:
            station_type = detect_station_type(callsign)
            decision = self.access_rules.check(callsign, station_type)
            if not decision.allowed:
                log.warning("echolink_station_rejected callsign=%s reason=%s station_type=%s", callsign, decision.reason, decision.station_type)
                raise PermissionError(decision.reason)
            peer = EchoLinkPeer(callsign=callsign)
            self.peers_by_call[callsign] = peer
            resolved = self.identity_resolver.resolve_echolink(callsign)
            st = EchoLinkStation(
                callsign=callsign,
                normalized_callsign=resolved.normalized_callsign,
                station_type=decision.station_type,
                ip_address=(control_addr or audio_addr or ("", 0))[0] or None,
                name=name or "",
                location=location or "",
                client=client or "",
                resolved_dmr_id=resolved.dmr_id,
                fallback_source_id_in_use=resolved.fallback_used,
            )
            self.conference.add_station(st)
            self.stats.stations_connected += 1
            self.last_heard.record(callsign=callsign, dmr_id=resolved.dmr_id, name=name or "", source="echolink", event="connected")
            log.info("echolink_station_connected callsign=%s dmr_id=%s fallback=%s total=%s", callsign, resolved.dmr_id, resolved.fallback_used, len(self.conference.stations))
        if control_addr is not None:
            peer.control_addr = control_addr
            self.peers_by_ip[control_addr[0]] = peer
        if audio_addr is not None:
            peer.audio_addr = audio_addr
            self.peers_by_ip[audio_addr[0]] = peer
        if name:
            peer.name = name
        if location:
            peer.location = location
        if client:
            peer.client = client
        st = self.conference.stations.get(callsign)
        if st is not None:
            if name:
                st.name = name
            if location:
                st.location = location
            if client:
                st.client = client
        peer.last_rx = time.monotonic()
        return peer

    def _disconnect_peer(self, peer: EchoLinkPeer, *, reason: str = "client_disconnect") -> None:
        key = peer.callsign.upper().strip()
        if key not in self.peers_by_call:
            return
        was_active = self.conference.active_speaker == key
        dmr_id = self.current_source_id_for_callsign(key)
        self.conference.remove_station(peer.callsign)
        self.peers_by_call.pop(key, None)
        for ip, known in list(self.peers_by_ip.items()):
            if known is peer:
                self.peers_by_ip.pop(ip, None)
        if was_active:
            self._queue_usrp_unkey(clear_pending=True)
        self.stats.stations_disconnected += 1
        self.last_heard.record(callsign=peer.callsign, dmr_id=dmr_id, name=peer.name, source="echolink", event="disconnected")
        self._write_status(force=True)
        log.info("echolink_station_disconnected callsign=%s reason=%s", peer.callsign, reason)

    def _peer_for_disconnect_addr(self, addr: tuple[str, int]) -> EchoLinkPeer | None:
        """Locate the station that sent a disconnect/bye packet.

        EchoLink clients are inconsistent about which UDP source port is used
        for BYE/GOODBYE. Prefer an exact control/audio tuple, then IP address.
        If a client sends BYE from a new port/IP and only one station is
        connected, safely treat that lone station as the disconnect target.
        """
        for peer in self.peers_by_call.values():
            if peer.control_addr == addr or peer.audio_addr == addr:
                return peer
        peer = self.peers_by_ip.get(addr[0])
        if peer is not None:
            return peer
        if len(self.peers_by_call) == 1:
            return next(iter(self.peers_by_call.values()))
        return None

    def _disconnect_peer_by_addr(self, addr: tuple[str, int], *, reason: str) -> bool:
        peer = self._peer_for_disconnect_addr(addr)
        if peer is None:
            log.info("echolink_disconnect_unknown_peer source=%s:%s reason=%s connected=%s", addr[0], addr[1], reason, len(self.peers_by_call))
            return False
        self._disconnect_peer(peer, reason=reason)
        return True

    def _touch_peer_by_addr(self, addr: tuple[str, int]) -> EchoLinkPeer | None:
        peer = self._peer_for_disconnect_addr(addr)
        if peer is not None:
            peer.last_rx = time.monotonic()
        return peer


    def disconnect_station(self, callsign: str, reason: str = "Disconnected by admin") -> bool:
        peer = self.peers_by_call.get(callsign.upper().strip())
        if peer is None:
            return False
        text = build_ndata_info(f"{self.cfg.echolink.callsign}: {reason}")
        bye = build_rtcp_bye(ssrc=0)
        if self._audio_sock is not None and peer.audio_addr:
            try:
                self._audio_sock.sendto(text, peer.audio_addr)
            except OSError:
                pass
        if self._control_sock is not None:
            targets = []
            if peer.control_addr:
                targets.append(peer.control_addr)
            elif peer.audio_addr:
                targets.append((peer.audio_addr[0], self.cfg.echolink.control_port))
            for target in targets:
                try:
                    self._control_sock.sendto(bye, target)
                except OSError:
                    pass
        self._disconnect_peer(peer, reason="admin_disconnect")
        log.info("echolink_station_admin_disconnected callsign=%s reason=%r", callsign, reason)
        return True

    def notify_disconnect_all(self, reason: str = "Gateway shutting down") -> None:
        """Best-effort disconnect notification for connected EchoLink clients.

        Send both RTCP BYE on the control side and an EchoLink text notice on
        the audio side.  Mobile clients vary in which packet causes the UI to
        immediately leave the connection screen, so both are intentionally sent.
        """
        peers = list(self.peers_by_call.values())
        if not peers:
            return
        text = build_ndata_info(f"{self.cfg.echolink.callsign} disconnecting: {reason}")
        bye = build_rtcp_bye(ssrc=0)
        for peer in peers:
            if self._control_sock is not None:
                targets = []
                if peer.control_addr:
                    targets.append(peer.control_addr)
                elif peer.audio_addr:
                    targets.append((peer.audio_addr[0], self.cfg.echolink.control_port))
                for target in targets:
                    try:
                        self._control_sock.sendto(bye, target)
                    except OSError:
                        pass
            if self._audio_sock is not None and peer.audio_addr:
                try:
                    self._audio_sock.sendto(text, peer.audio_addr)
                except OSError:
                    pass
        log.info("echolink_disconnect_notice_sent stations=%s reason=%r", len(peers), reason)

    def disconnect_all(self, reason: str = "Gateway shutting down") -> None:
        self.notify_disconnect_all(reason=reason)
        for peer in list(self.peers_by_call.values()):
            self._disconnect_peer(peer, reason="disconnect_all")

    def _handle_text_identity(self, text: str, addr: tuple[str, int], *, from_control: bool) -> EchoLinkPeer | None:
        ident = parse_station_identity_text(text)
        callsign = ident.get("callsign")
        if not callsign:
            log.info("echolink_text source=%s:%s text=%r", addr[0], addr[1], text[:80])
            return None
        log.info(
            "echolink_station_identity source=%s:%s callsign=%s control=%s name=%r location=%r client=%r",
            addr[0],
            addr[1],
            callsign,
            from_control,
            ident.get("name", ""),
            ident.get("location", ""),
            ident.get("client", ""),
        )
        try:
            peer = self._connect_or_update_peer(
                callsign=callsign,
                control_addr=addr if from_control else None,
                audio_addr=None if from_control else addr,
                name=str(ident.get("name", "")),
                location=str(ident.get("location", "")),
                client=str(ident.get("client", "")),
            )
        except PermissionError as exc:
            self._send_reject_notice(addr, str(exc))
            return None
        # EchoLink mobile/Windows clients may start with a text station-info
        # datagram before RTCP SDES.  Send both the station roster text and an
        # SDES-style response so either path can complete the connect flow.
        if self._control_sock is not None:
            try:
                self._control_sock.sendto(
                    build_rtcp_sdes(callsign=self.cfg.echolink.callsign, name=self.cfg.echolink.status_text),
                    (addr[0], self.cfg.echolink.control_port if not from_control else addr[1]),
                )
            except OSError:
                pass
        if self._audio_sock is not None:
            try:
                self._audio_sock.sendto(build_ndata_info(self.conference.roster_text()), (addr[0], self.cfg.echolink.audio_port if from_control else addr[1]))
                self.stats.roster_packets_sent += 1
            except OSError:
                pass
        return peer

    def _handle_control(self, data: bytes, addr: tuple[str, int]) -> None:
        self.stats.control_packets += 1
        self._touch_peer_by_addr(addr)
        if is_rtcp_bye(data):
            self._disconnect_peer_by_addr(addr, reason="rtcp_bye_control")
            return
        if packet_contains_disconnect_command(data):
            self._disconnect_peer_by_addr(addr, reason="binary_goodbye_control")
            return
        text = parse_echolink_text_packet(data)
        if text is not None:
            if is_disconnect_text(text):
                self._disconnect_peer_by_addr(addr, reason="text_goodbye_control")
                return
            self._handle_text_identity(text, addr, from_control=True)
            return
        try:
            sdes = parse_rtcp_sdes(data)
        except Exception as exc:
            # Some official/mobile clients continue to send non-RTCP identity
            # keepalives on the control port after already being accepted via
            # audio-port station identity.  Do not count those as bad packets.
            if addr[0] in self.peers_by_ip:
                log.debug(
                    "echolink_ignored_control_keepalive source=%s:%s bytes=%s error=%s",
                    addr[0],
                    addr[1],
                    len(data),
                    exc,
                )
                return
            self.stats.bad_packets += 1
            log.warning("echolink_bad_control source=%s:%s bytes=%s error=%s", addr[0], addr[1], len(data), exc)
            return
        callsign = sdes.get("callsign") or f"UNKNOWN-{addr[0]}"
        try:
            peer = self._connect_or_update_peer(callsign=callsign, control_addr=addr)
        except PermissionError as exc:
            self._send_reject_notice(addr, str(exc))
            return
        if self._control_sock is not None:
            self._control_sock.sendto(
                build_rtcp_sdes(callsign=self.cfg.echolink.callsign, name=self.cfg.echolink.status_text),
                addr,
            )
        if peer.audio_addr and self._audio_sock is not None:
            self._audio_sock.sendto(build_ndata_info(self.conference.roster_text()), peer.audio_addr)
            self.stats.roster_packets_sent += 1

    def _handle_audio(self, data: bytes, addr: tuple[str, int]) -> None:
        self.stats.audio_packets += 1
        self._touch_peer_by_addr(addr)
        if is_rtcp_bye(data):
            self._disconnect_peer_by_addr(addr, reason="rtcp_bye_audio")
            return
        # Only apply binary-text disconnect detection on the audio port when the
        # packet does not look like normal GSM/RTP. This avoids false positives
        # from compressed voice payload bytes.
        if not (len(data) >= 12 and (data[0] >> 6) == 2 and (data[1] & 0x7F) == RTP_PAYLOAD_GSM):
            if packet_contains_disconnect_command(data):
                self._disconnect_peer_by_addr(addr, reason="binary_goodbye_audio")
                return
        text = parse_echolink_text_packet(data)
        if text is not None:
            if is_disconnect_text(text):
                self._disconnect_peer_by_addr(addr, reason="text_goodbye_audio")
                return
            self._handle_text_identity(text, addr, from_control=False)
            return
        peer = self.peers_by_ip.get(addr[0])
        if peer is None:
            try:
                peer = self._connect_or_update_peer(callsign=f"UNKNOWN-{addr[0]}", audio_addr=addr)
            except PermissionError as exc:
                self._send_reject_notice(addr, str(exc))
                return
        else:
            peer.audio_addr = addr
        try:
            pkt = parse_rtp(data)
            self.stats.rtp_packets_received += 1
            if pkt.marker:
                self.stats.rtp_marker_packets_received += 1
            if pkt.payload_type != RTP_PAYLOAD_GSM:
                raise ValueError(f"unsupported RTP payload type {pkt.payload_type}; only GSM is enabled")
            self.stats.rtp_gsm_frames_received += max(1, len(pkt.payload) // GSM_FRAME_BYTES)
            pcm = self.decode_codec.decode_gsm(pkt.payload)
        except Exception as exc:
            self.stats.bad_packets += 1
            log.warning("echolink_bad_audio source=%s:%s bytes=%s error=%s", addr[0], addr[1], len(data), exc)
            return
        self.stats.gsm_packets_decoded += 1
        st = self.conference.stations.get(peer.callsign)
        if st is not None:
            st.last_heard_at = time.monotonic()
            self.last_heard.record(callsign=st.callsign, dmr_id=st.resolved_dmr_id, name=st.name, source="echolink", event="heard")
        for i in range(0, len(pcm), GSM_PCM_BYTES):
            self.router.speaker_pcm(peer.callsign, pcm[i:i + GSM_PCM_BYTES], gateway_allowed=True)

    def _send_pcm_to_station(self, callsign: str, pcm_frame: bytes) -> None:
        peer = self.peers_by_call.get(callsign.upper())
        if peer is None or peer.audio_addr is None or self._audio_sock is None:
            return
        peer.pending_pcm.extend(pcm_frame)
        # Restore the original working EchoLink outbound packetization: four
        # 20 ms GSM 06.10 frames per RTP packet using the legacy EchoLink RTP
        # header form. The skipping observed earlier was caused by the slow
        # remote AMBEServer path, not by this packetization.
        packet_pcm_len = GSM_PCM_BYTES * 4
        while len(peer.pending_pcm) >= packet_pcm_len:
            block = bytes(peer.pending_pcm[:packet_pcm_len])
            del peer.pending_pcm[:packet_pcm_len]
            gsm_payload = self.encode_codec.encode_pcm(block)
            payload = build_gsm_rtp(gsm_payload, sequence=peer.rtp_sequence)
            try:
                self._audio_sock.sendto(payload, peer.audio_addr)
            except OSError as exc:
                self.stats.bad_packets += 1
                log.warning("echolink_rtp_send_failed callsign=%s target=%s:%s error=%s", callsign, peer.audio_addr[0], peer.audio_addr[1], exc)
                return
            peer.next_rtp_marker = False
            peer.rtp_sequence = (peer.rtp_sequence + 1) & 0xFFFF
            peer.rtp_timestamp = (peer.rtp_timestamp + (len(block) // 2)) & 0xFFFFFFFF
            self.stats.gsm_packets_sent += 1
            self.stats.rtp_packets_sent += 1

    def _next_usrp_sequence(self) -> int:
        with self._usrp_seq_lock:
            seq = self._usrp_seq
            self._usrp_seq = (self._usrp_seq + 1) & 0xFFFFFFFF
            return seq

    def _send_usrp_packet(self, *, keyup: bool, payload: bytes = b"") -> None:
        if self._usrp_tx_sock is None or self._usrp_target is None:
            return
        pkt = UsrpPacket(
            sequence=self._next_usrp_sequence(),
            keyup=keyup,
            payload=payload,
            talkgroup=self.cfg.openbridge.fixed_tgid,
        )
        self._usrp_tx_sock.sendto(pkt.to_bytes(), self._usrp_target)
        if keyup:
            self._usrp_keyed = True
            self.stats.usrp_keyup_packets_sent += 1
            if payload:
                self.stats.pcm_frames_to_usrp += 1
            else:
                self.stats.usrp_header_keyups_sent += 1
        else:
            self._usrp_keyed = False
            self.stats.usrp_unkey_packets_sent += 1

    def _drain_usrp_queue(self) -> None:
        while True:
            try:
                self._usrp_tx_queue.get_nowait()
            except queue.Empty:
                return

    def _queue_usrp_keyup(self) -> None:
        with self._usrp_state_lock:
            if self._usrp_key_requested:
                return
            self._usrp_key_requested = True
        try:
            self._usrp_tx_queue.put_nowait(("keyup", b""))
        except queue.Full:
            # Preserve real-time behavior but make room for the PTT edge.  A
            # dropped first frame is preferable to a missing keyup transition.
            try:
                self._usrp_tx_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._usrp_tx_queue.put_nowait(("keyup", b""))
            except queue.Full:
                self.stats.usrp_frames_dropped += 1

    def _queue_usrp_unkey(self, *, clear_pending: bool = False) -> None:
        with self._usrp_state_lock:
            self._usrp_key_requested = False
        if clear_pending:
            self._drain_usrp_queue()
        try:
            self._usrp_tx_queue.put_nowait(("unkey", b""))
        except queue.Full:
            self._drain_usrp_queue()
            try:
                self._usrp_tx_queue.put_nowait(("unkey", b""))
            except queue.Full:
                pass

    def _usrp_tx_loop(self) -> None:
        frame_interval_s = max(0.005, float(self.cfg.audio.frame_ms) / 1000.0)
        next_voice_at = time.monotonic()
        while not self._usrp_tx_stop.is_set() or not self._usrp_tx_queue.empty():
            try:
                kind, payload = self._usrp_tx_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if kind == "keyup":
                if not self._usrp_keyed:
                    self._send_usrp_packet(keyup=True, payload=b"")
                next_voice_at = time.monotonic()
            elif kind == "voice":
                if not self._usrp_keyed:
                    # Defensive fallback if a keyup marker was dropped by queue
                    # pressure.  Analog_Bridge receives an explicit PTT edge
                    # before the first PCM frame instead of relying on the
                    # first audio packet to imply transmit.
                    self._send_usrp_packet(keyup=True, payload=b"")
                    next_voice_at = time.monotonic()
                now = time.monotonic()
                if now < next_voice_at:
                    time.sleep(next_voice_at - now)
                self._send_usrp_packet(keyup=True, payload=payload)
                next_voice_at = max(next_voice_at + frame_interval_s, time.monotonic())
            elif kind == "unkey":
                if self._usrp_keyed:
                    self._send_usrp_packet(keyup=False)
                next_voice_at = time.monotonic()

    def _send_pcm_to_usrp(self, pcm_frame: bytes, callsign: str, source_id: int | None) -> None:
        if len(pcm_frame) != USRP_VOICE_FRAME_BYTES:
            return
        self._queue_usrp_keyup()
        try:
            self._usrp_tx_queue.put_nowait(("voice", bytes(pcm_frame)))
            self.stats.pcm_frames_queued_to_usrp += 1
        except queue.Full:
            # Keep the bridge real-time. If the queue falls behind, discard the
            # oldest pending frame rather than building delay that later sounds
            # like slowed audio.
            try:
                self._usrp_tx_queue.get_nowait()
            except queue.Empty:
                pass
            self.stats.usrp_frames_dropped += 1
            try:
                self._usrp_tx_queue.put_nowait(("voice", bytes(pcm_frame)))
                self.stats.pcm_frames_queued_to_usrp += 1
            except queue.Full:
                self.stats.usrp_frames_dropped += 1

    def _mark_dmr_audio_start(self) -> None:
        if not self._dmr_audio_active:
            self._dmr_audio_active = True
            self.stats.dmr_streams_to_echolink += 1
            for peer in self.peers_by_call.values():
                peer.next_rtp_marker = True
            log.info("echolink_dmr_audio_stream_start recipients=%s", len(self.peers_by_call))

    def _release_inactive_dmr_audio(self) -> None:
        if not self._dmr_audio_active:
            return
        timeout_s = max(0.25, float(max(self.cfg.bridge.tx_hang_ms, self.cfg.audio.silence_gap_ms)) / 1000.0)
        if self._last_dmr_usrp_at and time.monotonic() - self._last_dmr_usrp_at >= timeout_s:
            self._dmr_audio_active = False
            log.info("echolink_dmr_audio_stream_idle_released idle_ms=%s", int(timeout_s * 1000))

    def _handle_usrp(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            pkt = UsrpPacket.from_bytes(data)
        except Exception as exc:
            self.stats.bad_packets += 1
            log.warning("echolink_usrp_bad_packet source=%s:%s error=%s", addr[0], addr[1], exc)
            return
        self.stats.usrp_packets_from_dmr += 1
        payload_len = len(pkt.payload)
        if pkt.packet_type == USRP_VOICE_TYPE and payload_len == USRP_VOICE_FRAME_BYTES:
            # Analog_Bridge builds are not consistent about the PTT word on
            # decoded PCM packets.  Treat any full-size USRP voice payload as
            # audio; use header-only unkey packets to end/reset the stream.
            if not pkt.keyup:
                self.stats.usrp_payload_unkey_frames_from_dmr += 1
            self._mark_dmr_audio_start()
            self._last_dmr_usrp_at = time.monotonic()
            self.stats.usrp_frames_from_dmr += 1
            self.router.dmr_pcm(pkt.payload)
            return
        if not pkt.keyup and payload_len == 0:
            self.stats.usrp_unkey_packets_from_dmr += 1
            if self._dmr_audio_active:
                self._dmr_audio_active = False
                log.info("echolink_dmr_audio_stream_end source=%s:%s", addr[0], addr[1])
            return
        self.stats.usrp_ignored_packets_from_dmr += 1
        log.debug(
            "echolink_usrp_ignored source=%s:%s keyup=%s type=%s payload_len=%s",
            addr[0],
            addr[1],
            pkt.keyup,
            pkt.packet_type,
            payload_len,
        )

    def _send_periodic_roster(self, interval_s: float = 30.0) -> None:
        now = time.monotonic()
        if now - self._last_roster < interval_s:
            return
        self._last_roster = now
        data = build_ndata_info(self.conference.roster_text())
        for peer in self.peers_by_call.values():
            if peer.audio_addr and self._audio_sock:
                self._audio_sock.sendto(data, peer.audio_addr)
                self.stats.roster_packets_sent += 1


    def _expire_stale_peers(self) -> None:
        timeout_s = int(getattr(self.cfg.echolink, "client_timeout_seconds", 120))
        if timeout_s <= 0:
            return
        now = time.monotonic()
        if now - self._last_peer_expire_check < 1.0:
            return
        self._last_peer_expire_check = now
        for peer in list(self.peers_by_call.values()):
            if now - peer.last_rx >= timeout_s:
                self.stats.peer_timeouts += 1
                self._disconnect_peer(peer, reason=f"peer_timeout_{timeout_s}s")

    def current_source_id_for_callsign(self, callsign: str | None) -> int:
        if callsign:
            st = self.conference.stations.get(callsign.upper())
            if st is not None and st.resolved_dmr_id:
                return int(st.resolved_dmr_id)
        return int(self.cfg.identity.fallback_source_id)

    def current_gateway_source_id(self) -> int:
        return self.current_source_id_for_callsign(self.conference.active_speaker)

    def connected_station_rows(self) -> list[dict[str, object]]:
        rows = []
        for st in self.conference.stations.values():
            rows.append({
                "callsign": st.callsign,
                "normalized_callsign": st.normalized_callsign,
                "name": st.name,
                "location": st.location,
                "client": st.client,
                "dmr_id": st.resolved_dmr_id,
                "fallback": st.fallback_source_id_in_use,
                "active": st.callsign == self.conference.active_speaker,
                "connected_seconds": int(st.connected_seconds),
                "idle_seconds": None if st.idle_seconds is None else int(st.idle_seconds),
            })
        return rows

    def _write_status(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_status < 5.0:
            return
        self._last_status = now
        status = {
            "callsign": self.cfg.echolink.callsign,
            "connected_stations": len(self.conference.stations),
            "active_speaker": self.conference.active_speaker,
            "roster": self.conference.roster_text(),
            "stations": self.connected_station_rows(),
            "stats": self.stats.__dict__,
            "router": self.router.snapshot(),
            "dmr_audio_active": self._dmr_audio_active,
            "note": "EchoLink UDP conference service is running.",
        }
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        self.status_file.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        log.info(
            "echolink_status stations=%s active=%s gsm_rx=%s gsm_tx=%s usrp_tx=%s usrp_rx=%s",
            status["connected_stations"],
            status["active_speaker"],
            self.stats.gsm_packets_decoded,
            self.stats.gsm_packets_sent,
            self.stats.pcm_frames_to_usrp,
            self.stats.usrp_frames_from_dmr,
        )

    def run(self, *, seconds: float | None = None) -> None:
        self.start()
        end_at = time.monotonic() + seconds if seconds is not None and seconds > 0 else None
        try:
            while not self._stop:
                if end_at is not None and time.monotonic() >= end_at:
                    break
                readable = [s for s in (self._audio_sock, self._control_sock, self._usrp_sock) if s is not None]
                if not readable:
                    break
                try:
                    r, _, _ = select.select(readable, [], [], 0.25)
                except (OSError, ValueError) as exc:
                    if self._stop:
                        break
                    raise
                for sock in r:
                    try:
                        data, addr = sock.recvfrom(4096)
                    except OSError:
                        continue
                    if sock is self._control_sock:
                        self._handle_control(data, addr)
                    elif sock is self._audio_sock:
                        self._handle_audio(data, addr)
                    elif sock is self._usrp_sock:
                        self._handle_usrp(data, addr)
                # EchoLink RTP/GSM clients do not always send an explicit
                # unkey packet.  Release the active EchoLink speaker after a
                # short audio-idle timeout so the next connected station can
                # key up.
                speaker_timeout_s = max(0.25, float(max(self.cfg.bridge.tx_hang_ms, self.cfg.audio.silence_gap_ms)) / 1000.0)
                if self.conference.release_inactive_speaker(speaker_timeout_s):
                    self._queue_usrp_unkey(clear_pending=True)
                self._release_inactive_dmr_audio()
                self._expire_stale_peers()
                self._send_periodic_roster()
                self._write_status()
        finally:
            self.close()


def require_gsm() -> None:
    try:
        codec = Gsm610Codec.create()
        codec.close()
    except GsmUnavailable:
        raise
