Investigate a live user-mode target through a `cdb` debugging server: orient, form a hypothesis, test it against the process, and leave the target running.

The target is a **live process**, not a frozen dump. It keeps running unless you break into it, and state changes between commands. Break in before you inspect, and detach cleanly when you are done.

## WORKFLOW - Execute in this sequence:

### Step 1: Connect

**If no connection string provided:**
- Ask the user for it. Supported forms:
  - TCP: `tcp:Port=5005,Server=192.168.0.100`
  - Named pipe: `npipe:Pipe=MyPipe,Server=MyServer`
  - COM: `com:Port=COM1,Baud=115200`

**Tool:** `open_cdb_remote`
- **Parameters:**
  - `connection_string`: the `-remote` connection string

This attaches to an existing debugging *server* (`cdb`/WinDbg started with `-server`), not to a
process by PID. If the user wants a local process, tell them to start a `cdb -server` on it
first. The first output line carries a `session_id` (like `cdb-1a2b3c4d`) - keep it, every
follow-up command needs it.

### Step 2: Break in, if the target is running

Unlike a kernel session, connecting does **not** halt the target. If it is running, pause it
before inspecting state, or you will read values that are already stale.

**Tool:** `send_ctrl_break`
- **Parameters:**
  - `session_id`: the id from Step 1

Skip this if the target is already stopped (at a breakpoint or an exception).

### Step 3: Orient

Establish where the process is and why it stopped, using `run_cdb_command` with the
`session_id`:

- `r` - registers, and the instruction it stopped on
- `k` - the current thread's stack
- `~` - all threads, cheap and shows the shape of the process
- `lm` - loaded modules, when a stack crosses into something unexpected

State whether the process is wedged, crashed, idle, or simply mid-execution. It changes
everything that follows.

### Step 4: Investigate

Follow the evidence rather than running a fixed list. Pick what the situation calls for:

- `~*k` - every thread's stack, the fastest route into a hang
- `!runaway` - CPU time per thread, separates a spin from a block
- `!locks` - critical sections and who holds them
- `!analyze -v` - if the target stopped on an exception
- `!heap -s`, `!address -summary` - memory growth and layout
- `!peb`, `!teb` - process and thread environment
- `!handle 0 f` - handle leaks and what is open

For a hang: `~*k` plus `!runaway` usually separates a deadlock (threads blocked, no CPU) from
a spin (one thread burning CPU). For a crash: start at `!analyze -v` and the faulting stack.

State a hypothesis, then run the command that would **disprove** it. Say what you actually
observed rather than what you expected. Live state moves; if a reading looks inconsistent with
an earlier one, say so rather than smoothing it over.

### Step 5: Close the session

**Tool:** `close_cdb_session`
- **Parameters:**
  - `session_id`: the id from Step 1

This detaches and lets the target continue.

## REQUIRED OUTPUT FORMAT:

```markdown
# Live Target Investigation

**Target:** [Process name and PID, connection string]
**State:** [Hung | Crashed on [exception] | Running normally | Stopped at a breakpoint]

## Summary
[Two or three sentences: what is wrong, or that the process looks healthy.]

## Evidence
| Finding | Command | What it shows |
| --- | --- | --- |
| [Observation] | [`command`] | [Why it matters] |

## Likely cause
[The best-supported explanation, and how confident you are. Name the thread, module, or lock
if the evidence points at one. If several explanations fit, list them in order and say what
would distinguish them.]

## Next steps
1. [The command or check that would confirm or refute the above]
2. [What to collect if it needs escalating: a full dump, symbols, a repro]

**Session closed:** [Yes, target resumed | No, still attached and why]
```

## RULES:

- Break in before reading state on a running target, or the values are stale on arrival.
- Close the session when the investigation ends; do not leave it attached.
- The target is live. Re-read anything you are about to draw a conclusion from, and never
  report a stale value as current.
- Ask the user before anything that changes target state or execution (`g`, `p`, `t`, `bp`,
  `ed`). Inspection is safe; control is not.
- If a command times out, the target may simply be busy. Raise `timeout_seconds` on
  `run_cdb_command` rather than declaring a hang.
