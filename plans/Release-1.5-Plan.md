# Release 1.5 plan: land the backlog without regressions

Status: v3, 2026-10-08. The inventory is complete: all six PRs and both issues reviewed, with
every premise checked against real `cdb` and `kd` rather than taken from the PR text. D2, D7
and open question 1 are decided, so no decision blocks the work. Next action is **M0**, which
needs you (approving the fork-PR workflow runs). Nothing has been merged, pushed or closed,
and the working tree holds only this `plans/` directory.

## Goal

Ship 1.5.0 with the real defects from the #124 to #131 backlog fixed, and with nothing in it
that was not demonstrated to be both real and correctly fixed.

In scope:

- the session-ownership leak (issue #125), which is the one uncontested defect in the backlog;
- dump-session command cancellation (#124), whose mechanism was measured to work;
- session-unique completion markers, the genuine half of #128, implemented minimally in-house;
- two one-line correctness fixes the review surfaced (`--verbose` on stdout, unbounded reader
  retention);
- CI actually running on contributor PRs, which is what would have caught #128.

Deliberately dropped, each with evidence in the inventory:

- **#128 as written**: it stops the product opening any session against real `cdb`/`kd`.
- **#129 entirely**: its premise is false; `-y` does not shadow `_NT_SYMBOL_PATH`.
- **#127's break-in-on-connect**: it does not fix the case in its own title, and it regresses
  no-context opens.
- **#131 as a whole**: see D2.
- `list_dumps` pagination, `error_log.py`, Unicode-log rotation, the worker-pool split and the
  `server_support.py` extraction: feature work and refactors riding along with bug fixes.

Out of scope, with the plan that will take it: the remote-readiness problem behind #127 (a
connected remote target can legitimately be running, and `.echo` is not proof of readiness)
is real but unsolved. It needs its own change with a live running target to test against, so
it goes to 1.6 and stays open as issue #125's remaining half.

## Approach

The merge order problem is not a sequence, it is a **fork**. Because the project
squash-merges, taking #131 and taking #126 to #129 are mutually exclusive: doing both leaves
7 conflicted files and 17 blocks. So the release takes one path.

This plan takes the four-PR path, trimmed, plus in-house minimal fixes where a PR's
implementation was wrong but its diagnosis was right. The reasoning is in D2.

Each milestone is one branch off `develop`, one PR, squash-merged with `--body ""`, and it
has to be green in CI before the next one starts. `debug_session.py` is touched by M2 and M3,
so those two are strictly ordered.

## Interfaces

The MCP tool surface does not change in 1.5. No tool is added, removed or renamed, and no
parameter is added, removed or given a new default. This is a deliberate constraint: it is
what makes the release safe for existing user prompts and keeps `docs/reference/` accurate.
It is also the specific reason #131's `list_dumps` `offset`/`limit` is cut, since its
`limit: int = Field(default=50)` silently truncates an existing caller's result.

## Milestones

### M0: make CI run on contributor PRs

Features:

- [ ] Approve the six pending workflow runs, or set the repository's
      `fork-pr-contributor-approval` to `first_time_contributors_new_to_github`.
- [ ] Confirm a run actually completes on one contributor branch.

Acceptance:

- [ ] `gh pr checks <n>` reports the full matrix for at least one of #124 or #126, instead of
      "no checks reported".

This is first because it is the cheapest quality gate available and it is the gate that would
have rejected #128: `kernel_dump.yaml` runs in CI against a committed kernel minidump, and
#128 fails to open it.

### M1: session ownership on a failed open (issue #125, from #126)

Features:

- [ ] Take #126's two fix commits (`59af40f` then `0e48bfb`, in that order: the first alone
      has the thread-affinity problem). Drop the extraction commit `a4f01eb`.
- [ ] Apply 126-D3: use `except BaseException: rollback; raise` so the rollback does not run
      on the success path, where it currently costs a second thread hop and an uncancellable
      wait on the limiter.
- [ ] Apply 126-D5: make `cleanup` a keyword argument so it cannot be positionally shifted.
- [ ] Bound the rollback teardown (126-D1): give `taskkill` a timeout and the stdin release
      write a deadline, so a cancelled open against a wedged `cdb` cannot hang teardown.
- [ ] Change #126's rollback so it never resumes a live target (126-D2, per D7). As submitted
      it sends a CTRL+B detach for a cdb remote and three `g` for kd, so this is an edit to the
      PR, not just a trim. A stopped target stays stopped; resuming remains explicit through
      `close_kd_session`'s `resume` parameter.
