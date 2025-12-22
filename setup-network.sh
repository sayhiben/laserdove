#!/usr/bin/env bash
set -euo pipefail

# setup-network.sh
#
# Hardened, remote-safe Ethernet static IP configuration for Raspberry Pi OS.
#
# Primary goal:
#   Put ETH_IFACE on STATIC_IP_CIDR so traffic to 10.0.3.0/24 uses Ethernet.
#
# Safety goal (hard constraint for remote admin):
#   Keep Wi-Fi (wlan0) as the default route so SSH over Wi-Fi does not break.
#
# Key hardening behaviors:
#   - Never installs a default route on ETH_IFACE (NetworkManager: ipv4.never-default=yes; ipv4.gateway="").
#   - Refuses to apply if it would make ETH_IFACE the default route, unless --force is used.
#   - Post-apply verification: confirms default route remains on wlan0; confirms route to TEST_DEST_IP uses ETH_IFACE.
#   - Automatic rollback on verification failure (restores prior NetworkManager settings or dhcpcd.conf).
#   - Detects and removes duplicate definitions this script may have created.
#
# Failure modes this script is designed to prevent:
#   - Losing SSH over wlan0 because replies follow a new default route via eth0.
#   - Multiple competing autoconnect profiles on eth0 (dueling profiles).
#   - Multiple dhcpcd managed blocks causing ambiguous configuration.
#
# Usage:
#   sudo ./setup-network.sh --diagnose
#   sudo ./setup-network.sh --apply
#   sudo ./setup-network.sh --uninstall
#
# Overrides:
#   ETH_IFACE=eth0
#   STATIC_IP_CIDR=10.0.3.10/24
#   NM_CONN_NAME=eth0-static
#   TEST_DEST_IP=10.0.3.3
#   TEST_DEST_PORT=50200
#   VERIFY_INTERNET_IP=1.1.1.1
#
# Optional flags:
#   --force        Apply even if preflight detects risk; rollback still occurs if verification fails.
#   --no-rollback  Do not rollback on failure (not recommended for remote sessions).

ETH_IFACE="${ETH_IFACE:-eth0}"
STATIC_IP_CIDR="${STATIC_IP_CIDR:-10.0.3.10/24}"
NM_CONN_NAME="${NM_CONN_NAME:-eth0-static}"

TEST_DEST_IP="${TEST_DEST_IP:-10.0.3.3}"
TEST_DEST_PORT="${TEST_DEST_PORT:-50200}"
VERIFY_INTERNET_IP="${VERIFY_INTERNET_IP:-1.1.1.1}"

DHCPCD_CONF_PATH="/etc/dhcpcd.conf"

BEGIN_MARKER="# BEGIN setup-network.sh: ${ETH_IFACE}"
END_MARKER="# END setup-network.sh: ${ETH_IFACE}"

ACTION="apply"          # apply|diagnose|uninstall
FORCE="no"
ROLLBACK="yes"

usage() {
  cat <<EOF
Usage:
  sudo $0 --apply [--force] [--no-rollback]
  sudo $0 --diagnose
  sudo $0 --uninstall

Environment overrides:
  ETH_IFACE=${ETH_IFACE}
  STATIC_IP_CIDR=${STATIC_IP_CIDR}
  NM_CONN_NAME=${NM_CONN_NAME}
  TEST_DEST_IP=${TEST_DEST_IP}
  TEST_DEST_PORT=${TEST_DEST_PORT}
  VERIFY_INTERNET_IP=${VERIFY_INTERNET_IP}
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Error: must run as root. Use: sudo $0" >&2
    exit 1
  fi
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

service_active() {
  local svc="$1"
  systemctl is-active --quiet "$svc" >/dev/null 2>&1
}

detect_manager() {
  if command_exists nmcli && service_active NetworkManager; then
    echo "networkmanager"
    return 0
  fi
  if service_active dhcpcd; then
    echo "dhcpcd"
    return 0
  fi
  if command_exists dhcpcd; then
    echo "dhcpcd"
    return 0
  fi
  echo "unknown"
}

print_header() {
  echo "Target interface: ${ETH_IFACE}"
  echo "Desired: static IPv4 ${STATIC_IP_CIDR} on ${ETH_IFACE}"
  echo "Safety: wlan0 must remain default route"
  echo "Test: ${TEST_DEST_IP}:${TEST_DEST_PORT} should route via ${ETH_IFACE}"
  echo
}

print_current_state() {
  echo "=== Current state ==="
  ip -br link show "${ETH_IFACE}" 2>/dev/null || echo "(no such interface: ${ETH_IFACE})"
  ip -br link show wlan0 2>/dev/null || true
  ip -4 addr show "${ETH_IFACE}" 2>/dev/null || true
  ip -4 addr show wlan0 2>/dev/null || true
  ip route show default 2>/dev/null || true
  ip route get "${TEST_DEST_IP}" 2>/dev/null || true
  ip route get "${VERIFY_INTERNET_IP}" 2>/dev/null || true
  echo
}

default_route_dev() {
  # Prints interface name for default route; empty if none.
  ip route show default 2>/dev/null | awk 'NR==1{for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}'
}

route_dev_for_ip() {
  local dst="$1"
  ip route get "${dst}" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}'
}

