#!/usr/bin/env python3
"""rearm_hermes.py — one-shot: re-arm ToolR's hook inside a running hermes-gateway.

Why this exists (measured 2026-10-08, twice): Hermes' boot-time plugin
activation does not register gateway-transform hooks (pre_gateway_dispatch),
while the post-boot activation path does. So after every gateway restart the
router hook on Hermes is silently dead — plugin files intact, mandate intact,
every stage fails open, no error anywhere. This unit re-applies the activation
once the gateway is up, closing that gap automatically.

Wiring (lay_hermes_plugin.install_rearm):
  - a user unit `toolr-rearm.service` (oneshot)
  - a drop-in on hermes-gateway.service adding `Wants=toolr-rearm.service`,
    so every gateway (re)start pulls this unit after it.

Contract: NEVER fail loudly. If Hermes isn't installed, exit 0. If the gateway
isn't up yet, retry with backoff. If activation ultimately fails, log one line
and exit 0 — the mandate + enforcement still work; only the auto-card waits
for the next gateway start.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("TOOLR_HOME", Path.home() / ".tool-router"))
HERMES_VENV = Path(os.environ.get(
    "TOOLR_HERMES_VENV", Path.home() / ".hermes/hermes-agent/venv/bin/python"))
LOG = HOME / "rearm.log"

_ACTIVATE = """
import sys, json
sys.path.insert(0, {hermes_root!r})
from hermes_cli.plugins_activation import activate_plugin_now
r = activate_plugin_now("tool-router")
a = (r.get("activation") or {{}}).get("activated_now") or {{}}
print(json.dumps({{"transforms": a.get("gateway_transforms") or []}}))
"""


def _log(msg: str) -> None:
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _hermes_root() -> Path:
    return HERMES_VENV.parent.parent


def _activate_once() -> bool:
    """One activation attempt in the Hermes venv interpreter. True when the
    pre_gateway_dispatch transform came back registered."""
    if not HERMES_VENV.is_file():
        return False
    try:
        code = _ACTIVATE.format(hermes_root=str(_hermes_root()))
        r = subprocess.run([str(HERMES_VENV), "-c", code], timeout=60,
                           capture_output=True, text=True)
        for line in reversed((r.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                transforms = json.loads(line).get("transforms") or []
                return "pre_gateway_dispatch" in transforms
        return False
    except Exception as exc:
        _log(f"attempt error: {type(exc).__name__}: {str(exc)[:80]}")
        return False


def main() -> int:
    if not HERMES_VENV.is_file():
        return 0  # no Hermes install on this machine — public no-op
    for attempt in range(6):                       # ~75 s worst case
        if _activate_once():
            _log(f"re-armed (attempt {attempt + 1})")
            return 0
        time.sleep(15 if attempt < 3 else 5)
    _log("gave up after retries — hook NOT armed; mandate/enforce unaffected")
    return 0                                       # fail-open, always


if __name__ == "__main__":
    sys.exit(main())
