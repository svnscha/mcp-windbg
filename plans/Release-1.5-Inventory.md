# Release 1.5 inventory: open issues and pull requests

Status: complete except PR #124 and PR #131, whose reviews are still running (2026-10-08).

Baseline commit: `bd7052b` on `develop`. Nothing in this document is a decision. It records
what the code does, what the open contributions do to it, and what was measured. The
decisions live in [Release-1.5-Plan.md](Release-1.5-Plan.md).

## The backlog

| # | Kind | Author | Title | Size |
| :- | :--- | :----- | :---- | :--- |
| 125 | issue | xiaozhu1337 | Failed open tools leak undisclosed debugger sessions | - |
| 130 | issue | xiaozhu1337 | Follow-up safety gaps: marker framing, bounded output, cleanup | - |
| 124 | PR | robster7674 | `fix(debugger)`: cancel timed-out commands on dump sessions | +164/-11, 2 files |
| 126 | PR | xiaozhu1337 | `fix(server)`: clean up undisclosed sessions when open fails | +620/-383, 4 files |
| 127 | PR | xiaozhu1337 | `fix(cdb)`: confirm remote thread context before triage | +163/-9, 4 files |
| 128 | PR | xiaozhu1337 | `fix(debugger)`: require session-unique standalone markers | +1151/-1036, 3 files |
| 129 | PR | xiaozhu1337 | `fix(symbols)`: preserve inherited path when prepending dump dir | +62/-1, 4 files |
| 131 | PR | xiaozhu1337 | `fix(debugger)`: bound output, make context and cleanup reliable | +2880/-1303, 24 files |
| 132 | PR | dependabot | `deps(python)`: pymdown-extensions >=12.1 | +1/-1, docs only |
| 134 | PR | release-please | `chore(develop)`: release 1.4.1 | version bumps |

PR #131 declares itself a consolidation of #126, #127, #128 and #129, offered as an
alternative to merging those four, and states that it corrects a native-marker regression
that #128 introduced.

No CI ran on any of the six contributor PRs ("no checks reported"), while Dependabot's #132
ran the full matrix green. Nothing in #124 to #131 has been exercised by the project's own
test matrix.

## Part 0: what was measured on real debuggers

Everything in this part was produced in this session against real binaries, not inferred
from the PR descriptions. It is the ground truth that falsifies several claims below.
Debuggers: Windows SDK `cdb.exe` and `kd.exe` 10.0.26100.1742 at
`C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\`. The probe scripts are in this
session's scratchpad (`probe_marker.py`, `probe_remote.py`); they spawn the debugger with the
same pipes and `creationflags` as `debug_session.py` and print `repr()` of every raw line.

### 0.1 The completion marker is always prompt-prefixed

`cdb -z src/mcp_windbg/tests/dumps/DemoCrash1.exe.7088.dmp`:

```
'0:000> COMMAND_COMPLETED_MARKER_1\n'
'0:000> start             end                 module name\n'
'0:000> COMMAND_COMPLETED_MARKER_2\n'
```

`kd -z src/mcp_windbg/tests/dumps/KernelMiniDump.0xE2.dmp`:

```
'8: kd> COMMAND_COMPLETED_MARKER_1\n'
```

Consequences, verified rather than assumed:

- The marker line never arrives bare. The debugger writes its prompt without a trailing
  newline, so prompt and `.echo` output share one line.
- For the same reason the **first line of a command's real output is prompt-prefixed too**
  (`'0:000> start             end ...'`).
- `cdb` and `kd` do **not** echo piped input. There is no `.echo COMMAND_COMPLETED_MARKER_1`
  line on the pipe. The command echo exists only in the `.logopen /u` transcript, which is
  why `_LOGGED_PROMPT` (`debug_session.py:172`) is needed on the log path alone.
- The kernel prompt carries a varying processor number: `8: kd> `, not `0: kd> `.

This decides PR #128. Any matcher requiring the marker to stand alone on its line rejects
every real marker and wedges every session.

### 0.2 Remote client prompt forms

A real local dump debug server (`cdb -server tcp:port=51777 -z <dump>`) with a real
`cdb -remote tcp:port=51777,server=localhost` client, reading the client's pipe:

```
'SVNSCHA-BOX\SvenScharmentke (tcp [::ffff:127.0.0.1]:65329) connected at Thu Oct  8 22:16:45 2026\n'
'0:000> COMMAND_COMPLETED_MARKER_1\n'
'0:000> rax=000000000000005b rbx=0000000000000003 rcx=0000000000000003\n'
'rdx=000000087fafec48 rsi=0000000000000000 rdi=0000000000000003\n'
'0:000> COMMAND_COMPLETED_MARKER_2\n'
```

- A remote client on a stopped target uses the plain `0:000> ` prompt. The
  `[server (tcp ...)]` banner is its own line at connect time, not a prompt prefix.
- `?:???> ` is the no-current-process-or-thread state, not a general remote form.
- The **first line of `r` output is prompt-prefixed**, which is what breaks PR #127's
  register check (see part D).

### 0.3 `-y` does not shadow `_NT_SYMBOL_PATH`

With `_NT_SYMBOL_PATH=SRV*C:\probe-cache*https://msdl.microsoft.com/download/symbols` in the
environment and `-y C:\probe-dumpdir` on the command line, real `cdb` reports:

```
Symbol search path is: C:\probe-dumpdir;SRV*C:\probe-cache*https://msdl.microsoft.com/download/symbols
```

The engine appends `_NT_SYMBOL_PATH` after `-y`; only `-sins` suppresses it. The PR #129
form (`-y "<dumpdir>;<env>"`) yields a byte-identical effective path. Reproduced
independently twice: by the #129 reviewer on `cdb`/`kd` 10.0.28000.2705 and by this session
on 10.0.26100.1742.

This decides PR #129: its premise is false.

### 0.4 CTRL+BREAK does interrupt a running command on a dump session

This is PR #124's premise, and it holds. `cdb -z <dump>` spawned in its own process group with
`CREATE_NEW_PROCESS_GROUP`, given a deliberately slow engine-side loop, then sent
`CTRL_BREAK_EVENT`:

