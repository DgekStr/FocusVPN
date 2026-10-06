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

log() {
  printf '[focusvpn] %s\n' "$*"
}

fail() {
  printf '[focusvpn] error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install.sh [--enable]

Installs FocusVPN runtime files on a supported Debian or Ubuntu server.

Options:
  --enable  Validate completed runtime configuration and enable FocusVPN services.
  -h, --help  Show this help.

The default mode installs dependencies, code, systemd units and safe templates,
then initializes wg-easy automatically with private service credentials.
Its web UI is restricted to localhost; sing-box/HAPP require real configs.
For a one-command GitHub bootstrap, see scripts/bootstrap.sh.
EOF
}

while (($#)); do
  case "$1" in
    --enable) ENABLE_SERVICES=1 ;;
    --start-wg-easy) log "--start-wg-easy is deprecated; wg-easy starts automatically" ;;
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
  apt-get install -y --no-install-recommends ca-certificates curl docker.io nftables nginx openssl python3 python3-xlwt qrencode tar wireguard-tools
  systemctl stop nginx.service 2>/dev/null || true
  systemctl enable --now docker
}

confirm_install_plan() {
  local answer
  [[ "${FOCUSVPN_INSTALL_PLAN_CONFIRMED:-}" == "1" ]] && return 0
  cat <<'EOF'
[focusvpn] WARNING: installation will make system-level changes.
Packages: ca-certificates, curl, Docker, nftables, Nginx, OpenSSL, Python 3, python3-xlwt, qrencode, tar, wireguard-tools, sing-box.
Services/container to enable: docker, nginx, sing-box-admin, wg-easy-private-ui, wg-easy.
Admin panel: https://<server-ip>:7445 (self-signed certificate; browser warning expected).
wg-easy administrator/API setup is automatic; private credentials are stored with mode 0600.
WireGuard is initialized; sing-box/HAPP remain stopped unless --enable is requested with real configurations.
EOF
  [[ -r /dev/tty && -w /dev/tty ]] || fail "interactive confirmation is required before installation"
  read -r -p "Install these packages and services? [y/N] " answer < /dev/tty
  case "$answer" in
    y|Y|yes|YES) ;;
    *) fail "installation cancelled before system changes" ;;
  esac
  export FOCUSVPN_INSTALL_PLAN_CONFIRMED=1
}

