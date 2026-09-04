"""Hermetic tests for the Unicode-log content extraction.

The full log transport (opening ``.logopen /u`` and reading complete UTF-16 for
every command on a multibyte code page) is exercised end to end against a real
cdb on the VM noted in the pull request; CI cannot reach that. What is unit-
tested here is the pure slice-to-output-lines logic, which is where the parsing
risk lives.
"""

from __future__ import annotations

from mcp_windbg import debug_session
from mcp_windbg.debug_session import _extract_log_output


def test_drops_the_command_echo_and_keeps_the_output():
    segment = "0:000> du @rsp L20\n00000008`7fafe858  \"中文\"\n"
    assert _extract_log_output(segment) == ['00000008`7fafe858  "中文"']


def test_a_command_that_prints_nothing_yields_no_lines():
    # ew echoes the command and prints nothing; the pipe path returns [] too.
    assert _extract_log_output("0:000> ew @rsp 4e2d 0\n") == []


def test_multiple_output_lines_are_kept_in_order():
    segment = (
        "0:000> dw @rsp L8\n"
        "00000008`7fafe858  4e2d 6587 0000 0000 0000 0000 0000 0000\n"
        "00000008`7fafe868  0000 0000\n"
    )
    assert _extract_log_output(segment) == [
        "00000008`7fafe858  4e2d 6587 0000 0000 0000 0000 0000 0000",
        "00000008`7fafe868  0000 0000",
    ]


def test_kernel_and_wow64_prompts_are_recognised_as_scaffolding():
    segment = "1:001:x86> r eax\neax=00000001\n0: kd> \n"
    assert _extract_log_output(segment) == ["eax=00000001"]


def test_local_kernel_prompt_is_recognised_as_scaffolding():
    # Local kernel debugging (kd -kl) prompts with "lkd>", not "N: kd>".
    segment = "lkd> lm m nt\nfffff803`ec600000 nt\nlkd> \n"
    assert _extract_log_output(segment) == ["fffff803`ec600000 nt"]


def test_a_remote_servers_bracketed_prompt_is_recognised_as_scaffolding():
    # A user-mode -remote client prompt carries a [server (tcp ...)] banner.
    segment = (
        "[BOX\\user (tcp [::1]:5005)] 0:000> du @rsp L20\n"
        "0000004a`5ccff240  \"中文\"\n"
    )
    assert _extract_log_output(segment) == ['0000004a`5ccff240  "中文"']


def test_output_lines_are_never_mistaken_for_prompts():
    # A real output line does not start with the N:...> prompt shape.
    segment = "0:000> lm\nstart    end    module\n00400000 0041f000 app\n"
    assert _extract_log_output(segment) == [
        "start    end    module",
        "00400000 0041f000 app",
    ]


def test_acp_gate_returns_a_bool():
    assert isinstance(debug_session._acp_is_multibyte(), bool)