is_remote_ssh_session() {
  # True if the current process tree includes sshd with a remote client.
  # This is conservative; it triggers on real SSH sessions.
  [[ -n "${SSH_CONNECTION:-}" ]] || [[ -n "${SSH_CLIENT:-}" ]]
}

# -------------------------
# Preflight and verification
# -------------------------

preflight_safety_checks() {
  local defdev
  defdev="$(default_route_dev || true)"

  if [[ -z "${defdev}" ]]; then
    echo "Preflight warning: no default route detected."
  fi

  if is_remote_ssh_session; then
    echo "Preflight: running inside an SSH session."
    if [[ "${defdev}" != "wlan0" ]]; then
      echo "Preflight risk: default route is not wlan0 (current: ${defdev:-none})."
      if [[ "${FORCE}" != "yes" ]]; then
        echo "Refusing to apply because this can lock you out of SSH. Re-run with --force if you accept the risk." >&2
        exit 10
      fi
    fi
  fi

  if ! ip link show "${ETH_IFACE}" >/dev/null 2>&1; then
    echo "Error: interface ${ETH_IFACE} not found." >&2
    exit 11
  fi

  if command_exists ethtool; then
    local link
    link="$(ethtool "${ETH_IFACE}" 2>/dev/null | awk -F': ' '/Link detected/ {print $2}' || true)"
    if [[ "${link}" == "no" ]]; then
      echo "Preflight warning: ${ETH_IFACE} link not detected; apply will still write config but it may not be usable until plugged in."
    fi
  fi
}

verify_post_apply() {
  # Returns 0 if safe; non-zero if it looks like we broke remote access assumptions.
  local defdev ethdev internetdev
  defdev="$(default_route_dev || true)"
  ethdev="$(route_dev_for_ip "${TEST_DEST_IP}" || true)"
  internetdev="$(route_dev_for_ip "${VERIFY_INTERNET_IP}" || true)"

  echo "Verification: default route dev=${defdev:-none}; ${TEST_DEST_IP} dev=${ethdev:-none}; ${VERIFY_INTERNET_IP} dev=${internetdev:-none}"

  # Hard requirements:
  [[ "${ethdev}" == "${ETH_IFACE}" ]] || return 21
  [[ "${defdev}" == "wlan0" ]] || return 22

  return 0
}

# -------------------------
# NetworkManager implementation
# -------------------------

nm_profile_exists() {
  nmcli -t -f NAME con show 2>/dev/null | grep -Fxq "${NM_CONN_NAME}"
}

