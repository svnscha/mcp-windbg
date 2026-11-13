#!/bin/bash
# Boot an installed target with QEMU's Hyper-V synthetic debugger aimed at the debugger VM.
# Usage: run-target.sh <name> <debugger-ip> [port] [use_hcalls: on|off]
# hv-syndbg sends KDNET as UDP from this host to <debugger-ip>:<port>, whatever IP the guest's
# dbgsettings name, so a rebuilt debugger VM needs only a new argument here.
set -euo pipefail
name=$1 dbg=$2 port=${3:-50000} hcalls=${4:-off}
dir=${KD_DIR:-/srv/kd}
[ "$hcalls" = on ] && hcalls=true || hcalls=false
# A host that went away under a running guest leaves the image unclean; catch that before booting.
qemu-img check -q "$dir/$name.qcow2" || { echo "$dir/$name.qcow2 needs: qemu-img check -r all" >&2; exit 1; }
exec qemu-system-x86_64 -name "$name" -machine q35,accel=kvm -smp 4 -m 6G \
  -cpu host,hv-relaxed,hv-spinlocks=0x1fff,hv-time,hv-vapic,hv-vpindex,hv-synic,hv-runtime,hv-stimer,hv-syndbg \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file="$dir/$name.vars.fd" \
  -drive file="$dir/$name.qcow2",if=none,id=d0,format=qcow2 -device ide-hd,drive=d0,bus=ide.0 \
  -device vmbus-bridge,irq=15 \
  -device hv-syndbg,host_ip="$dbg",host_port="$port",use_hcalls="$hcalls" \
  -nic none -display none -vnc 127.0.0.1:${VNC:-1} \
  -qmp unix:"$dir/$name.qmp",server=on,wait=off