```
[  4.00s] startup done, sending slow loop + MARKER_1
[  8.00s] after 4s, MARKER_1 seen = False (the loop is still running)
[  8.00s] sending CTRL_BREAK_EVENT to the process group
[  8.50s] MARKER_1 arrived -> the command was ABORTED by the break
[  9.00s] MARKER_2 arrived -> the session SURVIVED and accepts commands

[  8.00s] "0:000>     ^ User interrupted operation error in '.for (r $t0 = 0; ...)'"
[  8.00s] '0:000> COMMAND_COMPLETED_MARKER_1\n'
[  8.50s] '0:000> start             end                 module name\n'
[  8.50s] '0:000> COMMAND_COMPLETED_MARKER_2\n'
```

So on a dump session the break aborts the command with a "User interrupted operation" error,
the pending marker then lands, and the session stays usable for the next command. Without the
break the command runs to completion and every later command queues behind it, which is the
wedge #124 describes.

### 0.5 Test baseline on `develop` (`bd7052b`)

```
uv run pytest src/mcp_windbg/tests/ -m "not live" -q   ->  143 passed, 15 deselected, 38s
uv run pytest src/mcp_windbg/tests/ -q                 ->  156 passed, 2 skipped, 62s
```

Measured on this host, with `cdb`/`kd` present, `_NT_SYMBOL_PATH` unset and
`MCP_WINDBG_KERNEL_CONNECTION` unset. Any candidate branch has to match or beat this.

### 0.6 The minimal marker fix was prototyped and passes

To check that the replacement for #128 is real and not just plausible, it was applied to
`develop` in a throwaway worktree: `import uuid`, a `self._marker_nonce = uuid.uuid4().hex`
beside `_marker_seq`, the nonce in `_next_marker`, and `:354` changed to
`line.rstrip().endswith(self._expected_marker)`. Four lines of production change, `:348` left
exactly as it is.

```
uv run pytest src/mcp_windbg/tests/ -q   ->  156 passed, 2 skipped, 60s
```

Identical to the 0.5 baseline, with the live tests driving real `cdb` and `kd`. So the whole
of #128's genuine value is available for four lines and no regression, against #128's
+1151/-1036 that stops the product opening at all.

### 0.7 TESTBOX-1 state (2026-10-08)

The VM is running and reachable over SSH from the PowerShell tool. But:

- ACP/OEMCP/MACCP are 1252/437/10000, so it is **not** on a multibyte code page right now.
  The multibyte regression test needs the registry switch plus a reboot.
- `C:\work\mcp-windbg` **no longer exists**. The checkout from the issue #102 work is gone,
  so a manual run needs a fresh clone.
- `uv` 0.12.9 at `C:\tools\uv\uv.exe`; `cdb.exe` and `kd.exe` 10.0.26100.1742 present.
- `bcdedit /enum` reports `debug  No`, so kernel debugging is off. A kernel-session test
  needs `/debug on` plus `/dbgsettings net` and a reboot, and the host Default Switch IP may
  have changed since it was last set.

## Part A: baseline, the session layer

`D` = `src/mcp_windbg/debug_session.py` (1036 lines) unless stated otherwise.

- Markers are generated at `D:418-420` as `COMMAND_COMPLETED_MARKER_<n>` from a per-session
  counter starting at 1, and written at `D:624` (`cmd\n.echo marker\n`), `D:504` and `D:934`.
- The reader loop is `D:342-361` inside `_read_output` (`D:334-373`). It applies **two
  separate substring tests**: `D:348` `MARKER_BASE in line` only classifies a line as the
  wrapper's own and drops it from output (`D:360`); `D:354` `_expected_marker in line`
  decides completion.
- `send_command` is `D:574-611`, `_send_marked` `D:613-659`, the timeout branch `D:630-649`,
  `_abort_running_command` `D:764-782`, `_abandon_marker` `D:458-479`.
- `shutdown` is `D:988-1014`, `_terminate_process` (`taskkill /F /T`) `D:1016-1030`. The KD
  resume loop (three `g`, about 1.8s) is `kd_session.py:204-208`. There is no reader join and
  no explicit stream close.
- `MAX_PARTIAL_OUTPUT_*` (`D:55-56`) bound only the timeout diagnostic (`D:429-456`), not
  retained output.
- Verbose diagnostics go to **stdout** at `D:345`, `D:365` and `D:1011`.

Defects and rough edges:

- **A-D1** (`D:172`, `D:214`, `D:674`): `_LOGGED_PROMPT` does not match `?:???>`. Inert today
  because `-remote` never uses the log path (`cdb_session.py:89`, `D:674`); if it did, the
  command echo would leak as the first output line.
- **A-D2** (`D:354`, `D:348`, `D:710`): the unanchored `_expected_marker in line` test is the
  sole cause of the `_1` versus `_10` prefix collision. It is **unreachable on a private
  pipe**, because one session's counter is monotonic. It needs several clients on one
  `-remote` server with independent counters. `D:710-711` repeats the pattern in the log
  reader but is unreachable.
- **A-D3** (`D:338`, `D:361`): retained output is unbounded.
- **A-D4** (`D:345`): `--verbose` writes to stdout and corrupts MCP stdio.
- **A-D5** (`D:360`, `D:476`): after a timeout the abandoned command's late output stays in
  the reader buffer and leads the next command's reply. Always the case for dump sessions,
  which never break in.
- **A-D6** (`D:320-322`): every startup error is reported as "initialization timed out".
- **A-D7** (`D:656-659`, `D:716`): on a log-to-pipe fallback the byte offset is not advanced,
  so the next segment includes the old transcript.
- **A-D8** (`D:172`): the permissive prompt regex drops real output lines such as `5: a -> b`.
  `PROMPT_REGEX` (`D:46`) is dead code.
- **A-D9** (`D:691`, `D:722`): a log setup failure after `.logopen` succeeds leaks the temp
  file.
- **A-D10** (`D:1013`): no thread join, no stream close, and a racy `self.process = None`.
- **A-D11** (`D:808`): `_resume_target` writes stdin directly, bypassing exit detection.
- **A-D12** (`D:890`, `D:838`): fixed 2s and 0.5s probe windows can misjudge the running state
  and send a needless CTRL+BREAK.
