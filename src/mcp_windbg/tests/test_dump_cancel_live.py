"""A runaway command on a dump session is cancelled, against a real engine.

The hermetic tests cannot cover this: whether CTRL+BREAK actually cancels a
command the engine is executing on a static dump is a property of cdb, not of
our fake. Before the cancel existed for dump sessions the engine kept running
the command, so every later command on that session queued behind it and timed
out as well, and the session was wedged until it was closed.
"""

from __future__ import annotations

import time

import pytest

from e2e import harness
from mcp_windbg.cdb_session import CDBSession
from mcp_windbg.debug_session import DebuggerError

#: An engine-side busy loop. It runs for far longer than the timeout below and
#: needs no symbols, so it is a dependable stand-in for the real case: a
#: ``!process 0 7`` over a large kernel dump.
BUSY_LOOP = ".for (r $t0 = 0; @$t0 < 400000000; r $t0 = @$t0 + 1) { }"

DUMP = "DemoCrash1.exe.7088.dmp"


@pytest.mark.live
def test_a_timed_out_dump_command_is_cancelled_and_the_session_survives():
    if not harness.cdb_available():
        pytest.skip("cdb.exe not found")
    dump = harness.dump_file(DUMP)
    if not dump.exists():
        pytest.skip(f"{DUMP} not present")

    session = CDBSession(dump_path=str(dump))
    try:
        with pytest.raises(DebuggerError) as timeout:
            session.send_command(BUSY_LOOP, timeout=3)

        # Cancelled, not merely abandoned: the session resynced, so it is not
        # reported as closed and stays usable.
        assert "closed" not in str(timeout.value)

        started = time.time()
        output = session.send_command(".lastevent", timeout=20)
        elapsed = time.time() - started

        assert output, "the next command returned nothing"
        # The point of the cancel. Without it this command queues behind the
        # busy loop and times out in its turn.
        assert elapsed < 5, f"the next command took {elapsed:.1f}s, so it queued"
    finally:
        session.shutdown()
