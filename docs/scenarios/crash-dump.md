# Analyze a crash dump

You have a `.dmp` file and want to know what went wrong. Point the model at it:

```text
Analyze the crash dump at C:\dumps\app.dmp
```

This calls [`open_windbg_dump`](../reference/tools.md#open_windbg_dump), which opens the dump
in `cdb.exe` and runs the standard triage commands in one pass:

- the crash information (`.lastevent`)
- the automated analysis (`!analyze -v`)
- the stack trace, loaded modules, and threads

The session stays open, so every follow-up question reuses it instead of reloading the dump.

## Ask follow-up questions

Once the dump is open, keep going in plain language. Each request becomes a
[`run_windbg_cmd`](../reference/tools.md#run_windbg_cmd) call against the same session:

```text
Show the call stack with kb and explain the access violation
Run .ecxr then u to disassemble around the faulting instruction
Dump the locals for frame 2
Check the heap around 0x1f2a0040 with !heap -p -a 0x1f2a0040
List the loaded modules with lm and flag anything unsigned
```

You do not have to memorize commands, describe what you want and let the model choose. The
[Tools reference](../reference/tools.md#common-windbg-commands) lists the commands that come
up most often if you want to be specific.

## Symbols make or break this

Readable stack traces need symbols. The Microsoft symbol server is configured through
`_NT_SYMBOL_PATH` in your client config (see [Getting started](../getting-started.md)). For
your own binaries, put the matching `.pdb` files next to the dump, the server automatically
adds the dump's own directory to the symbol path. To add more locations for a single call:

```text
Analyze C:\dumps\app.dmp using symbols from C:\build\symbols
```

That maps to the per-call `symbols_path` parameter, which only applies when the session is
first created. See [`open_windbg_dump`](../reference/tools.md#open_windbg_dump) and
[`--symbols-path`](../reference/cli.md#symbols-and-cdb) for the details.

## Close the session when done

Each open dump holds a `cdb.exe` process. Free it when you finish:

```text
Close the crash dump session for C:\dumps\app.dmp
```

!!! tip "Large dumps and timeouts"
    Heavy commands on big dumps can exceed the default 30-second command timeout. Raise it
    with [`--timeout`](../reference/cli.md#general), for example `--timeout 120`.

## Related

- [Triage multiple dumps](triage.md) - run this flow across a whole folder.
- [Tools reference](../reference/tools.md) - parameters for every tool.
- [Troubleshooting](../troubleshooting.md) - missing symbols, CDB not found, timeouts.