nm_remove_duplicate_profiles() {
  # Removes profiles this script likely created as duplicates.
  # Conservative rules:
  #   - Delete "NM_CONN_NAME <n>" name-pattern profiles.
  #   - Delete eth0-bound profiles that exactly match our static address AND are never-default=yes.
  # Leaves unrelated profiles untouched.
  local removed_any="no"

  while IFS= read -r name; do
    [[ -z "${name}" ]] && continue
    if [[ "${name}" =~ ^${NM_CONN_NAME}[[:space:]][0-9]+$ ]]; then
      nmcli con delete "${name}" >/dev/null 2>&1 || true
      echo "Removed duplicate NetworkManager profile by name: ${name}"
      removed_any="yes"
    fi
  done < <(nmcli -t -f NAME con show 2>/dev/null || true)

  while IFS=: read -r name ifname method addresses never_default gateway; do
    [[ -z "${name}" ]] && continue
    [[ "${name}" == "${NM_CONN_NAME}" ]] && continue
    [[ "${ifname}" == "${ETH_IFACE}" ]] || continue
    [[ "${method}" == "manual" ]] || continue
    [[ "${addresses}" == "${STATIC_IP_CIDR}" ]] || continue
    [[ "${never_default}" == "yes" ]] || continue
    [[ -z "${gateway}" ]] || continue

    nmcli con delete "${name}" >/dev/null 2>&1 || true
    echo "Removed duplicate NetworkManager profile by identical hardened settings: ${name}"
    removed_any="yes"
  done < <(nmcli -t -f NAME,connection.interface-name,ipv4.method,ipv4.addresses,ipv4.never-default,ipv4.gateway con show 2>/dev/null || true)

  if [[ "${removed_any}" == "no" ]]; then
    echo "No removable NetworkManager duplicates detected."
  fi
}

nm_snapshot() {
  # Snapshot only what we touch, for rollback.
  # Output is key=value lines.
  if ! nm_profile_exists; then
    echo "exists=no"
    return 0
  fi

  echo "exists=yes"
  echo "ipv4.method=$(nmcli -g ipv4.method con show "${NM_CONN_NAME}" 2>/dev/null || true)"
  echo "ipv4.addresses=$(nmcli -g ipv4.addresses con show "${NM_CONN_NAME}" 2>/dev/null || true)"
  echo "ipv4.gateway=$(nmcli -g ipv4.gateway con show "${NM_CONN_NAME}" 2>/dev/null || true)"
  echo "ipv4.never-default=$(nmcli -g ipv4.never-default con show "${NM_CONN_NAME}" 2>/dev/null || true)"
  echo "connection.interface-name=$(nmcli -g connection.interface-name con show "${NM_CONN_NAME}" 2>/dev/null || true)"
  echo "connection.autoconnect=$(nmcli -g connection.autoconnect con show "${NM_CONN_NAME}" 2>/dev/null || true)"
}

nm_rollback_from_snapshot() {
  local snap_file="$1"
  [[ -f "${snap_file}" ]] || return 0

  local exists
  exists="$(grep -E '^exists=' "${snap_file}" | head -n1 | cut -d= -f2- || true)"

  if [[ "${exists}" != "yes" ]]; then
    # Profile did not exist previously; delete it if we created it.
    if nm_profile_exists; then
      nmcli con delete "${NM_CONN_NAME}" >/dev/null 2>&1 || true
    fi
    nmcli device connect "${ETH_IFACE}" >/dev/null 2>&1 || true
    return 0
  fi

  local method addresses gateway never_default ifname autoconnect
  method="$(grep -E '^ipv4.method=' "${snap_file}" | cut -d= -f2- || true)"
  addresses="$(grep -E '^ipv4.addresses=' "${snap_file}" | cut -d= -f2- || true)"
  gateway="$(grep -E '^ipv4.gateway=' "${snap_file}" | cut -d= -f2- || true)"
  never_default="$(grep -E '^ipv4.never-default=' "${snap_file}" | cut -d= -f2- || true)"
  ifname="$(grep -E '^connection.interface-name=' "${snap_file}" | cut -d= -f2- || true)"
  autoconnect="$(grep -E '^connection.autoconnect=' "${snap_file}" | cut -d= -f2- || true)"

  nmcli con mod "${NM_CONN_NAME}" connection.interface-name "${ifname}" >/dev/null 2>&1 || true
  nmcli con mod "${NM_CONN_NAME}" connection.autoconnect "${autoconnect}" >/dev/null 2>&1 || true
  nmcli con mod "${NM_CONN_NAME}" ipv4.method "${method}" >/dev/null 2>&1 || true
  nmcli con mod "${NM_CONN_NAME}" ipv4.addresses "${addresses}" >/dev/null 2>&1 || true
  nmcli con mod "${NM_CONN_NAME}" ipv4.never-default "${never_default}" >/dev/null 2>&1 || true

  # gateway can be empty; nmcli accepts empty string to clear.
  nmcli con mod "${NM_CONN_NAME}" ipv4.gateway "${gateway}" >/dev/null 2>&1 || true

  nmcli con down "${NM_CONN_NAME}" >/dev/null 2>&1 || true
  nmcli con up "${NM_CONN_NAME}" >/dev/null 2>&1 || true
}

