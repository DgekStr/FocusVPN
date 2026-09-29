#!/usr/bin/env bash
set -Eeuo pipefail

readonly SING_BOX_VERSION="1.14.2"
readonly WG_EASY_IMAGE="ghcr.io/wg-easy/wg-easy:15.4.0"
readonly INSTALL_ROOT="/opt/focusvpn-installer"
readonly APP_ROOT="/opt/sing-box-admin"
readonly CONFIG_ROOT="/etc/focusvpn"
readonly SING_BOX_ROOT="/etc/sing-box"
readonly HAPP_ROOT="/etc/sing-box-happ-server"
readonly ADMIN_ROOT="/etc/sing-box-admin"

ENABLE_SERVICES=0
START_WG_EASY=0

log() {
  printf '[focusvpn] %s\n' "$*"
}

fail() {
  printf '[focusvpn] error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install.sh [--start-wg-easy] [--enable]

Installs FocusVPN runtime files on a supported Debian or Ubuntu server.

Options:
  --start-wg-easy  Create and start the wg-easy container for its initial setup.
  --enable  Validate completed runtime configuration and enable FocusVPN services.
  -h, --help  Show this help.

The default mode installs dependencies, code, systemd units and safe templates,
but does not start network services. Complete runtime config files first, then
run the command again with --enable.
EOF
}

while (($#)); do
  case "$1" in
    --enable) ENABLE_SERVICES=1 ;;
    --start-wg-easy) START_WG_EASY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
  shift
done

[[ ${EUID} -eq 0 ]] || fail "run as root via sudo"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$REPO_ROOT/server/panel/app.py" ]] || fail "run this script from a FocusVPN git clone"

source /etc/os-release
case "${ID:-}" in
  debian|ubuntu) ;;
  *) fail "supported distributions: Debian 12+ and Ubuntu 22.04+" ;;
esac

ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  amd64|arm64) ;;
  *) fail "unsupported architecture: $ARCH (supported: amd64, arm64)" ;;
esac

install_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl docker.io nftables python3 qrencode tar
  systemctl enable --now docker
}

install_sing_box() {
  local current=""
  if command -v sing-box >/dev/null 2>&1; then
    current="$(sing-box version 2>/dev/null | awk 'NR == 1 { print $3 }')"
  fi
  if [[ "$current" == "$SING_BOX_VERSION" ]]; then
    log "sing-box $SING_BOX_VERSION already installed"
    return
  fi

  local temporary archive checksums asset base
  temporary="$(mktemp -d)"
  trap 'rm -rf "$temporary"' RETURN
  asset="sing-box-${SING_BOX_VERSION}-linux-${ARCH}.tar.gz"
  base="https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}"
  archive="$temporary/$asset"
  checksums="$temporary/checksums.txt"

  log "downloading sing-box $SING_BOX_VERSION for $ARCH"
  curl --fail --location --retry 3 --output "$archive" "$base/$asset"
  curl --fail --location --retry 3 --output "$checksums" "$base/checksums.txt"
  (cd "$temporary" && grep -F " $asset" checksums.txt | sha256sum --check --status) || fail "sing-box checksum verification failed"
  tar --extract --gzip --file "$archive" --directory "$temporary"
  install -m 0755 "$(find "$temporary" -type f -name sing-box -print -quit)" /usr/bin/sing-box
  [[ "$(sing-box version | awk 'NR == 1 { print $3 }')" == "$SING_BOX_VERSION" ]] || fail "installed sing-box version mismatch"
  trap - RETURN
  rm -rf "$temporary"
}

ensure_sing_box_account() {
  getent group sing-box >/dev/null || groupadd --system sing-box
  id -u sing-box >/dev/null 2>&1 || useradd --system --gid sing-box --home-dir /var/lib/sing-box --shell /usr/sbin/nologin sing-box
  install -d -m 0750 -o sing-box -g sing-box /var/lib/sing-box /var/lib/sing-box-happ-server
}

