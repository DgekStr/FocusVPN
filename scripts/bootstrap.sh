#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPOSITORY="https://github.com/DgekStr/FocusVPN.git"
readonly DEFAULT_REF="master"

fail() {
  printf '[focusvpn-bootstrap] error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  curl -fsSL https://raw.githubusercontent.com/DgekStr/FocusVPN/master/scripts/bootstrap.sh | sudo bash -s -- master [installer-options]

The bootstrapper confirms the package/service plan before installing dependencies,
then clones the selected branch/tag into a private temporary directory and runs
scripts/install.sh. Prompts are read from /dev/tty when bootstrap.sh is piped.

Installer options: --start-wg-easy, --enable
EOF
}

if (($#)) && [[ "$1" == '-h' || "$1" == '--help' ]]; then
  usage
  exit 0
fi

[[ ${EUID} -eq 0 ]] || fail "run via sudo, for example: curl -fsSL https://raw.githubusercontent.com/DgekStr/FocusVPN/master/scripts/bootstrap.sh | sudo bash -s -- master"
[[ -r /etc/os-release ]] || fail 'cannot identify Linux distribution'
source /etc/os-release
case "${ID:-}" in
  debian|ubuntu) ;;
  *) fail 'supported distributions: Debian 12+ and Ubuntu 22.04+' ;;
esac

ref="${1:-$DEFAULT_REF}"
if (($#)); then
  shift
fi
[[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$ref" != *'..'* ]] || fail 'invalid Git branch/tag name'
for option in "$@"; do
  case "$option" in
    --enable|--start-wg-easy) ;;
    *) fail "unsupported installer option: $option" ;;
  esac
done

export DEBIAN_FRONTEND=noninteractive
if [[ -r /dev/tty && -w /dev/tty ]]; then
  installer_tty=/dev/tty
else
  [[ -t 0 ]] || fail 'interactive terminal required for install confirmation and panel password'
  installer_tty=/dev/stdin
fi

cat <<'EOF'
[focusvpn-bootstrap] WARNING: installation will make system-level changes.
Packages: Git/curl if missing, Docker, Nginx, OpenSSL, nftables, Python 3, sing-box and panel dependencies.
Services/container: Docker, Nginx HTTPS panel on :7445, sing-box-admin, wg-easy with automatic administrator setup.
WireGuard is initialized; sing-box/HAPP remain stopped unless --enable is requested with real configurations.
Existing WireGuard runtime will trigger a separate warning and backup-or-cancel prompt.
EOF
read -r -p 'Install this package/service plan? [y/N] ' answer < "$installer_tty"
case "$answer" in
  y|Y|yes|YES) export FOCUSVPN_INSTALL_PLAN_CONFIRMED=1 ;;
  *) fail 'installation cancelled before package changes' ;;
esac

if ! command -v git >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates git
fi
command -v git >/dev/null 2>&1 || fail 'Git installation failed'
command -v curl >/dev/null 2>&1 || { apt-get update; apt-get install -y --no-install-recommends ca-certificates curl; }

temporary="$(mktemp -d /tmp/focusvpn-bootstrap.XXXXXX)"
chmod 0700 "$temporary"
cleanup() { rm -rf -- "$temporary"; }
trap cleanup EXIT

printf '[focusvpn-bootstrap] cloning FocusVPN ref %s\n' "$ref"
git clone --depth 1 --single-branch --branch "$ref" "$REPOSITORY" "$temporary/repo"
[[ -f "$temporary/repo/scripts/install.sh" && -f "$temporary/repo/VERSION" ]] || fail 'cloned repository is incomplete'
printf '[focusvpn-bootstrap] running installer from cloned source\n'
if bash "$temporary/repo/scripts/install.sh" "$@" < "$installer_tty"; then
  printf '[focusvpn-bootstrap] base installation completed\n'
else
  status=$?
  fail "installer exited with status $status"
fi