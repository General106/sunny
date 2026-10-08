"""Battery mode: on battery, keep the NVIDIA GPU free so it can power down.

Speech recognition holds a CUDA context, which stops the GPU sleeping. A CUDA context can only
be fully released by ending the process, so when the power source changes Sunny exits with
RESTART_CODE and systemd starts him again in the right mode (GPU when plugged in, CPU on battery).
"""
import glob
import os
import sys
import threading
import time

import config

RESTART_CODE = 75
FLAG = config.DATA / "power_switch"


def on_ac():
    """True when plugged in (or if there's no battery info at all)."""
    mains = [p for p in glob.glob("/sys/class/power_supply/*")
             if open(f"{p}/type").read().strip() == "Mains"]
    if not mains:
        return True
    return any(open(f"{p}/online").read().strip() == "1" for p in mains)


def watch(started_on_ac):
    """Restart Sunny in the other mode when the charger is plugged in or pulled out."""
    def loop():
        while True:
            time.sleep(10)
            if on_ac() != started_on_ac:
                time.sleep(5)                      # ignore a quick wiggle of the cable
                if on_ac() != started_on_ac:
                    FLAG.write_text("ac" if on_ac() else "battery")
                    print(f"[power] switched to {'charger' if on_ac() else 'battery'} - restarting")
                    sys.stdout.flush()
                    os._exit(RESTART_CODE)
    threading.Thread(target=loop, daemon=True).start()


def switched_mode():
    """'ac' / 'battery' if this start was caused by a power switch (consumes the flag)."""
    if FLAG.exists():
        mode = FLAG.read_text().strip()
        FLAG.unlink()
        return mode
    return None
