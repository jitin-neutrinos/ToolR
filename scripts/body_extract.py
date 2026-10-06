#!/usr/bin/env python3
"""body_extract.py — index-side signal extraction from SKILL.md bodies.

The research finding (SkillRouter, arXiv:2603.22455; TOOL-REX, ICLR 2026) is
that the skill BODY is the decisive retrieval signal, and that LLM-free
index-side document expansion closes most of the vocabulary gap. This module
is the stdlib-only version of that: it reads what discovery already has on
disk (the SKILL.md path is already in every item) and extracts:

  body    — the first N words of the SKILL.md body after the frontmatter
            (truncated hard: the index must stay small)
  when    — the "Use when ..." trigger clause from the description
  triggers— literal trigger phrases, quoted or after "Use when/for"

Nothing here calls a model, the network, or numpy. An unreadable body yields
"" and the item indexes exactly as before — fail-open.

Extraction contract (what BM25 and the dense lane both consume):
  it["body"]     plain text, <= BODY_MAX_WORDS words
  it["when"]     the when-clause sentence, <= 240 chars
  it["triggers"] concatenated trigger phrases, <= 240 chars
"""
from __future__ import annotations

import re

BODY_MAX_WORDS = 120          # hard cap; ~700 tokens worst case per item
WHEN_MAX_CHARS = 240
TRIGGER_MAX_CHARS = 240

_FM_END = re.compile(r"^---\s*$", re.M)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]*")
# The trigger clause: "Use when ..." / "Use this when ..." / "Use for ..."
_WHEN_RE = re.compile(
    r"(?:use (?:this )?(?:when|whenever|for)|triggers? when|invoke (?:when|if))[:\s].{10,240}",
    re.I)
# Literal quoted trigger phrases inside a description: "janky", 'flaky tests'
_QUOTED_RE = re.compile(r"[\"']([a-z][a-z0-9 +#./-]{3,60})[\"']")


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body_text). No frontmatter -> ("", text)."""
    if not text.startswith("---"):
        return "", text
    m = _FM_END.search(text, 3)
    if not m:
        return "", text
    return text[3:m.start()], text[m.end():]


def extract_body(text: str) -> str:
    """First BODY_MAX_WORDS words of the body, markdown stripped coarsely."""
    _, body = split_frontmatter(text or "")
    # strip code fences and html comments: they are noise for retrieval
    body = re.sub(r"```.*?```", " ", body, flags=re.S)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    # headings and emphasis markers carry words; keep the words, drop the marks
    body = re.sub(r"[#*_`>|]", " ", body)
    words = _WORD.findall(body)
    return " ".join(words[:BODY_MAX_WORDS])


def extract_when(desc: str) -> str:
    """The 'Use when ...' clause of a description, if one exists."""
    m = _WHEN_RE.search(desc or "")
    return m.group(0).strip()[:WHEN_MAX_CHARS] if m else ""


def extract_triggers(desc: str) -> str:
    """Quoted trigger phrases ('janky', 'flaky tests') plus the when-clause."""
    parts = _QUOTED_RE.findall(desc or "")
    when = extract_when(desc)
    if when:
        parts.append(when)
    out = " ".join(parts)[:TRIGGER_MAX_CHARS]
    return out