install_tree() {
  install -d -m 0755 "$APP_ROOT/static" /usr/local/libexec /etc/systemd/system/sing-box.service.d
  install -d -m 0750 "$CONFIG_ROOT" "$SING_BOX_ROOT" "$HAPP_ROOT" "$ADMIN_ROOT"
  install -d -m 0700 "$ADMIN_ROOT/backups" /etc/wg-easy

  find "$REPO_ROOT/server/panel" -maxdepth 1 -type f -name '*.py' -exec install -m 0644 {} "$APP_ROOT/" \;
  find "$REPO_ROOT/server/panel/static" -maxdepth 1 -type f -exec install -m 0644 {} "$APP_ROOT/static/" \;
  find "$REPO_ROOT/server/libexec" -maxdepth 1 -type f -exec install -m 0750 {} /usr/local/libexec/ \;
  find "$REPO_ROOT/server/systemd" -maxdepth 1 \( -name '*.service' -o -name '*.timer' \) -type f -exec install -m 0644 {} /etc/systemd/system/ \;
  install -d -m 0755 /etc/systemd/system/sing-box.service.d
  install -m 0644 "$REPO_ROOT/server/systemd/sing-box.service.d/gateway.conf" /etc/systemd/system/sing-box.service.d/gateway.conf
  install -m 0644 "$REPO_ROOT/server/systemd/sing-box.service" /etc/systemd/system/sing-box.service
}

install_if_missing() {
  local source="$1"
  local destination="$2"
  local mode="$3"
  local owner="$4"
  local group="$5"
  if [[ ! -e "$destination" ]]; then
    install -m "$mode" -o "$owner" -g "$group" "$source" "$destination"
    log "created template $destination"
  fi
}

