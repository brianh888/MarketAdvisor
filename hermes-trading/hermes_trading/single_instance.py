"""One copy of each app part at a time: worker, dashboard, Discord bot.

Each part holds an OS file lock (state/locks/<name>.lock) for as long as its process
lives. The OS releases the lock automatically when the process ends — even on a crash,
a closed window or a PC restart — so a stale lock can never block a fresh start.

A second copy finds the lock taken and exits straight away. Duplicates used to trade the
same paper book independently (see docs/strategy-srsr.md, Sept 2026 infrastructure note).

start.bat asks before launching each part:
    python -m hermes_trading.single_instance running worker     (exit 0 = running, 1 = not)
"""
from __future__ import annotations

import os
import sys
from typing import IO

from .paths import STATE_DIR

LOCK_DIR = STATE_DIR / "locks"
_held: dict[str, IO[bytes]] = {}   # keep the open handles alive for the life of the process


def _try_lock(name: str) -> IO[bytes] | None:
    """Take the lock without waiting; the open handle on success, None if another process has it."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_DIR / f"{name}.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def acquire(name: str, label: str | None = None) -> None:
    """Hold the lock for `name` until this process exits; if another copy holds it, exit now."""
    if name in _held:
        return
    f = _try_lock(name)
    if f is None:
        print(f"{label or name} is already running - not starting a second copy.", flush=True)
        sys.exit(0)
    _held[name] = f
    (LOCK_DIR / f"{name}.pid").write_text(str(os.getpid()), encoding="ascii")


def is_running(name: str) -> bool:
    """True if some process currently holds the lock for `name`."""
    if name in _held:
        return True
    f = _try_lock(name)
    if f is None:
        return True
    f.close()   # closing releases the probe lock
    return False


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "running":
        return 0 if is_running(argv[1]) else 1
    print("usage: python -m hermes_trading.single_instance running <worker|dashboard|discord_bot>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
