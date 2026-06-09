#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/echolink-ob}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
INSTALL_DEV="${INSTALL_DEV:-1}"
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-0}"
NO_APT="${NO_APT:-0}"
SKIP_PIP_UPGRADE="${SKIP_PIP_UPGRADE:-0}"
OVERWRITE_CONFIG="${OVERWRITE_CONFIG:-0}"
INSTALL_DVSWITCH_DEPS="${INSTALL_DVSWITCH_DEPS:-1}"
INSTALL_DVSWITCH_REPO="${INSTALL_DVSWITCH_REPO:-1}"
DOWNLOAD_RADIOID="${DOWNLOAD_RADIOID:-1}"
SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<USAGE
Usage: ./scripts/install.sh [options]

Installs echolink-ob from a Git clone/source checkout into /opt/echolink-ob by default.
It is safe to run from a clone directory, such as ~/src/echolink-ob.
It is also safe to re-run from /opt/echolink-ob.

Options:
  --app-root PATH       Install/runtime directory. Default: /opt/echolink-ob
  --python PATH         Python executable. Default: python3.12
  --no-apt              Do not install apt dependencies automatically
  --no-dev              Install runtime package only, not test/dev dependencies
  --systemd             Install systemd unit, reload daemon, and enable service
  --overwrite-config    Replace /opt config.toml from source config.toml/config-sample.toml
  --no-dvswitch-deps    Do not try to install Analog_Bridge/md380-emu
  --no-dvswitch-repo    Do not add the DVSwitch apt repository automatically
  --no-radioid-download Do not download /opt/echolink-ob/data/users.json during install
  -h, --help            Show this help

Environment overrides:
  APP_ROOT=/opt/echolink-ob
  PYTHON_BIN=python3.12
  INSTALL_DEV=1
  INSTALL_SYSTEMD=0
  NO_APT=0
  OVERWRITE_CONFIG=0
  INSTALL_DVSWITCH_DEPS=1
  INSTALL_DVSWITCH_REPO=1
  DOWNLOAD_RADIOID=1
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --app-root)
      APP_ROOT="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --no-apt)
      NO_APT=1
      shift
      ;;
    --no-dev)
      INSTALL_DEV=0
      shift
      ;;
    --systemd)
      INSTALL_SYSTEMD=1
      shift
      ;;
    --overwrite-config)
      OVERWRITE_CONFIG=1
      shift
      ;;
    --no-dvswitch-deps)
      INSTALL_DVSWITCH_DEPS=0
      shift
      ;;
    --no-dvswitch-repo)
      INSTALL_DVSWITCH_REPO=0
      shift
      ;;
    --no-radioid-download)
      DOWNLOAD_RADIOID=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "ERROR: sudo is required when not running as root." >&2
    exit 1
  fi
  SUDO="sudo"
fi

log() { echo "[install] $*"; }
warn() { echo "[install] WARNING: $*" >&2; }
fail() { echo "[install] ERROR: $*" >&2; exit 1; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

apt_install_deps() {
  if [ "$NO_APT" = "1" ]; then
    log "Skipping apt dependency install because --no-apt/NO_APT=1 was set"
    return 0
  fi
  if ! have_cmd apt-get; then
    warn "apt-get not found. Skipping automatic system dependency install."
    return 0
  fi

  local packages=(
    ca-certificates
    git
    rsync
    build-essential
    python3-pip
    curl
    wget
    gnupg
    lsb-release
    libgsm1
    libgsm1-dev
  )

  # Prefer the requested Python package name when it looks like python3.12.
  if [ "$PYTHON_BIN" = "python3.12" ]; then
    packages+=(python3.12 python3.12-venv python3.12-dev)
  else
    packages+=(python3 python3-venv python3-dev)
  fi

  log "Installing/checking apt dependencies: ${packages[*]}"
  $SUDO apt-get update
  if ! DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "${packages[@]}"; then
    warn "apt install did not complete successfully. Continuing to check existing tools."
  fi
}

find_python() {
  if have_cmd "$PYTHON_BIN"; then
    printf '%s\n' "$PYTHON_BIN"
    return 0
  fi
  if have_cmd python3.12; then
    printf '%s\n' python3.12
    return 0
  fi
  if have_cmd python3; then
    local pyver
    pyver="$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
    case "$pyver" in
      3.12|3.13|3.14|3.15|3.16|3.17|3.18|3.19)
        printf '%s\n' python3
        return 0
        ;;
    esac
  fi
  return 1
}

check_python_version() {
  local py="$1"
  "$py" - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit(f"Python 3.12+ required, found {sys.version.split()[0]}")
print(sys.version.split()[0])
PY
}

