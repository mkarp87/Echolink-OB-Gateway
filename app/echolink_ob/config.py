from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class BridgeConfig:
    max_transmit_seconds: int
    tx_hang_ms: int
    enabled: bool


@dataclass(frozen=True)
class EchoLinkConfig:
    callsign: str
    password: str
    max_connected_stations: int
    bind_host: str
    audio_port: int
    control_port: int
    directory_host: str
    directory_port: int
    location: str
    status_text: str
    register_with_directory: bool
    directory_refresh_seconds: int
    client_timeout_seconds: int


@dataclass(frozen=True)
class ConferenceConfig:
    max_stations: int
    always_repeat_echolink_audio: bool
    one_echolink_speaker_at_a_time: bool


@dataclass(frozen=True)
class OpenBridgeConfig:
    host: str
    port: int
    passphrase: bytes
    network_id: int
    fixed_tgid: int
    slot: int
    call_type: str
    local_bind_host: str
    local_bind_port: int
    both_slots: bool


@dataclass(frozen=True)
class IdentityConfig:
    fallback_source_id: int
    strip_suffixes: tuple[str, ...]
    radioid_file: str
    radioid_url: str
    radioid_fallback_url: str
    auto_download_radioid: bool
    manual_overrides_file: str
    positive_cache_days: int
    negative_cache_hours: int


@dataclass(frozen=True)
class VocoderConfig:
    preferred: str
    fallback: str
    allow_fallback: bool
    switch_back_when_idle: bool
    allow_mid_stream_switch: bool


@dataclass(frozen=True)
class EndpointConfig:
    host: str
    port: int
    timeout_ms: int
    enabled: bool = True
    auto_start: bool = False
    reuse_existing: bool = True
    qemu_path: str = "/opt/md380-emu/qemu-arm-static"
    binary_path: str = "/opt/md380-emu/md380-emu"
    startup_wait_seconds: float = 2.0


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int
    channels: int
    fmt: str
    frame_ms: int
    jitter_buffer_ms: int
    silence_gap_ms: int


@dataclass(frozen=True)
class PortManagerConfig:
    enabled: bool
    host: str
    range_start: int
    range_end: int
    reserved_ports: tuple[int, ...]
    state_file: str
    reuse_existing_allocation: bool


@dataclass(frozen=True)
class AnalogBridgeConfig:
    enabled: bool
    auto_manage_ports: bool
    ini_path: str
    app_usrp_rx_port: int | str
    app_usrp_tx_port: int | str
    app_tlv_rx_port: int | str
    app_tlv_tx_port: int | str
    log_level: int
    export_metadata: bool
    transfer_root_dir: str
    subscriber_file: str
    decoder_fallback: bool
    use_emulator: bool
    emulator_address: str
    pcm_port: int
    min_tx_time_ms: int
    repeater_id: int
    tx_ts: int
    color_code: int
    usrp_audio: str
    usrp_gain: float
    usrp_agc: str
    tlv_audio: str
    tlv_gain: float


@dataclass(frozen=True)
class AccessConfig:
    allow_echolink_callsigns: tuple[str, ...]
    deny_echolink_callsigns: tuple[str, ...]
    banlist_file: str
    allowlist_file: str
    allow_duplicate_callsigns: bool
    kick_idle_stations: bool
    max_idle_minutes: int
    allow_echolink_users: bool
    allow_echolink_links: bool
    allow_echolink_repeaters: bool
    allow_echolink_conferences: bool


@dataclass(frozen=True)
class DashboardConfig:
    enabled: bool
    listen_host: str
    listen_port: int
    require_auth: bool
    last_heard_file: str
    last_heard_limit: int
    control_file: str
    push_interval_seconds: float


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    log_file: str


@dataclass(frozen=True)
class AppConfig:
    bridge: BridgeConfig
    echolink: EchoLinkConfig
    conference: ConferenceConfig
    openbridge: OpenBridgeConfig
    identity: IdentityConfig
    vocoder: VocoderConfig
    ambeserver: EndpointConfig
    md380emu: EndpointConfig
    audio: AudioConfig
    port_manager: PortManagerConfig
    analog_bridge: AnalogBridgeConfig
    access: AccessConfig
    dashboard: DashboardConfig
    logging: LoggingConfig


