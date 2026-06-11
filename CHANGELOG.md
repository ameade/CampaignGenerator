# Changelog

## 2026-06-04 — README rewrite + project analysis

### Added
- Full jcodemunch + jdocmunch index of the repo (2,623 symbols, 489 doc sections)
- CHANGELOG.md, HANDOFF.md, ROADMAP.md (this session)

### Changed
- `README.md` — complete rewrite to reflect current project state:
  - Web UI promoted to first-class feature alongside CLI and RLM
  - Session doc pipeline updated from stale `session_doc.py` to `sd_consistency → sd_plan → sd_narrate → assemble`
  - Script reference expanded with ~15 missing scripts, organized into logical groups
  - Setup switched from bare `pip install` to `uv venv && uv pip install`
  - Model names fixed: `claude-opus-4-7` → `claude-opus-4-8`, Sonnet default named explicitly
  - Startup script added to setup section
  - Docs reference table added

### Meta
- PR #77 opened: https://github.com/kostadis/CampaignGenerator/pull/77
