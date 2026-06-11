# Handoff

## Current State

**Branch:** `docs/update-readme-and-about`
**PR:** https://github.com/kostadis/CampaignGenerator/pull/77 — open, not merged
**Last commit:** `c6e4cb2` — README rewrite (uv + venv, sd_*.py pipeline, Web UI, model names)

The codebase itself is unchanged — this session was analysis and documentation only.

### Health snapshot (as of 2026-06-04)
- Overall grade: **C (76.3/100)**
- 0 dependency cycles ✅
- 45 unstable modules ⚠️ (inherent to flat CLI architecture, not actionable)
- Top hotspot: `planning.py::main` (cc=84, 18 commits) — prime refactor candidate
- Dead code: 5.6% — mostly false positives in Vue frontend (router-mounted components)
- 138 broken doc links — maintenance debt in `docs/`

### Indexes
- jcodemunch: `kostadis/CampaignGenerator` — 2,623 symbols, 170 files
- jdocmunch: `local/CampaignGenerator` — 489 sections, 33 docs

## What's In Progress / Blocked

- PR #77 needs review and merge by repo owner (kostadis)
- No code changes this session — nothing blocked

## Most Important Thing Next Session Should Know

A new project (`/opt/proj/campaign-forge`) was scoped this session. It builds the **self-hosted infrastructure layer** beneath CampaignGenerator:

- **Kanka Community Edition** — self-hosted world-state store with native MCP
- **Foundry VTT + foundry-vtt-mcp** — self-hosted VTT with 37 MCP tools for Claude
- **dnd-llm-game** — local AI + LanceDB RAG over PDF rulebooks (Ollama, no cloud)
- **Fantasy Map Generator** — self-hosted map generation, GeoJSON export

CampaignGenerator is NOT superseded. It becomes the application-layer brain that reads from and writes back to these tools. Full handoff and build plan is at `/opt/proj/campaign-forge/HANDOFF.md`.

## Next Session

1. Merge or follow up on PR #77
2. Switch to `/opt/proj/campaign-forge` and start Phase 1:
   - Clone Kanka CE, Docker compose up
   - Audit `.mcp.json` — does it expose entity CRUD?
   - Write first Kanka CE integration test

## Environment Notes

- `git remote` is SSH (`git@github.com:williamblair333/CampaignGenerator.git` fork of `kostadis/CampaignGenerator`)
- `gh auth` is active as `williamblair333`
- Always use `uv venv + uv pip install` for Python deps