find_analog_bridge_binary() {
  local candidates=(
    "$(command -v Analog_Bridge 2>/dev/null || true)"
    "$(command -v analog_bridge 2>/dev/null || true)"
    /usr/bin/Analog_Bridge
    /usr/local/bin/Analog_Bridge
    /opt/Analog_Bridge/Analog_Bridge
    /opt/Analog_Bridge/analog_bridge
  )
  local c
  for c in "${candidates[@]}"; do
    if [ -n "$c" ] && [ -x "$c" ]; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

find_md380emu_binary() {
  local candidates=(
    "$(command -v md380-emu 2>/dev/null || true)"
    "$(command -v md380emu 2>/dev/null || true)"
    /usr/bin/md380-emu
    /usr/local/bin/md380-emu
    /opt/md380-emu/md380-emu
  )
  local c
  for c in "${candidates[@]}"; do
    if [ -n "$c" ] && [ -x "$c" ]; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

try_install_dvswitch_packages() {
  if [ "$NO_APT" = "1" ]; then
    warn "Skipping DVSwitch dependency install because --no-apt/NO_APT=1 was set"
    return 0
  fi
  if [ "$INSTALL_DVSWITCH_DEPS" != "1" ]; then
    log "Skipping Analog_Bridge/md380-emu install because --no-dvswitch-deps was set"
    return 0
  fi
  if ! have_cmd apt-get; then
    warn "apt-get not found. Cannot automatically install Analog_Bridge/md380-emu."
    return 0
  fi

  local need_ab=0
  local need_emu=0
  if ! find_analog_bridge_binary >/dev/null 2>&1; then
    need_ab=1
  fi
  if ! find_md380emu_binary >/dev/null 2>&1; then
    need_emu=1
  fi

  if [ "$need_ab" = "0" ] && [ "$need_emu" = "0" ]; then
    log "Analog_Bridge and md380-emu appear to be installed"
    return 0
  fi

  local packages=()
  [ "$need_ab" = "1" ] && packages+=(analog-bridge)
  [ "$need_emu" = "1" ] && packages+=(md380-emu)

  log "Installing/checking DVSwitch dependencies: ${packages[*]}"
  $SUDO apt-get update
  if DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "${packages[@]}"; then
    return 0
  fi

  if [ "$INSTALL_DVSWITCH_REPO" != "1" ]; then
    warn "Could not install ${packages[*]} and DVSwitch repo auto-install is disabled."
    return 0
  fi

  warn "DVSwitch packages were not available from current apt sources; attempting to add DVSwitch apt repository."
  if ! have_cmd wget && ! have_cmd curl; then
    warn "wget/curl is required to add the DVSwitch repository. Install wget or curl and rerun."
    return 0
  fi

  local repo_script=/tmp/install-dvswitch-repo
  rm -f "$repo_script"
  if have_cmd wget; then
    wget -q -O "$repo_script" http://dvswitch.org/install-dvswitch-repo || true
  fi
  if [ ! -s "$repo_script" ] && have_cmd curl; then
    curl -fsSL http://dvswitch.org/install-dvswitch-repo -o "$repo_script" || true
  fi
  if [ ! -s "$repo_script" ]; then
    warn "Could not download http://dvswitch.org/install-dvswitch-repo. Install Analog_Bridge manually, then rerun."
    return 0
  fi

  chmod +x "$repo_script"
  if ! $SUDO "$repo_script"; then
    warn "DVSwitch repository setup script failed. Install Analog_Bridge manually, then rerun."
    return 0
  fi

  $SUDO apt-get update
  if ! DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "${packages[@]}"; then
    warn "Could not install ${packages[*]} after adding DVSwitch repo. Continuing; app components can still be tested without Analog_Bridge."
  fi
}

report_external_deps() {
  local ab=""
  local emu=""
  if ab="$(find_analog_bridge_binary 2>/dev/null)"; then
    log "Analog_Bridge found: $ab"
  else
    warn "Analog_Bridge not found. Analog_Bridge audio tests will not run until analog-bridge is installed."
  fi
  if emu="$(find_md380emu_binary 2>/dev/null)"; then
    log "md380-emu found: $emu"
  else
    warn "md380-emu not found. This is acceptable if AMBEServer is available; md380 fallback will be unavailable."
  fi
}

apt_install_deps
try_install_dvswitch_packages
report_external_deps

if ! PYTHON_ACTUAL="$(find_python)"; then
  fail "Python 3.12+ was not found. Install python3.12 and python3.12-venv, or rerun with --python /path/to/python3.12."
fi
PYTHON_VERSION="$(check_python_version "$PYTHON_ACTUAL")"
log "Using Python: $PYTHON_ACTUAL ($PYTHON_VERSION)"

if ! have_cmd rsync; then
  fail "rsync is required. Install it with: apt-get install -y rsync"
fi

$SUDO mkdir -p "$APP_ROOT"
APP_ROOT_REAL="$(cd "$APP_ROOT" && pwd)"
SRC_ROOT_REAL="$(cd "$SRC_ROOT" && pwd)"

EXISTING_CONFIG=0
if [ -f "$APP_ROOT_REAL/config/config.toml" ]; then
  EXISTING_CONFIG=1
fi

if [ "$SRC_ROOT_REAL" != "$APP_ROOT_REAL" ]; then
  log "Copying source from $SRC_ROOT_REAL to $APP_ROOT_REAL"
  $SUDO rsync -a \
    --exclude .git \
    --exclude .github \
    --exclude .pytest_cache \
    --exclude "*.egg-info" \
    --exclude __pycache__ \
    --exclude venv \
    --exclude .venv \
    --exclude logs/ \
    --exclude diagnostics/ \
    --exclude data/cache.sqlite \
    --exclude data/users.json \
    --exclude data/users.csv \
    --exclude config/config.toml \
    "$SRC_ROOT_REAL"/ "$APP_ROOT_REAL"/
else
  log "Source and install directory are the same; skipping copy step"
fi

$SUDO mkdir -p "$APP_ROOT_REAL/data" "$APP_ROOT_REAL/logs" "$APP_ROOT_REAL/diagnostics" "$APP_ROOT_REAL/config" "$APP_ROOT_REAL/generated"

if [ "$OVERWRITE_CONFIG" = "1" ] && [ -f "$APP_ROOT_REAL/config/config.toml" ]; then
  log "Backing up existing config because --overwrite-config was requested"
  $SUDO cp "$APP_ROOT_REAL/config/config.toml" "$APP_ROOT_REAL/config/config.toml.bak.$(date +%Y%m%d%H%M%S)"
  $SUDO rm -f "$APP_ROOT_REAL/config/config.toml"
fi

if [ ! -f "$APP_ROOT_REAL/config/config.toml" ]; then
  if [ -f "$SRC_ROOT_REAL/config/config.toml" ]; then
    log "Installing pre-populated private config from source package"
    $SUDO cp "$SRC_ROOT_REAL/config/config.toml" "$APP_ROOT_REAL/config/config.toml"
    $SUDO chmod 600 "$APP_ROOT_REAL/config/config.toml"
  elif [ -f "$APP_ROOT_REAL/config/config-sample.toml" ]; then
    log "Creating $APP_ROOT_REAL/config/config.toml from config-sample.toml"
    $SUDO cp "$APP_ROOT_REAL/config/config-sample.toml" "$APP_ROOT_REAL/config/config.toml"
    $SUDO chmod 600 "$APP_ROOT_REAL/config/config.toml"
  else
    fail "Missing $APP_ROOT_REAL/config/config-sample.toml"
  fi
else
  log "Keeping existing private config: $APP_ROOT_REAL/config/config.toml"
fi

cd "$APP_ROOT_REAL"

if [ ! -x "$APP_ROOT_REAL/venv/bin/python" ] || [ ! -x "$APP_ROOT_REAL/venv/bin/pip" ]; then
  log "Creating Python virtualenv at $APP_ROOT_REAL/venv"
  $SUDO rm -rf "$APP_ROOT_REAL/venv"
  $SUDO "$PYTHON_ACTUAL" -m venv "$APP_ROOT_REAL/venv"
fi

if [ ! -x "$APP_ROOT_REAL/venv/bin/python" ]; then
  fail "Virtualenv python was not created at $APP_ROOT_REAL/venv/bin/python"
fi

log "Ensuring pip is available in the virtualenv"
$SUDO "$APP_ROOT_REAL/venv/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true

if [ ! -x "$APP_ROOT_REAL/venv/bin/pip" ]; then
  fail "pip was not created in the virtualenv. Install the matching python venv package, such as python3.12-venv."
fi

if [ "$SKIP_PIP_UPGRADE" != "1" ]; then
  log "Upgrading pip/setuptools/wheel"
  if ! $SUDO "$APP_ROOT_REAL/venv/bin/python" -m pip install --upgrade pip setuptools wheel; then
    warn "pip/setuptools/wheel upgrade failed. Continuing; package install may use direct-source fallback."
  fi
fi

create_direct_source_wrappers() {
  log "Ensuring command wrappers exist in $APP_ROOT_REAL/venv/bin"
  local py="$APP_ROOT_REAL/venv/bin/python"
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.main "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-openbridge" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.openbridge.test_sender "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-audio-selftest" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.tools.audio_selftest "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-record-dmr" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.openbridge.recorder "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-analyze-dmr" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.openbridge.analyzer "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-extract-ambe" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.dmr.ambe "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-analog-plan" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.analog.ports "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-replay-dmr" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.openbridge.replay "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-run" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.run "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-analog-tone" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.analog.tone "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-analog-tone-openbridge" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.analog.tone_openbridge "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-bridge" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.bridge.runtime "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-usrp-capture" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.analog.usrp_capture "\$@"
WRAP
  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-echolink-selftest" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.echolink.selftest "\$@"
WRAP

  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-echolink-preflight" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.echolink.network "\$@"
WRAP

  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-echolink-runtime" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.echolink.runtime "\$@"
WRAP

  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-echolink-integration-selftest" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.echolink.integration_selftest "\$@"
WRAP

  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-directory" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.echolink.directory "\$@"
WRAP

  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-full" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.full_runtime "\$@"
WRAP

  cat > "$APP_ROOT_REAL/venv/bin/echolink-ob-radioid-update" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_ROOT_REAL/app:\${PYTHONPATH:-}"
exec "$py" -m echolink_ob.identity.radioid_update "\$@"
WRAP
  chmod +x "$APP_ROOT_REAL/venv/bin"/echolink-ob*
  touch "$APP_ROOT_REAL/venv/.direct-source-install"
}

INSTALL_OK=0
if [ "$INSTALL_DEV" = "1" ]; then
  log "Installing echolink-ob with dev/test dependencies"
  if $SUDO "$APP_ROOT_REAL/venv/bin/python" -m pip install --no-build-isolation -e '.[dev]'; then
    INSTALL_OK=1
  fi
else
  log "Installing echolink-ob runtime package"
  if $SUDO "$APP_ROOT_REAL/venv/bin/python" -m pip install --no-build-isolation -e .; then
    INSTALL_OK=1
  fi
fi

if [ "$INSTALL_OK" != "1" ]; then
  warn "Python package install failed. Falling back to direct source wrappers."
  warn "The direct-source wrappers are usable because the runtime imports from the installed source tree."
fi

# Always create/update direct-source wrappers for command entry points.
# This prevents stale editable-install entry points when installing over an older package.
create_direct_source_wrappers

if [ "$DOWNLOAD_RADIOID" = "1" ]; then
  if grep -q '^auto_download_radioid *= *false' "$APP_ROOT_REAL/config/config.toml" 2>/dev/null; then
    log "RadioID auto-download disabled by config"
  elif [ -s "$APP_ROOT_REAL/data/users.json" ]; then
    log "RadioID database already exists: $APP_ROOT_REAL/data/users.json"
  else
    log "Downloading RadioID database to $APP_ROOT_REAL/data/users.json"
    if ! $SUDO "$APP_ROOT_REAL/venv/bin/echolink-ob-radioid-update" --config "$APP_ROOT_REAL/config/config.toml" --force; then
      warn "RadioID database download failed. You can retry later with: $APP_ROOT_REAL/venv/bin/echolink-ob-radioid-update --config $APP_ROOT_REAL/config/config.toml --force"
    fi
  fi
else
  log "Skipping RadioID database download because --no-radioid-download/DOWNLOAD_RADIOID=0 was set"
fi

$SUDO chmod +x "$APP_ROOT_REAL/scripts"/*.sh 2>/dev/null || true

if [ "$INSTALL_SYSTEMD" = "1" ]; then
  if ! have_cmd systemctl; then
    warn "systemctl not found; skipping systemd install"
  else
    log "Installing systemd unit"
    $SUDO cp "$APP_ROOT_REAL/systemd/echolink-ob.service" /etc/systemd/system/echolink-ob.service
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable echolink-ob.service
    log "systemd unit installed and enabled. Start later with: sudo systemctl start echolink-ob"
  fi
fi

cat <<MSG

Installed echolink-ob to: $APP_ROOT_REAL
Private config file:      $APP_ROOT_REAL/config/config.toml
Virtualenv Python:        $APP_ROOT_REAL/venv/bin/python

Next commands:
  $APP_ROOT_REAL/scripts/run-tests.sh
  $APP_ROOT_REAL/venv/bin/echolink-ob --version
  $APP_ROOT_REAL/venv/bin/echolink-ob-run --config $APP_ROOT_REAL/config/config.toml
  $APP_ROOT_REAL/venv/bin/echolink-ob-analog-tone --config $APP_ROOT_REAL/config/config.toml --seconds 3 --frequency 1000 --output-dir $APP_ROOT_REAL/diagnostics/analog-tone
  $APP_ROOT_REAL/venv/bin/echolink-ob-bridge --config $APP_ROOT_REAL/config/config.toml --start-analog-bridge --dry-run
  $APP_ROOT_REAL/venv/bin/echolink-ob-openbridge --config $APP_ROOT_REAL/config/config.toml --mode listen --seconds 60
  $APP_ROOT_REAL/venv/bin/echolink-ob-echolink-preflight --config $APP_ROOT_REAL/config/config.toml --skip-directory

Review or edit the private config if needed:
  nano $APP_ROOT_REAL/config/config.toml

To replace an existing config from the source package:
  ./scripts/install.sh --overwrite-config

MSG
