# Release 1.5 manual walkthrough on TESTBOX-1

What CI cannot do: a multibyte code page, a live kernel target, and a remote server whose
target is running. Those three are where every defect in this backlog hid, so this runs by
hand before 1.5.0 ships. Run it once when M3 is merged, and again on the release candidate.

Bracketed references point at [Release-1.5-Inventory.md](Release-1.5-Inventory.md).

## Before you start

TESTBOX-1 as found on 2026-10-08: running and reachable, ACP 1252, `uv` 0.12.9 at
`C:\tools\uv\uv.exe`, `cdb.exe` and `kd.exe` 10.0.26100.1742, `bcdedit` reports `debug No`,
and **`C:\work\mcp-windbg` no longer exists**.

Connect from the **PowerShell** tool, not Bash: Windows OpenSSH uses the Windows ssh-agent
that holds the key. The remote default shell is `cmd.exe`, so wrap everything in
`powershell -NoProfile -Command "..."`, or use `-EncodedCommand` for anything with nested
quotes. The SSH session is elevated, so `bcdedit` works.

```powershell
ssh TESTBOX-1 hostname          # expect: TESTBOX-1
```

## Step 0: fresh checkout

```powershell
ssh TESTBOX-1 'powershell -NoProfile -Command "git clone -b develop https://github.com/svnscha/mcp-windbg C:\work\mcp-windbg; cd C:\work\mcp-windbg; C:\tools\uv\uv.exe sync --dev"'
```

Expected: the clone succeeds and `uv sync --dev` installs without error. Check out the release
candidate branch rather than `develop` on the second pass.

## Part A: Western code page (1252, the state it is already in)

### A1. The suite is green on the VM

```powershell
ssh TESTBOX-1 'powershell -NoProfile -Command "cd C:\work\mcp-windbg; C:\tools\uv\uv.exe run pytest src/mcp_windbg/tests/ -q"'
```

Expected: at or above the baseline, which was 156 passed, 2 skipped on the dev host
[test baseline 0.5]. The 2 skips are the kernel scenarios; Part C removes them.

### A2. A user-mode dump opens in under a second

Open `src\mcp_windbg\tests\dumps\DemoCrash1.exe.7088.dmp` through `open_cdb_dump`.

Expected: a session id comes back promptly with `.lastevent` and `!analyze -v` output. This is
the exact check PR #128 fails after 15 seconds [0.1, PR #128 128-D1].

### A3. A kernel dump opens

Open `src\mcp_windbg\tests\dumps\KernelMiniDump.0xE2.dmp` through `open_kd_dump`.

Expected: a `kd` session id, with `vertarget` and `!analyze -v` output. The prompt in this
session is `8: kd>` with a varying processor number, which is why no code may assume `0: kd>`
[0.1].

### A4. A timed-out command is cancelled and the session survives

With a short timeout, run a deliberately slow command on the A2 session, for example
`.for (r $t0 = 0; @$t0 < 200000000; r $t0 = @$t0 + 1) { }`, then run `.lastevent` immediately
afterwards.

