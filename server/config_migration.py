"""Pure migration: legacy ``ui_config.yaml`` flat dict → typed ``UIState``.

No I/O. The service decides when to call this. Idempotent: a dict already
in the new shape (``version: 2`` present) round-trips through ``UIState``
unchanged with no warnings.

The mapping table is the source of truth for which legacy prefix lands in
which typed section. Every prefix from the pre-refactor
``_SAVE_KEY_PREFIXES`` registry has a home; nothing should reach the
``legacy.unmigrated`` quarantine in normal use.
"""

from __future__ import annotations

from typing import Any

from server.config_models import (
    SCHEMA_VERSION,
    LocalConfig,
    UIState,
)

# Keys that the old ``save_ui_config`` already excluded — runtime/Streamlit
# leftovers. Drop without warnings.
_DROP_KEYS: frozenset[str] = frozenset(
    {
        "sd_server_pid",
        "_refs_initialized",
        "__show_guide__",
        "ui_config_loaded",
        "nav_page",
        "FormSubmitter",
        # Comes from CLI flag / CWD discovery only — never tracked.
        "campaign_dir",
    }
)

# Prefix → (ui section name) for "strip prefix and stash under ui.<section>".
# Ordered longest-first so ``session_doc_`` is matched before ``sd_``-style
# prefixes that happen to share leading letters with another section.
_PREFIX_TO_SECTION: tuple[tuple[str, str], ...] = (
    ("session_doc_", "session_doc"),
    ("distill_", "distill"),
    ("party_", "party"),
    ("plan_", "planning"),
    ("query_", "query"),
    ("prep_", "prep"),
    ("npc_", "npc"),
    ("vtt_", "vtt_summary"),
    ("sw_", "workflow"),
    ("cs_", "campaign_state"),
    ("cg_", "connections"),
    ("sd_", "session_doc"),
)

# Prefix → (experimental subsection name). Each lands under
# ``ui.experimental.<sub>.<rest>``.
_PREFIX_TO_EXPERIMENTAL: tuple[tuple[str, str], ...] = (
    ("narr_", "narrative"),
    ("er_", "enhance_recap"),
    ("dnd_", "dnd_sheet"),
    ("mt_", "make_tracking"),
)

# Reverse of _PREFIX_TO_SECTION — for the step-C ``legacy_values`` shim
# that lets the un-reshaped frontend keep reading flat keys
# (``sd_narrate_tokens`` etc.) while the rest of the system has already
# moved to typed sections. Removed in step F.
_SECTION_TO_PREFIX: dict[str, str] = {
    "session_doc": "sd_",
    "vtt_summary": "vtt_",
    "campaign_state": "cs_",
    "distill": "distill_",
    "party": "party_",
    "planning": "plan_",
    "query": "query_",
    "prep": "prep_",
    "npc": "npc_",
    "workflow": "sw_",
    "connections": "cg_",
}

_EXPERIMENTAL_SUB_TO_PREFIX: dict[str, str] = {
    "narrative": "narr_",
    "enhance_recap": "er_",
    "dnd_sheet": "dnd_",
    "make_tracking": "mt_",
}


def flatten_to_legacy(state: UIState) -> dict[str, Any]:
    """Project a typed ``UIState`` back into the flat ``ui_config.yaml``
    shape the un-reshaped frontend still expects.

    Used by the ``GET /api/config/`` legacy overlay. Stays as long as
    any view reads ``config.values.<prefix>_<field>``; removed once the
    full frontend sweep migrates to ``config.resolved``.
    """
    return _flatten_ui_dict(
        ui=state.ui.model_dump(mode="json"),
        runtime=state.runtime.model_dump(mode="json"),
    )


def flatten_resolved_to_legacy(resolved: dict[str, Any]) -> dict[str, Any]:
    """Same as :func:`flatten_to_legacy` but for the service's ``resolved()``
    view (paths absolute, boot overrides applied).

    Use this in route handlers so that CLI flags like ``--session-dir``
    and ``--narrate-tokens`` flow into the legacy overlay for the lifetime
    of the process — without ever being persisted to disk.
    """
    return _flatten_ui_dict(
        ui=resolved.get("ui", {}),
        runtime=resolved.get("runtime", {}),
    )