- [ ] Soften the two `shutdown_thread != get_ident()` asserts, which pin the implementation.

Defects fixed: inv. B-D1, B-D3, and part of B-D5.

Acceptance:

- [ ] The repro in the inventory's server-layer part is clean: two failed `open_cdb_dump` calls leave
      `len(_sessions) == 0` and call `shutdown()` twice.
- [ ] #126's 26 ownership cases pass, minus the extraction-only prompt test.
- [ ] Full suite at or above the 156-passed baseline.
- [ ] Real-target: a forced `!peb` timeout against a real `cdb -server` leaves no orphan
      `cdb.exe`, including the Store-alias child process.

### M2: session-unique completion markers (replaces #128)

Features:

- [ ] The four-line fix verified in inventory 0.6: `import uuid`, a per-session
      `self._marker_nonce`, the nonce in `_next_marker`, and `:354` becomes
      `line.rstrip().endswith(self._expected_marker)`. Leave `:348` exactly as it is.
- [ ] Frame the marker with a bare `.echo`, taken from #131 and verified under "Verified independently" in the inventory: a
      bare `.echo` emits exactly one prompt-only line, so the marker always starts a fresh
      line. This is **not** a DBCS workaround, as the PR descriptions suggest; because the
      prompt has no trailing newline, a command whose output does not end in a newline
      otherwise glues the prompt and marker onto its last output line, and `:348` then drops
      that whole line. Keep it on every code page. It rides the same write and the same wait,
      so it costs no extra round trip.
- [ ] Change the test fake to emit `0:000> {marker}` rather than a bare marker, and
      parameterise it over the real prompt forms (`0:000>`, `1:001:x86>`, `8: kd>`, `lkd>`,
      `?:???>`), so the suite can no longer certify the #128 or 131T-D1 failure modes.
- [ ] Add a regression test that a prompt-prefixed marker line **does** complete a command,
      and one for the glued-output case the framing fixes.

Defects fixed: inv. A-D2.

Acceptance:

- [ ] Full suite at the 156-passed baseline. The nonce plus `endswith` half is already
      measured at 156 passed, 2 skipped; the framing addition needs its own run.
- [ ] Real-target: open a user-mode dump and a kernel dump with the patched tree and confirm
      startup in under a second, which is the check #128 fails after 15s.
- [ ] A command whose output does not end in a newline returns its last line intact.

Deliberately not done here: **no prompt parsing on the pipe path.** Enumerating prompt forms is
what broke #128, and it is what breaks #131 too (131T-D1 rejects `?:???>` and `*BUSY*`).
`:348`'s "a marker line is ours, never the target's" rule plus an end-anchored match needs no
prompt knowledge at all, which is the whole reason to prefer it.

### M3: cancel timed-out commands on dump sessions (from #124)

Depends on M2 (same file).

Features:

- [ ] Take #124's per-session process group and the dump-session CTRL+BREAK cancellation.
- [ ] Drop the stray-break absorb (124-D1): its premise is wrong, a break to an idle debugger
      is dropped rather than pended, and it was measured not to close the window it targets.
      About 25 source and 16 test lines go.
- [ ] Adopt the hybrid for lost sync: cancel first, and close the session only if the cancel
      failed to resync. This keeps #124's benefit and takes #131's safety property without
      #131's cost of closing a session that would have recovered in under a second.
- [ ] Cap the Unicode-log skip list and stop it stalling on a head marker that never lands
      (124-D4).
- [ ] Fix the error message that tells a dump-session user to break in manually, which
      `server.py:112` rejects (124-D2), and the stale comments and non-existent issue
      references (124-D5, 124-D6).

Defects fixed: inv. A-D5, part of A-D7.

Acceptance:

- [ ] A real-engine scenario on a committed dump: a `.for` busy loop times out, is cancelled,
      and the next command returns. This runs in CI, which is what 124-D7 is missing.
- [ ] Full suite at or above baseline.
- [ ] Real-target: `!process 0 7` on a large kernel dump at a 20s timeout cancels, and
      `!irql` immediately afterwards succeeds, 10 times in a row.

### M4: small hardening

Features:

- [ ] `--verbose` writes to stderr, not stdout (inv. A-D4, B-D11). **Smaller than it first
      looked**: `mcp` 2.1.1's `stdio_server()` already diverts fd 1 to stderr while serving,
      so of 921 measured `DBG >` lines only 12 reached stdout, all after `restore_stdout()` at
      exit. So this is trailing garbage, never a split JSON-RPC frame. Still worth the one-line
      `file=sys.stderr`, but it is not the protocol corruption the PR descriptions imply.
