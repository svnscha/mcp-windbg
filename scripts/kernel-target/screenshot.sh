#!/bin/bash
# screenshot.sh <name>: save the guest screen to /tmp/<name>.png, the only window into a headless guest
printf '{"execute":"qmp_capabilities"}\n{"execute":"screendump","arguments":{"filename":"/tmp/%s.png","format":"png"}}\n' "$1" | socat - UNIX-CONNECT:"${KD_DIR:-/srv/kd}/$1.qmp" >/dev/null; sleep 1