Expected: the slow command reports a timeout, and `.lastevent` answers normally rather than
queueing behind it. On 1.4.0 the session wedges instead [0.4, PR #124]. The session must still
be open: closing it on a timeout is the behaviour this release deliberately does not take from
#131 [131T-D2].

### A5. A failed open leaves nothing behind

Call `open_cdb_dump` on a path that does not exist, twice.

Expected: an error both times, and no leaked session. Check with
`Get-Process cdb -ErrorAction SilentlyContinue` that no `cdb.exe` is left, including the Store
alias child. On 1.4.0 a failure after a successful spawn leaks a registered session the caller
can never close [issue #125, B-D1].

### A6. `--verbose` keeps stdout clean

```powershell
ssh TESTBOX-1 'powershell -NoProfile -Command "cd C:\work\mcp-windbg; C:\tools\uv\uv.exe run python -m mcp_windbg --verbose 1>out.txt 2>err.txt"'
```

Drive one tool call, then stop it.

Expected: `out.txt` contains only JSON-RPC frames and `err.txt` carries the `DBG >` lines
[A-D4, B-D11, 131S-D4a]. Also confirm no `logs\errors.log` appears anywhere under the
installed package: that file is #131's, it is not in this release, and it would contain
absolute dump paths [131S-D4b].

### A7. `list_dumps` order is unchanged

Point `list_dumps` at a directory holding more than 50 dumps with mixed-case names.

Expected: the same order as 1.4.0, which is `sorted()`, and no truncation at 50
[131S-D1, 131S-D5].

## Part B: multibyte code page (needs a reboot)

This is the issue #102 configuration. It is the only way to exercise the `.logopen /u` Unicode
log path, which M2 and M4 both touch.

### B1. Switch the code page and reboot

```powershell
$s = @'
Set-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage -Name ACP -Value 936
Set-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage -Name OEMCP -Value 936
Set-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage -Name MACCP -Value 10008
Set-WinSystemLocale zh-CN
shutdown /r /t 3
'@
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($s))
ssh TESTBOX-1 "powershell -NoProfile -EncodedCommand $enc"
```

Then poll `ssh TESTBOX-1 hostname` until it answers. Only effective after the reboot.

### B2. Unicode output survives a round trip

Run `.echo unicode_中文` on a dump session.

Expected: `unicode_中文` comes back intact. This is the fix from issue #102, and it works only
on this code page: on 1252 the same command returns `unicode_??`, which is why #131's own
native check fails on a Western machine [PR #131, native check].

### B3. A spaced temp path still opens its log

Set `TEMP` to a directory whose name contains a space, then open a dump session.

```powershell
$s = @'
$d = "C:\temp with spaces"; New-Item -ItemType Directory -Force $d | Out-Null
$env:TEMP = $d; $env:TMP = $d
cd C:\work\mcp-windbg
C:\tools\uv\uv.exe run pytest src/mcp_windbg/tests/test_unicode_log.py -q
Get-ChildItem $d
'@
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($s))
ssh TESTBOX-1 "powershell -NoProfile -EncodedCommand $enc"
```

Expected after M4: the log opens at its intended path and Unicode output still works. Before
M4, `cdb` reports "Extra character error", opens a different log at the truncated prefix, and
leaves a junk file there, silently disabling the #102 fix [#131 "Verified independently"].
Confirm there is no stray extensionless file like `C:\temp` left behind.

### B4. The whole suite on the multibyte page

Re-run A1 here. Expected: still green. A multibyte host must not change the outcome, which is
what the `_single_byte_code_page` pin at `test_debug_session.py:195-201` is for; note that
`test_kd_session.py` does not have that pin [B-D17].

### B5. Switch back

Repeat B1 with ACP/OEMCP/MACCP 1252/437/10000 and `Set-WinSystemLocale en-US`, then reboot.

## Part C: live kernel target over KDNET (needs a reboot)

### C1. Enable kernel debugging

Get the host's Default Switch address first, since it changes after a host reboot (it was
172.24.144.1).

```powershell
Get-NetIPAddress -InterfaceAlias "vEthernet (Default Switch)" -AddressFamily IPv4 | Select-Object IPAddress
```

Then on the VM, with `<HOSTIP>` substituted:

```powershell
$s = @'
bcdedit /dbgsettings net hostip:<HOSTIP> port:50005 key:1.2.3.4
bcdedit /debug on
shutdown /r /t 3
'@
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($s))
ssh TESTBOX-1 "powershell -NoProfile -EncodedCommand $enc"
```

### C2. The kernel scenarios run

On the **dev host**, not the VM:

```powershell
$env:MCP_WINDBG_KERNEL_CONNECTION = "net:port=50005,key=1.2.3.4"
uv run pytest src/mcp_windbg/tests/ -m kernel -v
$env:MCP_WINDBG_KERNEL_CONNECTION = $null
```

Expected: green, with no skips. CLAUDE.md requires this before shipping a kernel change, and
M2, M3 and M4 all touch code a kernel session uses.

### C3. Closing a kernel session resumes the target

Open a kernel session, run `vertarget`, then close it with the default resume policy.

Expected: the VM keeps running afterwards rather than sitting frozen. #131 shortens the
shutdown grace to 2.0s while `KDSession._release_target` needs 1.8s minimum, which can
taskkill `kd` mid-resume and leave the target frozen; this release does not take that, and
this step is the check [131T-D3].

### C4. Turn kernel debugging back off

```powershell
ssh TESTBOX-1 'powershell -NoProfile -Command "bcdedit /debug off; shutdown /r /t 3"'
```

## Part D: a remote server whose target is running

The path with no automated coverage at all, and where both #127 and #131 have defects. Every
live scenario in the suite opens a *stopped* target, so this step is the only thing that
exercises it.

### D1. Start a server whose target is already running

On the VM, start a debug server against a long-lived process and let it run:

```powershell
cdb -server tcp:port=5005 -c "g" notepad.exe
```

### D2. Open it

Call `open_cdb_remote` against `tcp:port=5005,server=TESTBOX-1`.

Expected on this release: the same behaviour as 1.4.0, no worse. Record exactly what happens,
because this is the open question going into 1.6: 1.4.0 and #127 both fail here with
"Debugger initialization timed out", and the measurement that matters is whether a CTRL+BREAK
during the handshake recovers it, which it did for the base tree in about 2 seconds
[PR #127 127-D1].

Do **not** expect this release to fix it. D5 in the plan defers it deliberately, and the point
of this step is to capture the real behaviour so the 1.6 fix has a baseline.

### D3. A stopped remote target still opens cleanly

Restart the server without `-c "g"`, so the target is stopped, and open it again.

Expected: a session id, with `!peb` and `r` output. The first line of `r` output arrives
prompt-prefixed as `0:000> rax=...`, which is what breaks #127's register check [0.2, 127-D6].

### D4. Closing the client leaves the server's target alone

Close the session and check the server.

Expected: the target stays stopped and is not resumed, and the server keeps running. Closing a
client is not a promise to resume an independently owned target, and this release must not
start sending `g` on its own [127-D4, part of #126 126-D2].

## Results

Record each run here with the date, what was observed, and anything skipped on purpose.

### Run 1 (date, commit)

- [ ] Part A
- [ ] Part B
- [ ] Part C
- [ ] Part D

### Run 2, release candidate (date, commit)

- [ ] Part A
- [ ] Part B
- [ ] Part C
- [ ] Part D
