#!/usr/bin/env python3
"""Aggregate exhaustive atomic facts into per-entity current-state dossiers.

The ensemble (`ensemble_extract.py`) produces tens of thousands of atomic
facts — far too many to feed `synthesise_world_state.py` directly. This script
is the missing COMPRESSION layer: it bundles every fact about one entity and
asks a local model to collapse them into a single current-state dossier
(`distill_extractions` density), with an explicit Uncertainty block so a human
can correct scope/ordering/attribution before the dossiers feed synthesis.

Two stages:

  1. BUNDLE (deterministic, no LLM): group facts by (type, canonical subject),
     ordered by CHAPTER index (not in-world date), keeping only "stateful"
     entity types above a fact-count floor. `--list` stops here so you can see
     exactly what will be aggregated — the human checkpoint on scope.

  2. AGGREGATE (local model): per entity, feed its facts in chapter order to the
     `state_aggregate` prompt -> one dossier .md per entity in --out-dir.

Examples:
  # See the bundles (no model call) — sorted by fact count
  python facts_to_state.py --corpus 'scratch_output/full-oota/gen-ch*/merged.json' --list

  # Prototype: aggregate one dense entity on the local Spark
  python facts_to_state.py --corpus 'scratch_output/full-oota/gen-ch*/merged.json' \
      --only Daz --out-dir scratch_output/state-proto \
      --dgx-endpoint http://192.168.1.147:8001/v1 \
      --model Qwen/Qwen3-Next-80B-A3B-Instruct-FP8

  # Top 10 densest entities
  python facts_to_state.py --corpus '...' --top 10 --out-dir scratch_output/state-proto \
      --dgx-endpoint http://192.168.1.147:8001/v1 --model Qwen/...
"""

import argparse
import queue
import re
import sys
import threading
from collections import Counter
from pathlib import Path

from campaignlib import (
    DEFAULT_MODEL,
    load_agent_prompt,
    make_client,
    stream_api,
)
from ensemble_merge import _norm_subject
from synthesise_world_state import (
    expand_globs,
    load_aliases,
    session_index,
    session_label,
)

AGGREGATE_SYSTEM = load_agent_prompt("state_aggregate")

# Entity types worth aggregating into a state model. event/date/thread are
# cross-cutting (history / temporal anchors / mysteries) and handled on a
# separate track, not per-entity.
STATEFUL_TYPES = ["npc", "faction", "location", "object", "monster"]

_INT_RE = re.compile(r"\d+")


def chapter_num(path: Path) -> int:
    """Integer chapter index from a merged.json's label (gen-ch03 -> 3)."""
    nums = _INT_RE.findall(session_label(path))
    return int(nums[0]) if nums else 0


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "entity"


class Bundle:
    """All facts about one (type, canonical-subject), in chapter order."""

    def __init__(self, type_: str, key: str):
        self.type = type_
        self.key = key
        self.facts: list[tuple[int, dict]] = []   # (chapter, fact)
        self._names: Counter = Counter()

    def add(self, chapter: int, fact: dict, display: str) -> None:
        self.facts.append((chapter, fact))
        self._names[display] += 1

    @property
    def display(self) -> str:
        return self._names.most_common(1)[0][0]

    @property
    def chapters(self) -> tuple[int, int]:
        chs = [c for c, _ in self.facts]
        return (min(chs), max(chs))

    def ordered(self) -> list[tuple[int, dict]]:
        # Stable sort by chapter; preserves within-chapter merged.json order.
        return sorted(self.facts, key=lambda cf: cf[0])


def load_bundles(corpus_paths: list[Path], aliases: dict[str, str],
                 types: list[str]) -> dict[str, Bundle]:
    """Group facts across all corpus files by (type, canonical subject)."""
    import json
    bundles: dict[str, Bundle] = {}
    type_set = set(types)
    # Load files in chronological (chapter) order so chapter tags are right.
    for path in sorted(corpus_paths, key=session_index):
        ch = chapter_num(path)
        try:
            facts = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  warn: skipping {path}: {e}", file=sys.stderr)
            continue
        for f in facts:
            t = f.get("type", "")
            if t not in type_set:
                continue
            raw = (f.get("subject") or "").strip()
            if not raw:
                continue
            display = aliases.get(raw, raw)
            norm = _norm_subject(display)
            if not norm:
                continue
            gkey = f"{t}\x00{norm}"
            b = bundles.get(gkey)
            if b is None:
                b = bundles[gkey] = Bundle(t, norm)
            b.add(ch, f, display)
    return bundles


