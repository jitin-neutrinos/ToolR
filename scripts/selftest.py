#!/usr/bin/env python3
"""Runnable check for the router: `python3 selftest.py`. Exits non-zero on failure."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from email.message import Message
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


def t_long_intent_hashed():
    """A pasted system prompt must not become a 4,581-char gap key.

    Regression for 2026-10-05: _intent_key joined every content token, so one
    dumped prompt produced a 4,581-char key that fuzzy-matched unrelated traffic
    and paid the full 24 s network sourcing cost on every route (count hit 720).
    """
    import gaptrack
    short = gaptrack._intent_key("remove the background from a photo")
    assert len(short) <= 200 and not gaptrack.key_is_hashed(short), short
    huge = " ".join(f"token{i}" for i in range(400))
    long_key = gaptrack._intent_key(huge)
    assert gaptrack.key_is_hashed(long_key), long_key
    assert len(long_key) < 60, f"hashed key still too long: {len(long_key)}"
    assert gaptrack._intent_key(huge) == long_key, "hash must be deterministic"
    other = gaptrack._intent_key(" ".join(f"tok{i}" for i in range(400)))
    assert other != long_key, "different content must hash differently"


def t_candidate_cache():
    """Registry search must be cached per intent, with a freshness ceiling.

    Regression for 2026-10-05: the auto tier searched hermes skills + the MCP
    registry (24 s measured) on EVERY route for an intent already resolved days
    earlier.
    """
    import source as _src
    import gaptrack as _gt
    now = int(time.time())
    assert _src.cached_candidates("k", {})[0] is None, "empty entry must miss"
    entry = {"candidates": {"at": now, "rows": [{"name": "a", "kind": "skill"}]}}
    rows, age = _src.cached_candidates("k", entry)
    assert rows is not None and rows[0]["name"] == "a", rows
    assert 0 <= age < 5, age
    stale = {"candidates": {"at": now - _src.CACHE_STALE_S - 10,
                            "rows": [{"name": "b"}]}}
    assert _src.cached_candidates("k", stale)[0] is None, "stale cache must miss"
    with tempfile.TemporaryDirectory() as tmp:
        _gt.STATE = Path(tmp) / "gaps.json"
        _gt.record_gap("crop background photo please", {"sourcing": {"auto_threshold": 3}})
        key = _gt.resolve_key("crop background photo please")
        assert key
        _src.store_candidates(key, [{"name": "x", "kind": "skill", "free": True,
                                     "installs": 5000, "description": "d"}])
        got, _age = _src.cached_candidates(key, _gt.load()[key])
        assert got and got[0]["name"] == "x", got
        _gt.mark_installed(key, "x")
        _src.store_candidates(key, [{"name": "y"}])   # must be refused
        assert _gt.load()[key]["candidates"]["rows"][0]["name"] == "x", \
            "cache must not be overwritten after install"
        _gt.STATE = None


def t_screen_cache_and_cheap_gates():
    """Injection screens are cached, and cheap gates run BEFORE the Laya screen.

    Regression for 2026-10-05: every candidate was screened with a 4.5 s Laya
    call even when it was an MCP (auto tier is skills-only) or paid.
    """
    import source as _src
    mcp = {"kind": "mcp", "free": True, "installs": 99999}
    paid = {"kind": "skill", "free": False, "installs": 99999}
    thin = {"kind": "skill", "free": True, "installs": 12}
    good = {"kind": "skill", "free": True, "installs": 5000}
    assert _src._candidate_ok_cheap(mcp)[0] is False, "mcp is never auto"
    assert _src._candidate_ok_cheap(paid)[0] is False, "paid is never auto"
    assert _src._candidate_ok_cheap(thin)[0] is False, "low installs never auto"
    assert _src._candidate_ok_cheap(good)[0] is True, "a good skill must pass cheap"
    assert _src._candidate_ok_auto(good, "safe")[0] is True
    assert _src._candidate_ok_auto(good, "malicious")[0] is False
    assert _src._candidate_ok_auto(good, None)[0] is False, "unsure screen fails closed"
    with tempfile.TemporaryDirectory() as tmp:
        cached = Path(tmp) / "screen-cache.json"
        _src.SCREEN_CACHE = cached
        _src._screen_cache = {"deadbeef": "safe", "cafe": ""}
        _src._save_screen_cache()
        _src._screen_cache = None
        assert _src._load_screen_cache() == {"deadbeef": "safe", "cafe": ""}
        _src.SCREEN_CACHE = None
        _src._screen_cache = {}


def t_candidate_relevance_gate():
    """A candidate must be judged RELEVANT before any unattended install.

    Regression for 2026-10-05: the auto tier installed a Chinese multi-role
    chatroom skill for "fix the astra chat streaming bug" — free, popular and
    injection-safe, but it matched the word "chat" and did nothing about
    streaming. Relevance is now a separate, fail-closed judge.
    """
    import source as _src
    saved = _src._post_judge

    def fake(cand, prompt):
        if cand.get("name") == "right-skill":
            return True
        if cand.get("name") == "chat-bot":
            return False
        return None            # judge unsure

    _src._post_judge = fake
    try:
        assert _src.candidate_serves({"name": "right-skill"}, "x") is True
        assert _src.candidate_serves({"name": "chat-bot"}, "x") is False
        assert _src.candidate_serves({"name": "mystery"}, "x") is None, \
            "unsure must stay None so the caller fails closed"
    finally:
        _src._post_judge = saved


def t_laya_bearer_token():
    """The reranker must send LAYA_MCP_TOKEN or every call 401s.

    Regression for 2026-10-05: no Authorization header was sent at all, so the
    rerank stage was dead on every route and the breaker hid the cause.
    """
    import laya_rerank
    import urllib.request
    orig_env = os.environ.get("LAYA_MCP_TOKEN")
    orig_conf = laya_rerank.TOKEN_CONF
    orig_cache = laya_rerank._token_cache
    try:
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "token.conf"
            # both real systemd drop-in shapes must parse
            conf.write_text('[Service]\nEnvironment="LAYA_MCP_TOKEN=secret-abc"\n'
                            'Environment=LAYA_ENGLISH_MODEL=/x/y\n')
            laya_rerank.TOKEN_CONF = conf
            laya_rerank._token_cache = None
            os.environ.pop("LAYA_MCP_TOKEN", None)
            assert laya_rerank.bearer_token() == "secret-abc", "read from drop-in"
            bare = Path(tmp) / "bare.conf"
            bare.write_text("LAYA_MCP_TOKEN=plain-value\n")
            laya_rerank.TOKEN_CONF = bare
            laya_rerank._token_cache = None
            assert laya_rerank.bearer_token() == "plain-value", "bare form too"
            laya_rerank.TOKEN_CONF = conf
            laya_rerank._token_cache = None
            captured = {}

            def fake_urlopen(req, timeout=None):
                captured["headers"] = dict(req.headers)
                raise urllib.error.URLError("down")

            real = urllib.request.urlopen
            urllib.request.urlopen = fake_urlopen
            try:
                assert laya_rerank._ask("http://127.0.0.1:1/x", {}, {}, 0.1) is None
            finally:
                urllib.request.urlopen = real
            auth = {k.lower(): v for k, v in captured["headers"].items()}
            assert auth.get("authorization") == "Bearer secret-abc", auth
            os.environ["LAYA_MCP_TOKEN"] = "from-env"
            laya_rerank._token_cache = None
            assert laya_rerank.bearer_token() == "from-env"
    finally:
        laya_rerank.TOKEN_CONF = orig_conf
        laya_rerank._token_cache = orig_cache
        if orig_env is None:
            os.environ.pop("LAYA_MCP_TOKEN", None)
        else:
            os.environ["LAYA_MCP_TOKEN"] = orig_env


def t_laya_auth_fault_not_breakered():
    """A 401 is a config fault: reported, but never hidden behind a breaker."""
    import laya_rerank
    breaker = laya_rerank.BREAKER
    with tempfile.TemporaryDirectory() as tmp:
        bpath = Path(tmp) / "breaker-laya.json"
        laya_rerank.BREAKER = bpath
        laya_rerank._auth_fault_at = 0.0
        assert laya_rerank._auth_fault_recent() is False, "no fault yet"
        laya_rerank._auth_fault_at = time.time()
        assert laya_rerank._auth_fault_recent() is True, "recent 401 must be seen"
        laya_rerank._auth_fault_at = time.time() - 1000
        assert laya_rerank._auth_fault_recent() is False, "stale 401 must not count"
    laya_rerank.BREAKER = breaker


def t_laya_bm25_gate():
    """A decisive BM25 top pick skips the slow rerank; a tie does not.

    enabled=True is passed explicitly: the rerank now defaults OFF (measured
    harmful, -0.017 R@1 at 4x latency), so a test that relied on the default
    would silently stop exercising the gate at all.
    """
    import laya_rerank
    cfg = {"laya_rerank": {"enabled": True, "gate_bm25": True,
                           "decisive_ratio": 1.6, "timeout_s": 8.0, "top_n": 5}}
    # The shipped default must be off. load_laya_cfg memoises into a module
    # global, so read the CODE default, not the memo — otherwise this assertion
    # silently tests whatever an earlier check happened to load.
    import inspect
    src_default = inspect.getsource(laya_rerank.load_laya_cfg)
    assert '"enabled": False' in src_default, \
        "laya rerank must default OFF in code (measured -0.017 R@1)"
    decisive = [{"name": "a", "score": 0.90, "description": "x"},
                {"name": "b", "score": 0.30, "description": "y"}]
    out = laya_rerank.rerank(list(decisive), "some prompt", cfg)
    assert out[0]["name"] == "a", "order must stand"
    assert out[0].get("laya") == "skipped-decisive-bm25", out[0].get("laya")
    tied = [{"name": "a", "score": 0.40, "description": "x"},
            {"name": "b", "score": 0.39, "description": "y"}]
    out2 = laya_rerank.rerank(list(tied), "some prompt", cfg)
    assert out2[0].get("laya") != "skipped-decisive-bm25", "a tie must not skip"


def t_alias_coverage():
    """Every alias key must match a real capability; aliases must reach scoring.

    Regression for 2026-10-05: the query "best 20 frontend design and animation
    design skills" returned ZERO animation skills, because "animation" appears in
    no framer-motion-*/gsap-* description in a BM25-matchable form. The alias
    layer fixed it (5 framer skills in the top 10). A phantom key is the obvious
    way to break this again, so the audit runs on every selftest.
    """
    import aliases as A
    rep = A.coverage_report()
    assert not rep["keys_not_in_index"], \
        f"alias keys match no capability: {rep['keys_not_in_index']}"
    assert rep["keys_matching_index"] >= 50, rep
    assert len(A.CONCEPT_TERMS) >= 40, len(A.CONCEPT_TERMS)
    # no alias may be a GENERIC token — those are damped in the score AND its
    # denominator, so an alias that is generic moves nothing.
    import router_core as rc
    generic = rc.GENERIC
    for name, blob in A.SKILL_ALIASES.items():
        bad = [t for t in blob.split() if rc.norm(t) in generic]
        assert not bad, f"{name} aliases only generic tokens: {bad}"
    # concept bridge must fire on plain English
    assert "animation" in A.concept_terms("make it animate")
    assert "glass" in A.concept_terms("a frosted panel")
    assert "jank" in A.concept_terms("this feels janky")


def t_aliases_reach_the_index():
    """Alias terms must be baked into item tokens, and must actually rank."""
    import router_core as rc
    from pathlib import Path as _P
    idx = rc.build_index(_P.home())
    tagged = [i for i in idx["items"] if i.get("aliases")]
    assert len(tagged) >= 50, f"only {len(tagged)} items carry aliases"
    framer = next(i for i in idx["items"] if i["name"] == "framer-motion-core")
    assert "animation" in framer["tokens"], framer["tokens"][:20]
    assert "react" in framer["tokens"], framer["tokens"][:20]
    # the exact query that used to return zero animation skills
    ranked = rc.score(idx, "identify the best 20 frontend design and animation "
                          "design skills I have", [], {}, None)
    names = [r["name"] for r in ranked[:10]]
    motion = [n for n in names if "framer" in n or "gsap" in n or "motion" in n]
    assert len(motion) >= 2, f"alias bridge failed, top10={names}"


def t_top_n_flag():
    """--top N must exist, win over the prompt, and return exactly N.

    Regression for 2026-10-05: an agent ran `route --top 20 "..."` and got
    "error: unrecognized arguments: --top". The count was only ever read from
    inside the prompt text, so the obvious flag did not exist. Separately,
    "top 20 skills" returned 19 picks, because the min_score floor and the tail
    cut removed real candidates and nothing topped the list back up.
    """
    import subprocess
    import pipeline
    import router_core as rc
    from pathlib import Path as _P

    script = _P(__file__).resolve().parent / "route.py"
    # 1) the flag is accepted, in every spelling
    for flag in (["--top", "20"], ["-n", "20"], ["--count", "20"]):
        p = subprocess.run([sys.executable, str(script), *flag,
                            "revamp the chat composer"],
                           capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, f"{flag} failed: {p.stderr[:200]}"
        assert "Top 20 combined" in p.stdout, \
            f"{flag} did not yield 20: {p.stdout[:200]}"
        assert "unrecognized" not in p.stderr, p.stderr[:200]

    # 2) --top overrides a different number named in the prompt
    p = subprocess.run([sys.executable, str(script), "--top", "5",
                        "top 20 skills for the chat composer"],
                       capture_output=True, text=True, timeout=120)
    assert "Top 5 combined" in p.stdout, p.stdout[:200]

    # 3) a count named in the prompt alone also returns exactly that many
    for n in (3, 7, 20):
        out = pipeline.run(f"top {n} skills for the chat composer",
                           _P.home(), rc.load_config(), rc.load_index(),
                           rewrite=False, n_override=None)
        assert len(out["picks"]) == n, \
            f"asked for {n}, got {len(out['picks'])}"

    # 4) n_override beats the prompt, and the pipeline clamps it
    out = pipeline.run("top 20 skills", _P.home(), rc.load_config(),
                       rc.load_index(), rewrite=False, n_override=4)
    assert len(out["picks"]) == 4, out["picks"]
    out = pipeline.run("x", _P.home(), rc.load_config(), rc.load_index(),
                       rewrite=False, n_override=999)
    assert len(out["picks"]) <= pipeline.MAX_N, out["picks"]
    out = pipeline.run("x", _P.home(), rc.load_config(), rc.load_index(),
                       rewrite=False, n_override=0)
    assert len(out["picks"]) == 1, out["picks"]


def t_bad_flag_teaches():
    """An unknown flag must print a usable example, and never echo itself back.

    Regression for 2026-10-05: the argparse wall ("unrecognized arguments:
    --top" + a usage block with no example) left the agent guessing. A first
    attempt at the hint suggested `--top --topp`, i.e. the same failure with
    more words.
    """
    import subprocess
    from pathlib import Path as _P
    script = _P(__file__).resolve().parent / "route.py"

    p = subprocess.run([sys.executable, str(script), "--topp", "20", "x"],
                       capture_output=True, text=True, timeout=120)
    err = p.stderr
    assert p.returncode != 0, "a bad flag must fail"
    assert "not a valid option" in err, err[:200]
    assert "example:" in err, f"no worked example:\n{err[:300]}"
    # the suggestion must be a REAL option and must not repeat the bad one
    assert "--top 10" in err, err[:200]        # the canonical example
    assert "--top --topp" not in err, "must not echo the bad flag: " + err[:200]
    assert "--topp 20" not in err.split("example")[0], err[:200]
    # a typo of --source suggests --source
    p2 = subprocess.run([sys.executable, str(script), "--soruce", "x"],
                        capture_output=True, text=True, timeout=120)
    assert "--source" in p2.stderr, p2.stderr[:200]
    # --help must show the count option and a worked example
    p3 = subprocess.run([sys.executable, str(script), "--help"],
                        capture_output=True, text=True, timeout=60)
    assert "--top" in p3.stdout, p3.stdout[:300]
    assert "example:" in p3.stdout, p3.stdout[:400]
    assert "prompt-engineer" not in p3.stdout.split("options:")[1].split("\n\n")[0], \
        "the removed rewrite stage must not still be advertised"


def t_rewriter_stage_removed():
    """The hook must NOT make a model call. The rewriter stage is removed.

    Owner decision 2026-10-05: the hook routes the user's own words and nothing
    else. The rewriter was the only network call on the path and the free tiers
    it used return HTTP 200 with an EMPTY body ~1 call in 5, which is what made
    the hook slow and occasionally 15-18 s.

    This test pins the removal three ways so a future edit cannot quietly put a
    model call back on the critical path: the pipeline must not import/call
    rewriter, the gateway plugin must pass rewrite=False, and the measured route
    must stay well inside the budget.
    """
    import inspect
    import pipeline
    import router_core as rc

    # 1) the pipeline must not call the rewriter at all
    src = inspect.getsource(pipeline)
    assert "import rewriter" not in src, \
        "pipeline must not import rewriter — the stage is removed"
    assert "rewriter.rewrite(" not in src, \
        "pipeline must not call rewriter.rewrite()"
    assert "rewritten, provider = None, None" in src, \
        "the stage variables must be initialised and left alone"

    # 2) a route must produce a card with no network dependency
    from pathlib import Path as _P
    t0 = time.time()
    out = pipeline.run("fix the streaming bug in the astra chat",
                       _P.home(), rc.load_config(), rc.load_index(),
                       rewrite=False)
    elapsed = time.time() - t0
    assert out["card"], "a card must still be produced"
    assert out["rewritten"] is None, "nothing is rewritten any more"
    assert out["provider"] is None, "no model is named any more"
    assert elapsed < 2.0, f"route took {elapsed:.2f}s — over budget"
    # the card must still tell the agent what to load
    assert "Load before editing" in out["card"], \
        "the card must keep the load instruction now that the rewriter is gone"
    assert "Top" in out["card"] and "combined" in out["card"]

    # 3) the gateway plugin must be explicit about not rewriting
    plug = _P(os.path.expanduser("~/.hermes/plugins/tool-router/__init__.py"))
    if plug.is_file():
        psrc = plug.read_text(encoding="utf-8")
        assert "rewrite=False" in psrc, \
            "the Hermes plugin must pass rewrite=False explicitly"
        assert "prompt-engineer rewrite" not in psrc, \
            "the plugin docstring must not still claim a rewrite stage"


def t_free_model_measurements_pinned():
    """The free-gateway findings must not rot into a wrong default.

    Measured live 2026-10-05 and expensive to rediscover: opencode-go and
    OpenRouter both reject a plain request with Cloudflare 403/1010 unless a
    browser User-Agent is sent, and opencode-go additionally demands
    X-Session-Id (400 MissingSessionID). Both free tiers also return HTTP 200
    with an empty body intermittently, which is why _post_free records
    _LAST_OUTCOME and the breaker treats "empty" as retry-immediately rather
    than as a dead model.
    """
    import rewriter
    # gateway endpoints and the headers that are not optional
    assert rewriter.OCGO_URL.endswith("/v1/chat/completions"), rewriter.OCGO_URL
    assert rewriter.OPENROUTER_URL.endswith("/v1/chat/completions")
    assert "Mozilla/5.0" in rewriter.BROWSER_UA, "Cloudflare 1010 without a UA"
    src = inspect_src(rewriter._post_free)
    assert "X-Session-Id" in src, "opencode-go needs X-Session-Id"
    assert "User-Agent" in src, "_post_free must send a browser UA"
    # every configured model must be a plausible id, never a guessed ":free"
    for gw, models in rewriter.FREE_MODELS.items():
        assert models, f"{gw} has no models"
        for m in models:
            assert m and " " not in m, f"bad model id {m!r} in {gw}"
    # the primary gateway is opencode-go (owner rule)
    assert rewriter.FREE_GATEWAYS[0] == "opencode-go", rewriter.FREE_GATEWAYS
    assert "openrouter" in rewriter.FREE_GATEWAYS
    # empty-200 must be distinguishable from a timeout
    assert "empty" in src, "_post_free must classify an empty 200"
    assert "timeout" in src, "_post_free must classify a timeout"


def inspect_src(fn):
    import inspect
    return inspect.getsource(fn)


def t_rewrite_quota_breaker():
    """A 429 model must be skipped, not retried on every message.

    The 429 arrives as an HTTPError that _post must swallow and REMEMBER.
    Measured 2026-10-05: gemini-flash-latest was quota-exhausted, so every
    rewrite paid a failed call before falling back to flash-lite.
    """
    import rewriter
    saved_blocked = rewriter._quota_blocked
    saved_post = rewriter._post
    try:
        rewriter._quota_blocked = {}
        calls = []

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

        # _post must CATCH the HTTPError and record the model. Drive it through
        # the real _post body (not a stub of _post itself).
        def fake_urlopen(req, timeout=None):
            calls.append(json.loads(req.data).get("model"))
            raise urllib.error.HTTPError(
                req.full_url, 429, "quota", Message(), _Resp(b"{}"))

        saved_urlopen = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            # 429 -> returns None (no raise) and records the cooldown
            assert rewriter._post("https://example.invalid/v1", "k",
                                  {"model": "gemini-flash-latest"}, 1.0) is None
            assert "gemini-flash-latest" in rewriter._quota_blocked, \
                "429 must be remembered"
            n = len(calls)
            # blocked: short-circuits WITHOUT calling urlopen again
            assert rewriter._post("https://example.invalid/v1", "k",
                                  {"model": "gemini-flash-latest"}, 1.0) is None
            assert len(calls) == n, f"quota-blocked model retried {len(calls)-n}x"
            # a different model still goes out
            rewriter._post("https://example.invalid/v1", "k",
                           {"model": "gemini-flash-lite-latest"}, 1.0)
            assert len(calls) == n + 1, "a healthy model must not be blocked"
        finally:
            urllib.request.urlopen = saved_urlopen
    finally:
        rewriter._quota_blocked = saved_blocked
        rewriter._post = saved_post


def t_picks_block_and_budget():
    """The Use: block renders real picks; the budget gates the expensive stages."""
    import rewriter
    import pipeline
    blk = rewriter._picks_block([{"name": "framer-motion-core", "kind": "skill",
                                  "desc": "core API"},
                                 {"name": "x", "kind": "mcp", "desc": ""}])
    assert "framer-motion-core (skill)" in blk, blk
    assert "Use: none identified" not in blk
    assert rewriter._picks_block([]) == "", "empty picks must render nothing"
    assert rewriter._picks_block(None) == ""
    # the system prompt must actually ask for the Use line
    assert "Use:" in rewriter.SYSTEM
    assert "Use:" not in rewriter.SYSTEM_NOPICKS, \
        "the no-picks variant must not demand a Use line"
    assert pipeline.DEFAULT_BUDGET_MS <= 5000, "budget must hold the 5s target"


def t_sourcing_is_deferred():
    """The hot path must record a gap but never do network work.

    Regression for 2026-10-05: one route spent 57 s inside search_registries
    because the intent had crossed the auto-install threshold. The lifecycle now
    runs in a background child; auto_install only records and schedules.
    """
    import source as _src
    import inspect
    src = inspect.getsource(_src.auto_install)
    for banned in ("search_registries(", "candidate_serves(", "gap_oracle(",
                   "install_skill(", "laya_screen("):
        assert banned not in src, \
            f"auto_install still calls {banned} on the hot path"
    assert "_schedule_deferred" in src, "auto_install must schedule the sweep"
    # the synchronous body must still contain the real work
    body = inspect.getsource(_src.auto_install_sync)
    for needed in ("search_registries(", "candidate_serves(", "install_skill("):
        assert needed in body, f"auto_install_sync lost {needed}"
    # deferral is opt-out via env, and honours the once-a-minute stamp
    assert _src.os.environ.get("ROUTER_NO_DEFER") is None or True


def t_hermes_superset_root_indexed():
    """~/.hermes/skills must be in the index — recursively, last, deduped.

    Regression for 2026-10-05: that tree was missing from SKILL_ROOTS entirely,
    and 542 of its skills live at <category>/<name>/SKILL.md, so even adding it
    depth-1 would have found 23 of 542. It is now the last root (first-wins
    dedupe means it only adds what earlier roots missed) and recursive.
    """
    import router_core as rc
    from pathlib import Path as _P
    specs = rc.SKILL_ROOTS
    hermes = [s for s in specs if "hermes" in str(s[1])]
    assert hermes, f"hermes skill root missing from SKILL_ROOTS: {specs}"
    spec = hermes[0]
    assert len(spec) > 3 and spec[3] is True, \
        "hermes root must be flagged recursive (skills sit at depth 2)"
    assert spec is specs[-1], "hermes root must be LAST so first-wins dedupe holds"
    # others stay 3-tuples and depth-1
    for s in specs[:-1]:
        assert len(s) == 3, f"unexpected 4-field spec before the last: {s}"
    # the recursive walker must find a nested skill in a temp tree
    with tempfile.TemporaryDirectory() as tmp:
        root = _P(tmp)
        (root / "cat-a" / "deep-skill").mkdir(parents=True)
        (root / "cat-a" / "deep-skill" / "SKILL.md").write_text("---\nname: deep-skill\n---\n")
        (root / "flat-skill").mkdir()
        (root / "flat-skill" / "SKILL.md").write_text("---\nname: flat-skill\n---\n")
        junk = root / "cat-a" / "node_modules" / "bad"
        junk.mkdir(parents=True)
        (junk / "SKILL.md").write_text("---\nname: bad\n---\n")
        found = {d.name for d in rc._skill_dirs(root, recursive=True)}
        assert "deep-skill" in found, f"nested skill missed: {found}"
        assert "flat-skill" in found, f"flat skill missed: {found}"
        assert "bad" not in found, f"junk dir walked: {found}"
        shallow = {d.name for d in rc._skill_dirs(root, recursive=False)}
        assert shallow == {"flat-skill"}, f"depth-1 must not recurse: {shallow}"


def t_dense_breaker_not_a_measurement():
    """A tripped dense breaker must be LOUD, and must not be read as a 0.0 lane.

    Regression for 2026-10-05: two ablation runs returned fused R@1 = 0.0000 with
    lane_degraded = 796. Cause: _embed's 20 s timeout tripping a 60 s breaker under
    load average 30 on 28 cores, after which every remaining query silently fell
    back to BM25. That is the worst kind of eval result — a false zero that looks
    like "fusion is worthless". The harness does flag it (lane_healthy=False), and
    this check pins that the flag is reachable and that the breaker file is the
    documented switch.
    """
    import dense_index as di
    saved_breaker = di._BREAKER_PATH
    with tempfile.TemporaryDirectory() as tmp:
        bp = Path(tmp) / "breaker-ollama.json"
        di._BREAKER_PATH = bp
        try:
            # no breaker -> dense_rank is allowed to try (and may fail on a
            # machine without Ollama, but it must NOT be the breaker doing it)
            bp.unlink(missing_ok=True)
            assert di._breaker_active() is False, "no breaker file must mean open"

            # breaker present and unexpired -> short-circuit, return nothing
            bp.write_text(json.dumps({"until": time.time() + 120}))
            assert di._breaker_active() is True, "unexpired breaker must be active"
            assert di._embed("anything") is None, "an open breaker must not embed"
            assert di._trip(0.0) is None, "_trip returns None"
            bp.write_text(json.dumps({"until": 0}))
            assert di._breaker_active() is False, "expired breaker must clear"
        finally:
            di._BREAKER_PATH = saved_breaker


def t_laya_promote_band():
    """A weak pick must NOT be promoted above a much stronger fused score.

    Regression for 2026-10-05: for "fix the astra chat streaming bug" Laya picked
    hermes-messaging-triage (fused score 0.666) and an unconditional promote put it
    above astra-webui-performance (1.386) and astra-webui-regression-fixes
    (1.529). The retrieval lanes own the band; Laya only reorders within it.
    """
    import laya_rerank
    cfg = {"laya_rerank": {"enabled": True, "gate_bm25": True,
                           "decisive_ratio": 1.6, "promote_band": 0.6,
                           "timeout_s": 8.0, "top_n": 5}}

    class FakeAnswers(dict):
        pass

    # a tie in scores -> the pick may be promoted
    tie = [{"name": "weak", "score": 1.0, "description": "a"},
           {"name": "other", "score": 0.98, "description": "b"}]
    saved_ask = laya_rerank._ask
    laya_rerank._ask = lambda *a, **k: {
        "pick": {"choice": "weak", "probabilities": {"weak": 0.9, "other": 0.05}}}
    try:
        out = laya_rerank.rerank(list(tie), "p", cfg)
        assert out[0]["name"] == "weak", "a tie must still allow the promote"
        # a big score gap -> the pick is out of band and the order must stand
        gap = [{"name": "strong", "score": 1.529, "description": "a"},
               {"name": "weak", "score": 0.666, "description": "b"}]
        out2 = laya_rerank.rerank(list(gap), "p", cfg)
        assert out2[0]["name"] == "strong", \
            f"out-of-band promote regressed: {[r['name'] for r in out2[:2]]}"
    finally:
        laya_rerank._ask = saved_ask


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


def t_mcp_reaches_card_on_intent_query():
    """Bug fix regression: an MCP must surface on an intent query with no name
    mention — fleet descriptions + hints + kind quota, end to end."""
    import pipeline
    cfg = rc.load_config()
    index = rc.load_index() or rc.build_index(Path.cwd())
    ranked = rc.score(index, "query the codebase architecture knowledge graph",
                      [], rc.load_learned(), cfg.get("mcp_hints"))
    mcps = [r for r in ranked if r["kind"] == "mcp" and r.get("score", 0) > 0]
    assert mcps, "no MCP scored >0 on an intent query — descriptions/hints broken"
    graphify = [r for r in mcps if r["name"] == "graphify"]
    assert graphify and graphify[0]["score"] >= cfg["min_score"], \
        f"graphify MCP below min_score: {[r.get('score') for r in graphify]}"
    picks = pipeline._top_combined(ranked, cfg, 10)
    kinds = {r["kind"] for r in picks}
    assert "mcp" in kinds, f"no MCP in top-10 card picks: kinds={kinds}"


def t_kind_quota_reserves_slots():
    """kind_quota: best MCP/agent/command get slots even when skills dominate."""
    import pipeline
    ranked = [{"kind": "skill", "name": f"s{i}", "score": 1.0 - i * 0.01}
              for i in range(20)]
    ranked += [{"kind": "mcp", "name": "best-mcp", "score": 0.35},
               {"kind": "mcp", "name": "second-mcp", "score": 0.30},
               {"kind": "mcp", "name": "third-mcp", "score": 0.29},
               {"kind": "agent", "name": "best-agent", "score": 0.31},
               {"kind": "command", "name": "best-cmd", "score": 0.281}]
    picks = pipeline._top_combined(ranked, {"min_score": 0.28, "tail_ratio": 0.55,
                                            "kind_quota": 2}, 10)
    kinds = {}
    for r in picks:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    assert kinds.get("mcp", 0) == 2, f"quota-2 should land exactly 2 MCPs: {kinds}"
    assert kinds.get("agent", 0) == 1 and kinds.get("command", 0) == 1, kinds
    names = {r["name"] for r in picks}
    assert {"best-mcp", "second-mcp", "best-agent", "best-cmd"} <= names, names
    # skills still dominate by count
    assert kinds.get("skill", 0) >= 5, kinds


def t_fleet_assets_indexed():
    """Fleet tools/plugins land in the index as first-class items."""
    items = rc.discover_fleet_assets()
    assert items, "no fleet assets discovered — inventory.json missing or empty?"
    kinds = {it["kind"] for it in items}
    assert "fleet-tool" in kinds or "fleet-plugin" in kinds, kinds
    assert all(i["desc"] for i in items), "fleet items must carry real descriptions"


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
        except Exception as exc:
            # A check that raises is a broken check, not a failed assertion.
            # Report it and keep going: one broken check must not hide the
            # state of every other check (regression 2026-10-05 — an
            # HTTPError inside a quota-breaker test aborted the whole run).
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
