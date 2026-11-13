# A live kernel target for `kernel_session.yaml`

CI has no machine to kernel-debug, so `kernel_session.yaml` and `kernel_resume_and_wait.yaml`
skip there. These scripts build one: a Windows Server guest in QEMU/KVM whose kernel debugger
talks KDNET through QEMU's emulated Hyper-V synthetic debugger (`hv-syndbg`). QEMU sends the
debug packets as plain UDP from the Linux host to the machine running `kd.exe`, so the guest needs
no network card at all.

```
Linux host (KVM)                              Windows debugger
  QEMU ── Windows Server guest                  kd.exe -k net:port=50000,key=1.2.3.4
          bcdedit /debug on, net, nodhcp        mcp-windbg + pytest -m kernel
     └─ hv-syndbg ─── UDP 50000 ──────────────▶
```

Any Linux box with KVM works as the host. `gcp-rig.sh` sets the pair up on Google Cloud, where
the host is an N2 VM with nested virtualization.

## Licensing

The target is installed from Microsoft's **evaluation** ISOs (Windows Server 2022 and 2025,
Standard, Desktop Experience). An evaluation install runs for 180 days, then needs rebuilding.
Do not boot a cloud provider's Windows image inside QEMU instead: those images are licensed to
run on that provider's own VMs. The debugger VM can use one, because it runs where it is licensed.

## Steps

1. **Machines** (GCP; skip on your own hardware): `scripts/kernel-target/gcp-rig.sh up`. It
   prints the debugger's internal IP.
2. **Host**: copy this directory to the host and run `./host-setup.sh`. It installs QEMU and
   OVMF, mounts the targets disk at `/srv/kd`, and downloads both ISOs, checked against pinned
   SHA-256 hashes.
3. **Install the targets**, unattended, about 10 to 20 minutes each, both at once if you like:

   ```bash
   export ADMIN_PASSWORD='<a password for the guest Administrator>'
   VNC=1 ./build-target.sh ws2025 /srv/kd/iso/ws2025-eval.iso 2
   VNC=2 ./build-target.sh ws2022 /srv/kd/iso/ws2022-eval.iso 2
   ```

   Image index 2 is Standard with Desktop Experience on both ISOs. The guest sets its debug
   options at first logon and powers off, which ends the script.
4. **Debugger**: on the debugger VM, put `uv.zip` (a hash-checked
   [uv release](https://github.com/astral-sh/uv/releases)) and `src.zip` (`git archive
   --format=zip HEAD`) in `C:\stage`, then run `dbg-setup.ps1`. It installs the debuggers from
   the Windows SDK installer, after checking its Authenticode signature, because Windows Server
   has no winget.
5. **Boot a target** at the debugger: `./run-target.sh ws2025 <debugger internal IP>`. Add
   `50000 on` to try `use_hcalls=true`. `./screenshot.sh ws2025` shows the screen;
   `./stop-target.sh ws2025` shuts it down cleanly.
6. **Run the scenarios** on the debugger:

   ```powershell
   $env:MCP_WINDBG_KERNEL_CONNECTION = "net:port=50000,key=1.2.3.4"
   uv run pytest src/mcp_windbg/tests/ -m kernel -v
   ```

7. **Afterwards**: `gcp-rig.sh down` deletes both VMs and the firewall rule and keeps the
   targets disk, so the next run starts at step 2 with nothing left to install.

## Things that are not obvious

- **The debugger's address is a QEMU argument, not a guest setting.** `hv-syndbg` sends to its
  own `host_ip` and reports that address to the guest, so the `hostip` in the guest's
  `dbgsettings` is never used. The targets are built with a placeholder there, and a rebuilt
  debugger VM with a new IP needs only a new `run-target.sh` argument.
- **`nodhcp` is required**: QEMU's synthetic debugger implements no DHCP.
- **The ISO's "Press any key to boot from CD or DVD"** is answered over QMP for the first 15
  seconds. Not for longer: Enter would then press Setup's Cancel button.
- **GCE Windows SSH** needs both `enable-windows-ssh=TRUE` and `ssh-keys` metadata before the
  guest agent creates the user. `debugger-startup.ps1` adds the OpenSSH capability, the key and
  the firewall rules.
- **A process started over Windows OpenSSH dies with the session** if it was started with
  `Start-Process`. For a long-running server use
  `Invoke-CimMethod Win32_Process -MethodName Create`.
- **`uv sync` needs `--python 3.13`** while the newest CPython has no `pyyaml` wheel for Windows;
  building it needs a C compiler the VM does not have.