apply_networkmanager_hardened() {
  echo "Configuring via NetworkManager (hardened)."

  nmcli device set "${ETH_IFACE}" managed yes >/dev/null 2>&1 || true

  nm_remove_duplicate_profiles

  if nm_profile_exists; then
    nmcli con mod "${NM_CONN_NAME}" connection.interface-name "${ETH_IFACE}"
    nmcli con mod "${NM_CONN_NAME}" connection.autoconnect yes
    nmcli con mod "${NM_CONN_NAME}" ipv4.method manual
    nmcli con mod "${NM_CONN_NAME}" ipv4.addresses "${STATIC_IP_CIDR}"

    # Hardening: do not allow a default route on eth0.
    nmcli con mod "${NM_CONN_NAME}" ipv4.never-default yes
    nmcli con mod "${NM_CONN_NAME}" ipv4.gateway ""
  else
    nmcli con add \
      type ethernet \
      ifname "${ETH_IFACE}" \
      con-name "${NM_CONN_NAME}" \
      ipv4.method manual \
      ipv4.addresses "${STATIC_IP_CIDR}" \
      ipv4.never-default yes \
      autoconnect yes >/dev/null
    # Ensure gateway is cleared even on older nmcli behavior.
    nmcli con mod "${NM_CONN_NAME}" ipv4.gateway "" >/dev/null 2>&1 || true
  fi

  # Avoid dueling profiles: disable autoconnect on other profiles bound to the same interface.
  while IFS=: read -r name ifname; do
    [[ -z "${name}" ]] && continue
    [[ "${name}" == "${NM_CONN_NAME}" ]] && continue
    [[ "${ifname}" == "${ETH_IFACE}" ]] || continue
    nmcli con mod "${name}" connection.autoconnect no >/dev/null 2>&1 || true
  done < <(nmcli -t -f NAME,connection.interface-name con show 2>/dev/null || true)

  nmcli con down "${NM_CONN_NAME}" >/dev/null 2>&1 || true
  nmcli con up "${NM_CONN_NAME}" >/dev/null 2>&1 || true
}

uninstall_networkmanager_hardened() {
  echo "Uninstalling NetworkManager configuration created by this script."

  nm_remove_duplicate_profiles

  if nm_profile_exists; then
    nmcli con delete "${NM_CONN_NAME}" >/dev/null 2>&1 || true
    echo "Deleted connection: ${NM_CONN_NAME}"
  else
    echo "Connection ${NM_CONN_NAME} not present; nothing to delete."
  fi

  nmcli device connect "${ETH_IFACE}" >/dev/null 2>&1 || true
}

# -------------------------
# dhcpcd implementation
# -------------------------

dhcpcd_marker_counts() {
  if [[ ! -f "${DHCPCD_CONF_PATH}" ]]; then
    echo "0 0"
    return 0
  fi
  local b e
  b="$(grep -Fxc "${BEGIN_MARKER}" "${DHCPCD_CONF_PATH}" 2>/dev/null || true)"
  e="$(grep -Fxc "${END_MARKER}" "${DHCPCD_CONF_PATH}" 2>/dev/null || true)"
  echo "${b} ${e}"
}