def _flatten_ui_dict(ui: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Shared body for :func:`flatten_to_legacy` and
    :func:`flatten_resolved_to_legacy` — operates on plain dicts."""
    out: dict[str, Any] = {}

    for section_name, prefix in _SECTION_TO_PREFIX.items():
        section = ui.get(section_name) or {}
        for field, value in section.items():
            if value is None:
                continue
            out[f"{prefix}{field}"] = value

    experimental = ui.get("experimental") or {}
    for sub_name, sub_prefix in _EXPERIMENTAL_SUB_TO_PREFIX.items():
        sub_value = experimental.get(sub_name)
        if isinstance(sub_value, dict):
            for field, value in sub_value.items():
                if value is None:
                    continue
                out[f"{sub_prefix}{field}"] = value

    grounding = ui.get("grounding") or {}
    if grounding.get("summaries") is not None:
        out["summaries"] = grounding["summaries"]

    if runtime.get("session_dir") is not None:
        out["session_dir"] = runtime["session_dir"]
    if runtime.get("default_model") is not None:
        out["global_model"] = runtime["default_model"]

    return out


# Top-level keys (no prefix) that have explicit homes.
_TOP_LEVEL: dict[str, tuple[str, str]] = {
    # legacy_key → (root_section, field)
    "summaries": ("grounding", "summaries"),
    "session_dir": ("runtime", "session_dir"),
    "global_model": ("runtime_named", "default_model"),
}


def _looks_already_migrated(legacy: dict) -> bool:
    """A dict produced by this migrator (or hand-written in v2 form) has
    ``version`` and ``ui`` at the top level."""
    return legacy.get("version") == SCHEMA_VERSION and "ui" in legacy


def migrate_ui_config(legacy: dict) -> tuple[UIState, list[str]]:
    """Convert a flat legacy ``ui_config.yaml`` dict into a typed ``UIState``.

    Returns the model plus a list of human-readable warnings. Pure function;
    safe to call from anywhere.
    """
    warnings: list[str] = []

    if _looks_already_migrated(legacy):
        # Already in v2 shape — round-trip through the model and return.
        return UIState.model_validate(legacy), warnings

    # Build the nested dict that UIState will validate.
    ui: dict[str, dict[str, Any]] = {}
    runtime: dict[str, Any] = {}
    unmigrated: dict[str, Any] = {}

    # Track per-section which fields have been written and from which legacy
    # key, so duplicate-collapse (sd_voice_dir + session_doc_voice_dir) can
    # warn instead of silently picking one.
    written: dict[tuple[str, str], str] = {}

    def _stash(section: str, field: str, value: Any, source_key: str) -> None:
        prior = written.get((section, field))
        if prior is not None:
            # Newer prefix wins. Migration prefix order puts the legacy
            # ``session_doc_`` ahead of ``sd_`` in the iteration, so we
            # need to compare prefixes explicitly: ``sd_*`` is the canonical
            # generation and overrides ``session_doc_*``.
            if prior.startswith("session_doc_") and source_key.startswith("sd_"):
                warnings.append(
                    f"collapsed duplicate keys '{prior}' and '{source_key}' "
                    f"into ui.{section}.{field}; kept '{source_key}'"
                )
                ui.setdefault(section, {})[field] = value
            else:
                # First-write wins for non-conflicting prefix orders.
                warnings.append(
                    f"collapsed duplicate keys '{prior}' and '{source_key}' "
                    f"into ui.{section}.{field}; kept '{prior}'"
                )
            return
        ui.setdefault(section, {})[field] = value
        written[(section, field)] = source_key

    for raw_key, value in legacy.items():
        if raw_key in _DROP_KEYS:
            continue

        # Top-level explicit mappings first.
        if raw_key in _TOP_LEVEL:
            root, field = _TOP_LEVEL[raw_key]
            if root == "runtime_named":
                runtime[field] = value
            elif root == "runtime":
                runtime[field] = value
            elif root == "grounding":
                ui.setdefault("grounding", {})[field] = value
            continue

        # Experimental subsection prefixes (narr_, er_, dnd_, mt_).
        matched = False
        for prefix, sub in _PREFIX_TO_EXPERIMENTAL:
            if raw_key.startswith(prefix):
                rest = raw_key[len(prefix):] or "_value"
                ui.setdefault("experimental", {}).setdefault(sub, {})[rest] = value
                matched = True
                break
        if matched:
            continue

        # Standard ui.<section> prefixes.
        for prefix, section in _PREFIX_TO_SECTION:
            if raw_key.startswith(prefix):
                field = raw_key[len(prefix):]
                if not field:
                    # Bare prefix as a key (e.g. ``sd_:`` with no field) —
                    # nothing useful to do; quarantine.
                    unmigrated[raw_key] = value
                    warnings.append(
                        f"key '{raw_key}' has no field after prefix; "
                        f"quarantined under legacy.unmigrated"
                    )
                else:
                    _stash(section, field, value, raw_key)
                matched = True
                break
        if matched:
            continue

        # Anything else: quarantine with a warning so the user can see it.
        unmigrated[raw_key] = value
        warnings.append(
            f"unknown legacy key '{raw_key}' preserved under "
            f"legacy.unmigrated for review"
        )

    raw: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "ui": ui,
        "runtime": runtime,
        "legacy": {"unmigrated": unmigrated},
    }

    # Pydantic does the type coercion ('4000' → 4000, etc.). If a coercion
    # fails (e.g. a non-numeric narrate_tokens), surface it as a warning and
    # drop the offending field rather than blowing up the whole migration.
    try:
        state = UIState.model_validate(raw)
    except Exception as exc:  # pragma: no cover — exercised via tests
        warnings.append(
            f"some legacy values failed type coercion ({exc}); "
            f"falling back to defaults for those fields"
        )
        state = _validate_with_quarantine(raw, warnings)

    return state, warnings


def _validate_with_quarantine(raw: dict, warnings: list[str]) -> UIState:
    """Best-effort: try the whole dict; on failure, drop offending leaves
    into ``legacy.unmigrated`` and try again.

    Pydantic's ValidationError carries the path of every failing field. We
    walk those, remove them from the input, and quarantine the original
    value so nothing is silently dropped.
    """
    from pydantic import ValidationError

    quarantine: dict[str, Any] = raw.setdefault("legacy", {}).setdefault("unmigrated", {})

    for _ in range(8):  # bounded; each pass either succeeds or drops a field
        try:
            return UIState.model_validate(raw)
        except ValidationError as exc:
            progressed = False
            for err in exc.errors():
                loc = err.get("loc", ())
                if len(loc) < 2:
                    continue
                # loc looks like ('ui', 'session_doc', 'narrate_tokens')
                # or ('runtime', 'session_dir').
                cursor: Any = raw
                for part in loc[:-1]:
                    cursor = cursor.get(part) if isinstance(cursor, dict) else None
                    if cursor is None:
                        break
                if isinstance(cursor, dict) and loc[-1] in cursor:
                    bad_value = cursor.pop(loc[-1])
                    flat_key = ".".join(str(p) for p in loc)
                    quarantine[flat_key] = bad_value
                    warnings.append(
                        f"value at {flat_key!r} ({bad_value!r}) failed type "
                        f"validation; quarantined under legacy.unmigrated"
                    )
                    progressed = True
            if not progressed:
                break

    # Last resort: discard ui/runtime entirely so we can at least construct.
    return UIState(legacy={"unmigrated": quarantine})


def migrate_local_config(legacy: dict) -> tuple[LocalConfig, list[str]]:
    """Migrate a legacy local-only payload into ``LocalConfig``.

    Today there is no separate local file in the legacy layout — the
    information that will live there (``server.host``, ``server.port``,
    ``nav.last_page``) was never persisted in ``ui_config.yaml``. This
    function exists so the service has a uniform shape for both
    documents during step B/C of the rollout.
    """
    warnings: list[str] = []
    if not legacy:
        return LocalConfig(), warnings
    return LocalConfig.model_validate(legacy), warnings