- [ ] Give `--verbose` a logging handler at all (inv. B-D12). Neither tree configures one, so
      every `logger.info` in `server.py` and `filter_script.py` is discarded today. Four lines
      of `basicConfig(stream=sys.stderr)` is the whole fix, and it is the half of the verbose
      story that actually changes behaviour.
- [ ] `close_*` stops reporting success when shutdown failed (inv. B-D5). This is worth taking
      from #131's diagnosis: the base `_close_session` swallows the error and still says
      "Successfully closed".
- [ ] Bound the reader's retained output (inv. A-D3) while still draining the pipe and still
      recognising a marker, including one split across chunks.
- [ ] Move dump discovery off the event loop (inv. B-D9), but **keep `sorted()`**. #131
      replaced `sorted(glob.glob(...))` with a bare `iglob` (`server.py:396`), which silently
      changes the result order for every caller: `['a','B','c','Mid','Z0','zz','_z']` instead
      of `['B','Mid','Z0','_z','a','c','zz']`. The author does not mention it anywhere.
- [ ] Quote the `.logopen /u` path (from #131, verified under "Verified independently" in the inventory). A real
      user-affecting bug: on a log path containing spaces, `cdb` does not merely fail, it opens
      a **different** log at the truncated prefix and leaves a junk file behind, so the issue
      #102 multibyte fix is silently disabled. Reachable whenever the resolved temp path has a
      space in it, such as a username with a space or a redirected `TEMP`. One line.
- [ ] Port #128's torn-read guard for the UTF-16 log, which 128-D5 calls genuinely good, and
      #131's log rotation if it comes out small once the 8 MiB session kill (131T-D8) is left
      out.

Acceptance:

- [ ] A `--verbose` stdio server completes an MCP handshake and a tool call with no protocol
      error, and its diagnostics appear on stderr.
- [ ] A command producing more output than the cap returns a clear truncation notice and the
      session stays usable.
- [ ] A failing `close_*` reports the failure instead of success.
- [ ] `list_dumps` returns the same order as 1.4.0 for the same directory.
- [ ] Full suite at or above baseline.

### M5: answer the backlog

Features:

- [ ] #128: close with the measurement (base opens in 0.2s, pr/128 fails after 15s on both
      `cdb` and `kd`), explain that the marker line is prompt-prefixed, and point at M2 as the
      landed fix. Credit the diagnosis.
- [ ] #129: close with the two independent `.sympath` measurements showing `-y` does not
      shadow `_NT_SYMBOL_PATH`.
- [ ] #127: explain 127-D1 and 127-D2, land only the accurate docs correction ("closing a
      client does not resume the server's target"), and keep the readiness question open for
      1.6.
- [ ] #131: explain the fork (D2) and which parts were harvested.
- [ ] #125: close on M1, noting that the remote-readiness half stays open.
- [ ] #130: keep open for the items not taken, closing the ones M2 to M4 cover.
- [ ] #132: merge, docs-only and already green.

Both contributors did real work here, including reproductions this plan relies on. The replies
should say so and should lead with the measurement rather than the verdict.

### M6: manual walkthrough on TESTBOX-1

Must complete before M7. The steps are written out in
[Release-1.5-Walkthrough.md](Release-1.5-Walkthrough.md), which covers the three things CI
cannot: a multibyte code page, a live kernel target, and a remote server whose target is
running. Run it once when M3 is merged and again on the release candidate.

Features:

- [ ] Fresh clone on the VM, since `C:\work\mcp-windbg` is gone.
- [ ] Western code page (1252, current state): dump open, kernel dump open, command timeout
      and cancel, failed open leaves no session.
- [ ] Multibyte code page (936 or 65001, needs the registry switch and a reboot): the same,
      to prove the Unicode-log path still works after M2 to M4 touched the marker and log
      code.
- [ ] Kernel session over KDNET (needs `bcdedit /debug on`, `/dbgsettings net`, a reboot, and
      the current host Default Switch IP): `pytest -m kernel` green.

Acceptance:

- [ ] Every step's expected result observed and recorded in the walkthrough with the date.
- [ ] `pytest -m kernel` green against the live target, which CI cannot do.

### M7: release 1.5.0

Features:

- [ ] Retire the open 1.4.1 release PR #134: delete the branch
      `release-please--branches--develop--components--mcp-windbg`.
