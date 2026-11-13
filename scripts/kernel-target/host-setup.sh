#!/bin/bash
# Prepare the Linux host: QEMU, the targets disk at $KD_DIR, and the evaluation ISOs.
# Safe to re-run: it formats the disk only when it has no filesystem yet.
set -euo pipefail
dir=${KD_DIR:-/srv/kd}
dev=/dev/disk/by-id/google-kdimages

sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update
sudo DEBIAN_FRONTEND=noninteractive apt-get -qq install -y qemu-system-x86 qemu-utils ovmf genisoimage socat
sudo usermod -aG kvm "$USER"
[ -e /dev/kvm ] || { echo "no /dev/kvm: is nested virtualization enabled on this VM?" >&2; exit 1; }

sudo blkid "$dev" >/dev/null || sudo mkfs.ext4 -q -L kdimages "$dev"
sudo mkdir -p "$dir"
mountpoint -q "$dir" || sudo mount "$dev" "$dir"
sudo chown "$USER" "$dir"

# Windows Server evaluation media from Microsoft's Evaluation Center, used here only as a test
# target. An evaluation install expires 180 days after it is made; rebuild it then. GCE's own Windows
# images are licensed for running on GCE, not inside a VM of your own, so they are not used for the
# target. Pinned by hash: if Microsoft replaces an ISO behind its link, the check fails and the new
# hash is a deliberate update.
fetch() {
  local name=$1 link=$2 sha=$3
  [ -f "$dir/iso/$name" ] || curl -sfL -o "$dir/iso/$name" "$link"
  echo "$sha  $dir/iso/$name" | sha256sum -c --quiet
}
mkdir -p "$dir/iso"
fetch ws2025-eval.iso 'https://go.microsoft.com/fwlink/?linkid=2293312&clcid=0x409&culture=en-us&country=us' \
  d0ef4502e350e3c6c53c15b1b3020d38a5ded011bf04998e950720ac8579b23d
fetch ws2022-eval.iso 'https://go.microsoft.com/fwlink/?linkid=2195280&clcid=0x409&culture=en-us&country=us' \
  3e4fa6d8507b554856fc9ca6079cc402df11a8b79344871669f0251535255325
echo "ready: $dir"