build_dhcpcd_desired_hardened() {
  # Removes all existing managed blocks for this iface, then appends exactly one block.
  # Hardening: no "static routers" line; no DNS here; keep wlan0 as default route.
  awk -v begin="${BEGIN_MARKER}" -v end="${END_MARKER}" '
    $0 == begin {inblock=1; next}
    $0 == end {inblock=0; next}
    !inblock {print}
  ' "${DHCPCD_CONF_PATH}"

  echo
  echo "${BEGIN_MARKER}"
  echo "interface ${ETH_IFACE}"
  echo "static ip_address=${STATIC_IP_CIDR}"
  echo "${END_MARKER}"
  echo
}

build_dhcpcd_uninstall() {
  awk -v begin="${BEGIN_MARKER}" -v end="${END_MARKER}" '
    $0 == begin {inblock=1; next}
    $0 == end {inblock=0; next}
    !inblock {print}
  ' "${DHCPCD_CONF_PATH}"
}

apply_dhcpcd_hardened() {
  echo "Configuring via dhcpcd (hardened)."

  if [[ ! -f "${DHCPCD_CONF_PATH}" ]]; then
    echo "Error: ${DHCPCD_CONF_PATH} not found." >&2
    exit 1
  fi

  local tmp_file
  tmp_file="$(mktemp)"
  build_dhcpcd_desired_hardened > "${tmp_file}"

  if cmp -s "${tmp_file}" "${DHCPCD_CONF_PATH}"; then
    rm -f "${tmp_file}"
    echo "dhcpcd.conf already in desired state; no changes made."
  else
    cp "${DHCPCD_CONF_PATH}" "${DHCPCD_CONF_PATH}.bak.$(date +%Y%m%d%H%M%S)"
    mv "${tmp_file}" "${DHCPCD_CONF_PATH}"
    systemctl restart dhcpcd >/dev/null 2>&1 || true
    echo "Updated dhcpcd.conf and restarted dhcpcd."
  fi
}

uninstall_dhcpcd_hardened() {
  echo "Uninstalling dhcpcd configuration created by this script."

  if [[ ! -f "${DHCPCD_CONF_PATH}" ]]; then
    echo "${DHCPCD_CONF_PATH} not found; nothing to uninstall."
    return 0
  fi

  local b e
  read -r b e < <(dhcpcd_marker_counts)

  if [[ "${b}" == "0" && "${e}" == "0" ]]; then
    echo "Managed markers not present; nothing to remove."
    return 0
  fi

  local tmp_file
  tmp_file="$(mktemp)"
  build_dhcpcd_uninstall > "${tmp_file}"

  if cmp -s "${tmp_file}" "${DHCPCD_CONF_PATH}"; then
    rm -f "${tmp_file}"
    echo "dhcpcd.conf already without managed block(s); no changes made."
  else
    cp "${DHCPCD_CONF_PATH}" "${DHCPCD_CONF_PATH}.bak.$(date +%Y%m%d%H%M%S)"
    mv "${tmp_file}" "${DHCPCD_CONF_PATH}"
    systemctl restart dhcpcd >/dev/null 2>&1 || true
    echo "Removed managed block(s) and restarted dhcpcd."
  fi
}

# -------------------------
# Diagnose
# -------------------------

diagnose_common() {
  echo "=== Diagnosis ==="
  local defdev
  defdev="$(default_route_dev || true)"
  echo "Default route device: ${defdev:-none}"

  echo "Route to ${TEST_DEST_IP}: $(ip route get "${TEST_DEST_IP}" 2>/dev/null || echo "unavailable")"
  echo "Route to ${VERIFY_INTERNET_IP}: $(ip route get "${VERIFY_INTERNET_IP}" 2>/dev/null || echo "unavailable")"

  if is_remote_ssh_session; then
    echo "SSH session detected: yes"
    echo "SSH_CONNECTION=${SSH_CONNECTION:-}"
  else
    echo "SSH session detected: no"
  fi

  if command_exists nmcli; then
    echo
    echo "NetworkManager active connections:"
    nmcli -t -f NAME,TYPE,DEVICE con show --active 2>/dev/null || true
    echo
    echo "NetworkManager eth0 candidates:"
    nmcli -t -f NAME,connection.interface-name,ipv4.method,ipv4.addresses,ipv4.never-default,ipv4.gateway,connection.autoconnect con show 2>/dev/null \
      | awk -F: -v iface="${ETH_IFACE}" '$2==iface {print}' || true
  fi

  if [[ -f "${DHCPCD_CONF_PATH}" ]]; then
    local b e
    read -r b e < <(dhcpcd_marker_counts)
    echo
    echo "dhcpcd markers for ${ETH_IFACE}: BEGIN=${b} END=${e}"
  fi

  echo
  echo "Suggested safe apply behavior:"
  echo "  - Keep wlan0 as default route."
  echo "  - Ensure ${ETH_IFACE} routes ${TEST_DEST_IP}."
  echo
}

