#!/usr/bin/env python3
"""Runnable check for the router: `python3 selftest.py`. Exits non-zero on failure."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router_core as rc  # noqa: E402


def t_frontmatter():
    plain = "---\nname: tdd\ndescription: Write a failing test first\n---\nbody"
    assert rc.parse_frontmatter(plain)["name"] == "tdd"
    assert "failing test" in rc.parse_frontmatter(plain)["description"]

    folded = (
        "---\nname: rag-perf\nversion: \"2.6.0\"\ndescription: >-\n"
        "  Performance benchmarking for a server:\n  profiling pass plus load test.\n"
        "tags: [perf, rag]\n---\nbody"
    )
    fm = rc.parse_frontmatter(folded)
    assert fm["name"] == "rag-perf", fm
    assert fm["version"] == "2.6.0", fm
    assert fm["description"].startswith("Performance benchmarking"), fm
    assert "load test" in fm["description"], fm

    assert rc.parse_frontmatter("no frontmatter here") == {}
    # malformed values must not raise; unterminated frontmatter yields nothing
    assert rc.parse_frontmatter("---\nbroken: [\n---\n") == {"broken": "["}
    assert rc.parse_frontmatter("---\nname: x\n") == {}


def t_tokenize():
    toks = rc.tokenize("Fix the FAILING Docker-compose tests")
    assert "the" not in toks and "fix" in toks
    assert "docker" in toks and "compose" in toks, toks
    # stemming collapses plural/gerund so "test"/"tests"/"testing" match
    assert rc.tokenize("tests") == rc.tokenize("test") == rc.tokenize("testing"), (
        rc.tokenize("tests"), rc.tokenize("testing"))


def t_aliases_and_db_gate():
    """Spelling variants must unify, and storage engines must not cross-match."""
    assert rc.tokenize("postgres") == rc.tokenize("PostgreSQL") == ["postgres"], (
        rc.tokenize("postgres"), rc.tokenize("PostgreSQL"))
    assert rc.tokenize("k8s") == rc.tokenize("kubernetes")
    idx = _index([
        ("mongodb-query-optimizer", "optimize slow MongoDB queries, aggregation, indexes"),
        ("postgres-code-review", "review PostgreSQL schemas, slow queries, indexes, plans"),
    ])
    top = rc.score(idx, "the postgres query behind the dashboard is slow")[0]
    assert top["name"] == "postgres-code-review", top
    top = rc.score(idx, "this mongo aggregation is slow")[0]
    assert top["name"] == "mongodb-query-optimizer", top


def t_toml():
    text = "[mcp_servers.context7]\ncommand = \"npx\"\n\n[mcp_servers.github]\nurl = \"x\"\n"
    assert rc._toml_tables(text, "mcp_servers") == ["context7", "github"]


def _index(pairs):
    items = [{"kind": "skill", "name": n, "desc": d, "extra": "", "path": "", "harness": "x",
              "scope": "user"} for n, d in pairs]
    for it in items:
        it["tokens"] = rc.tokenize(it["name"] + " " + it["desc"])
        it["name_tokens"] = rc.tokenize(it["name"])
    return {"version": rc.INDEX_VERSION, "items": items, "stats": {"skill": len(items)}}


def t_ranking():
    idx = _index([
        ("docker-best-practices", "Dockerfile layering, multi-stage builds, image size"),
        ("python-testing-patterns", "pytest fixtures, parametrize, mocking, flaky tests"),
        ("postgres-code-review", "review SQL schemas, indexes, query plans"),
        ("svg-animation", "animate SVG paths, morphing, stroke-dashoffset"),
    ])
    top = rc.score(idx, "our pytest suite has a flaky mock, fix it")[0]
    assert top["name"] == "python-testing-patterns", top
    top = rc.score(idx, "shrink our Dockerfile image size")[0]
    assert top["name"] == "docker-best-practices", top
    # explicit name mention wins even with weak description overlap
    top = rc.score(idx, "use svg-animation for this")[0]
    assert top["name"] == "svg-animation", top
    # unrelated chatter must not produce a confident pick
    ranked = rc.score(idx, "thanks, that looks good")
    assert not ranked or ranked[0]["score"] < rc.DEFAULT_CONFIG["min_score"], ranked


def t_generic_damping():
    """A skill named after a generic verb must not beat a domain skill."""
    idx = _index([
        ("configure", "Enable or disable rules interactively"),
        ("nextjs-developer", "Next.js app router, middleware, server components"),
        ("code-review", "review a diff for correctness and security"),
    ])
    top = rc.score(idx, "how do I configure the nextjs app router with middleware")[0]
    assert top["name"] == "nextjs-developer", top
    # but a genuinely generic request still routes to the generic skill
    top = rc.score(idx, "review this diff for correctness")[0]
    assert top["name"] == "code-review", top


def t_stack_boost():
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d)
        (cwd / "mix.exs").write_text("defmodule X do end")
        stack = rc.stack_tokens(cwd)
        assert "elixir" in stack and "phoenix" in stack, stack
        # both skills match "form"; the repo's stack decides which one wins
        idx = _index([
            ("liveview", "Phoenix LiveView forms, mount, handle_event, assigns"),
            ("react-specialist", "React forms, hooks, components, state"),
        ])
        assert rc.score(idx, "add a validated form", [])[0]["name"] in {"liveview",
                                                                        "react-specialist"}
        top = rc.score(idx, "add a validated form", stack)[0]
        assert top["name"] == "liveview", top


def t_dep_stack():
    """Frameworks live in dependency manifests, not always in config files."""
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d)
        (cwd / "package.json").write_text(
            '{"dependencies":{"next":"15","react":"19"},"devDependencies":{"vitest":"2"}}')
        stack = rc.stack_tokens(cwd)
        assert {"next", "react", "vitest", "typescript"} <= set(stack) | {"typescript"}, stack
        assert "next" in stack and "react" in stack, stack
        assert "javascript" in rc.langs_of(stack), rc.langs_of(stack)
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d)
        (cwd / "requirements.txt").write_text("fastapi==0.115\npytest\n")
        stack = rc.stack_tokens(cwd)
        assert "fastapi" in stack and rc.langs_of(stack) == {"python"}, stack
    with tempfile.TemporaryDirectory() as d:  # malformed manifest must not raise
        cwd = Path(d)
        (cwd / "package.json").write_text("{not json")
        assert isinstance(rc.stack_tokens(cwd), list)


def t_scaffold():
    sc = rc.scaffold("the login test in test/auth_test.exs fails with a 401, don't touch the schema")
    assert "debug" in sc["intents"] and "test" in sc["intents"], sc
    assert "test/auth_test.exs" in sc["paths"], sc
    assert any("touch the schema" in c for c in sc["constraints"]), sc
    risky = rc.scaffold("drop table users and rerun the migration")
    assert "destructive" in risky["risks"] and "migration" in risky["risks"], risky
    thin = rc.scaffold("fix it")
    assert thin["gaps"], thin


def t_card():
    idx = _index([
        ("docker-best-practices", "Dockerfile layering multi-stage image size"),
        ("svg-animation", "animate SVG paths, morphing, stroke-dashoffset"),
        ("postgres-code-review", "review SQL schemas, indexes, query plans"),
    ])
    ranked = rc.score(idx, "reduce our docker image size")
    sc = rc.scaffold("reduce our docker image size")
    card = rc.render_card("reduce our docker image size", ranked, sc, [], rc.DEFAULT_CONFIG,
                          idx["stats"])
    assert "docker-best-practices" in card and "Step 1 — enrich" in card, card
    # chit-chat renders nothing: no card, no injected tokens
    assert rc.render_card("ok", [], rc.scaffold("ok"), [], rc.DEFAULT_CONFIG, {}) == ""


def t_skip_reason():
    """Machine-generated submissions and bypassed prompts must not be routed."""
    assert rc.skip_reason("fix the failing docker build") is None
    assert rc.skip_reason("", {}) == "empty"
    assert rc.skip_reason("anything", {"source": "loop_wakeup"}) == "source=loop_wakeup"
    assert rc.skip_reason("anything", {"source": "user"}) is None
    assert rc.skip_reason("* just chatting, no routing") == "bypass prefix"
    assert rc.skip_reason("/caveman-help") == "slash command"
    assert rc.skip_reason("<system-reminder>foo</system-reminder>") == "machine-generated content"
    assert rc.skip_reason("<task-notification>done</task-notification>") == (
        "machine-generated content")
    # a real request that merely starts with a slash command still routes
    assert rc.skip_reason("/review the docker build and fix the layer order") is None


def t_repeat_compaction():
    """Repeated identical routes shrink: injected cards are never freed."""
    idx = _index([("docker-best-practices", "Dockerfile layering multi-stage image size"),
                  ("svg-animation", "animate SVG paths morphing")])
    prompt = "reduce our docker image size"
    ranked, sc = rc.score(idx, prompt), rc.scaffold(prompt)
    full = rc.render_card(prompt, ranked, sc, [], rc.DEFAULT_CONFIG, idx["stats"], None)
    again = rc.render_card(prompt, ranked, sc, [], rc.DEFAULT_CONFIG, idx["stats"],
                           ["docker-best-practices"])
    assert "Same route as the previous turn" in again, again
    assert len(again) < len(full) / 2, (len(again), len(full))
    # a changed route renders in full again
    changed = rc.render_card(prompt, ranked, sc, [], rc.DEFAULT_CONFIG, idx["stats"],
                             ["something-else"])
    assert "Step 2" in changed, changed


def t_framing():
    """The card must mark itself as advice and its content as data."""
    idx = _index([("docker-best-practices", "Dockerfile layering multi-stage image size")])
    prompt = "reduce our docker image size"
    card = rc.render_card(prompt, rc.score(idx, prompt), rc.scaffold(prompt), [],
                          rc.DEFAULT_CONFIG, idx["stats"], None)
    assert "authoritative" in card and "not instructions" in card, card


def t_fusion_scale():
    """The P0: a dense-only top hit must clear min_score and reach the card.

    Regression for the RRF*10 rescale whose ceiling (~0.33) sat below the
    0.28 floor, silently discarding every semantic-only discovery.
    """
    idx = rc.load_index() or rc.build_index(Path.cwd())
    if len(idx["items"]) < 5:
        return
    # synthetic dense ranking: item[3] is dense #1, absent from bm25 rows
    bm25_rows = [{"kind": it["kind"], "name": it["name"], "score": 1.2 - i * 0.2,
                  "hits": ["x"]} for i, it in enumerate(idx["items"][:4])]
    target = idx["items"][3]
    import dense_index as di
    dense_hashes = [di.item_hash(target)]
    cfg = rc.load_config()
    fused = rc.fuse(bm25_rows, dense_hashes, idx)
    top_names = [r["name"] for r in fused]
    assert target["name"] in top_names[:5], f"dense #1 fell out of fused top5: {top_names}"
    skills, _ = rc.select(fused, cfg)
    assert any(r["name"] == target["name"] for r in skills), (
        f"dense-only hit below min_score after fusion: {[(r['name'], r['score']) for r in fused]}")
    # scale contract: every row score comparable (no RRF*10 tiny scores)
    assert all(r["score"] >= 0.28 for r in skills), "select() emitted sub-floor rows"


def t_user_n():
    import pipeline
    assert pipeline.user_n("use my top 20 tools for this") == 20
    assert pipeline.user_n("give me 5 skills for rust") == 5
    assert pipeline.user_n("top-7 mcp servers") == 7
    assert pipeline.user_n("just fix the bug") is None
    assert pipeline.user_n("top 999 tools please") == 50, "clamp to MAX_N"
    assert pipeline.user_n("top 0 tools") == 1, "clamp to >=1"


def t_gaptrack():
    """Repeat-gap tracking: oracle-gated promotion, install lockout, temp state."""
    import tempfile
    import gaptrack
    with tempfile.TemporaryDirectory() as tmp:
        gaptrack.STATE = Path(tmp) / "gaps.json"
        k1 = gaptrack._intent_key("How do I crop the background out of a photo?")
        k2 = gaptrack._intent_key("photo background crop how")
        assert k1 == k2, "same intent regardless of phrasing/order"
        assert gaptrack._intent_key("the a an and") == "", "stopwords only"
        cfg = {"sourcing": {"auto_threshold": 3, "auto_skills_only": True}}
        e = gaptrack.record_gap("crop background from photo", cfg)
        assert e["count"] == 1
        gaptrack.record_gap("crop background from photo", cfg)
        e = gaptrack.record_gap("please crop the background out of my photo", cfg)
        assert e["count"] == 3
        # not eligible yet: oracle has not judged (served None = never auto)
        assert not gaptrack.auto_eligible(e, cfg), "served=None must never auto"
        e["served"] = False
        assert not gaptrack.auto_eligible(e, cfg), "served=False (a local pick serves)"
        e["served"] = True
        assert gaptrack.auto_eligible(e, cfg), "oracle-confirmed gap + 3 repeats"
        # persist like auto_install does (verdict written back to the state file)
        fresh = gaptrack.load(); fresh[k1]["served"] = True; gaptrack._save(fresh)
        assert gaptrack.eligible_key_for_prompt("crop photo background", cfg)
        # installed lockout
        gaptrack.mark_installed(k1, "some-skill")
        assert not gaptrack.auto_eligible(gaptrack.load()[k1], cfg), "installed = no re-auto"
        # mcp-style tier off -> never auto
        cfg_off = {"sourcing": {"auto_threshold": 1, "auto_skills_only": False}}
        e2 = gaptrack.record_gap("another thing entirely", cfg_off)
        e2["served"] = False
        assert not gaptrack.auto_eligible(e2, cfg_off), "auto_skills_only=false disables tier"
        gaptrack.STATE = None  # restore real state path


def t_source_free():
    """Free/paid heuristics + installs parsing (pure functions)."""
    import source as _src
    assert _src._free("Convert images to webp. Fast and local.")
    assert not _src._free("Requires an API key from vendor.com")
    assert not _src._free("14-day free trial then $10 per month")
    assert not _src._free("Upgrade to Pro plan for batch mode")
    assert _src._installs("Indexed by skills.sh from x/y · 10,059 installs") == 10059
    assert _src._installs("no numbers here") is None


def t_laya_gate():
    """gate_accept beats chance, not a fixed margin: calibrated on 2026-09-26
    probe data where correct picks over 5 similar candidates scored p1
    0.25-0.32, margin 0.02-0.08 — all previously vetoed by margin < 0.10."""
    import laya_rerank
    g = laya_rerank.gate_accept
    # the four real declined probes must now pass
    assert g(0.246, 0.022, 5), "review-elixir case"
    assert g(0.304, 0.077, 5), "rest-api-design case"
    assert g(0.318, 0.029, 5), "prisma-postgres case"
    assert g(0.290, 0.065, 5), "tool-router-engineering case"
    # uniform noise (all options ~equal) must still fail: margin ~0
    assert not g(0.21, 0.001, 5), "flat distribution = no signal"
    # below-chance p1 must fail even with a margin (impossible-ish, but guard)
    assert not g(0.15, 0.02, 5), "below 1.2x uniform"
    # legacy strong pick passes regardless of margin
    assert g(0.90, 0.01, 5), "strong p1 passes"
    # 2-option case: uniform = 0.5, needs p1 >= 0.6 + margin 0.02
    assert not g(0.55, 0.05, 2), "2-option near-tie declined"
    assert g(0.65, 0.10, 2), "2-option clear winner accepted"


def t_breaker_files():
    import json as _json
    import laya_rerank, dense_index
    # trip and observe persistence
    laya_rerank._trip(120.0)
    assert laya_rerank._breaker_active(), "laya breaker should be active after trip"
    data = _json.loads(laya_rerank.BREAKER.read_text())
    assert data["until"] > time.time(), "breaker timestamp should be in the future"
    laya_rerank.BREAKER.unlink(missing_ok=True)
    assert not laya_rerank._breaker_active(), "breaker should clear when file removed"
    # dense side shares the shape
    dense_index._trip(120.0)
    assert dense_index._breaker_active()
    dense_index._BREAKER_PATH.unlink(missing_ok=True)
    assert not dense_index._breaker_active()


def t_live_speed():
    """Index whatever this machine actually has, and check routing stays fast."""
    idx = rc.load_index() or rc.build_index(Path.cwd())
    n = len(idx["items"])
    if n < 5:
        print(f"  (skipped live speed check: only {n} items indexed)")
        return
    t0 = time.time()
    for p in ("fix the flaky pytest mock", "shrink the docker image", "review this SQL migration"):
        rc.score(idx, p, [], {})
    ms = (time.time() - t0) * 1000 / 3
    assert ms < 250, f"routing too slow: {ms:.0f}ms per prompt over {n} items"
    print(f"  ({n} items indexed, {ms:.0f}ms per route)")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("t_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