- [ ] Force the minor bump with `Release-As: 1.5.0`, since six `fix:` commits compute 1.4.1.
- [ ] Hand-write the `## [1.5.0] - YYYY-MM-DD` CHANGELOG entry on the release PR, crediting
      both contributors.
- [ ] Check the release PR's diff touches `pyproject.toml`, `.release-please-manifest.json`,
      all three `server.json` versions, the plugin manifest, the marketplace entry and the
      `uvx` pin.
- [ ] Verify `docs/reference/` still matches the tool schemas (it should: M1 to M4 change no
      tool surface).

Acceptance:

- [ ] `scripts/check-version-consistency.ps1` passes.
- [ ] The tag, the GitHub release, PyPI, the MCP registry and the docs deploy all complete.

### M8: audit against the inventory

Features:

- [ ] Re-read every inventory defect and mark it fixed, deliberately not fixed, or moved.
- [ ] Run `svnscha-review-loop` on the merged result, then `svnscha-decide` on what is left.

## Tests

| Test | Kind | Milestone |
| :--- | :--- | :-------- |
| Failed-open rollback across all four tools | unit, hermetic | M1 |
| Cancelled open shields and bounds its rollback | unit, hermetic | M1 |
| Prompt-prefixed marker completes a command | unit, hermetic | M2 |
| Fake emits a realistic prompt-prefixed marker | fixture change | M2 |
| Dump timeout cancels, next command succeeds | e2e scenario, live | M3 |
| Lost sync closes the session, recovered sync does not | unit, hermetic | M3 |
| `--verbose` stdio server survives a tool call | e2e, hermetic | M4 |
| Oversized output truncates and leaves the session usable | unit, hermetic | M4 |
| Marker fake parameterised over real prompt forms | fixture change | M2 |
| Glued prompt-and-marker line keeps its output | unit, hermetic | M2 |
| `.logopen /u` on a spaced temp path opens the intended log | unit plus manual | M4 |
| `list_dumps` order matches 1.4.0 | unit, hermetic | M4 |
| Multibyte code page end to end | manual walkthrough | M6 |
| Live kernel target | `pytest -m kernel`, manual | M6 |
| No orphan `cdb.exe` after a forced open failure | manual, real target | M1 |
| `open_cdb_remote` against a **running** target | manual, real target | M6 |

## Risks

- **The hermetic suite cannot catch this class of bug.** #128 passed 107 tests and does not
  open a session; #127 passed 7 and does not fix its case. Every milestone therefore needs a
  real-debugger check, and M0 exists to make CI provide one automatically.
- **M2 and M3 both touch the marker and reader code**, which is the most load-bearing code in
  the project. They are ordered, each lands separately, and each is measured against the
  156-passed baseline rather than only its own new tests.
- **Closing three PRs from two active contributors** risks discouraging them. M5 treats that
  as a real deliverable, not an afterthought.
- **TESTBOX-1 needs two reboots** (code page, kernel flags), so M6 is not a quick step.
- **The running-remote-target path has no automated coverage at all.** Every live scenario
  opens a stopped target, which is why both #127 and #131 shipped a defect there (127-D2,
  131T-D1) and why pr/131 can pass 236 tests with a wedge in it. M6 covers it by hand; a
  scenario for it is worth adding in 1.6 alongside the readiness fix.
- **The project's own temp path hides the `.logopen` bug.** This host resolves `TEMP` to the
  8.3 form `C:\Users\SVENSC~1\...`, which has no space, so the defect is invisible here and in
  CI. It had to be reproduced with a deliberately spaced directory.

## Defects not fixed

In code this release does not touch, recorded so they are not lost: inv. A-D6, A-D8 to A-D15,
B-D4, B-D6 to B-D10, B-D12 to B-D17. Two worth naming because they are user-visible:

- **B-D14**: over the HTTP transport the session registry is shared by all clients.
- The prompt prefix is glued to the first line of every command's returned output
  (`'0:000> Last event: ...'`). Measured on `develop`, so it is long-standing rather than a
  regression. Cosmetic, and changing it would disturb the marker code this release is already
  reworking.

## Decisions

- **D1** Base every milestone branch on `develop`, one PR each, squash-merged with
  `--body ""`. Reason: `develop` is the working branch and the project's documented rule; the
  empty body avoids the release-please footer-parsing failure that bit #121.