initialize_runtime_files() {
  install_if_missing "$REPO_ROOT/server/config/focusvpn/focusvpn.env.example" "$CONFIG_ROOT/focusvpn.env" 0600 root root
  install_if_missing "$REPO_ROOT/server/config/sing-box/config.example.json" "$SING_BOX_ROOT/config.json" 0640 root sing-box
  install_if_missing "$REPO_ROOT/server/config/sing-box/ru.zone" "$SING_BOX_ROOT/ru.zone" 0640 root sing-box
  install_if_missing "$REPO_ROOT/server/config/sing-box-happ-server/config.example.json" "$HAPP_ROOT/config.json" 0640 root sing-box
  install_if_missing "$REPO_ROOT/server/config/sing-box-admin/happ-server.example.json" "$ADMIN_ROOT/happ-server.json" 0600 root root
  install_if_missing "$REPO_ROOT/server/config/sing-box-admin/wg-easy-api.example.json" "$ADMIN_ROOT/wg-easy-api.json" 0600 root root
  install_if_missing "$REPO_ROOT/server/config/sing-box-admin/wg-lan-deny.example.json" "$ADMIN_ROOT/wg-lan-deny.json" 0600 root root

  set -a
  source "$CONFIG_ROOT/focusvpn.env"
  set +a
  [[ "${FOCUSVPN_WG_INTERFACE:-}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "FOCUSVPN_WG_INTERFACE is invalid"
  python3 - <<'PY'
import ipaddress
import os
for name in ('FOCUSVPN_WG_NETWORK', 'FOCUSVPN_LAN_NETWORK', 'FOCUSVPN_MANAGEMENT_NETWORK'):
    ipaddress.ip_network(os.environ[name], strict=False)
print('FocusVPN network settings validated')
PY

  render_template "$REPO_ROOT/server/config/sing-box/tproxy.nft.template" "$SING_BOX_ROOT/tproxy.nft"
  render_template "$REPO_ROOT/server/config/sing-box-admin/wg-easy-private-ui.nft.template" "$ADMIN_ROOT/wg-easy-private-ui.nft"
  chown root:sing-box "$SING_BOX_ROOT/tproxy.nft" "$SING_BOX_ROOT/ru.zone" "$SING_BOX_ROOT/config.json"
  chmod 0640 "$SING_BOX_ROOT/tproxy.nft" "$SING_BOX_ROOT/ru.zone" "$SING_BOX_ROOT/config.json"
}

render_template() {
  local source="$1"
  local destination="$2"
  python3 - "$source" "$destination" <<'PY'
import os
import sys
from pathlib import Path
source = Path(sys.argv[1])
destination = Path(sys.argv[2])
content = source.read_text(encoding='utf-8')
for name in ('FOCUSVPN_WG_INTERFACE', 'FOCUSVPN_WG_NETWORK', 'FOCUSVPN_LAN_NETWORK', 'FOCUSVPN_MANAGEMENT_NETWORK'):
    content = content.replace(f'__{name}__', os.environ[name])
if '__FOCUSVPN_' in content:
    raise SystemExit('unresolved FocusVPN template value')
destination.write_text(content, encoding='utf-8')
PY
}

initialize_auth() {
  [[ -f "$ADMIN_ROOT/auth.json" ]] && return
  [[ -t 0 ]] || fail "no auth.json found; run interactively once to create panel credentials"
  local username password confirmation
  read -r -p 'FocusVPN panel username [admin]: ' username
  username="${username:-admin}"
  read -r -s -p 'FocusVPN panel password: ' password
  printf '\n'
  read -r -s -p 'Repeat panel password: ' confirmation
  printf '\n'
  [[ "$password" == "$confirmation" ]] || fail "passwords do not match"
  [[ ${#password} -ge 12 ]] || fail "panel password must contain at least 12 characters"
  printf '%s\n%s\n' "$username" "$password" | python3 - "$ADMIN_ROOT/auth.json" <<'PY'
import base64
import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path
username = sys.stdin.readline().rstrip('\n')
password = sys.stdin.readline().rstrip('\n')
salt = secrets.token_bytes(16)
payload = {
    'username': username,
    'salt': base64.b64encode(salt).decode('ascii'),
    'hash': base64.b64encode(__import__('hashlib').scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1, dklen=32)).decode('ascii'),
    'realm': f'FocusVPN {secrets.token_hex(4)}',
    'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
}
path = Path(sys.argv[1])
path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
os.chmod(path, 0o600)
PY
  unset password confirmation
  log "created $ADMIN_ROOT/auth.json"
}

contains_placeholder() {
  grep -Eq '<[A-Za-z0-9_.-]+>' "$1"
}

create_wg_easy_container() {
  if docker inspect wg-easy >/dev/null 2>&1; then
    return
  fi
  docker pull "$WG_EASY_IMAGE"
  docker run --detach --name wg-easy --network host --cap-add NET_ADMIN --restart unless-stopped --volume /etc/wg-easy:/etc/wireguard "$WG_EASY_IMAGE"
  log "created wg-easy container; finish its initial setup at http://<server-lan-ip>:51821 before exposing the panel"
}

enable_services() {
  [[ -f "$HAPP_ROOT/config.json" ]] || fail "create $HAPP_ROOT/config.json from your HAPP Server configuration before --enable"
  contains_placeholder "$SING_BOX_ROOT/config.json" && fail "replace placeholders in $SING_BOX_ROOT/config.json before --enable"
  contains_placeholder "$HAPP_ROOT/config.json" && fail "replace placeholders in $HAPP_ROOT/config.json before --enable"
  [[ -s "$ADMIN_ROOT/wg-easy-api.json" ]] || fail "configure $ADMIN_ROOT/wg-easy-api.json before --enable"
  contains_placeholder "$ADMIN_ROOT/wg-easy-api.json" && fail "replace placeholders in $ADMIN_ROOT/wg-easy-api.json before --enable"
  docker inspect wg-easy >/dev/null 2>&1 || fail "start wg-easy first: sudo ./scripts/install.sh --start-wg-easy"

  sing-box check -C "$SING_BOX_ROOT"
  sing-box check -C "$HAPP_ROOT"
  nft --check -f "$SING_BOX_ROOT/tproxy.nft"
  systemd-analyze verify /etc/systemd/system/sing-box.service /etc/systemd/system/sing-box-admin.service /etc/systemd/system/sing-box-happ-server.service
  systemctl daemon-reload
  systemctl enable --now sing-box sing-box-happ-server sing-box-admin wg-easy-private-ui sing-box-ru-zone-update.timer
  systemctl is-active --quiet sing-box sing-box-happ-server sing-box-admin wg-easy-private-ui
  log "FocusVPN services are active"
}

main() {
  install_packages
  install_sing_box
  ensure_sing_box_account
  install_tree
  initialize_runtime_files
  initialize_auth
  systemctl daemon-reload
  if (( START_WG_EASY )); then
    create_wg_easy_container
  fi
  if (( ENABLE_SERVICES )); then
    enable_services
  else
    log "runtime installed without starting network services"
    log "complete configs, then run: sudo ./scripts/install.sh --enable"
  fi
}

main "$@"