- **A-D13** (`D:941`): on a `wait_for_break` timeout the partial output is discarded.
- **A-D14** (`D:599`): `timeout=0` silently becomes the default.
- **A-D15** (`D:776`, `D:998`): `shutdown` sets `ready_event`, which falsely signals a resync.

## Part B: baseline, the server layer

Paths relative to `src/mcp_windbg`.

- `_sessions` is a module-level dict with **no lock** (`server.py:67`); ids are the kind plus
  8 hex characters (`server.py:70-71`). `_register_session` is `server.py:74-77`,
  `_close_session` `server.py:120-136` (pops at 127, swallows shutdown errors at 132-135).
- The four handlers are `open_cdb_dump` 617-642, `open_cdb_remote` 644-665, `open_kd_session`
  667-688, `open_kd_dump` 690-711. Each registers the session immediately after construction
  and **before** init commands and triage: 633, 656, 679, 702.
- Concurrency: the server installs no limiter, so both `to_thread.run_sync` calls
  (`server.py:522`, `server.py:744`) use AnyIO's default `CapacityLimiter(40)`. `open_*`,
  `run_*`, `close_*` and `wait_for_break` share those 40 tokens; `list_dumps` (530) and
  `send_ctrl_break` (575) run on the event loop. A parked `wait_for_break` holds a token in
  `ready_event.wait` (`debug_session.py:937`, 300s default). Filters run synchronously on the
  loop thread (527, 530-578).

Defects and rough edges:

- **B-D1**: a session is registered before init and triage with no rollback (633, 656, 679,
  702), so a failure leaks the process and the registry entry and loses the id. This is issue
  #125 and it is **real**.
- **B-D2**: the shared 40-token limiter can starve a `close_*` behind parked waiters (522,
  744), and the shutdown that would wake them needs a token itself.
- **B-D3**: no cancellation handling (522, 744); a cancelled `open_*` still registers a
  session nobody holds an id for.
- **B-D4**: closing with the wrong kind reports "No active ... session found" rather than a
  kind mismatch (121-123).
- **B-D5**: shutdown errors are swallowed and the close still reports success (132-135).
- **B-D6**: `_sessions` is mutated unlocked and `cleanup_sessions` iterates it uncopied (872).
- **B-D7**: a session-id collision overwrites the existing entry (74-77).
- **B-D8**: error paths bypass the output filter (582-590).
- **B-D9**: `list_dumps` globs on the event loop (530, 594-615).
- **B-D10**: the discovery helper is unsorted and non-recursive (767-777).
- **B-D11**: `--verbose` prints to stdout on the stdio transport
  (`debug_session.py:344-345`), corrupting JSON-RPC. Same as A-D4.
- **B-D12**: `--verbose` configures no logging, so `logger.info` has no handler
  (`server.py:365`, `filter_script.py:38`).
- **B-D13**: a non-positive timeout is silently replaced by the default (58-60, 736-740).
- **B-D14**: over HTTP the registry is shared by all clients (67, 343).
- **B-D15**: `open_cdb_dump` returns guidance on an empty `dump_path` before validation, while
  `open_kd_dump` errors (619 versus 691).
- **B-D16**: a stale `open_windbg_dump` example in `tests/e2e/README.md:91`.
- **B-D17**: the `launch` factory in `test_kd_session.py` builds a real `KDSession` without
  the code-page pin used in `test_debug_session.py:195-201`.

B-D1 reproduced independently in this session, against the real registered `on_call_tool`
handler on `develop`, with a fake debugger that constructs successfully and fails its first
triage command the way a `!peb` timeout does:

```
attempt 1: raised MCPError: Error executing tool open_cdb_dump: Command timed out after
attempt 2: raised MCPError: Error executing tool open_cdb_dump: Command timed out after

sessions left registered : 2
registry keys            : ['cdb-07e64f5a', 'cdb-3c717b8b']
shutdown() calls         : 0
```

Two failed opens leave two registered sessions, the debugger is never shut down, and the
caller received no id for either, so neither can ever be closed. This is the one defect in
the whole backlog that is real, uncontested and independently confirmed.

Test layout: `conftest.py` has no autouse fixtures; the code-page pin is
`_single_byte_code_page` at `test_debug_session.py:195-201`, local to that file. Of 31
scenarios, 17 carry no marker, 9 are `live`, 3 `live` plus `remote`, 2 `live` plus `kernel`.

## PR #124: cancel timed-out commands on dump sessions

Review verdict: **merge with changes.** The defect is real and the core mechanism works. Run
against real `cdb` and `kd` on committed dumps: on base a dump session wedges after a timeout;
on the PR a CTRL+BREAK cancels the runaway command and the next command returns in about 0s,
8 of 8 repeats on both `cdb` and `kd`. This agrees with the independent measurement in 0.4.

`CREATE_NEW_PROCESS_GROUP` is safe on all four session types: a hard-killed parent leaves no
orphan `cdb`, and the tree kill is unaffected. The one behaviour change is that Ctrl+C in the
server's own terminal no longer reaches dump children.

- **124-D1** (medium) The stray-break "absorb" rests on a **wrong premise**. A break sent to an
  idle debugger is dropped, not pended. The only victim is a command that starts within about
  10ms of the signal (measured: at a 0ms gap 7 of 15 truncated; at 10ms or more, 0 of 15). With
  absorb enabled 3 of 12 are still truncated, so it does not even close the window it targets.
  In practice the next command arrives after an LLM round trip, so it buys almost nothing for
  about 25 source and 16 test lines. Drop it, or replace it with a short sleep.
- **124-D2** A break that is not honoured, for example during `.sleep`, wedges the session for
  that phase and costs a 10s resync wait. The "manual break-in" hint in the error message is
  wrong for dump sessions, because `server.py:112` rejects `send_ctrl_break` on them.
- **124-D3** Dropping late output is sound, but only because stdin is FIFO. Real marker lines
  are prompt-prefixed and the reader's substring test handles that; no pollution was observed
  on real `cdb` or `kd`. The cost is that any output line containing the marker text now wipes
  the earlier output on a dump session.
