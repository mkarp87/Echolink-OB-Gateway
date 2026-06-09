from __future__ import annotations

from dataclasses import dataclass, field
import logging
import socket
import time
from typing import Iterator

from .dmrd import DMRDPacket, verify_signed_dmrd

log = logging.getLogger(__name__)


@dataclass
class OpenBridgeCounters:
    packets_sent: int = 0
    packets_received: int = 0
    packets_rejected_hmac: int = 0
    packets_rejected_source: int = 0
    packets_rejected_tgid: int = 0
    packets_rejected_slot: int = 0
    packets_rejected_call_type: int = 0


@dataclass
class OpenBridgeClient:
    host: str
    port: int
    passphrase: bytes
    network_id: int
    fixed_tgid: int
    slot: int = 1
    call_type: str = "group"
    local_bind_host: str = "0.0.0.0"
    local_bind_port: int = 0
    both_slots: bool = False
    timeout: float = 0.5
    counters: OpenBridgeCounters = field(default_factory=OpenBridgeCounters)

    def __post_init__(self) -> None:
        self.target = (self.host, self.port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(self.timeout)
        self.sock.bind((self.local_bind_host, self.local_bind_port))
        log.info("openbridge_bound local=%s target=%s", self.sock.getsockname(), self.target)

    def close(self) -> None:
        self.sock.close()

    def send_packet(self, packet: DMRDPacket) -> None:
        signed = packet.to_signed(self.passphrase)
        self.sock.sendto(signed, self.target)
        self.counters.packets_sent += 1
        log.debug(
            "openbridge_sent seq=%s src=%s dst=%s stream=%s slot=%s frame=%s dtype=%s",
            packet.sequence,
            packet.rf_source_id,
            packet.destination_id,
            packet.stream_id,
            packet.slot,
            packet.frame_type,
            packet.dtype_vseq,
        )

    def recv_packet(self) -> DMRDPacket | None:
        try:
            data, sockaddr = self.sock.recvfrom(4096)
        except TimeoutError:
            return None
        except socket.timeout:
            return None

        if sockaddr != self.target:
            self.counters.packets_rejected_source += 1
            log.warning("openbridge_reject_source sockaddr=%s expected=%s", sockaddr, self.target)
            return None

        if not verify_signed_dmrd(data, self.passphrase):
            self.counters.packets_rejected_hmac += 1
            log.warning("openbridge_reject_hmac len=%s sockaddr=%s", len(data), sockaddr)
            return None

        packet = DMRDPacket.from_signed(data, self.passphrase)
        if packet.destination_id != self.fixed_tgid:
            self.counters.packets_rejected_tgid += 1
            log.info("openbridge_ignore_tgid got=%s wanted=%s", packet.destination_id, self.fixed_tgid)
            return None
        if packet.slot != self.slot and not self.both_slots and packet.call_type != "unit":
            self.counters.packets_rejected_slot += 1
            log.info("openbridge_ignore_slot got=%s wanted=%s", packet.slot, self.slot)
            return None
        if packet.call_type != self.call_type:
            self.counters.packets_rejected_call_type += 1
            log.info("openbridge_ignore_call_type got=%s wanted=%s", packet.call_type, self.call_type)
            return None

        self.counters.packets_received += 1
        log.info(
            "openbridge_received seq=%s src=%s dst=%s peer=%s slot=%s call=%s frame=%s dtype=%s stream=%s",
            packet.sequence,
            packet.rf_source_id,
            packet.destination_id,
            packet.network_id,
            packet.slot,
            packet.call_type,
            packet.frame_type,
            packet.dtype_vseq,
            packet.stream_id,
        )
        return packet

    def listen(self, seconds: float | None = None) -> Iterator[DMRDPacket]:
        end = None if seconds is None else time.monotonic() + seconds
        while end is None or time.monotonic() < end:
            pkt = self.recv_packet()
            if pkt is not None:
                yield pkt
