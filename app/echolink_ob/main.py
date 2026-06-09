from __future__ import annotations

import argparse
from . import __version__
from .config import load_config
from .logging_setup import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EchoLink OpenBridge Gateway")
    parser.add_argument("--config", default="/opt/echolink-ob/config/config.toml")
    parser.add_argument("--version", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"echolink-ob {__version__}")
        return 0
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.log_file)
    print("echolink-ob Beta 1.0 loaded")
    print(f"OpenBridge target {cfg.openbridge.host}:{cfg.openbridge.port}")
    print("Use echolink-ob-full to start the full gateway runtime.")
    print("Use scripts/run-tests.sh for self-tests and echolink-ob-full for production use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
