#!/usr/bin/env python3
"""dense_index.py — local dense-embedding layer for the tool-router (Stage 0.5).

Embeds every router item once with Ollama nomic-embed-text (768d) and stores the
vectors beside the BM25 index in .dense.npz + a compact .meta.json. At query time,
embeds the prompt (~10ms) and cosine-ranks all items (665x768 matmul, sub-ms).

All functions fail-open: any Ollama/numpy problem returns empty results and the
router keeps working BM25-only. Embeddings are keyed by content hash so unchanged
items are never re-embedded.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

# Script may live in ~/Work/tool-router (repo) or any skills dir (installed copy);
# the breaker state belongs to the machine's router home either way.
_BREAKER_PATH = Path(os.path.expanduser("~/.tool-router/breaker-ollama.json"))

OLLAMA = "http://localhost:11434/api/embeddings"
MODEL = "nomic-embed-text"
DIM = 768
_DOWN_UNTIL = 0.0

def _breaker_active() -> bool:
    """The breaker FILE is the source of truth — deleting it clears the breaker
    (manual override); the in-memory var is only the within-process fast path."""
    try:
        until = float(json.loads(_BREAKER_PATH.read_text()).get("until", 0))
        if time.time() < until:
            globals()["_DOWN_UNTIL"] = until
            return True
        globals()["_DOWN_UNTIL"] = 0.0
        return False
    except FileNotFoundError:
        globals()["_DOWN_UNTIL"] = 0.0
        return False
    except (OSError, ValueError):
        return time.time() < globals()["_DOWN_UNTIL"]


def _trip(seconds: float = 60.0) -> None:
    global _DOWN_UNTIL
    _DOWN_UNTIL = time.time() + seconds
    try:
        _BREAKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BREAKER_PATH.write_text(json.dumps({"until": _DOWN_UNTIL}))
    except Exception:
        pass


def _embed(text: str, timeout: float = 20.0) -> list[float] | None:
    """One embedding via Ollama. None on any failure."""
    global _DOWN_UNTIL
    if _breaker_active():
        return None
    body = json.dumps({"model": MODEL, "prompt": text[:4000]}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            vec = json.load(resp).get("embedding")
        if vec and len(vec) == DIM:
            return vec
        return None
    except Exception:
        _trip(60.0)
        return None


def item_hash(item: dict) -> str:
    key = f"{item.get('kind','')}|{item.get('name','')}|{item.get('desc','')}|{item.get('extra','')}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def build_dense(index: dict, dense_path: Path) -> dict:
    """(Re)build embeddings for new/changed items. Returns stats."""
    import numpy as np

    old_meta = {}
    old_vecs = None
    if dense_path.with_suffix(".meta.json").is_file():
        try:
            old_meta = json.loads(dense_path.with_suffix(".meta.json").read_text())
        except (OSError, ValueError):
            old_meta = {}

    items = index["items"]
    hashes, texts = [], []
    for it in items:
        h = item_hash(it)
        it["_h"] = h
        hashes.append(h)
        texts.append(f"{it.get('kind', '')}: {it.get('name', '')}. {it.get('desc', '')}")

    keep_h, keep_v = [], []
    if old_meta.get("hashes") and dense_path.with_suffix(".npz").is_file():
        try:
            old_vecs = np.load(dense_path.with_suffix(".npz"))["vecs"]
            old_h = old_meta["hashes"]
            if len(old_h) == len(old_vecs):
                keep_h, keep_v = old_h, old_vecs
        except Exception:
            keep_h, keep_v = [], []

    reuse = {h: keep_v[i] for i, h in enumerate(keep_h)}
    out_h, rows, new_n = [], [], 0
    for h, text in zip(hashes, texts):
        if h in reuse:
            out_h.append(h)
            rows.append(reuse[h])
            continue
        vec = _embed(text)
        if vec is None:
            continue  # skip items we cannot embed; they just miss the dense lane
        out_h.append(h)
        rows.append(vec)
        new_n += 1
    if not rows:
        return {"embedded": 0, "reused": 0, "total": len(items), "ok": False}

    arr = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    arr = arr / (norms > 0) / (norms + 1e-9)  # L2 normalize (safe div)
    np.savez_compressed(dense_path.with_suffix(".npz"), vecs=arr)
    dense_path.with_suffix(".meta.json").write_text(
        json.dumps({"hashes": out_h, "model": MODEL, "built_at": int(time.time())}))
    return {"embedded": new_n, "reused": len(rows) - new_n, "total": len(items), "ok": True}


def dense_rank(prompt: str, index: dict, dense_path: Path, top_n: int = 50) -> list[str]:
    """Return item content-hashes ranked by cosine to the prompt. [] on failure."""
    if _breaker_active():
        return []
    try:
        import numpy as np
        meta = json.loads(dense_path.with_suffix(".meta.json").read_text())
        vecs = np.load(dense_path.with_suffix(".npz"))["vecs"]
        qv = _embed(prompt, timeout=8.0)
        if qv is None or len(meta["hashes"]) != len(vecs):
            return []
        q = np.asarray(qv, dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-9)
        sims = vecs @ q
        order = np.argsort(-sims)[:top_n]
        return [meta["hashes"][i] for i in order]
    except Exception:
        _trip(60.0)
        return []
