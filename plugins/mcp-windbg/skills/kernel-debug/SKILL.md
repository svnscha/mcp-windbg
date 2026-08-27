---
name: kernel-debug
description: Attach to a live Windows kernel target over KDNET, a named pipe, or serial and drive it. Use when the user wants to debug a kernel, a driver, or a bugchecking VM.
---

# Debug a live Windows kernel target

Drive a kernel target with the `mcp-windbg` kd tools. A kernel session halts the
whole machine while it is broken in, so treat the target's running state as
something you are responsible for.

## Connecting

`open_kd_session` with the `-k` connection string:

- KDNET: `net:port=50000,key=1.2.3.4`
- Named pipe: `com:pipe,port=\.\pipe\com_1,baud=115200,reconnect`
- Serial: `com:port=COM1,baud=115200`

Ask for the string rather than guessing it. The session arrives already broken
in, so you can issue commands immediately.

## Working the target

`run_kd_command` with the `session_id`. Common ground:

- `vertarget`, `!pcr`, `lm m nt` to confirm what you are attached to
- `!process 0 0`, `!thread`, `!irp` for OS state
- `bp <module>!<symbol>` to set a breakpoint
- `!analyze -v` after a bugcheck

## Letting the machine run

This is where a kernel session differs from a dump, and where it is easy to
leave a machine frozen:

- `g` resumes the target and returns immediately. It does not wait, and it does
  not produce output until the target stops again.
- `wait_for_break` blocks until the target stops on its own - a bugcheck, a
  breakpoint, a manual break - and returns what the debugger printed.
- `send_ctrl_break` halts a running target now.
- Any ordinary command issued while the target runs breaks in automatically
  first, and the reason it stopped leads that command's output.

A typical loop: set a breakpoint, `g`, tell the user to trigger the code path,
then `wait_for_break`.

## Finishing

`close_kd_session` with `resume: true` (the default) so the machine runs again.
Only pass `resume: false` when the user explicitly wants it left halted, and say
plainly that the machine will stay frozen until a debugger releases it.

If you set breakpoints, clear them with `bc *` before closing unless the user
wants them kept.