- **124-D4** The Unicode-log skip list stalls if its head marker never lands, and it has no
  cap. With the multibyte check forced on, the log path is correct on real engines; the base
  code leaks there.
- **124-D5** The new comments cite issues #449 and #468, which do not exist in this repository.
- **124-D6** `debug_session.py:232`, `debug_session.py:593` and `kd_session.py:113` are left as
  stale comments.
- **124-D7** CI never ran, and no test pins the process-group flag on dump sessions.

Test quality: the new tests mostly encode the fake, which has no stray-break model and emits
bare markers rather than the real prompt-prefixed ones. A real-engine `.for`-loop scenario on
the committed dumps would pin the fix in CI, which is what 0.4 shows is possible.

#124 versus #131 on the same problem: #131 never breaks into dump sessions, so **every** dump
timeout closes the session, including ones that would have cancelled in under a second. For
someone working a 60 GB kernel dump that costs a reopen and all session state. #124 keeps the
session in the common case but can leave it wedged when the break is ignored. The reviewer's
recommendation, which this plan adopts, is the hybrid: cancel first, and close only if the
cancel failed to resync.

## PR #126: session ownership on a failed open

Review verdict: **merge with changes.** Take the two fix commits, drop the extraction commit
`a4f01eb`, apply 126-D3 and 126-D5, and decide 126-D2. This is the only PR in the backlog whose
defect and fix both hold up.

The defect is real and was reproduced twice independently (by the reviewer on a `3d7dbbf`
export and by this session above): 2 records left, 0 shutdowns. A leaked session is
unreachable, because there is no session-enumeration tool, until the `atexit` handler at
`server.py:870-884`.

The `ExitStack` fix is correct on all six paths:

- constructor failure: the stack is still empty and `_startup` self-cleans
  (`debug_session.py:313-322`), so it is correctly out of scope;
- mandatory triage and optional-section failures raise in the same frame, so `pop_all()` is
  skipped;
- a filter-hook exception is covered, because `filter_tool_content` (PR `server.py:256`)
  precedes `pop_all()` at `:258`;
- cancellation mid-initialization is covered: `to_thread.run_sync` runs with
  `abandon_on_cancel=False` (anyio 4.14.2 is pinned), so the worker is awaited and then the
  shielded rollback runs, with `checkpoint_if_cancelled` at `:257` covering cancel-after-worker.

It can **never** close a session whose id the caller received: `pop_all()` discards the
callbacks, and the id appears only in `results[0]`, never in the error or the traceback. There
is no double-close, because the stack pops and `_close_session` claims the record via
`_sessions.pop`. The shield was verified real: with a 3s shutdown under `fail_after(1.0)` the
request exited at 3.02s with the session gone.

Important sequencing constraint: the thread-affinity claim is true for the PR as a whole but
false for its first commit alone, so `59af40f` must not be merged without `0e48bfb`.

- **126-D1** (medium-high, invisible to the hermetic tests) The shielded rollback is unbounded.
  `taskkill` has no `timeout=` (`debug_session.py:1021-1024`) and `_release_target`'s stdin
  write can block (`:982-986`), so a cancelled open against a wedged `cdb` can hang teardown
  and stop the server exiting.
- **126-D2** (medium) The rollback resumes live targets: CTRL+B detach for a cdb remote, three
  `g` for kd (`resume_on_close=True`, `kd_session.py:74`). The user loses a break with no way
  to opt out, while `close_kd_session` does expose a `resume` parameter. Dump sessions are
  unaffected.
- **126-D3** (low) The `finally` fires on success too, costing two thread hops instead of one
  and an uncancellable wait on the 40-slot limiter. Use `except BaseException: rollback; raise`.
- **126-D4** (low) A residual cancel window remains after the checkpoint, and the filter hook
  still runs for a response that is then discarded.
- **126-D5** (low) `cleanup` as a trailing positional argument can be silently shifted.
- **126-D6/D7** (extraction only) A dead `PROMPT_SPECS` import at PR `server.py:29`, a double
  blank line at `:233-234`, a silently shortened comment, and `CLAUDE.md` left describing the
  old layout.