check_legacy_wireguard() {
  local managed_container=0
  local container_label=""
  local answer backup_root unit interface
  local -a legacy_details=()
  local -a wireguard_configs=()
  local -a legacy_configs=()
  local -a legacy_interfaces=()

  if command -v docker >/dev/null 2>&1 && docker inspect wg-easy >/dev/null 2>&1; then
    container_label="$(docker inspect --format '{{ index .Config.Labels "com.focusvpn.managed" }}' wg-easy 2>/dev/null || true)"
    if [[ "$container_label" == "true" ]]; then
      managed_container=1
    else
      legacy_details+=("unmanaged Docker container named wg-easy")
    fi
  fi

  if command -v systemctl >/dev/null 2>&1; then
    while IFS= read -r unit; do
      [[ "$unit" == "wg-quick@wg-client.service" ]] && continue
      [[ -n "$unit" ]] && legacy_details+=("active service $unit")
    done < <(systemctl list-units --type=service --state=running --no-legend 'wg-quick@*.service' 2>/dev/null | awk '{print $1}')
    while IFS= read -r unit; do
      [[ "$unit" == "wg-quick@wg-client.service" ]] && continue
      [[ -n "$unit" ]] && legacy_details+=("enabled service $unit")
    done < <(systemctl list-unit-files --type=service --state=enabled --no-legend 2>/dev/null | awk '$1 ~ /^wg-quick@.+\.service$/ {print $1}')
  fi

  shopt -s nullglob
  wireguard_configs=(/etc/wireguard/*.conf)
  shopt -u nullglob
  for answer in "${wireguard_configs[@]}"; do
    [[ "${answer##*/}" == "wg-client.conf" ]] || legacy_configs+=("$answer")
  done
  if ((${#legacy_configs[@]})); then
    legacy_details+=("existing /etc/wireguard configuration files")
  fi

  if (( ! managed_container )) && [[ -d /etc/wg-easy ]] && find /etc/wg-easy -mindepth 1 -print -quit | grep -q .; then
    legacy_details+=("existing /etc/wg-easy data")
  fi

  if command -v ip >/dev/null 2>&1; then
    while IFS= read -r interface; do
      [[ -z "$interface" ]] && continue
      if (( managed_container )) && [[ "$interface" == "wg0" ]]; then
        continue
      fi
      [[ "$interface" == "wg-client" ]] && continue
      legacy_interfaces+=("$interface")
      legacy_details+=("active WireGuard interface $interface")
    done < <(ip -o link show type wireguard 2>/dev/null | awk -F ': ' '{print $2}' | cut -d@ -f1)
  fi

  ((${#legacy_details[@]})) || return 0
  log "WARNING: an existing WireGuard installation was detected:"
  for answer in "${legacy_details[@]}"; do
    printf '  - %s\n' "$answer" >&2
  done
  log "Continuing will stop/remove the detected legacy runtime and move its config data to a private backup."
  [[ -t 0 ]] || fail "interactive confirmation required; rerun from a terminal to remove the old installation or cancel"
  read -r -p "Move old WireGuard data to backup and continue? [y/N] " answer < /dev/tty
  case "$answer" in
    y|Y|yes|YES) ;;
    *) fail "installation cancelled; existing WireGuard installation was not changed" ;;
  esac

  backup_root="/root/focusvpn-wireguard-backup-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  install -d -m 0700 "$backup_root"
  if [[ -f /etc/wireguard/wg-client.conf ]]; then
    install -d -m 0700 "$backup_root/managed"
    cp -a /etc/wireguard/wg-client.conf "$backup_root/managed/wg-client.conf"
  fi
  if command -v docker >/dev/null 2>&1 && docker inspect wg-easy >/dev/null 2>&1 && (( ! managed_container )); then
    install -d -m 0700 "$backup_root/docker-wg-easy"
    docker inspect wg-easy > "$backup_root/docker-wg-easy/container-inspect.json"
    docker cp wg-easy:/etc/wireguard/. "$backup_root/docker-wg-easy/"
    docker rm -f wg-easy
  fi
  if command -v systemctl >/dev/null 2>&1; then
    while IFS= read -r unit; do
      [[ "$unit" == "wg-quick@wg-client.service" ]] && continue
      [[ -z "$unit" ]] || systemctl disable --now "$unit"
    done < <(systemctl list-units --type=service --state=running --no-legend 'wg-quick@*.service' 2>/dev/null | awk '{print $1}')
    while IFS= read -r unit; do
      [[ "$unit" == "wg-quick@wg-client.service" ]] && continue
      [[ -z "$unit" ]] || systemctl disable "$unit"
    done < <(systemctl list-unit-files --type=service --state=enabled --no-legend 2>/dev/null | awk '$1 ~ /^wg-quick@.+\.service$/ {print $1}')
  fi
  for path in /etc/wireguard /etc/wg-easy; do
    if [[ -d "$path" ]] && find "$path" -mindepth 1 -print -quit | grep -q .; then
      mv "$path" "$backup_root/${path##*/}"
    fi
  done
  if [[ -f "$backup_root/managed/wg-client.conf" ]]; then
    install -d -m 0700 /etc/wireguard
    cp -a "$backup_root/managed/wg-client.conf" /etc/wireguard/wg-client.conf
  fi
  for interface in "${legacy_interfaces[@]}"; do
    ip link delete dev "$interface" 2>/dev/null || log "could not remove WireGuard interface $interface; check it manually"
  done
  log "legacy WireGuard data was preserved in $backup_root"
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

  local temporary archive release_metadata asset base expected_checksum
  temporary="$(mktemp -d)"
  trap 'rm -rf "$temporary"' RETURN
  asset="sing-box-${SING_BOX_VERSION}-linux-${ARCH}.tar.gz"
  base="https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}"
  archive="$temporary/$asset"
  release_metadata="$temporary/release.json"

  log "downloading sing-box $SING_BOX_VERSION for $ARCH"
  curl --fail --location --retry 3 --output "$archive" "$base/$asset"
  curl --fail --location --retry 3 --header 'Accept: application/vnd.github+json' --header 'User-Agent: FocusVPN-installer' --output "$release_metadata" "https://api.github.com/repos/SagerNet/sing-box/releases/tags/v${SING_BOX_VERSION}"
  expected_checksum="$(python3 "$REPO_ROOT/scripts/installer_utils.py" "$release_metadata" "$asset")" || fail "could not read sing-box release digest"
  printf '%s  %s\n' "$expected_checksum" "$archive" | sha256sum --check --status || fail "sing-box checksum verification failed"
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
  install -d -m 0750 "$CONFIG_ROOT" "$ADMIN_ROOT"
  install -d -m 0750 -o root -g sing-box "$SING_BOX_ROOT" "$HAPP_ROOT"
  install -d -m 0700 "$ADMIN_ROOT/backups" /etc/wg-easy /etc/wireguard /mnt/stat

  find "$REPO_ROOT/server/panel" -maxdepth 1 -type f -name '*.py' -exec install -m 0644 {} "$APP_ROOT/" \;
  install -m 0644 "$REPO_ROOT/VERSION" "$APP_ROOT/VERSION"
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

  python3 "$REPO_ROOT/scripts/installer_utils.py" remove-placeholders "$SING_BOX_ROOT/config.json" "$ADMIN_ROOT/backups"

  set -a
  source "$CONFIG_ROOT/focusvpn.env"
  set +a
  [[ "${FOCUSVPN_WG_INTERFACE:-}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "FOCUSVPN_WG_INTERFACE is invalid"
  python3 - <<'PY'
import ipaddress
import os
for name in ('FOCUSVPN_WG_NETWORK', 'FOCUSVPN_LAN_NETWORK', 'FOCUSVPN_MANAGEMENT_NETWORK'):
    ipaddress.ip_network(os.environ[name], strict=False)
for network in filter(None, (item.strip() for item in os.environ.get('FOCUSVPN_TRUSTED_PROXY_NETWORKS', '').split(','))):
  ipaddress.ip_network(network, strict=False)
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
import ipaddress
import os
import sys
from pathlib import Path
source = Path(sys.argv[1])
destination = Path(sys.argv[2])
content = source.read_text(encoding='utf-8')
admin_network = ipaddress.ip_network(os.environ.get('FOCUSVPN_ADMIN_NETWORK') or os.environ['FOCUSVPN_LAN_NETWORK'], strict=False)
if admin_network.version != 4:
  raise ValueError('FOCUSVPN_ADMIN_NETWORK must be an IPv4 network')
content = content.replace('__FOCUSVPN_ADMIN_NETWORK__', str(admin_network))
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
  python3 "$REPO_ROOT/scripts/create_panel_auth.py" "$ADMIN_ROOT/auth.json" || fail "panel credentials setup failed"
  log "created $ADMIN_ROOT/auth.json"
}

configure_nginx_proxy() {
  local server_ip certificate key site
  server_ip="$(ip -4 route show default | awk '{ for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit } }')"
  [[ "$server_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "could not determine the server IPv4 address for the panel URL"
  certificate="/etc/ssl/certs/focusvpn-panel.crt"
  key="/etc/ssl/private/focusvpn-panel.key"
  site="/etc/nginx/sites-available/focusvpn-7445"
  install -d -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/ssl/certs /etc/ssl/private
  if [[ ! -s "$certificate" || ! -s "$key" ]] || ! openssl x509 -in "$certificate" -noout -ext subjectAltName 2>/dev/null | grep -Fq "IP Address:$server_ip"; then
    openssl req -x509 -nodes -days 365 -newkey rsa:3072 \
      -keyout "$key" -out "$certificate" -subj "/CN=$server_ip" \
      -addext "subjectAltName=IP:$server_ip" >/dev/null 2>&1
    chmod 0600 "$key"
    chmod 0644 "$certificate"
  fi

  python3 - "$CONFIG_ROOT/focusvpn.env" <<'PY'
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding='utf-8').splitlines()
updated = []
found_host = False
found_proxy = False
for line in lines:
    if line.startswith('SING_BOX_ADMIN_HOST='):
        updated.append('SING_BOX_ADMIN_HOST=127.0.0.1')
        found_host = True
    elif line.startswith('FOCUSVPN_TRUSTED_PROXY_NETWORKS='):
        current = line.split('=', 1)[1].strip().strip('"\'')
        networks = [item.strip() for item in current.split(',') if item.strip()]
        if '127.0.0.1/32' not in networks:
            networks.append('127.0.0.1/32')
        updated.append('FOCUSVPN_TRUSTED_PROXY_NETWORKS=' + ','.join(networks))
        found_proxy = True
    else:
        updated.append(line)
if not found_host:
    updated.append('SING_BOX_ADMIN_HOST=127.0.0.1')
if not found_proxy:
    updated.append('FOCUSVPN_TRUSTED_PROXY_NETWORKS=127.0.0.1/32')
path.write_text('\n'.join(updated) + '\n', encoding='utf-8')
os.chmod(path, 0o600)
PY

  cat > "$site" <<EOF
server {
    listen 7445 ssl;
    listen [::]:7445 ssl;
    server_name $server_ip;

    ssl_certificate $certificate;
    ssl_certificate_key $key;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:9443;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header Connection "";
        proxy_read_timeout 120s;
        proxy_redirect off;
    }
}
EOF
  ln -sfn "$site" /etc/nginx/sites-enabled/focusvpn-7445
  if [[ -L /etc/nginx/sites-enabled/default ]] && [[ "$(readlink -f /etc/nginx/sites-enabled/default)" == "/etc/nginx/sites-available/default" ]]; then
    rm -f /etc/nginx/sites-enabled/default
  fi
  nginx -t
  log "configured HTTPS panel at https://$server_ip:7445"
}

contains_placeholder() {
  grep -Eq '<[A-Za-z0-9_.-]+>' "$1"
}

create_wg_easy_container() {
  local server_ip initialization_environment setup_location
  initialization_environment="$ADMIN_ROOT/wg-easy-init.env"
  if docker inspect wg-easy >/dev/null 2>&1; then
    docker start wg-easy >/dev/null
    curl --fail --silent --retry 10 --retry-connrefused --retry-delay 1 --max-time 5 http://127.0.0.1:51821/ >/dev/null || fail "wg-easy did not become ready"
    setup_location="$(curl --silent --show-error --max-time 5 --output /dev/null --write-out '%{redirect_url}' http://127.0.0.1:51821/)"
    if [[ "$setup_location" != *'/setup/'* ]]; then
      python3 "$REPO_ROOT/scripts/configure_wg_easy.py" verify "$ADMIN_ROOT/wg-easy-api.json" || fail "existing wg-easy administrator does not match panel credentials; refusing to reset it"
      if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' wg-easy | grep -q '^INIT_PASSWORD='; then
        log "removing one-time wg-easy initialization credentials from container environment"
        docker stop wg-easy >/dev/null
        docker rm wg-easy >/dev/null
        rm -f "$initialization_environment"
        docker run --detach --name wg-easy --label com.focusvpn.managed=true --network host --cap-add NET_ADMIN --restart unless-stopped --volume /etc/wg-easy:/etc/wireguard "$WG_EASY_IMAGE"
        curl --fail --silent --retry 15 --retry-connrefused --retry-delay 1 --max-time 5 http://127.0.0.1:51821/ >/dev/null || fail "wg-easy did not become ready after removing initialization environment"
        python3 "$REPO_ROOT/scripts/configure_wg_easy.py" verify "$ADMIN_ROOT/wg-easy-api.json" || fail "wg-easy API authorization failed after removing initialization environment"
      fi
      log "existing wg-easy API authorization verified"
      return
    fi
    log "completing unfinished managed wg-easy setup"
    docker stop wg-easy >/dev/null
    docker rm wg-easy >/dev/null
  fi
  server_ip="$(ip -4 route get 1.1.1.1 | awk '{ for (field = 1; field <= NF; field++) if ($field == "src") { print $(field + 1); exit } }')"
  python3 "$REPO_ROOT/scripts/configure_wg_easy.py" prepare "$ADMIN_ROOT/wg-easy-api.json" "$initialization_environment" "$server_ip" "$FOCUSVPN_WG_NETWORK"
  docker pull "$WG_EASY_IMAGE"
  docker run --detach --name wg-easy --label com.focusvpn.managed=true --network host --cap-add NET_ADMIN --restart unless-stopped --env-file "$initialization_environment" --volume /etc/wg-easy:/etc/wireguard "$WG_EASY_IMAGE"
  curl --fail --silent --retry 15 --retry-connrefused --retry-delay 1 --max-time 5 http://127.0.0.1:51821/ >/dev/null || fail "wg-easy did not become ready"
  python3 "$REPO_ROOT/scripts/configure_wg_easy.py" verify "$ADMIN_ROOT/wg-easy-api.json" || fail "automatic wg-easy setup or API authorization failed"
  docker stop wg-easy >/dev/null
  docker rm wg-easy >/dev/null
  rm -f "$initialization_environment"
  docker run --detach --name wg-easy --label com.focusvpn.managed=true --network host --cap-add NET_ADMIN --restart unless-stopped --volume /etc/wg-easy:/etc/wireguard "$WG_EASY_IMAGE"
  curl --fail --silent --retry 15 --retry-connrefused --retry-delay 1 --max-time 5 http://127.0.0.1:51821/ >/dev/null || fail "wg-easy did not become ready"
  python3 "$REPO_ROOT/scripts/configure_wg_easy.py" verify "$ADMIN_ROOT/wg-easy-api.json" || fail "wg-easy API authorization failed after removing initialization environment"
  docker inspect --format '{{.State.Status}}' wg-easy | grep -Fxq running || fail "wg-easy container failed to start"
  log "wg-easy administrator created automatically; private API credentials saved, initialization environment removed"
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
  systemd-analyze verify \
    /etc/systemd/system/sing-box.service \
    /etc/systemd/system/sing-box-gateway.service \
    /etc/systemd/system/sing-box-admin.service \
    /etc/systemd/system/sing-box-happ-server.service \
    /etc/systemd/system/sing-box-ru-zone-update.service \
    /etc/systemd/system/sing-box-ru-zone-update.timer \
    /etc/systemd/system/wg-easy-private-ui.service \
    /etc/systemd/system/wg-peer-keepalive.service \
    /etc/systemd/system/wg-peer-keepalive.timer \
    /etc/systemd/system/focusvpn-gateway-mode.service \
    /etc/systemd/system/focusvpn-gateway-mode@.service \
    /etc/systemd/system/focusvpn-service-control@.service \
    /etc/systemd/system/focusvpn-outbound-test@.service
  systemctl daemon-reload
  systemctl enable --now sing-box sing-box-happ-server sing-box-admin nginx wg-easy-private-ui sing-box-ru-zone-update.timer wg-peer-keepalive.timer focusvpn-gateway-mode.service
  systemctl is-active --quiet sing-box sing-box-gateway sing-box-happ-server sing-box-admin wg-easy-private-ui sing-box-ru-zone-update.timer wg-peer-keepalive.timer focusvpn-gateway-mode.service
  log "FocusVPN services are active"
}

main() {
  confirm_install_plan
  check_legacy_wireguard
  install_packages
  install_sing_box
  ensure_sing_box_account
  install_tree
  initialize_runtime_files
  initialize_auth
  configure_nginx_proxy
  systemctl daemon-reload
  systemctl enable --now wg-easy-private-ui.service
  create_wg_easy_container
  systemctl enable nginx.service wg-easy-private-ui.service
  systemctl restart sing-box-admin.service
  systemctl enable --now sing-box-admin.service nginx.service
  if (( ENABLE_SERVICES )); then
    enable_services
  else
    log "panel and wg-easy installed; sing-box/HAPP services were not enabled"
    log "complete configs, then run: sudo ./scripts/install.sh --enable"
  fi
  server_ip="$(ip -4 route show default | awk '{ for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit } }')"
  log "installed packages: Docker, Nginx, OpenSSL, Python 3, nftables, WireGuard tools, sing-box and panel dependencies"
  log "started services: docker, nginx, sing-box-admin, wg-easy-private-ui, wg-easy container"
  log "admin panel: https://$server_ip:7445 (self-signed certificate; browser warning expected)"
  log "wg-easy API configured automatically; credentials stored privately in $ADMIN_ROOT/wg-easy-api.json"
}

main "$@"