def select(bundles: dict[str, Bundle], min_facts: int, only: str | None,
           top: int | None) -> list[Bundle]:
    items = [b for b in bundles.values() if len(b.facts) >= min_facts]
    if only is not None:
        target = _norm_subject(only)
        items = [b for b in items if b.key == target]
    items.sort(key=lambda b: (-len(b.facts), b.type, b.display.lower()))
    if top is not None:
        items = items[:top]
    return items


def build_user_prompt(b: Bundle, with_quotes: bool) -> str:
    lo, hi = b.chapters
    span = f"chapter {lo}" if lo == hi else f"chapters {lo}-{hi}"
    head = (f"ENTITY: {b.display}   (type: {b.type})\n"
            f"{len(b.facts)} fact(s) across {span}.\n\n"
            "FACTS (chronological — earliest chapter first; later overrides "
            "earlier for current state):\n")
    lines = [head]
    for ch, f in b.ordered():
        lines.append(f"- [ch{ch:02d}] {f.get('fact', '').strip()}")
        if with_quotes:
            q = " ".join((f.get("source_quote") or "").split())
            if q:
                lines.append(f'  > "{q}"')
    return "\n".join(lines)


def dossier_path(out_dir: Path, b: Bundle) -> Path:
    return out_dir / f"{b.type}_{slugify(b.display)}.md"


def render_bundles(bundles: list[Bundle], with_quotes: bool) -> str:
    """Deterministic markdown dump of bundles, grouped by type then subject,
    facts in chapter order. Used for the cross-cutting threads/events track —
    facts that don't aggregate per-entity but feed the synthesis directly."""
    by_type: dict[str, list[Bundle]] = {}
    for b in bundles:
        by_type.setdefault(b.type, []).append(b)
    out: list[str] = []
    for t in sorted(by_type):
        out.append(f"## {t.title()}s\n")
        for b in sorted(by_type[t], key=lambda b: b.display.lower()):
            out.append(f"### {b.display}")
            for ch, f in b.ordered():
                out.append(f"- [ch{ch:02d}] {f.get('fact', '').strip()}")
                if with_quotes:
                    q = " ".join((f.get("source_quote") or "").split())
                    if q:
                        out.append(f'  > "{q}"')
            out.append("")
    return "\n".join(out)