- **D2** Decided by the user (2026-10-08): take the four-PR path (#124, #126, trimmed) plus
  in-house minimal fixes, and do **not** take #131. Reasons, in order of weight:
  - The two paths are mutually exclusive under squash-merge, so this is a real fork.
  - #131 adds roughly +2450/-1440 across 17 files beyond the four PRs, reviewed by nobody but
    its author.
  - It introduces a regression in a routine flow: `server.py:113-115` pops the session only
    after `shutdown()` and `_require_session` never consults `_closing_sessions`, so a model
    that calls `close_cdb_session` and then `run_cdb_command` on the same id now waits out the
    full 60s or 120s timeout against a dying debugger. Base answered `Unknown session_id`
    immediately.
  - `error_log.py` writes full tracebacks, absolute dump paths and `partial_output` target
    data to a hard-coded `<package dir>/logs/errors.log` (site-packages or the uv cache; a
    machine-wide install is readable by `Users`), and it **bypasses `--filter-script`**, which
    the README presents as redacting data before it leaves the machine. Running the suite
    created a 28,939 byte file. That is the single strongest reason not to take the PR whole.
  - It changes `list_dumps` ordering for every caller via a bare `iglob`, undeclared, and
    truncates to 50 entries by default (measured: 60 dumps in, base 60 out, PR 50 out).
  - `docs/reference/` is left out of sync, which CLAUDE.md forbids.
  - It reimplements #127, whose premise is broken, and drops #124's dump cancellation in
    favour of closing the session.
  - Its own native-protocol gate fails on a Western code page.

  Against all that: #131 passes the live suite here (236 tests), its `server_support.py` is
  clean and packages correctly, and on one point it is genuinely better than #126, namely that
  `close_*` stops lying about success. Those are harvested in M1 and M4 rather than merged
  wholesale. So this decision is about reviewable scope and the log-file privacy problem, not
  a claim that #131 is broken throughout.
- **D3** Replace #128 with the four-line in-house fix rather than reworking the PR. Reason:
  the fix is verified at the 156-passed baseline, and #128's value is a diagnosis, not an
  implementation.
- **D4** Close #129 without merging. Reason: the premise is false, measured twice.
- **D5** Do not ship break-in-on-connect in 1.5. Reason: 127-D1 and 127-D2. If a readiness fix
  lands later it should be opt-in, so that connecting never stops someone else's shared target
  silently.
- **D6** For #124, drop the absorb and adopt the cancel-then-close-if-not-resynced hybrid.
  Reason: measured, the absorb does not close its window; the hybrid keeps the session in the
  common case and still guarantees no stale output.
- **D7** Decided by the user (2026-10-08): M1's rollback must **never** resume a live target
  (126-D2). It closes the debugger without sending `g`, leaving a stopped target stopped, and
  resuming stays an explicit choice through `close_kd_session`'s `resume` parameter. Reason: an
  open that never returned an id should leave the world as it found it, and silently resuming a
  kernel target is the more surprising of the two. Consequence for M1: #126's rollback path has
  to be changed, not just taken, since it currently resumes.
- **D8** Release as 1.5.0 using `Release-As: 1.5.0`, and retire #134. Reason: six `fix:`
  commits compute 1.4.1, but the marker protocol, cancellation semantics and session-ownership
  changes are a minor-version story.
- **D9** M0 before any merge. Reason: it is the gate that rejects #128.

## Open questions

1. Answered by the user (2026-10-08): the remote-readiness problem (#127's real subject) goes
   to **1.6**, not 1.5. Testing it needs a live running remote target, which nothing automated
   covers today, so 1.6 should add that scenario alongside an opt-in break-in. Walkthrough
   part D records 1.5's actual behaviour so the 1.6 fix has a baseline.
2. What cap for the bounded reader output in M4? #131 chose 2000 lines / 65536 characters.
   Recommendation: adopt those numbers, since they are already reasoned about, but make the
   truncation notice explicit enough that a model does not reason on half an answer.
3. Should the `plans/` directory be committed, or kept local? Recommendation: commit it, since
   the plan is the working memory for this release.

## User tasks

- **M0**: approve the six pending workflow runs, or change the fork-PR approval policy. Only
  you can do this.
- **D2 and D7**: confirm the two decisions above.
- **M6**: the TESTBOX-1 reboots for the code-page switch and the kernel-debug flags, and the
  current host Default Switch IP for KDNET.
- **Backlog, from memory rather than this review**: robster7674's five large kernel dumps
  (0xD1, 0x139, 0xBE, 0x80 NMI hang, 0xEF) are still to be backtested through `open_kd_dump`
  when their link arrives on #120. Worth doing against the 1.5.0 candidate rather than 1.4.0.
