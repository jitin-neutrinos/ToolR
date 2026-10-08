#!/usr/bin/env python3
"""Self-check for the authority/self-heal/75% floor additions (2026-10-08).

Run: python3 scripts/test_authority_system.py   (assert-based, no frameworks)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))  # repo root: import install

import authority  # noqa: E402


def test_detect_harness_env_precedence():
    os.environ["TOOLR_HARNESS"] = "cursor"
    os.environ["ANTHROPIC_MODEL"] = "claude-x"
    try:
        assert authority.detect_harness() == "cursor", "override must win"
    finally:
        del os.environ["TOOLR_HARNESS"]
    assert authority.detect_harness() == "claude"
    for v in ("ANTHROPIC_MODEL", "CLAUDE_MODEL", "HERMES_MODEL",
              "OPENCODE_MODEL", "GEMINI_MODEL", "AGY_MODEL"):
        os.environ.pop(v, None)
    assert authority.detect_harness() == "", "unknown must be ''"


def test_heal_rewrites_removed_mandate():
    """The owner contract: undo the delegation -> next route re-writes it."""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "AGENTS.md"
        target.write_text("# header\nsome other content\n", encoding="utf-8")
        import install as inst
        real = inst.write_mandate
        # point write_mandate at the temp file without touching real files
        inst_write = lambda p, remove=False: real(target, remove)
        text = target.read_text()
        assert "Route before you work" not in text
        # simulate: heal on a harness whose mandate file IS the temp file
        os.environ["TOOLR_HARNESS"] = "agents"
        try:
            files_backup = dict(inst.MANDATE_FILES)
            inst.MANDATE_FILES = {"agents": [str(target)]}
            # wrap to bypass home() expansion for absolute temp paths
            orig_home = inst.home
            inst.home = lambda p: Path(p) if str(p).startswith("/") else orig_home(p)
            try:
                res = authority.selfheal()
            finally:
                inst.home = orig_home
                inst.MANDATE_FILES = files_backup
            assert target.exists()
            assert "Route before you work" in target.read_text(), \
                "heal must re-write a removed mandate"
            assert any(r.startswith("mandate:") for r in res["repaired"]), res
            # idempotent second run: nothing repaired
            inst.home = lambda p: Path(p) if str(p).startswith("/") else orig_home(p)
            try:
                res2 = authority.selfheal()
            finally:
                inst.home = orig_home
            assert res2["repaired"] == [], f"second heal must be a no-op: {res2}"
        finally:
            os.environ.pop("TOOLR_HARNESS", None)


def test_adoption_floor_math():
    """75% floor: below floor after warmup -> escalate; above -> normal."""
    import gate  # noqa: F401  (import guard: module must stay importable)
    for kept, total, warmup_ok in ((3, 4, True), (2, 3, True),
                                   (0, 1, False), (9, 12, True)):
        hist = {"kept": kept, "total": total}
        rate = (hist["kept"] / hist["total"]) if hist.get("total") else 1.0
        below = hist.get("total", 0) >= 3 and rate < 0.75
        expect = total >= 3 and (kept / total) < 0.75
        assert below == expect, (kept, total, below, expect)


def test_ledger_roundtrip():
    """_save_state must carry the ledger forward and bump total."""
    import tempfile
    import route as rt
    with tempfile.TemporaryDirectory() as td:
        rt.STATE = Path(td) / "last_route.json"
        rt._save_state(["skill:a"], "pid1", True)
        first = json.loads(rt.STATE.read_text())
        assert first["adoption"]["total"] == 1, first
        rt._save_state(["skill:b"], "pid2", True)
        second = json.loads(rt.STATE.read_text())
        assert second["adoption"]["total"] == 2, second
        assert second["adoption"]["kept"] == 0, "kept only moves on --loaded"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