def write_dossier(out_dir: Path, b: Bundle, body: str) -> Path:
    lo, hi = b.chapters
    fm = (f"---\nname: {b.display}\ntype: {b.type}\n"
          f"n_facts: {len(b.facts)}\nchapters: {lo}-{hi}\n---\n\n")
    dest = dossier_path(out_dir, b)
    dest.write_text(fm + body.strip() + "\n", encoding="utf-8")
    return dest


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", required=True, nargs="+", metavar="GLOB",
                   help="merged.json glob(s) (e.g. 'scratch_output/full-oota/gen-ch*/merged.json')")
    p.add_argument("--out-dir", metavar="DIR",
                   help="Where to write per-entity dossiers (required unless --list)")
    p.add_argument("--aliases", metavar="FILE", default=None,
                   help="aliases.json ({canonical: [variants]}) for subject canonicalisation")
    p.add_argument("--types", nargs="+", default=STATEFUL_TYPES, metavar="TYPE",
                   help=f"Entity types to aggregate (default: {' '.join(STATEFUL_TYPES)})")
    p.add_argument("--min-facts", type=int, default=3, metavar="N",
                   help="Only aggregate entities with at least N facts (default 3); "
                        "below this there's nothing to collapse.")
    p.add_argument("--only", metavar="NAME", default=None,
                   help="Aggregate only the entity whose normalised name matches NAME (prototype).")
    p.add_argument("--top", type=int, default=None, metavar="N",
                   help="Aggregate only the N densest entities (prototype).")
    p.add_argument("--list", action="store_true",
                   help="Stage 1 only: print the selected bundles (no model call). "
                        "The human checkpoint on what will be aggregated.")
    p.add_argument("--render-only", metavar="FILE", default=None,
                   help="Deterministic dump of the selected bundles to FILE as "
                        "grouped markdown (no model call). Used to build the "
                        "cross-cutting threads/events track for synthesis "
                        "(e.g. --types thread --min-facts 2 --render-only threads.md).")
    p.add_argument("--quotes", action=argparse.BooleanOptionalAction, default=True,
                   help="Include source_quote lines in the model input (default on).")
    p.add_argument("--dgx-endpoint", default=None, metavar="URL",
                   help="Single OpenAI-compatible local endpoint (else Anthropic API).")
    p.add_argument("--endpoints", nargs="+", default=None, metavar="URL",
                   help="Multiple endpoints to fan out across concurrently (one worker per "
                        "endpoint, work-stealing). Overrides --dgx-endpoint. All must serve --model.")
    p.add_argument("--model", default=None, metavar="ID",
                   help=f"Model id (default: $DGX_MODEL on a DGX endpoint, else {DEFAULT_MODEL}).")
    p.add_argument("--max-tokens", type=int, default=8000, metavar="N",
                   help="max_tokens per aggregation call (default 8000).")
    args = p.parse_args()

    if not args.list and not args.render_only and not args.out_dir:
        p.error("--out-dir is required unless --list / --render-only")

    corpus = expand_globs(args.corpus)
    if not corpus:
        print("Error: no corpus files matched.", file=sys.stderr)
        sys.exit(1)
    aliases = load_aliases(Path(args.aliases).expanduser()) if args.aliases else {}

    bundles = load_bundles(corpus, aliases, args.types)
    selected = select(bundles, args.min_facts, args.only, args.top)

    total_entities = len(bundles)
    print(f"Corpus:   {len(corpus)} file(s)")
    print(f"Entities: {total_entities} of types {args.types} "
          f"(>= {args.min_facts} facts: {sum(1 for b in bundles.values() if len(b.facts) >= args.min_facts)})")
    print(f"Selected: {len(selected)} for aggregation")
    print("=" * 60)

    if args.render_only:
        md = render_bundles(selected, args.quotes)
        dest = Path(args.render_only).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md, encoding="utf-8")
        print(f"Rendered {len(selected)} bundle(s) -> {dest} "
              f"(~{len(md)//4:,} tokens)")
        return

    if args.list or not selected:
        for b in selected:
            lo, hi = b.chapters
            print(f"  {len(b.facts):>5}  {b.type:9s}  {b.display}  (ch {lo}-{hi})")
        if not selected:
            print("(nothing selected)")
        return

    model = args.model
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resumable: skip entities whose dossier already exists (survives sleeps /
    # interrupts; re-run to fill in the rest).
    todo = [b for b in selected if not dossier_path(out_dir, b).exists()]
    already = len(selected) - len(todo)

    endpoints = args.endpoints or [args.dgx_endpoint]
    where = ", ".join(e or "Anthropic API" for e in endpoints)
    print(f"Aggregating {len(todo)} entitie(s) on {where} (model: {model or 'default'})"
          f"{f'; {already} already done (skipped)' if already else ''}\n")

    work: queue.Queue = queue.Queue()
    for item in enumerate(todo, 1):
        work.put(item)
    lock = threading.Lock()
    done = 0
    errors: list[str] = []

    def worker(endpoint: str | None) -> None:
        nonlocal done
        client = make_client(endpoint=endpoint, model_override=model)
        while True:
            try:
                i, b = work.get_nowait()
            except queue.Empty:
                return
            try:
                user = build_user_prompt(b, args.quotes)
                body = stream_api(client, AGGREGATE_SYSTEM, user, model,
                                  max_tokens=args.max_tokens, silent=True)
                write_dossier(out_dir, b, body)
                with lock:
                    done += 1
                    print(f"[{done}/{len(todo)}] {b.type}: {b.display} "
                          f"({len(b.facts)} facts) -> {dossier_path(out_dir, b).name}")
            except Exception as e:  # one bad entity must not kill the batch
                with lock:
                    errors.append(f"{b.type}:{b.display}: {e!r}")
                    print(f"  ERROR {b.type}: {b.display}: {e!r}", file=sys.stderr)

    threads = [threading.Thread(target=worker, args=(ep,), daemon=True) for ep in endpoints]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"\nWrote {done} dossier(s) to {out_dir} ({already} pre-existing)")
    if errors:
        print(f"{len(errors)} entitie(s) failed (re-run to retry):", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
    print("REVIEW these (esp. the ## Uncertainty blocks) before synthesis.")


if __name__ == "__main__":
    main()