The extraction commit does not pay for itself. `git revert a4f01eb` applies cleanly, and
cherry-picking only the two fix commits onto `3d7dbbf` conflicts with nothing and yields a
byte-identical tree; 169 of 170 non-live tests pass without it (the full PR gives 170, matching
the author's claim). The only loss is one 19-line prompt-error test, which can be rewritten
through `_create_server`'s captured handler. The move was proven semantics-preserving by
normalised diff, and prompt path resolution, the re-export surface, the e2e harness and
`scripts/validate-server-schema.py` were all checked (the script only validates `server.json`
and never imports the package). The stated "keep both files under 800 lines" rationale is
invented: `develop`'s `server.py` is 884 lines and the repo has no linter enforcing any limit.

Test quality: 26 cases confirmed; run against unpatched `develop` 25 fail and 1 passes, which
matches "21 initial" plus 4 later cancellation cases, the single pass being the thread-affinity
no-regression guard. Mostly pins behaviour, but the two `shutdown_thread != get_ident()`
asserts (lines 89 and 167) encode the implementation and should be softened. The fakes make
126-D1 and 126-D2 structurally invisible.

## PR #127: remote thread context

Review verdict: **rework**. The reviewer drove both the base tree and the PR tree against a
real `cdb` named-pipe debug server with `waitfor.exe` as the target.

- **127-D1** The PR does not fix the case in its own title. With a genuinely running target
  (server started `-c "g"`) the `.echo` handshake itself never completes, and base and PR both
  fail with "Debugger initialization timed out" after the full timeout. The PR sends
  CTRL+BREAK only **after** the handshake returns. Sending CTRL+BREAK during the handshake made
  the base tree open in 2s with working `!peb` and `r`, so the fix is aimed at the wrong point
  in the sequence.
- **127-D2** Regression: a target with no thread context, which the base tolerated, now fails
  the open after the full timeout and kills the client.
- **127-D3** Every remote open now sends CTRL+BREAK, including to stopped targets, with no
  parameter, no tool-description text and no open-result text.
- **127-D4** Connect-then-fail, or connect-then-close, leaves a shared server's target stopped
  until a human types `g`.
- **127-D5** Handshake, break and probes now share one deadline, so a slow but healthy link can
  fail to open (a 50s handshake of a 60s budget leaves 10s for the context probe). `!peb` and
  `r` still get a fresh timeout each (`server.py:660`, `server.py:662`).
- **127-D6** The register matcher is
  `_REGISTER_CONTEXT = re.compile(r"^\s*(?:eax|rax|r0|x0|pc|ip|eip|rip|iip)\s*=\s*[0-9a-f]+", re.I)`
  (pr/127 `cdb_session.py:31`, used at `:147`). Against the real strings from 0.2:
  `'0:000> rax=...'` **no match**, `'0:000> x0=...'` **no match**, `'0:000> eax=...'` **no
  match**; only a later `rip=`/`eip=`/`pc=` line matches. The first-register alternatives are
  dead code on real output and the probe passes by accident. x64 passes via `rip=`, x86 and
  WOW64 should pass via `eip=`, arm64 relies on `pc=` and is unverified. Any truncated or
  filtered `r` makes the open fail after the full timeout. The PR's fake process emits an
  unprefixed `rax=` line, which is exactly why its tests miss this.
- **127-D7** A retry loop writes `r` plus a marker every 50ms, which piles up on a shared
  server.
- **127-D8** Redundant double shutdown.
- **127-D9** The docs and the triage prompt claim behaviour the code does not deliver.

Accurate and separable: the "closing a client does not resume the server's target" correction
was measured true in both trees and is worth landing on its own.

Test quality: the 7 fake-process tests pin the new implementation, not `cdb`. The fake answers
`.echo` while the target runs, which real `cdb` does not do.

## PR #129: symbol path

Review verdict: **rework or close**. The premise is false, see 0.3.

- **129-D1** `-y` does not shadow `_NT_SYMBOL_PATH`; the change is a no-op that produces an
  identical effective symbol path. `docs/reference/cli.md:73-78` already documents the
  behaviour the engine delivers.
- **129-D2** It duplicates the engine's own environment logic in Python, and only for
  `_NT_SYMBOL_PATH`, not `_NT_ALT_SYMBOL_PATH`.
- **129-D3** The PR breaks an existing test on most debugging machines. `test_kd_session.py:222`
  asserts the exact `-y C:\fake\dumps` and never clears the environment, so once the code
  appends `_NT_SYMBOL_PATH` it fails wherever that variable is set. Verified here: on
  `develop` with `_NT_SYMBOL_PATH=SRV*...` the file is green (18 passed), because the base
  code does not put the environment into the arguments. The author's green run and this
  host's green baseline both had the variable unset, and CI never ran on the PR. The latent
  fragility (the test does not isolate the environment) is real but harmless on `develop`.
- **129-D4** The added docs use `.sympath+ srv*C:\\Symbols*...` with JSON-style doubled
  backslashes, which is a wrong path in a debugger command.
- **129-D5** The `-remote` bullet is plausible and consistent with `cdb_session.py:86-88` but
  unverified.
- **129-D6** The docs rewrite an existing msdl line, which is churn.

Docs accuracy: `_NT_SYMBOL_PROXY` **is** real with `host:port` syntax (Microsoft's "Firewalls
and Proxy Servers" page, which the PR links), the default-proxy statement is supported by the
same page, and the `!sym noisy` / `.reload /f` / `!sym quiet` advice is accurate. Typography is
clean. The content is nonetheless off-topic for this fix.

Test quality: the 44 added lines assert only the `-y` string and rebuild the expected value
with the same expression the code uses, so they cannot detect that the premise is wrong, and
they miss 129-D3.

## PR #128: session-unique standalone markers

Review verdict: **reject as implemented**. The defect it names is real but narrow; the fix as
written stops the product from opening any session at all.

Measured, running pr/128's own modules against real debuggers and the repo's committed dumps:

```
base:    OPEN OK 0.2s, 'r' returns 10 lines   |  pr/128: FAILED after 15.0s
base:    KD OPEN OK 0.2s, vertarget OK        |  pr/128: FAILED after 15.0s
                                                 "Debugger initialization timed out"
```

- **128-D1** (blocker) `debug_process.py:201-224` has a single completion path, `:216`
  `self._expected_marker == line`, reachable only through `fullmatch` at `:212`, so it accepts
  a bare marker only. Per 0.1 a bare marker never occurs on the pipe. Chain:
  `__init__:178` -> `_startup` -> `_wait_for_prompt` blocks on `ready_event`, which is set only
  at `:221`, so it never fires and every open tool fails on its first command.
- **128-D2** (blocker) `:207` `if MARKER_BASE in line and _LOGGED_PROMPT.match(line): continue`
  runs first and discards every real marker line before `:216` can see it. `?:???>` is not
  matched by `_LOGGED_PROMPT`, so that form falls through to `:223` and is **published as tool
  output** while still never completing: a hang plus a marker leak into user-visible text.
- **128-D3** (blocker) Deleting `:207` does not rescue it. The matcher is wrong by
  construction, so #131 needs the same real-target gate before it can be trusted.
- **128-D4** `rstrip("\r\n")` replaces the base `rstrip()`, so output keeps trailing whitespace.
- **128-D5** `_log_offset` advances only if the `.echo` echo line matched; on `?:???>` it never
  advances, giving a 2s penalty and an O(log) rescan per command plus a silent fallback to
  truncated pipe output. Its torn-read guard is genuinely good and worth porting on its own.
- **128-D6** The tests encode a wrong model of `cdb`: `test_marker_protocol.py:38` asserts that
  a prompt-prefixed marker must **not** complete, and the fake emits bare markers
  (`test_debug_session.py:126`). The suite certifies the bug rather than catching it.
- **128-D7** The module split's seam is fictional: `DebuggerProcess` calls five methods only
  `DebuggerSession` defines (`_startup`, `_enable_unicode_log`, `_next_marker`,
  `_marker_landed`, `_abort_running_command`), so it is not constructible on its own.
- **128-D8** Both files were rewritten LF to CRLF (538/538 and 503/503 lines, no
  `.gitattributes`), which is why `--stat` reports 1574 changed lines where `--stat -w` reports
  530.
- **128-D9** `MAX_PARTIAL_OUTPUT_*` is enforced against `debug_process` globals, so a future
  monkeypatch through `debug_session` silently no-ops.

Why the base code works and this does not: the base `:354` `_expected_marker in line`
substring test is the **load-bearing mechanism**, the only reason a prompt-prefixed marker
line completes a command. In the base code the defect and the working mechanism are the same
expression. The `.logopen /u` transcript genuinely does frame markers standalone
(`0:000> .echo M` followed by `M`), and #128 transplanted that log reasoning into the pipe
reader, where the prompt has no newline and input is never echoed.

CI would have caught this: `kernel_dump.yaml` runs in the full suite in CI
(`build-and-test.yml:34,62`), and it opens a committed kernel minidump. `gh pr checks 128`
reports "no checks reported".

Scope: 1036 lines become 503 plus 538, a near-pure move. Nothing in the split is needed for
the fix; the subclass contract, the threading and the imports all survive dropping it.

The minimal replacement, three edits to the base `debug_session.py`:

1. `self._marker_nonce = uuid.uuid4().hex` at `:267`.
2. `_next_marker` returns `f"{MARKER_BASE}_{nonce}_{seq}"`.
3. Keep `:348`'s scaffolding rule verbatim; change only `:354` to
   `line.rstrip().endswith(self._expected_marker)`.

It stays prompt-agnostic, which matters: enumerating prompt forms is exactly what killed #128.
The nonce is the load-bearing part, since `_1` versus `_10` is reachable only through a shared
`-remote` transcript and the nonce alone makes it unreachable.

The `endswith` anchoring in edit 3 is defence in depth rather than a fix for anything
reachable today. The reviewer suggested its payoff was rejecting
`cdb: Reading initial command '.echo <marker>'`, but the product never passes `-c` to the
debugger (`grep` for `-c` across `src/mcp_windbg/*.py` finds nothing), so that line cannot
occur. Keep the anchoring anyway, because it costs one expression and removes the whole class,
but do not justify the change with it.

The fake process must also be changed to emit `0:000> {marker}`, or the next attempt at this
lands in exactly the same place.

## PR #131: the consolidated safety PR

Review verdict: **do not merge as-is; harvest.** Three genuine fixes are buried in a rewrite
that adds one session-wedging regression, one kernel hazard, one privacy problem, and a
timeout policy strictly worse than #124's.

Measured here, which is the important difference from #128: pr/131 **does** open sessions
against real debuggers.

```
pr/131, hermetic : 223 passed, 15 deselected, 44s
pr/131, full      : 236 passed, 2 skipped, 64s   (the 2 skips are the kernel scenarios)
```

So it carries no #128-class total failure. But the live suite never exercises a running remote
target or a live kernel session, which is exactly where its remaining defects sit.

Its own opt-in gate fails on this machine:

```
uv run python src/mcp_windbg/tests/e2e/check_native_protocol.py
AssertionError  at  assert session.send_command(".echo unicode_\u4e2d\u6587", timeout=3) == ["unicode_\u4e2d\u6587"]
```

On ACP 1252 the pipe is ANSI, so the CJK text comes back as `'0:000> unicode_??'`
(`_acp_is_multibyte()` is False). The check is code-page dependent with no guard, so the PR's
claim that it "passed on both the current WinDbg CDB execution alias and Windows SDK CDB"
does not reproduce on a Western machine, and it would fail in CI.

### Transport half

- **131T-D1** (critical) `debug_output.py:15` `LOGGED_PROMPT` is byte-identical to the base
  `debug_session.py:172`, but #131 promotes that **log-scaffolding** regex into the **pipe
  completion gate**, where the base used a plain substring (`3d7dbbf:debug_session.py:348`).
  Any prompt form the regex does not know now wedges that session type. By regex run it
  accepts `0:000>`, `1:001:x86>`, `8: kd>`, `lkd>`, `kd>`, `[server (...)] 0:000>`, a bare
  token and CRLF; it **rejects** `?:???>`, `*BUSY* 0:000>`, an unbracketed host banner, and a
  trailing-whitespace token that the base accepted. `?:???>` is the no-process state, i.e. a
  running live target, so `open_cdb_remote` against a running target fails where the base
  works, and mid-session every probe wedges too. This is #128's mistake again, narrower.
- **131T-D2** (critical) `debug_session.py:352-373`: dump sessions are not `is_live_session`,
  so `resynced` is False and the session is shut down. **Every** dump timeout kills `cdb`, so
  `!analyze -v` on a large `MEMORY.DMP` loses all resolved symbols. Its own justification
  (stale output) is already solved by this PR's unique markers plus stale-marker drop, so the
  close is both unnecessary and harmful.
- **131T-D3** (high) `_SHUTDOWN_GRACE_SECONDS = 2.0` against `KDSession._release_target`
  needing 1.8s minimum: kd gets taskkilled mid-resume and the kernel target is left frozen.
  The base released synchronously.
- **131T-D4** (high) Every remote command now returns a full `r` register dump as a preamble,
  plus three marker waits (`cdb_session.py:93-123`). `test_debugger_safety.py:307` encodes it.
- **131T-D5/6/7** (medium) `os.path.getsize(None)` raises `TypeError` and kills the reader,
  which then reports a fake "exited"; a `_cleanup_log` raise destroys an otherwise successful
  command; a taskkill exit-128 race produces a spurious close failure.
- **131T-D8/9** (medium) The 8 MiB kill fires on healthy huge commands and is DBCS-only, so
  behaviour depends on the locale; the bound is not configurable, the tail is unrecoverable,
  and the truncation notice omits how much was lost.
- **131T-D11** `_FakeProc` emits bare markers (`test_debug_session.py:138,154`), which real
  `cdb` never does. That is how #128 shipped green and how 131T-D1 would too. The fixture needs
  parameterised prompt forms. `check_native_protocol.py` is not pytest-collected (no `test_`
  name, no marker), writes `__tmp/` into the repo, fakes the DBCS gate at `:34` so issue #102
  is never actually reproduced, and never touches `kd` or a running target.

Two transport conclusions worth keeping, both verified independently below: the blank `.echo`
framing is **not** a DBCS workaround and should be kept on every code page, and the quoted
`.logopen /u` path is a real fix.

### Server half

- **131S-D2** (blocker) `server.py:113-115` pops the session only after `shutdown()`, and
  `_require_session:69` never consults `_closing_sessions`. A model that calls
  `close_cdb_session` and then `run_cdb_command` on the same id, which is routine, now sends a
  command into a dying debugger whose reader thread is gone, so no marker ever lands and the
  caller waits out the full 60s or 120s timeout. The base answered `Unknown session_id`
  immediately. Three-line fix, and untested in the PR.
- **131S-D4** (high) Three separate things:
  - (a) The base **does** print to stdout (`debug_session.py:344-345`, `:364`, `:1010`), 921
    `DBG >` lines measured. But `mcp` 2.1.1's `stdio_server()` already diverts fd 1 to stderr
    while serving: with `PYTHONUNBUFFERED=1`, 909 went to stderr and only 12 to stdout, all
    after `restore_stdout()` at exit. So the bug is real but it is trailing garbage, never a
    split JSON-RPC frame.
  - (b) `error_log.py:33-35` hard-codes `<package dir>/logs/errors.log`, which lands in
    site-packages or the uv cache. Running the suite created a 28,939 byte file containing full
    tracebacks and absolute dump paths (`Dump file not found: C:/...nope.dmp`), and
    `DebuggerError.partial_output` puts target data there too. It **bypasses `--filter-script`**,
    which the README presents as redacting data before it leaves the machine, and a
    machine-wide install is readable by `Users`. Bounded at 4 MiB, so no disk fill. The README's
    stated path is wrong for uvx and pip installs.
  - (c) Not justified: `--verbose` currently wires **no** logging at all in either tree (there
    is no `basicConfig` anywhere), so every `logger.info` is discarded. Four lines of
    `basicConfig(stream=sys.stderr)` is the minimal correct fix.
- **131S-D1** (medium) Measured over real stdio with 60 dumps: base returns 60 entries, pr/131
  returns **50** (`limit` default 50, `server_support.py:71`). Not strictly silent, since the
  header says "More dumps available", but the tool description is unchanged, so the model's
  schema view contains no notion of a page.
- **131S-D5** (medium, **the author never mentions it**) `sorted(glob.glob(...))` became a bare
  `iglob` (`server.py:396`), so the result order changes for **every** caller, paging or not:
  `['a','B','c','Mid','Z0','zz','_z']` instead of `['B','Mid','Z0','_z','a','c','zz']`.
- **131S-D12** (blocker) `docs/reference/` is untouched: `tools.md:50-58` lacks `offset`/`limit`
  and `:190-197` does not say a close can fail. CLAUDE.md requires these to stay in sync.
- **131S-D11** `_build_session`'s `cls.__new__`/`__init__` probes `.process`, but the test's
  fake sets that itself, so a rename would break production silently.
- **131S-D14** Three tests pass against a broken product: the error-log test pins magic numbers
  and never checks the directory; the starvation test mutates the global anyio limiter and only
  proves "a different limiter"; `test_debugger_safety.py:284` writes to the **repo root**
  `__tmp/` instead of `tmp_path`, which is the only reason for that new `.gitignore` line.
  `conftest.py:24-37` adds a suite-wide autouse patch of `_terminate_process` that imports
  `_FakeProc` from `test_debug_session`, duplicated again at `:52-57`.
- **131S-D17** The triage prompt now says "only break again if resumed", while the same PR's
  docs admit another client can resume unobserved. Keep "break before you inspect".
- **131S-D15** `troubleshooting.md:50` renders `C:\\Symbols` inside a code span, so it cannot be
  copied. **131S-D10** Leftover `# ponytail:` tags at `error_log.py:47` and `server.py:394`.

Schema diff across all 11 models: the **only** change is `ListDumps.offset`/`limit`. No tool,
parameter, default, required-ness or error text changes otherwise. One new surface though: a
`close_*` can now fail, where the base always said "Successfully closed".

`server_support.py` is identical to #126's but for the two field lines, and a built wheel
contains all new modules and the three prompts, with the prompt path resolving off
`prompts/__init__.py`. It is the cleanest part of the PR. Typography passes
`Format-Docs.ps1 -Check`.

### Verified independently: the two transport claims worth adopting

**The blank `.echo` framing is load-bearing on every code page.** Real `cdb`, bare `.echo` with
no argument:

```
'0:000> AAA\n'
'0:000> \n'      <- exactly one line, just the prompt
'0:000> BBB\n'
```

So it does end the current line. This matters because the prompt carries no trailing newline
(inventory 0.1), so any command whose output does not end in a newline glues the prompt and
the marker onto the output's last line. The framing guarantees the marker starts a fresh line.
It costs no extra round trip, since it rides the same write and the same wait.

**The quoted `.logopen /u` path is a real fix, and the base bug is worse than reported.** Real
`cdb`, a log path containing spaces:

```
.logopen /u C:\...\claude\probe dir with spaces\unquoted.log
  -> "Opened log file 'C:\...\claude\probe'"
     "    ^ Extra character error in '.logopen /u C:\...\unquoted.log'"
  -> intended log exists = False

.logopen /u "C:\...\claude\probe dir with spaces\quoted.log"
  -> "Opened log file 'C:\...\probe dir with spaces\quoted.log'"
  -> intended log exists = True
```

Unquoted, `cdb` does not merely fail. It opens a **different** log at the truncated prefix and
leaves a 954-byte junk file named `probe` behind. So on any machine whose resolved temp path
contains a space (a username with a space, or a redirected `TEMP`), the base code silently
disables the issue #102 multibyte fix and litters a stray file. One-line fix.

### Ship versus cut

The transport reviewer would keep: the bounded reader buffer (the actual memory fix behind
#125), the quoted `.logopen /u` path, log rotation, and the recovery id. It would cut the
three-way module split (land the fixes in `debug_session.py`), the `shutdown()` inside
`_abort_running_command`, the remote `r` probe (about 70 lines), the release thread (about 25),
the dead `_extract_log_output` (17 lines, which also orphans `test_unicode_log.py`'s 9
assertions) and `_bounded_reply` (6). About 390 of roughly 760 runtime lines are cuttable.

The server reviewer would ship: the ownership transaction, `close_*` honesty,
`server_support.py`, `file=sys.stderr`, off-loop discovery, the remote-debugging docs and the
prompt closing contract. It would cut about 470 of 1,460 added lines: `offset`/`limit`,
`error_log.py` and its 20 call sites, both `.gitignore` entries, the README "Debugger safety"
block and the troubleshooting proxy tutorial.

## Merge mechanics

All numbers below were measured in throwaway worktrees, not estimated.

### Overlap matrix

- Every PR merges into `bd7052b` cleanly **on its own**.
- #126, #127, #128 and #129 are mutually independent and land in any order. Only #127 and
  #129 share a file (`cdb_session.py`), with hunks far apart.
- #124 collides only with #128 and #131, both in `debug_session.py`.
- Because the project squash-merges, ancestry is destroyed, so **#131 is mutually exclusive
  with #126 to #128 in practice**. Squash #126 then merge #131 gives conflicts in `server.py`
  (8 blocks), `server_support.py` and `test_open_cleanup.py`; #127 then #131 conflicts in
  `cdb_session.py` (3 blocks) and `test_remote_startup.py`; #128 then #131 conflicts in
  `debug_process.py` and `debug_session.py`. All four first: 7 conflicted files, 17 blocks.
  The reverse order is equally bad. Only #129 is free either way, with an empty residual diff.
- #124 plus #128 is structurally the worst case: **one conflict block spanning lines 1 to 1639
  of a 1640-line file**, because pr/128 commits `debug_session.py` and `debug_process.py` with
  CRLF (538 and 503 CR bytes) into an all-LF repo that has no `.gitattributes`. #131
  renormalises them back to LF.

So the merge order question is really a fork: **either #126 to #129, or #131. Not both.**

### Is #131 a superset?

Commit-wise yes: all nine commits of #126 to #129 are ancestors, through five merge commits
whose `diff-tree --cc` is empty, so there are no evil merges. All modification comes from one
commit, `338a440`.

Content-wise it is a superset in intent, not byte for byte:

| From | How it appears in #131 |
| :--- | :--------------------- |
| #129 | verbatim, all four files |
| #126 | `server_support.py` and tests verbatim; `server.py` gains 14 hunks and drops one line (`await _run_debugger_handler(cleanup.close)`) |
| #127 | the `_startup` retry loop is **deleted and reimplemented**: 12 of 41 added lines gone, replaced by `_wait_for_target_context`, callbacks and `DebuggerContextError` |
| #128 | the reader is reworked: +168/-86 in `debug_process.py` after CR normalisation, 54 of 503 added lines gone, 78 deleted lines resurrected |

Reviewing #131 therefore adds roughly +2450/-1440 across 17 files **beyond** the four PRs,
including two new modules (`debug_output.py` 88 lines, `error_log.py` 51) and about 1150 lines
of new tests plus a test-file reshuffle.

### #124 is not in #131

Verified by absence: no `_abandoned_log_markers`, no `_absorb_stray_break`;
`debug_process.py:167` still gates `CREATE_NEW_PROCESS_GROUP` on `is_live_session`, and
`debug_session.py:360` still gates `_abort_running_command` on `is_live_session` with the
"nothing to break into" docstring. #131's alternative to the whole problem is
`if not resynced: self.shutdown()`.

Taking #131 instead of #124 therefore loses: dump-session cancel-on-timeout, the per-session
process group, stray-break absorption, abandoned-marker log skipping and the dump-only
reader-buffer reset. Given 0.4, that is losing a mechanism that was measured to work.

### #124 must be reimplemented, not rebased

`git apply --reject` of #124's 7 production hunks: **6 of 7 fail onto pr/128**, **7 of 7 fail
onto pr/131**. Rebase stops on the first commit both times. Six of the seven targets moved to
`debug_process.py`, and under #131 hunk 6's `_read_log_segment` no longer exists at all,
having become a module-level `read_log_segment` in `debug_output.py`. #124's test hunks still
apply, but to a gutted module.

### Version arithmetic

Six `fix:` commits is one patch bump, so release-please still computes **1.4.1** and leaves
#134 alone. To get 1.5.0, either retitle one PR as `feat:` (the only honest candidate is #131,
for adding `offset`/`limit` to `list_dumps`) or merge one with a `Release-As: 1.5.0` body.
Then delete the branch `release-please--branches--develop--components--mcp-windbg`, which
closes #134 (whose hand-added 1.4.1 CHANGELOG commit would otherwise be lost), re-run
`release-please.yml` by `workflow_dispatch`, and hand-write the `## [1.5.0] - YYYY-MM-DD`
heading. #134 has no file overlap with #132.

### Why no CI ran, and the fix

The workflow runs exist, but all nine sit at `conclusion: action_required`. The repository's
`fork-pr-contributor-approval` is set to `first_time_contributors`, and all six PRs are fork
PRs from two first-time contributors. Dependabot's #132 is same-repo, which is why it ran the
full matrix.

Fix per run: "Approve and run workflows" in the UI, or
`gh api -X POST repos/svnscha/mcp-windbg/actions/runs/<id>/approve`. Newest run per branch:
36974012036, 37174169961, 37176589194, 37176678035, 37176592729, 37185804216. Permanently:
set the policy to `first_time_contributors_new_to_github`.

`develop` is unprotected, so nothing blocks a merge mechanically. This is the single cheapest
quality gate available here: CI runs `kernel_dump.yaml` against a committed kernel minidump,
which is exactly the test that would have failed #128.

### Commit hygiene

All six PR titles are valid Conventional Commits (`fix:`). No body contains a footer-shaped
`Word:` line, so the #121 failure mode is not present. But the repository has
`squash_merge_commit_message = PR_BODY`, so every merge must still pass `--body ""` per the
project's own rule. #124's commits carry `Co-Authored-By: Claude Sonnet 5.5 / Opus 5.5`
trailers.
