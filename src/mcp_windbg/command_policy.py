"""Restricted command mode (``--restrict-commands``).

Everything a debugger target prints - strings in a dump, module names, a remote
target's output - reaches the model as tool output, and the model chooses the
next command. ``run_cdb_command`` / ``run_kd_command`` otherwise accept any
command, and the debugger command language reaches well past the target:
``.shell`` runs a process on the debugging host, ``.load`` loads a DLL into it,
``.writemem`` writes files, ``.server`` opens a listener. Text planted in a dump
can therefore try to talk the model into running code on the analyst's machine.

Restricted mode refuses those commands, and refuses network locations in the
paths a caller passes, so a planted path cannot make the debugger connect out
(and send the user's Windows credentials) to an attacker's SMB share or symbol
server.

This is defense in depth, not a sandbox. The debugger command language is large
and the checks are a deny-list over the command text. They deliberately err on
the side of refusing: a blocked token is refused wherever it appears, including
inside quotes and breakpoint command strings, and the features that could build
a blocked command out of harmless pieces (aliases, ``.foreach``, the ``dx``
command-execution and file-system objects) are refused as a whole. Commands the
server operator configures (``--init-command``) are trusted and never checked.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = [
    "CommandPolicyError",
    "check_command",
    "check_connection_string",
    "check_local_path",
    "check_symbols_path",
]


class CommandPolicyError(ValueError):
    """A command or path was refused by restricted command mode."""


# A token "starts" a command at the start of the text, after whitespace, after a
# command separator, or inside the brackets and quotes that nest commands
# (``.if (...) { ... }``, ``bp addr "..."``, ``~*e ...``).
_START = r"(?:^|(?<=[\s;{}()\"'`]))"

# Meta-commands refused outright: they run processes or open listeners on the
# debugging host, load code into the debugger, write files, open other targets,
# or evaluate text the caller can assemble at run time.
_BLOCKED_META = (
    "shell",           # run a process on the host
    "create",          # start a process
    "createdir",       # set the start directory for .create
    "attach",          # attach to a local process
    "dbgdbg",          # start a second debugger on this one
    "server",          # open a debugging server (a listener)
    "remote",          # start a remote.exe server
    "netuse",          # map network connections
    "load",            # load an extension DLL
    "loadby",          # load an extension DLL next to a module
    "setdll",          # set the default extension DLL
    "scriptload",      # load a JavaScript/NatVis script
    "scriptrun",       # load and run a script
    "opendump",        # open another target
    "ocommand",        # execute target output that starts with a prefix
    "writemem",        # write memory to a file
    "dump",            # write a dump file
    "dumpcab",         # write a CAB file
    "logopen",         # open a log file at a chosen path
    "logappend",       # append to a log file at a chosen path
    "write_cmd_hist",  # write the command history to a file
    "kdfiles",         # replace driver files on a kernel target
    "send_file",       # copy files to a remote target
    "foreach",         # run commands built from output tokens
    "readmem",         # read a host file (or a share) into target memory
    "cmdtree",         # open a command-tree file at a chosen path
    "nvload",          # load a NatVis file from a chosen path
    "dml_start",       # open a DML file at a chosen path
    "settings",        # load, save or set debugger settings, symbol path included
)

# Path-setting meta-commands: refused only with an argument, so displaying the
# current value (a bare ``.sympath``) still works. An argument can point at a
# network share or a server the debugger then connects to.
_PATH_META = (
    "sympath", "symfix", "srcpath", "srcfix", "lsrcpath", "lsrcfix",
    "exepath", "extpath",
)

_RULES = (
    (
        re.compile(_START + r"\.(?:" + "|".join(_BLOCKED_META) + r")(?![\w])", re.IGNORECASE),
        "it can run code, open a listener, or write files on the debugging host",
    ),
    (
        # Any argument at all, even one with no space before it (.sympath+\\host).
        re.compile(_START + r"\.(?:" + "|".join(_PATH_META) + r")(?![\w])\+?[ \t]*[^\s;+]",
                   re.IGNORECASE),
        "setting a search path can make the debugger connect to a network location",
    ),
    (
        # .cordll -lp <path> loads the CLR debugging DLL from a chosen directory.
        re.compile(_START + r"\.cordll\b[^;\n]*-lp\b", re.IGNORECASE),
        "loading a CLR debugging DLL from a path loads code into the debugger",
    ),
    (
        # $<, $><, $$<, $$><, $$>a< run a script file of commands.
        re.compile(r"\$\$?>?a?<", re.IGNORECASE),
        "it runs debugger commands from a script file",
    ),
    (
        # lsf / lsf- load a source file from a chosen path.
        re.compile(_START + r"lsf-?(?![\w])", re.IGNORECASE),
        "it opens a file at a chosen path on the debugging host",
    ),
    (
        # !C:\path\ext.cmd or !\\server\share\ext.cmd loads an extension by path.
        re.compile(_START + r"![^\s;]*[\\/:]"),
        "it loads a debugger extension from a path",
    ),
    (
        # as / aS define aliases, which are expanded into later commands. Matched
        # wherever a command can start, including after ~e and !for_each_* prefixes.
        re.compile(_START + r"as\s", re.IGNORECASE),
        "aliases can assemble a blocked command at run time",
    ),
    (
        # $u0 to $u9 are fixed-name aliases, set with r $.u0 = ...
        re.compile(r"\$\.?u\d", re.IGNORECASE),
        "aliases can assemble a blocked command at run time",
    ),
    (
        # dx Debugger.Utility.Control.ExecuteCommand("...") runs a command built
        # from a string expression; Debugger.Utility.FileSystem writes files. The
        # names are refused on their own, since dx can hold Debugger.Utility in a
        # variable and reach FileSystem through it.
        re.compile(r"ExecuteCommand|FileSystem|\bUtility\b", re.IGNORECASE),
        "the dx command-execution and file-system objects are not allowed",
    ),
)


def check_command(command: str) -> None:
    """Raise :class:`CommandPolicyError` if restricted mode refuses *command*."""
    for pattern, reason in _RULES:
        match = pattern.search(command)
        if match:
            token = match.group(0).strip()
            raise CommandPolicyError(
                f"Command refused by --restrict-commands: {token!r} is not allowed "
                f"because {reason}. Ask the user to run it themselves, or to add it "
                f"to the server's --init-command list if it is needed for every session."
            )


_NETWORK_PATH = re.compile(
    r"^(?:[\\/]{2}(?![?.][\\/][A-Za-z]:)"     # \\server\share, \\?\UNC\..., //server/share
    r"|[\\/]\?\?[\\/](?![A-Za-z]:)"           # \??\UNC\server\share (NT namespace)
    r"|[A-Za-z][\w+.-]*://)"                  # URLs
)


def _is_network_location(path: str) -> bool:
    """True for a UNC path (``\\\\server\\share``) or a URL; False for a local path.

    A ``\\\\?\\C:\\...`` long path, ``\\\\.\\C:\\...`` or ``\\??\\C:\\...`` names a local drive
    and is allowed.
    """
    return bool(_NETWORK_PATH.match(path.strip().strip('"')))


def check_local_path(path: Optional[str], what: str) -> None:
    """Refuse a network location for a path the caller passed (dump or directory)."""
    if path and _is_network_location(path):
        raise CommandPolicyError(
            f"{what} {path!r} refused by --restrict-commands: network paths are not "
            f"allowed, because opening one sends the user's Windows credentials to that "
            f"server. Copy the file to a local folder first."
        )


def check_symbols_path(symbols_path: Optional[str]) -> None:
    """Refuse network locations in a per-call symbol path.

    A symbol path is ``;``-separated, and each ``srv*`` / ``cache*`` element is
    ``*``-separated, so every piece is checked. The server's own symbol path
    (``--symbols-path``, ``_NT_SYMBOL_PATH``) is operator configuration and is
    not checked.
    """
    if not symbols_path:
        return
    for element in re.split(r"[;*]", symbols_path):
        if _is_network_location(element):
            raise CommandPolicyError(
                f"symbols_path element {element.strip()!r} refused by "
                f"--restrict-commands: per-call symbol paths must be local folders. "
                f"Configure symbol servers with the server's --symbols-path or "
                f"_NT_SYMBOL_PATH instead."
            )


_LOCAL_HOSTS = {".", "localhost", "127.0.0.1", "::1", "[::1]"}
_UNC_HOST = re.compile(r"[\\/]{2}([^\\/,]+)")
_PIPE_SERVER = re.compile(r"^\s*[ns]pipe:.*?\bserver=([^,\s]+)", re.IGNORECASE)


def check_connection_string(connection_string: Optional[str]) -> None:
    """Refuse a connection string that opens a named pipe on another machine.

    Named pipes travel over SMB, which authenticates with the user's Windows
    credentials: ``npipe:server=host,...`` / ``spipe:...`` for a remote cdb, and
    ``com:pipe,port=\\\\host\\pipe\\...`` for kd. A ``tcp:``/``ssl:`` remote and a
    KDNET ``net:`` target send no credentials and are left alone, as are pipes
    on this machine (``\\\\.\\pipe\\...``, ``server=localhost``).
    """
    if not connection_string:
        return
    hosts = [m.group(1) for m in _UNC_HOST.finditer(connection_string)]
    if m := _PIPE_SERVER.match(connection_string):
        hosts.append(m.group(1))
    for host in hosts:
        if host.lower() not in _LOCAL_HOSTS:
            raise CommandPolicyError(
                f"connection_string {connection_string!r} refused by --restrict-commands: "
                f"it opens a named pipe on {host!r}, and a pipe on another machine "
                f"authenticates with the user's Windows credentials. Use a tcp: connection, "
                f"or ask the user to open this session without --restrict-commands."
            )
