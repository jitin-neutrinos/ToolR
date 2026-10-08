"""Lay the Hermes gateway plugin: manifest + __init__ into ~/.hermes/plugins/tool-router.

Runs at the end of install.py when the hermes harness is wired. Kept as a
function (not inline) so --check and re-runs stay idempotent.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # ~/Work/tool-router/tools
PAYLOAD = ("plugin.yaml", "__init__.py")
DEST = Path("~/.hermes/plugins/tool-router").expanduser()
ENABLE_FLAG = "tool-router"


def _config_plugins_enabled() -> list:
    """Current plugins.enabled list from config.yaml ([] when absent)."""
    try:
        import yaml
        cfg = yaml.safe_load(Path("~/.hermes/config.yaml").expanduser().read_text()) or {}
        v = cfg.get("plugins") or {}
        return list(v.get("enabled") or [])
    except Exception:
        return []


def lay(remove: bool = False) -> str:
    if remove:
        if DEST.exists():
            shutil.rmtree(DEST)
            return f"removed {DEST}"
        return "nothing to remove"

    src = HERE / "hermes-plugin"
    missing = [f for f in PAYLOAD if not (src / f).exists()]
    if missing:
        return f"payload incomplete, missing: {', '.join(missing)}"

    DEST.mkdir(parents=True, exist_ok=True)
    for f in PAYLOAD:
        shutil.copy2(src / f, DEST / f)

    # Enable in config.yaml ONLY when the key is absent — never force-flip a
    # deliberate user disable.
    enabled = _config_plugins_enabled()
    if ENABLE_FLAG not in enabled:
        sys.path.insert(0, str(Path("~/.hermes/hermes-agent").expanduser()))
        try:
            from hermes_cli.config import atomic_config_write
            data = {"plugins": {"enabled": enabled + [ENABLE_FLAG]}}
            atomic_config_write(Path("~/.hermes/config.yaml").expanduser(), data)
            return f"laid plugin + enabled ({DEST})"
        except Exception as e:
            return f"laid plugin but enable failed ({e}); add 'tool-router' to plugins.enabled"
    return f"laid plugin ({DEST}); already enabled"
