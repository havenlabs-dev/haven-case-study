# Excerpt from HAVEN (private codebase): apps/local/compute_lease.py
# Published as written, except the lock path default and a few docstring lines
# trimmed. Logic is unchanged.
"""One heavy local-compute job at a time: the thermal rule for a 15 W part.

HAVEN runs its local model tier (speech-to-text, embeddings, a small LLM) on a
12-thread laptop CPU with no discrete GPU. Two heavy jobs at once (an LLM
decode and an STT batch, or two STT batches) halve each other and push the
package into thermal throttling. So every heavy local job takes this lease
first:

    with compute_lease("stt:meeting 12"):
        ...

It is a plain flock on one file. A second caller waits (default up to 30 min),
which is the right behavior for a night-window batch and wrong for an
interactive path: interactive callers pass wait_s=0 and fall back to the cloud
leg when they don't get it. A system pause (critical temperature) is honored
the same way: a paused box refuses the lease.
"""
from __future__ import annotations

import fcntl
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)
LOCK_PATH = Path(os.environ.get("HAVEN_COMPUTE_LOCK", "/var/tmp/haven-local-compute.lock"))
LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)


class ComputeBusy(RuntimeError):
    pass


def _paused() -> str | None:
    try:
        from apps.core import system_pause
        st = system_pause.state()
        if st.get("paused"):
            return st.get("reason") or "system paused"
    except Exception:
        logger.warning("compute lease: system pause state unreadable, proceeding as unpaused", exc_info=True)
    return None


@contextmanager
def compute_lease(label: str, wait_s: float = 1800.0):
    """Hold the one local-compute slot. Raises ComputeBusy when it cannot."""
    why = _paused()
    if why:
        raise ComputeBusy(f"local compute paused: {why}")
    fh = open(LOCK_PATH, "a+")
    t0 = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - t0 >= wait_s:
                    fh.close()
                    holder = ""
                    try:
                        fh2 = open(LOCK_PATH)
                        holder = fh2.read().strip()
                        fh2.close()
                    except Exception:
                        pass
                    raise ComputeBusy(f"local compute busy ({holder or 'unknown'})")
                time.sleep(2.0)
        try:
            fh.seek(0); fh.truncate()
            fh.write(f"{label} pid={os.getpid()} since={time.strftime('%H:%M:%S')}")
            fh.flush()
        except Exception:
            pass
        logger.info("compute lease: %s", label)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


def holder() -> str | None:
    """Who holds the lease right now, or None."""
    try:
        with open(LOCK_PATH, "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
                return None
            except BlockingIOError:
                fh.seek(0)
                return fh.read().strip() or "unknown"
    except Exception:
        return None
