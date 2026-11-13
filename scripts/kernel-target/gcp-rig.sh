#!/bin/bash
# Stand the kernel-target rig up on GCP, or take it down again.
# Usage: gcp-rig.sh up|down|ips
#
#   up    an n2 Linux host with nested virtualization (runs QEMU), a Windows debugger VM, an SSH
#         firewall rule from this machine's IPv4 address, and the disk that holds the targets
#   down  deletes both VMs and the firewall rule; keeps the targets disk for the next run
#   ips   prints the external IPs and the debugger's internal IP (run-target.sh needs that one)
#
# Settings, all optional: KD_PROJECT (default: gcloud's project), KD_ZONE, KD_PREFIX, KD_SSH_KEY.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
project=${KD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}
zone=${KD_ZONE:-europe-west4-a}
prefix=${KD_PREFIX:-mcpwindbg}
key=${KD_SSH_KEY:-$HOME/.ssh/google_compute_engine.pub}
host=$prefix-qemuhost dbg=$prefix-debugger disk=$prefix-kdimages rule=$prefix-ssh tag=$prefix
g() { gcloud --project "$project" "$@"; }

case ${1:-} in
up)
  myip=$(curl -4 -sf https://ifconfig.me)
  g compute firewall-rules describe "$rule" >/dev/null 2>&1 ||
    g compute firewall-rules create "$rule" --network default --allow tcp:22 \
      --source-ranges "$myip/32" --target-tags "$tag"
  g compute disks describe "$disk" --zone "$zone" >/dev/null 2>&1 ||
    g compute disks create "$disk" --zone "$zone" --size 64GB --type pd-standard \
      --description "mcp-windbg kernel targets built from Windows Server evaluation ISOs"
  # E2 machine types have no nested virtualization; N2 does.
  g compute instances create "$host" --zone "$zone" --machine-type n2-standard-8 \
    --enable-nested-virtualization --min-cpu-platform "Intel Cascade Lake" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size 60GB --boot-disk-type pd-balanced --tags "$tag" \
    --disk "name=$disk,device-name=kdimages,mode=rw,auto-delete=no"
  startup=$(mktemp)
  trap 'rm -f "$startup"' EXIT
  sed "s|@SSH_PUBKEY@|$(cut -d' ' -f1-2 "$key")|" "$here/debugger-startup.ps1" > "$startup"
  # The debugger runs on GCE's own Windows image, licensed by GCE for as long as it runs there.
  g compute instances create "$dbg" --zone "$zone" --machine-type e2-standard-4 \
    --image-family windows-2025 --image-project windows-cloud \
    --boot-disk-size 64GB --boot-disk-type pd-balanced --tags "$tag" \
    --metadata enable-windows-ssh=TRUE,ssh-keys="$USER:$(cat "$key")" \
    --metadata-from-file windows-startup-script-ps1="$startup"
  "$0" ips
  ;;
down)
  # Stop every target first (stop-target.sh): deleting the host under a running guest leaves its image unclean.
  g compute instances delete "$host" "$dbg" --zone "$zone" --quiet
  g compute firewall-rules delete "$rule" --quiet
  echo "kept $disk"
  ;;
ips)
  g compute instances list --filter "name=($host,$dbg)" \
    --format "table(name,networkInterfaces[0].networkIP:label=INTERNAL,networkInterfaces[0].accessConfigs[0].natIP:label=EXTERNAL)"
  ;;
*)
  sed -n '2,10p' "$0"; exit 2
  ;;
esac
