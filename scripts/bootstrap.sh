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

The bootstrapper clones the selected branch/tag into a private temporary directory,
then runs scripts/install.sh. It forwards stdin to /dev/tty so the installer can
securely prompt for the panel password even when bootstrap.sh is piped to bash.

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
if ! command -v git >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates git
fi
command -v git >/dev/null 2>&1 || fail 'Git installation failed'
command -v curl >/dev/null 2>&1 || { apt-get update; apt-get install -y --no-install-recommends ca-certificates curl; }

if [[ -r /dev/tty && -w /dev/tty ]]; then
  installer_tty=/dev/tty
else
  [[ -t 0 ]] || fail 'interactive terminal required for the initial panel password prompt'
  installer_tty=/dev/stdin
fi

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