"""Lay the Hermes gateway plugin: manifest + __init__ into ~/.hermes/plugins/tool-router.

Runs at the end of install.py when the hermes harness is wired. Kept as a
function (not inline) so --check and re-runs stay idempotent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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

    note = install_rearm()
    if note:
        print(f"  rearm: {note}")

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


REARM_UNIT = "toolr-rearm.service"
REARM_DROPIN = ("[Unit]\nWants=toolr-rearm.service\n")


def install_rearm(remove: bool = False) -> str:
    """One-shot re-arm wiring: user unit + Wants-drop-in on hermes-gateway.

    Closes the measured gap where Hermes' boot-time plugin activation skips
    gateway-transform hooks (2026-10-08, reproduced twice): after every
    gateway restart the hook is silently dead until something activates the
    plugin post-boot. The drop-in makes every gateway (re)start pull the
    oneshot re-arm automatically. Idempotent; never fails the install.
    """
    try:
        unit_dir = Path("~/.config/systemd/user").expanduser()
        dropin_dir = unit_dir / "hermes-gateway.service.d"
        if remove:
            for p in (unit_dir / REARM_UNIT, dropin_dir / "10-toolr-rearm.conf"):
                p.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=15)
            return "rearm removed"
        src = Path(__file__).resolve().parent
        unit = (src / "systemd" / "toolr-rearm.service").read_text()
        unit = unit.format(python=sys.executable, script=str(src / "rearm_hermes.py"))
        (unit_dir / f"{REARM_UNIT}").write_text(unit)
        dropin_dir.mkdir(parents=True, exist_ok=True)
        conf = dropin_dir / "10-toolr-rearm.conf"
        if not conf.exists() or conf.read_text() != REARM_DROPIN:
            conf.write_text(REARM_DROPIN)
        subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=15)
        return "wired (oneshot + gateway Wants=)"
    except Exception as exc:
        return f"rearm wiring skipped ({str(exc)[:80]})"


# install_rearm is also the public API used by authority.py's self-heal and by
# anyone running: python3 tools/lay_hermes_plugin.py --rearm
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--rearm", action="store_true", help="(re)wire the re-arm unit")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    if a.rearm:
        print(install_rearm(remove=a.remove))
    else:
        print(lay(remove=a.remove))
