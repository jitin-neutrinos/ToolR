#!/usr/bin/env python3
"""aliases.py — the vocabulary bridge.

WHY THIS EXISTS (measured 2026-10-05)
--------------------------------------
The eval harness scored the shipped router at recall@1 = 0.1056 with
`unreachable@10` = 0.642: for 64% of real prompts taken from 983 live sessions,
the correct capability shared ZERO tokens with its own label. The failure is
vocabulary, not ranking. Concretely, asking for

    "identify the best 20 frontend design and animation design skills"

returned design skills reliably but ZERO animation skills, because "animation"
does not appear in any framer-motion-* or gsap-* description in a form BM25 can
match, and the library names (framer, gsap, locomotive, lottie, three.js) are
what actually disambiguate those skills from each other.

Two mechanisms live here, both pure data + pure functions:

  1. SKILL_ALIASES: capability name -> extra query terms that should score it.
     Applied at INDEX time (tokens are baked into the item) so BM25, the dense
     lane and the Laya rerank all see them. One change, all three lanes.

  2. CONCEPT_TERMS: user words -> canonical concept tokens, applied at TOKENIZE
     time. This is the "people don't say library names" bridge: "smooth
     scrolling" -> locomotive, "frosted glass" -> glassmorphism.

Everything here is deterministic, dependency-free and covered by
selftest.py::t_alias_coverage.

DESIGN RULES (learned the hard way, do not violate)
----------------------------------------------------
* Aliases are ADDITIVE tokens, never replacements. Replacing a word loses the
  user's own vocabulary.
* Do not alias a capability to a GENERIC token ("design", "build"). GENERIC is
  damped in the score AND its denominator; adding generic terms moves nothing.
* Keep every alias a term a human would plausibly type. "backdrop-filter" is
  good; "css3" is noise.
* Prefer MANY precise terms over few broad ones. A capability with 12 aliases
  beats one with 3 vague ones, because IDF does the discrimination.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# 1. Capability -> alias terms. Keys are lower-cased capability names.
#    Value is a SPACE-SEPARATED string, added verbatim to the item's tokens.
#    Grouped by domain so a future maintainer can extend one block.
# --------------------------------------------------------------------------

SKILL_ALIASES: dict[str, str] = {

    # ---------------------------------------------------- animation: framer
    "framer-motion-core": "animation motion react spring transition ease transform "
                          "animate component interactive",
    "framer-motion-react": "animation motion react presence exit enter transition "
                           "animate component wrapper",
    "framer-motion-variants": "animation variants stagger sequence orchestrator "
                              "state machine repeat keyframes orchestrated",
    "framer-motion-layout": "animation layout shared layoutid layout transition "
                            "flip morph resize reorder grid",
    "framer-motion-scroll": "animation scroll parallax scrolltrigger useScroll "
                            "scroll-linked scrub viewport progress",
    "framer-motion-gestures": "animation gesture drag pan swipe tap hover focus "
                              "touch draggable press",
    "motion-framer": "animation motion react spring keyframe easing library "
                     "whats-new replaces popmotion",

    # ---------------------------------------------------- animation: gsap
    "gsap-core": "animation gsap tween timeline easing duration stagger callback",
    "gsap-scrolltrigger": "animation gsap scroll scrolltrigger pin scrub parallax "
                          "scrollytelling trigger enter leave",
    "gsap-timeline": "animation gsap timeline position parameter nesting playback "
                     "sequence seek",
    "gsap-performance": "animation gsap performance optimize fps transform "
                        "will-change batching compositor",
    "gsap-react": "animation gsap react hook usegsap context cleanup lifecycle",
    "gsap-frameworks": "animation gsap vue svelte angular lifecycle scoping",
    "gsap-plugins": "animation gsap plugin register scrollto smoother flip draggable",
    "gsap-utils": "animation gsap utils clamp maprange normalize interpolate "
                  "random snap wrap",
    "gsap-web": "animation gsap scroll website scrolltrigger web",

    # ---------------------------------------------------- animation: other
    "locomotive-scroll": "animation smooth scrolling parallax scrolltrigger "
                         "scrollytelling virtual scroll lenis inertial",
    "lottie-animation": "animation lottie bodymovin json rive player playback "
                        "loop after-export vector",
    "lottiefiles": "animation lottie lottiefiles library search fetch assets "
                   "free json",
    "svg-animation": "animation svg stroke dashoffset line draw path morph "
                     "logo animation vector draw-on",
    "ascii-animation": "animation ascii terminal intro loader retro cli text",
    "micro-interaction": "animation micro-interaction hover press ripple toggle "
                         "switch feedback affordance tactile",
    "page-transition-animation": "animation page transition route navigate "
                                 "view-transition shared-element enter exit",
    "60fps-animation": "animation performance 60fps jank lag stutter layout "
                       "thrash compositor transform optimize smooth",
    "accessible-animation": "animation accessibility a11y reduced-motion "
                            "prefers-reduced-motion vestibular vestibular-safe "
                            "respect-reduced-motion",
    "threejs-webgl": "3d webgl three.js threejs scene camera render 3d web",
    "react-three-fiber": "3d r3f react-three-fiber three fiber scene declarative "
                         "3d react",
    "manim-video": "animation video math 3blue1brown explainer render animation",

    # ---------------------------------------------------- design: opinion
    "frontend-design": "design frontend ui visual aesthetic taste polish premium "
                       "beautiful modern elegant crafted distinctive art-direction",
    "design-taste-frontend": "design taste frontend anti-slop generic bland "
                             "ai-slop distinctive art-direction craft brief",
    "emil-design-eng": "design polish interaction craft motion taste restraint "
                       "detail microcopy typography rhythm",
    "impeccable": "design polish critique refine harden optimize distill "
                  "clarify sharpen visual-craft design-review redesign",
    "supanova-premium-aesthetic": "design premium agency expensive luxury high-end "
                                  "floating-island typography spacing shadow "
                                  "premium-aesthetic $150k",
    "ui-ux-pro-max": "design ui ux interface mobile desktop dashboard layout "
                     "wireframe usability",
    "popular-web-designs": "design reference stripe linear vercel notion figma "
                           "tailwindui design-system real-world example",
    "web-design-guidelines": "design accessibility a11y guidelines aria keyboard "
                             "focus contrast compliance usability heuristic",
    "claude-design": "design html artifact landing page deck prototype "
                     "single-file showcase",
    "design-md": "design tokens spec material design-md token export validate",
    "glassmorphism": "design frosted glass blur backdrop-filter translucent "
                     "backdrop-blur liquid-glass apple-glass",
    "tailwind": "design tailwind utility-first css theme tokens tailwindcss "
                "responsive dark-mode",
    "jitinnair-ui": "design brand component registry jitinnair house-style "
                    "on-brand branded",
    "neutrinos-brand-core": "design brand neutrinos logo colors typography "
                            "brand-system on-brand voice",
    "neutrinos-component": "design branded component loader button tag card icon "
                           "neutrinos",
    "neutrinos-web": "design branded website landing microsite dashboard "
                     "on-brand web",
    "neutrinos-social": "design social post banner cover avatar linkedin "
                        "facebook branded",
    "neutrinos-print": "design print pdf brochure flyer poster business-card "
                       "printable",
    "neutrinos-presentations": "design slides deck pptx presentation pitch "
                               "branded-slides",
    "neutrinos-documents": "design document handbook playbook manual report "
                           "branded-pdf",

    # ---------------------------------------------------- frontend craft
    "frontend-layout-measurement": "frontend layout measure overflow clipping "
                                   "off-centre bleed alignment measurement",
    "21st-ui-review": "design ui accessibility responsive design-system "
                      "a11y heuristic usability interface critique 21st",
    "21st-design-sync": "design tokens shadcn tailwind sync publish design-system "
                        "21st",
    "21st-cli-use": "design component install pull catalog 21st shadcn registry",
    "kpi-dashboard-design": "design dashboard kpi metric chart monitoring "
                            "dashboard realtime visualization",
    "hermes-web-ui-qa": "frontend qa web ui verify screenshot dom chat "
                        "interface regression render-assert visual-proof",
    "astra-webui": "frontend astra chat web ui dashboard harness streaming",
    "astra-webui-performance": "frontend performance astra chat lag typing "
                               "streaming optimize",
    "render-defect-triage": "frontend render blank overlapping clipped defect "
                            "triage blank-screen",
    "opentui": "tui terminal ui opentui solid react core components",

    # ---------------------------------------------------- graphics / media
    "baoyu-infographic": "design infographic visual data diagram layout "
                         "information-design chart-visual",
    "architecture-diagram": "design diagram architecture infrastructure svg "
                            "cloud topology",
    "excalidraw": "diagram excalidraw flowchart sketch whiteboard wireframe",
    "manim-video": "animation video math explainer 3blue1brown render",

    # ---------------------------------------------------- infra / craft
    "systematic-debugging": "debug root-cause investigate reproduce trace "
                            "why-failing bug-hunt isolate regression",
    "bug-fix": "bug defect repair patch correct failing wrong-behavior",
    "diagnosing-bugs": "bug diagnose investigate debug isolate failing "
                       "symptom repro",
}

# --------------------------------------------------------------------------
# 2. Concept bridge: what a user TYPES -> canonical concepts added to the query.
#    Applied in tokenize(). Small, high-value, and the opposite direction to
#    SKILL_ALIASES (which is capability -> terms).
# --------------------------------------------------------------------------

CONCEPT_TERMS: dict[str, str] = {
    # motion words that must reach a motion library
    "animate": "animation motion",
    "animation": "animation motion",
    "animations": "animation motion",
    "animated": "animation motion",
    "motion": "animation motion",
    "transition": "animation motion transition",
    "transitions": "animation motion transition",
    "animate": "animation motion",
    "easing": "animation easing ease",
    "ease": "animation easing ease",
    "spring": "animation spring physics",
    "stagger": "animation stagger sequence",
    "keyframe": "animation keyframes timeline",
    "keyframes": "animation keyframes timeline",
    "parallax": "animation parallax scroll parallax",
    "scrollytelling": "animation scroll scrolltelling scrollytelling",
    "marquee": "animation marquee ticker loop",
    "kenburns": "animation kenburns zoompan",
    "morph": "animation morph shared-element",
    "flip": "animation flip transform",

    # scroll words
    "scroll": "scroll animation",
    "scrolling": "scroll animation",
    "smooth": "scroll smooth locomotive lenis",

    # hover / press affordances
    "hover": "hover micro-interaction",
    "ripple": "ripple micro-interaction",
    "toggle": "toggle micro-interaction switch",

    # visual treatments
    "frosted": "glass frosted blur",
    "blur": "blur backdrop-filter glass",
    "glassmorphism": "glass frosted backdrop-filter",
    "premium": "premium luxury high-end polish",
    "luxury": "premium luxury high-end",
    "expensive": "premium high-end",
    "elegant": "elegant refined polish",
    "refined": "elegant refined polish",
    "minimal": "minimal restrained clean",
    "restrained": "minimal restrained",
    "slop": "anti-slop distinctive craft",
    "generic": "anti-slop distinctive",
    "bland": "anti-slop distinctive",

    # performance words that must reach the perf skills
    "jank": "jank lag performance fps",
    "janky": "jank lag performance fps",
    "laggy": "lag performance",
    "lag": "lag performance",
    "stutter": "jank performance fps stuttering",
    "stuttering": "jank performance fps stuttering",
    "choppy": "jank performance fps",
    "smoothness": "performance fps 60fps",
    "flicker": "flicker performance paint",
    "flickering": "flicker performance paint",
    "60fps": "60fps performance",
    "framerate": "fps performance 60fps",
    "dropframe": "fps performance jank",

    # accessibility
    "reduced": "reduced-motion accessibility",
    "accessibility": "accessibility a11y",
    "a11y": "accessibility a11y",
    "wcag": "accessibility wcag a11y",

    # 3d
    "3d": "3d webgl three",
    "webgl": "3d webgl three",
    "threejs": "3d three webgl",
}

# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------


def alias_terms_for(name: str) -> list[str]:
    """Extra index-time terms for a capability. [] when it has no aliases."""
    return (SKILL_ALIASES.get((name or "").strip().lower()) or "").split()


def concept_terms(text: str) -> list[str]:
    """Query-time concept terms for raw user text (not normalized tokens)."""
    import re
    low = (text or "").lower()
    out: list[str] = []
    seen = set()
    for raw in re.findall(r"[a-z0-9][a-z0-9.+#-]{1,24}", low):
        key = raw.strip(".-+#")
        if not key or key in seen:
            continue
        seen.add(key)
        extra = CONCEPT_TERMS.get(key)
        if extra:
            out.extend(t for t in extra.split() if t not in seen)
            seen.update(extra.split())
    return out


def coverage_report() -> dict:
    """Self-audit: how many capabilities carry aliases, and any key typos.

    `typos` is the important field: a key that does not match any real
    capability name is dead weight, and it is the single most likely mistake
    when editing this file by hand.
    """
    import json
    import os
    from pathlib import Path as _P
    idx_path = _P(os.path.expanduser("~/.tool-router/index.json"))
    try:
        items = json.loads(idx_path.read_text()).get("items") or []
    except (OSError, ValueError):
        items = []
    names = {(i.get("name") or "").strip().lower() for i in items}
    known = {k for k in SKILL_ALIASES if k in names}
    return {
        "indexed_items": len(items),
        "alias_keys": len(SKILL_ALIASES),
        "keys_matching_index": len(known),
        "keys_not_in_index": sorted(set(SKILL_ALIASES) - names),
        "concept_keys": len(CONCEPT_TERMS),
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(coverage_report(), indent=1))