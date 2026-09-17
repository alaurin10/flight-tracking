#!/usr/bin/env bash
# =====================================================================
# Run ON THE PROXMOX HOST (as root) to create the flighttrack LXC.
#
#   bash pct-create.sh            # uses the defaults below
#   CTID=110 IP=192.168.1.60 GW=192.168.1.1 bash pct-create.sh
#
# Unprivileged Debian 12 container, 1 vCPU, 512 MB, 3 GB thin disk —
# the same shape as the Pi-hole LXC in proxmox/README.md of home-hub.
# =====================================================================
set -euo pipefail

CTID="${CTID:-110}"
HOSTNAME="${HOSTNAME_:-flights}"
IP="${IP:-192.168.1.60}"          # pick a free address in your LAN and reserve it on the router
CIDR="${CIDR:-24}"
GW="${GW:-192.168.1.1}"
BRIDGE="${BRIDGE:-vmbr0}"
STORAGE="${STORAGE:-local-lvm}"   # where the rootfs lives (pvesm status)
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
DISK_GB="${DISK_GB:-3}"
RAM_MB="${RAM_MB:-512}"
SWAP_MB="${SWAP_MB:-256}"
CORES="${CORES:-1}"

# Latest Debian 12 template (downloads it once if missing).
pveam update >/dev/null
TEMPLATE=$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)
[ -n "$TEMPLATE" ] || { echo "no debian-12-standard template listed by pveam"; exit 1; }
pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE" || pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"

pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$HOSTNAME" \
  --unprivileged 1 \
  --features nesting=0 \
  --cores "$CORES" --memory "$RAM_MB" --swap "$SWAP_MB" \
  --rootfs "$STORAGE:$DISK_GB" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=$IP/$CIDR,gw=$GW" \
  --onboot 1 \
  --start 1 \
  --description "flighttrack: airfare tracker (serve on :8080). Repo: github.com/alaurin10/flight-tracking"

echo "created CT $CTID ($HOSTNAME @ $IP). Next, inside it:"
echo "  pct exec $CTID -- bash -c 'apt-get update && apt-get install -y git && git clone https://github.com/alaurin10/flight-tracking /opt/flighttrack && bash /opt/flighttrack/deploy/lxc/install.sh'"