# -------------------------
# Argument parsing
# -------------------------

parse_args() {
  if [[ $# -eq 0 ]]; then
    ACTION="apply"
    return 0
  fi

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --apply) ACTION="apply" ;;
      --diagnose|--check|--dry-run) ACTION="diagnose" ;;
      --uninstall|--remove) ACTION="uninstall" ;;
      --force) FORCE="yes" ;;
      --no-rollback) ROLLBACK="no" ;;
      -h|--help) usage; exit 0 ;;
      *)
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"
  require_root

  print_header
  print_current_state

  local manager
  manager="$(detect_manager)"

  if [[ "${ACTION}" == "diagnose" ]]; then
    echo "Detected network stack: ${manager}"
    diagnose_common
    exit 0
  fi

  if [[ "${manager}" == "unknown" ]]; then
    echo "Error: could not detect NetworkManager or dhcpcd." >&2
    exit 1
  fi

  if [[ "${ACTION}" == "uninstall" ]]; then
    if command_exists nmcli; then
      uninstall_networkmanager_hardened || true
    fi
    uninstall_dhcpcd_hardened || true
    echo
    echo "After uninstall:"
    ip route show default 2>/dev/null || true
    ip route get "${TEST_DEST_IP}" 2>/dev/null || true
    exit 0
  fi

  # ACTION == apply
  preflight_safety_checks

  local snap_file=""
  if [[ "${manager}" == "networkmanager" ]]; then
    snap_file="$(mktemp)"
    nm_snapshot > "${snap_file}"
  fi

  # For dhcpcd, rollback snapshot is the file backup we create; we also keep a temp copy here for immediate rollback.
  local dhcpcd_backup=""
  if [[ "${manager}" == "dhcpcd" && -f "${DHCPCD_CONF_PATH}" ]]; then
    dhcpcd_backup="$(mktemp)"
    cp "${DHCPCD_CONF_PATH}" "${dhcpcd_backup}"
  fi

  set +e
  if [[ "${manager}" == "networkmanager" ]]; then
    apply_networkmanager_hardened
  else
    apply_dhcpcd_hardened
  fi
  local apply_rc=$?
  set -e
  if [[ ${apply_rc} -ne 0 ]]; then
    echo "Apply failed with code ${apply_rc}." >&2
    exit ${apply_rc}
  fi

  ip link set "${ETH_IFACE}" up >/dev/null 2>&1 || true

  # Post-apply verification and rollback if needed.
  if ! verify_post_apply; then
    local vrc=$?
    echo "Verification failed (code ${vrc}). This indicates a risk of loss of Wi-Fi SSH connectivity." >&2

    if [[ "${ROLLBACK}" == "yes" ]]; then
      echo "Rolling back changes."
      if [[ "${manager}" == "networkmanager" ]]; then
        nm_rollback_from_snapshot "${snap_file}" || true
      else
        if [[ -n "${dhcpcd_backup}" && -f "${dhcpcd_backup}" ]]; then
          cp "${dhcpcd_backup}" "${DHCPCD_CONF_PATH}" || true
          systemctl restart dhcpcd >/dev/null 2>&1 || true
        fi
      fi
      echo "Rollback complete."
    else
      echo "Rollback disabled (--no-rollback). You may be locked out if you are remote." >&2
    fi

    exit ${vrc}
  fi

  echo
  echo "Apply succeeded and safety checks passed."
  echo "Verify UDP over ${ETH_IFACE}:"
  echo "  nc -u -v -w2 -s ${STATIC_IP_CIDR%/*} ${TEST_DEST_IP} ${TEST_DEST_PORT}"
}

main "$@"