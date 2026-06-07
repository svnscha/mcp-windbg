# Triage multiple dumps

When you have a folder of dumps, for example from a fleet or a crash-reporting drop, start
by listing them and then analyze them as a batch to find the common thread.

## List what is there

```text
List all crash dumps in C:\dumps
```

This calls [`list_windbg_dumps`](../reference/tools.md#list_windbg_dumps), which finds dump
files and reports their sizes. To include subfolders:

```text
Find all crash dumps under C:\dumps including subdirectories
```

With no directory given, the server falls back to the configured local crash dump location.

## Analyze and compare

Let the model open each dump in turn (the [crash dump flow](crash-dump.md)) and summarize:

```text
Analyze each dump in C:\dumps and group them by faulting module and exception code
```

```text
For every dump under C:\dumps\incident-42, show the top stack frame and tell me
which ones share the same crash signature
```

The model opens each dump with [`open_windbg_dump`](../reference/tools.md#open_windbg_dump),
collects the signal it needs, and compares across them. Ask it to close sessions as it goes
so you do not accumulate `cdb.exe` processes:

```text
Close each dump session after you have analyzed it
```

!!! tip "Keep the batch focused"
    Full `!analyze -v` on every dump in a large folder is slow. Ask for just the crash type
    and top frame first, then deep-dive only the dumps that look interesting.

## Related

- [Analyze a crash dump](crash-dump.md) - the per-dump workflow.
- [Tools reference](../reference/tools.md#list_windbg_dumps) - `list_windbg_dumps` parameters.
