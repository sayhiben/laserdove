#!/usr/bin/env bash

# Idempotent bring-up for a fresh Raspberry Pi OS Lite host.
# - Configures eth0 for the Ruida subnet (defaults to 10.0.3.2/24).
# - Ensures base tooling (python venv/pip, git, vim, wget, gh).
# - Creates/uses a virtualenv and installs this project editable.

SOURCED=0
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  SOURCED=1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-${REPO_ROOT}/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NET_DEV="${NET_DEV:-eth0}"
NET_IP="${NET_IP:-10.0.3.2/24}"

fail() {
  echo "$*" >&2
  return 1
}

ensure_apt() {
  if ! command -v apt-get >/dev/null 2>&1; then
    fail "apt-get not found; this script targets Debian/RPi OS systems."
  fi
}

install_base_packages() {
  echo "Updating apt package lists and installing base tooling..."
  sudo apt-get update
  sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    git \
    vim \
    wget \
    iproute2
}

install_gh_cli() {
  if command -v gh >/dev/null 2>&1; then
    echo "GitHub CLI already present; skipping gh install."
    return
  fi

  echo "Installing GitHub CLI..."
  sudo mkdir -p -m 755 /etc/apt/keyrings
  tmp_keyring="$(mktemp)"
  wget -nv -O"$tmp_keyring" https://cli.github.com/packages/githubcli-archive-keyring.gpg
  sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg <"$tmp_keyring" >/dev/null
  rm -f "$tmp_keyring"
  sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  sudo mkdir -p -m 755 /etc/apt/sources.list.d
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y gh
}

configure_network() {
  if ! ip link show "$NET_DEV" >/dev/null 2>&1; then
    fail "Network device $NET_DEV not found; set NET_DEV to the correct interface and rerun."
  fi

  echo "Configuring $NET_DEV for Ruida subnet ($NET_IP)..."
  sudo ip addr flush dev "$NET_DEV"
  sudo ip addr add "$NET_IP" dev "$NET_DEV"
  sudo ip link set "$NET_DEV" up
}

setup_python() {
  echo "Preparing virtual environment in $VENV_DIR..."
  if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip setuptools wheel
  pip install -e "$REPO_ROOT"
}

run_setup() {
  (
    set -euo pipefail
    cd "$REPO_ROOT"
    ensure_apt
    install_base_packages
    install_gh_cli
    configure_network
    setup_python
  )
}

main() {
  if ! run_setup; then
    fail "Setup failed; see errors above." || true
    if (( SOURCED )); then
      return 1
    else
      exit 1
    fi
  fi

  if (( SOURCED )); then
    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"
    echo "Setup complete. Virtualenv activated from $VENV_DIR."
    return 0
  fi

  cd "$REPO_ROOT"
  echo ""
  echo "Setup complete. Activate the virtualenv with:"
  echo "  source \"$VENV_DIR/bin/activate\""
}

main "$@"