def load_config(path: str | Path) -> AppConfig:
    p = Path(path)
    with p.open("rb") as f:
        raw = tomllib.load(f)

    bridge = raw.get("bridge", {})
    echolink = raw.get("echolink", {})
    conference = raw.get("conference", {})
    ob = raw.get("openbridge", {})
    identity = raw.get("identity", {})
    vocoder = raw.get("vocoder", {})
    ambeserver = raw.get("ambeserver", {})
    md380emu = raw.get("md380emu", {})
    audio = raw.get("audio", {})
    port_manager = raw.get("port_manager", {})
    analog_bridge = raw.get("analog_bridge", {})
    access = raw.get("access", {})
    dashboard = raw.get("dashboard", {})
    logging = raw.get("logging", {})

    return AppConfig(
        bridge=BridgeConfig(
            max_transmit_seconds=int(bridge.get("max_transmit_seconds", 180)),
            tx_hang_ms=int(bridge.get("tx_hang_ms", 500)),
            enabled=bool(bridge.get("enabled", True)),
        ),
        echolink=EchoLinkConfig(
            callsign=str(echolink.get("callsign", "CHANGE_ME")),
            password=str(echolink.get("password", "CHANGE_ME")),
            max_connected_stations=int(echolink.get("max_connected_stations", 50)),
            bind_host=str(echolink.get("bind_host", "0.0.0.0")),
            audio_port=int(echolink.get("audio_port", 5198)),
            control_port=int(echolink.get("control_port", 5199)),
            directory_host=str(echolink.get("directory_host", "servers.echolink.org")),
            directory_port=int(echolink.get("directory_port", 5200)),
            location=str(echolink.get("location", "EchoLink OpenBridge Gateway")),
            status_text=str(echolink.get("status_text", "EchoLink OpenBridge Gateway")),
            register_with_directory=bool(echolink.get("register_with_directory", True)),
            directory_refresh_seconds=int(echolink.get("directory_refresh_seconds", 480)),
            client_timeout_seconds=int(echolink.get("client_timeout_seconds", 120)),
        ),
        conference=ConferenceConfig(
            max_stations=int(conference.get("max_stations", 50)),
            always_repeat_echolink_audio=bool(
                conference.get("always_repeat_echolink_audio", True)
            ),
            one_echolink_speaker_at_a_time=bool(
                conference.get("one_echolink_speaker_at_a_time", True)
            ),
        ),
        openbridge=OpenBridgeConfig(
            host=str(ob.get("host", "127.0.0.1")),
            port=int(ob.get("port", 62035)),
            passphrase=str(ob.get("passphrase", "CHANGE_ME")).encode(),
            network_id=int(ob.get("network_id", 310999901)),
            fixed_tgid=int(ob.get("fixed_tgid", 3100)),
            slot=int(ob.get("slot", 1)),
            call_type=str(ob.get("call_type", "group")),
            local_bind_host=str(ob.get("local_bind_host", "0.0.0.0")),
            local_bind_port=int(ob.get("local_bind_port", 0)),
            both_slots=bool(ob.get("both_slots", False)),
        ),
        identity=IdentityConfig(
            fallback_source_id=int(identity.get("fallback_source_id", 3109999)),
            strip_suffixes=tuple(identity.get("strip_suffixes", ["-L", "-R", "-M"])),
            radioid_file=str(identity.get("radioid_file", "/opt/echolink-ob/data/users.json")),
            radioid_url=str(identity.get("radioid_url", "https://radioid.net/static/users.json")),
            radioid_fallback_url=str(identity.get("radioid_fallback_url", "https://radioid.net/static/user.csv")),
            auto_download_radioid=bool(identity.get("auto_download_radioid", True)),
            manual_overrides_file=str(
                identity.get("manual_overrides_file", "/opt/echolink-ob/data/overrides.toml")
            ),
            positive_cache_days=int(identity.get("positive_cache_days", 30)),
            negative_cache_hours=int(identity.get("negative_cache_hours", 24)),
        ),
        vocoder=VocoderConfig(
            preferred=str(vocoder.get("preferred", "md380emu")),
            fallback=str(vocoder.get("fallback", "ambeserver")),
            allow_fallback=bool(vocoder.get("allow_fallback", True)),
            switch_back_when_idle=bool(vocoder.get("switch_back_when_idle", True)),
            allow_mid_stream_switch=bool(vocoder.get("allow_mid_stream_switch", False)),
        ),
        ambeserver=EndpointConfig(
            host=str(ambeserver.get("host", "127.0.0.1")),
            port=int(ambeserver.get("port", 2460)),
            timeout_ms=int(ambeserver.get("timeout_ms", 500)),
            enabled=True,
        ),
        md380emu=EndpointConfig(
            host=str(md380emu.get("host", "127.0.0.1")),
            port=int(md380emu.get("port", 2990)),
            timeout_ms=int(md380emu.get("timeout_ms", 500)),
            enabled=bool(md380emu.get("enabled", True)),
            auto_start=bool(md380emu.get("auto_start", False)),
            reuse_existing=bool(md380emu.get("reuse_existing", True)),
            qemu_path=str(md380emu.get("qemu_path", "/opt/md380-emu/qemu-arm-static")),
            binary_path=str(md380emu.get("binary_path", "/opt/md380-emu/md380-emu")),
            startup_wait_seconds=float(md380emu.get("startup_wait_seconds", 2.0)),
        ),
        audio=AudioConfig(
            sample_rate=int(audio.get("sample_rate", 8000)),
            channels=int(audio.get("channels", 1)),
            fmt=str(audio.get("format", "s16le")),
            frame_ms=int(audio.get("frame_ms", 20)),
            jitter_buffer_ms=int(audio.get("jitter_buffer_ms", 120)),
            silence_gap_ms=int(audio.get("silence_gap_ms", 700)),
        ),
        port_manager=PortManagerConfig(
            enabled=bool(port_manager.get("enabled", True)),
            host=str(port_manager.get("host", "127.0.0.1")),
            range_start=int(port_manager.get("range_start", 33000)),
            range_end=int(port_manager.get("range_end", 33199)),
            reserved_ports=tuple(int(p) for p in port_manager.get("reserved_ports", [])),
            state_file=str(port_manager.get("state_file", "/opt/echolink-ob/data/port-plan.json")),
            reuse_existing_allocation=bool(port_manager.get("reuse_existing_allocation", True)),
        ),
        analog_bridge=AnalogBridgeConfig(
            enabled=bool(analog_bridge.get("enabled", True)),
            auto_manage_ports=bool(analog_bridge.get("auto_manage_ports", True)),
            ini_path=str(analog_bridge.get("ini_path", "/opt/echolink-ob/generated/Analog_Bridge.ini")),
            app_usrp_rx_port=analog_bridge.get("app_usrp_rx_port", "auto"),
            app_usrp_tx_port=analog_bridge.get("app_usrp_tx_port", "auto"),
            app_tlv_rx_port=analog_bridge.get("app_tlv_rx_port", "auto"),
            app_tlv_tx_port=analog_bridge.get("app_tlv_tx_port", "auto"),
            log_level=int(analog_bridge.get("log_level", 0)),
            export_metadata=bool(analog_bridge.get("export_metadata", True)),
            transfer_root_dir=str(analog_bridge.get("transfer_root_dir", "/tmp")),
            subscriber_file=str(analog_bridge.get("subscriber_file", "/var/lib/dvswitch/subscriber_ids.csv")),
            decoder_fallback=bool(analog_bridge.get("decoder_fallback", True)),
            use_emulator=bool(analog_bridge.get("use_emulator", False)),
            emulator_address=str(analog_bridge.get("emulator_address", "127.0.0.1:2990")),
            pcm_port=int(analog_bridge.get("pcm_port", 2222)),
            min_tx_time_ms=int(analog_bridge.get("min_tx_time_ms", 2500)),
            repeater_id=int(analog_bridge.get("repeater_id", 31000190)),
            tx_ts=int(analog_bridge.get("tx_ts", 2)),
            color_code=int(analog_bridge.get("color_code", 1)),
            usrp_audio=str(analog_bridge.get("usrp_audio", "AUDIO_USE_AGC")),
            usrp_gain=float(analog_bridge.get("usrp_gain", 1.10)),
            usrp_agc=str(analog_bridge.get("usrp_agc", "-20,10,100")),
            tlv_audio=str(analog_bridge.get("tlv_audio", "AUDIO_BPF")),
            tlv_gain=float(analog_bridge.get("tlv_gain", 1.0)),
        ),
        access=AccessConfig(
            allow_echolink_callsigns=tuple(str(x) for x in access.get("allow_echolink_callsigns", ["*"])),
            deny_echolink_callsigns=tuple(str(x) for x in access.get("deny_echolink_callsigns", [])),
            banlist_file=str(access.get("banlist_file", "/opt/echolink-ob/data/banlist.txt")),
            allowlist_file=str(access.get("allowlist_file", "/opt/echolink-ob/data/allowlist.txt")),
            allow_duplicate_callsigns=bool(access.get("allow_duplicate_callsigns", False)),
            kick_idle_stations=bool(access.get("kick_idle_stations", False)),
            max_idle_minutes=int(access.get("max_idle_minutes", 0)),
            allow_echolink_users=bool(access.get("allow_echolink_users", True)),
            allow_echolink_links=bool(access.get("allow_echolink_links", True)),
            allow_echolink_repeaters=bool(access.get("allow_echolink_repeaters", True)),
            allow_echolink_conferences=bool(access.get("allow_echolink_conferences", False)),
        ),
        dashboard=DashboardConfig(
            enabled=bool(dashboard.get("enabled", True)),
            listen_host=str(dashboard.get("listen_host", "127.0.0.1")),
            listen_port=int(dashboard.get("listen_port", 8080)),
            require_auth=bool(dashboard.get("require_auth", False)),
            last_heard_file=str(dashboard.get("last_heard_file", "/opt/echolink-ob/data/lastheard.json")),
            last_heard_limit=int(dashboard.get("last_heard_limit", 20)),
            control_file=str(dashboard.get("control_file", "/opt/echolink-ob/data/dashboard-commands.jsonl")),
            push_interval_seconds=float(dashboard.get("push_interval_seconds", 1.5)),
        ),
        logging=LoggingConfig(
            level=str(logging.get("level", "INFO")),
            log_file=str(logging.get("log_file", "/opt/echolink-ob/logs/echolink-ob.log")),
        ),
    )
