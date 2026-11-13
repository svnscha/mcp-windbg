#!/bin/bash
# stop-target.sh <name>: ask the guest to shut down (ACPI power button) and wait for QEMU to exit.
# A guest held at a debugger break-in cannot react to the power button: resume it first
# (close_kd_session with resume=true, or `g` in kd), or this times out.
name=$1
printf '{"execute":"qmp_capabilities"}\n{"execute":"system_powerdown"}\n' | socat - UNIX-CONNECT:"${KD_DIR:-/srv/kd}/$name.qmp" >/dev/null
for _ in $(seq 1 180); do pgrep -f -- "-name $name -machine" >/dev/null || exit 0; sleep 1; done
echo "$name did not power off in 180 s. Is it stopped in the debugger? Resume it and run this again." >&2
exit 1
