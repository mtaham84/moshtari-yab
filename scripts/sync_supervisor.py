#!/usr/bin/env python3
"""
Sync process supervisor for Moshtari-Yab (P09.05 - R24, R34).
Supervises sync_opportunities and sync_x_status concurrently in a single container
without silent background '&' jobs, zombies, or masked crashes.
"""
import os
import signal
import subprocess
import sys
import time

CHILD_PROCS = []


def forward_signal(signum, frame):
    print(f"[supervisor] Received signal {signum}, terminating managed workers...", flush=True)
    for p in CHILD_PROCS:
        if p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
    time.sleep(1)
    for p in CHILD_PROCS:
        if p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    x_interval = os.environ.get("X_STATUS_SYNC_INTERVAL", "30")
    sync_interval = os.environ.get("SYNC_INTERVAL", "10")

    cmd_x = [sys.executable, "manage.py", "sync_x_status", "--follow", "--interval", x_interval]
    cmd_opps = [sys.executable, "manage.py", "sync_opportunities", "--follow", "--interval", sync_interval]

    print("[supervisor] Starting supervised sync workers...", flush=True)
    p_x = subprocess.Popen(cmd_x)
    p_opps = subprocess.Popen(cmd_opps)
    CHILD_PROCS.extend([p_x, p_opps])

    while True:
        code_x = p_x.poll()
        code_opps = p_opps.poll()

        if code_x is not None:
            print(f"[supervisor] FATAL: sync_x_status exited with code {code_x}. Shutting down remaining workers.", flush=True)
            if p_opps.poll() is None:
                p_opps.terminate()
            sys.exit(code_x or 1)

        if code_opps is not None:
            print(f"[supervisor] FATAL: sync_opportunities exited with code {code_opps}. Shutting down remaining workers.", flush=True)
            if p_x.poll() is None:
                p_x.terminate()
            sys.exit(code_opps or 1)

        time.sleep(1)


if __name__ == "__main__":
    main()
