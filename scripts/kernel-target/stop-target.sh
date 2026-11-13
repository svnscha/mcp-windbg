#!/bin/bash
# stop-target.sh <name>: ask the guest to shut down (ACPI power button) and wait for QEMU to exit.
name=$1
printf '{"execute":"qmp_capabilities"}\n{"execute":"system_powerdown"}\n' | socat - UNIX-CONNECT:"${KD_DIR:-/srv/kd}/$name.qmp" >/dev/null
for _ in $(seq 1 120); do pgrep -f -- "-name $name -machine" >/dev/null || exit 0; sleep 1; done
echo "$name did not power off in 120 s" >&2; exit 1
