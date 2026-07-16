Investigate a live kernel target over a `-k` connection: orient, form a hypothesis, test it against the machine, and always leave the target in a known state.

A kernel target is a whole machine, not a process, and it is **halted while you hold it**. Nothing on that machine runs until you release it. Work efficiently and release it when you are done.

## WORKFLOW - Execute in this sequence:

### Step 1: Attach

**If no connection string provided:**
- Ask the user for it. Supported forms:
  - KDNET (network): `net:port=50000,key=1.2.3.4`
  - Named pipe (VM): `com:pipe,port=\\.\pipe\com_1,baud=115200,reconnect,resets=0`
  - Serial: `com:port=COM1,baud=115200`

**Tool:** `open_kd_session`
- **Parameters:**
  - `connection_string`: the `-k` connection string

The server waits for the target to connect, breaks in for you, and returns a session already
stopped at a prompt. Do **not** send CTRL+BREAK after opening; the target is already halted.
The first output line carries a `session_id` (like `kd-1a2b3c4d`) - keep it, every follow-up
command needs it.

If this times out mentioning `no_debuggee`, the target is not transmitting: it is not booted
with debugging enabled, or another debugger already holds the connection (KDNET is
point-to-point). That is an environment problem, not a tool failure. Report it and stop rather
than retrying.

### Step 2: Orient

Establish what machine this is and why it stopped, before going deeper. Use `run_kd_command`
with the `session_id` from Step 1:

- `vertarget` - OS build, platform, and how long it has been up
- `!analyze -v` - **if the machine bugchecked**, this identifies the stop code and culprit.
  On a healthy machine you broke into, it reports the break itself; that is expected and not a
  finding.
- `lm` - loaded drivers, and the first place an unexpected or unsigned module shows up

State plainly whether the target is bugchecked or simply halted. It changes everything that
follows.

### Step 3: Investigate

Follow the evidence rather than running a fixed list. Pick what the situation calls for:

- `!process 0 0` - all processes; add a specific address for detail
- `!thread` - the current thread, or an address for a specific one
- `!running` - what every processor was doing (essential for hangs and deadlocks)
- `k` / `kb` - the stack at the break or fault
- `!irql`, `!locks`, `!deadlock` - contention and lock-order problems
- `!devnode 0 1`, `!drvobj <driver>` - device and driver state
- `!pool <address>`, `!poolused` - pool corruption and leaks

State a hypothesis, then run the command that would **disprove** it. Say what you actually
observed rather than what you expected. If the evidence does not support a conclusion, say
that instead of guessing.

### Step 4: Release the target

**This step is not optional.** A kernel target left at a break freezes the entire machine.

**Tool:** `close_kd_session`
- **Parameters:**
  - `session_id`: the id from Step 1
  - `resume`: `true` (the default) so the machine runs again

Only pass `resume: false` if the user explicitly wants the machine left halted for another
debugger to pick up.

## REQUIRED OUTPUT FORMAT:

```markdown
# Kernel Investigation

**Target:** [OS build, platform, connection string]
**State:** [Bugcheck 0xNN (NAME) | Halted at a break, no bugcheck]

## Summary
[Two or three sentences: what is wrong, or that the machine is healthy.]

## Evidence
| Finding | Command | What it shows |
| --- | --- | --- |
| [Observation] | [`command`] | [Why it matters] |

## Likely cause
[The best-supported explanation, and how confident you are. Name the driver or subsystem if
the evidence points at one. If several explanations fit, list them in order and say what would
distinguish them.]

## Next steps
1. [The command or check that would confirm or refute the above]
2. [What to collect if it needs escalating: a full dump, driver verifier, etc.]

**Target released:** [Yes, resumed | No, left halted at user request]
```

## RULES:

- Never leave a session open at the end of an investigation. Release the target.
- Do not send CTRL+BREAK right after opening. `open_kd_session` already broke in. Use
  `send_ctrl_break` only to re-halt a target you deliberately let run with `g`.
- Kernel commands can be slow on a busy machine or over a serial cable. Raise
  `timeout_seconds` on `run_kd_command` for heavy extensions (`!process 0 7`, `!poolused`)
  rather than declaring a hang.
- Distinguish "the machine bugchecked" from "I broke into a healthy machine". Do not report a
  routine break-in as a crash.
- Ask the user before running anything that changes target state (`ed`, `!process ... /r`, or
  anything that resumes execution mid-investigation).
