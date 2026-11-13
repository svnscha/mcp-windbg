#!/bin/bash
# Install a Windows Server evaluation ISO, unattended, into a qcow2 that boots as a KDNET target.
# Usage: build-target.sh <name> <iso> <image-index> [kd-port] [kd-key]
# Needs: qemu-system-x86 qemu-utils ovmf genisoimage socat, /dev/kvm, the admin password in $ADMIN_PASSWORD.
set -euo pipefail
name=$1 iso=$2 index=$3 port=${4:-50000} key=${5:-1.2.3.4}
dir=${KD_DIR:-/srv/kd}
here=$(cd "$(dirname "$0")" && pwd)
: "${ADMIN_PASSWORD:?set ADMIN_PASSWORD}"

disk=$dir/$name.qcow2 vars=$dir/$name.vars.fd qmp=$dir/$name.qmp
[ -e "$disk" ] && { echo "$disk exists" >&2; exit 1; }
qemu-img create -q -f qcow2 "$disk" 40G
cp /usr/share/OVMF/OVMF_VARS_4M.fd "$vars"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# The dbgsettings hostip is a placeholder: hv-syndbg routes by QEMU's host_ip (see run-target.sh).
sed -e "s|@IMAGE_INDEX@|$index|" -e "s|@ADMIN_PASSWORD@|$ADMIN_PASSWORD|g" \
    -e "s|@KD_HOSTIP@|169.254.0.1|" -e "s|@KD_PORT@|$port|" -e "s|@KD_KEY@|$key|" \
    "$here/autounattend.xml.in" > "$work/autounattend.xml"
genisoimage -quiet -J -r -o "$work/unattend.iso" "$work/autounattend.xml"

qemu-system-x86_64 -name "$name-install" -machine q35,accel=kvm -smp 4 -m 6G -cpu host \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file="$vars" \
  -drive file="$disk",if=none,id=d0,format=qcow2 -device ide-hd,drive=d0,bus=ide.0,bootindex=1 \
  -drive file="$iso",if=none,id=cd0,media=cdrom,readonly=on -device ide-cd,drive=cd0,bus=ide.1,bootindex=2 \
  -drive file="$work/unattend.iso",if=none,id=cd1,media=cdrom,readonly=on -device ide-cd,drive=cd1,bus=ide.2 \
  -nic none -display none -vnc 127.0.0.1:${VNC:-1} \
  -qmp unix:"$qmp",server=on,wait=off &
qemu=$!

# The UEFI boot loader on the ISO waits for "Press any key to boot from CD or DVD". Answer it,
# and stop well before Setup shows its Cancel button, which Enter would press.
for _ in $(seq 1 15); do
  sleep 1
  printf '{"execute":"qmp_capabilities"}\n{"execute":"send-key","arguments":{"keys":[{"type":"qcode","data":"ret"}]}}\n' \
    | socat - UNIX-CONNECT:"$qmp" >/dev/null 2>&1 || true
done

# Setup reboots by itself; FirstLogonCommands set the debug options and power the guest off.
wait $qemu
echo "$name: installed into $disk"
