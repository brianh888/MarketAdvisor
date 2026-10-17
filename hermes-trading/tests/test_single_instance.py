"""Single-instance lock: a second copy must refuse to start, and a dead copy must not block.

Uses a test-only lock name, so it is safe to run while the real app is running.
Run:  .venv\\Scripts\\python tests\\test_single_instance.py
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hermes_trading import single_instance as si  # noqa: E402

NAME = "selftest"
HOLD = ("import sys, time; sys.path.insert(0, r'%s'); from hermes_trading import single_instance as si; "
        "si.acquire('%s'); print('holding', flush=True); time.sleep(60)" % (ROOT, NAME))
FAILS = []


def ok(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def second_copy():
    """Start a second copy and return (exited_on_its_own, its output)."""
    p = subprocess.run([sys.executable, "-c", HOLD.replace("time.sleep(60)", "print('started')")],
                       capture_output=True, text=True, timeout=30)
    return p.returncode == 0, p.stdout


ok(not si.is_running(NAME), "nothing holds the lock at the start")
first = subprocess.Popen([sys.executable, "-c", HOLD], stdout=subprocess.PIPE, text=True)
ok(first.stdout.readline().strip() == "holding", "first copy takes the lock")
ok(si.is_running(NAME), "the lock shows as running")
done, out = second_copy()
ok(done and "already running" in out and "started" not in out, "a second copy refuses to start and exits")
first.kill()          # like closing the window, a crash or a PC restart
first.wait()
time.sleep(0.5)
ok(not si.is_running(NAME), "the lock is freed automatically when the first copy dies")
done, out = second_copy()
ok("started" in out, "a fresh copy starts normally afterwards")

print(f"\n{'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
