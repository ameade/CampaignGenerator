"""Tests for server/config_migration.py — pure legacy → typed migration.

These tests freeze the contract for what happens to every prefix family
the old ``_SAVE_KEY_PREFIXES`` registry knew about, plus the worked
example from docs/configuration.md and the duplicate-collapse case.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config_migration import migrate_local_config, migrate_ui_config
from server.config_models import SCHEMA_VERSION, UIState


class TestWorkedExample:
    """The bug that motivated the whole refactor."""

    def test_stringy_narrate_tokens_becomes_int(self):
        legacy = {"sd_narrate_tokens": "4000"}
        state, warnings = migrate_ui_config(legacy)
        assert state.ui.session_doc.narrate_tokens == 4000
        assert isinstance(state.ui.session_doc.narrate_tokens, int)
        # No warning needed — pydantic coercion is silent.
        assert warnings == []


class TestPrefixRouting:
    def test_sd_routes_to_session_doc(self):
        state, _ = migrate_ui_config({"sd_voice_dir": "voice/"})
        assert state.ui.session_doc.voice_dir == "voice/"

    def test_vtt_routes_to_vtt_summary(self):
        state, _ = migrate_ui_config({"vtt_input": "session.vtt"})
        assert state.ui.vtt_summary.input == "session.vtt"

    def test_cs_routes_to_campaign_state(self):
        state, _ = migrate_ui_config({"cs_output": "docs/campaign_state.md"})
        assert state.ui.campaign_state.model_dump()["output"] == "docs/campaign_state.md"

    def test_distill_routes_to_distill(self):
        state, _ = migrate_ui_config({"distill_output": "docs/world_state.md"})
        assert state.ui.distill.model_dump()["output"] == "docs/world_state.md"

    def test_party_routes_to_party(self):
        state, _ = migrate_ui_config({"party_mode": "summarize"})
        assert state.ui.party.model_dump()["mode"] == "summarize"

    def test_plan_routes_to_planning(self):
        state, _ = migrate_ui_config({"plan_npc": "docs/npcs/foo.md"})
        assert state.ui.planning.model_dump()["npc"] == "docs/npcs/foo.md"

    def test_cg_routes_to_connections(self):
        state, _ = migrate_ui_config({"cg_threshold": 3})
        assert state.ui.connections.model_dump()["threshold"] == 3

    def test_sw_routes_to_workflow(self):
        state, _ = migrate_ui_config({"sw_step": 2})
        assert state.ui.workflow.model_dump()["step"] == 2

    def test_narr_routes_to_experimental_narrative(self):
        state, _ = migrate_ui_config({"narr_mode": "lyric"})
        exp = state.ui.experimental.model_dump()
        assert exp["narrative"] == {"mode": "lyric"}

    def test_er_routes_to_experimental_enhance_recap(self):
        state, _ = migrate_ui_config({"er_session": "5"})
        exp = state.ui.experimental.model_dump()
        assert exp["enhance_recap"] == {"session": "5"}

    def test_dnd_routes_to_experimental_dnd_sheet(self):
        state, _ = migrate_ui_config({"dnd_pdf": "char.pdf"})
        exp = state.ui.experimental.model_dump()
        assert exp["dnd_sheet"] == {"pdf": "char.pdf"}

    def test_mt_routes_to_experimental_make_tracking(self):
        state, _ = migrate_ui_config({"mt_module": "module.md"})
        exp = state.ui.experimental.model_dump()
        assert exp["make_tracking"] == {"module": "module.md"}


class TestTopLevelKeys:
    def test_summaries_routes_to_grounding(self):
        state, _ = migrate_ui_config({"summaries": "summaries.md"})
        assert state.ui.grounding.summaries == "summaries.md"

    def test_session_dir_routes_to_runtime(self):
        state, _ = migrate_ui_config({"session_dir": "summaries/20260504"})
        assert state.runtime.session_dir == "summaries/20260504"

    def test_global_model_routes_to_runtime_default_model(self):
        state, _ = migrate_ui_config({"global_model": "claude-opus-4-6"})
        assert state.runtime.default_model == "claude-opus-4-6"

    def test_campaign_dir_is_dropped(self):
        # campaign_dir comes from the CLI flag / CWD discovery, not from
        # tracked state. Migrating it is a no-op (no warning either —
        # it's expected to be present in legacy files).
        state, warnings = migrate_ui_config({"campaign_dir": "/path/to/campaign"})
        # Not stored anywhere
        assert state.runtime.session_dir is None
        assert warnings == []


class TestDuplicateCollapse:
    """``sd_*`` and ``session_doc_*`` aliased the same field. After migration
    only one canonical key survives, with a warning so the user knows."""

    def test_sd_wins_over_session_doc(self):
        legacy = {
            "session_doc_voice_dir": "old/voice/",
            "sd_voice_dir": "new/voice/",
        }
        state, warnings = migrate_ui_config(legacy)
        assert state.ui.session_doc.voice_dir == "new/voice/"
        assert any("voice_dir" in w and "sd_voice_dir" in w for w in warnings)

    def test_first_write_wins_when_only_session_doc(self):
        # Only the legacy prefix present — value still lands on the canonical
        # field with no warning (no duplicate to collapse).
        legacy = {"session_doc_narrate_tokens": "8000"}
        state, warnings = migrate_ui_config(legacy)
        assert state.ui.session_doc.narrate_tokens == 8000
        assert warnings == []


class TestQuarantine:
    def test_unknown_key_lands_in_legacy_unmigrated(self):
        legacy = {"completely_made_up_key": "value"}
        state, warnings = migrate_ui_config(legacy)
        assert state.legacy.unmigrated == {"completely_made_up_key": "value"}
        assert any("completely_made_up_key" in w for w in warnings)

    def test_drop_keys_dropped_silently(self):
        legacy = {"sd_server_pid": 1234, "FormSubmitter": "x"}
        state, warnings = migrate_ui_config(legacy)
        assert state.legacy.unmigrated == {}
        assert warnings == []


class TestIdempotence:
    def test_already_migrated_round_trips_without_warnings(self):
        # First migration produces v2 dict.
        first, _ = migrate_ui_config({"sd_voice_dir": "voice/"})
        first_dump = first.model_dump(mode="json")
        # Second migration on the v2 dict should be a no-op.
        second, warnings = migrate_ui_config(first_dump)
        assert second.model_dump(mode="json") == first_dump
        assert warnings == []

    def test_migration_is_deterministic(self):
        legacy = {
            "sd_narrate_tokens": "4000",
            "vtt_input": "session.vtt",
            "global_model": "claude-opus-4-6",
        }
        a, _ = migrate_ui_config(legacy)
        b, _ = migrate_ui_config(legacy)
        assert a.model_dump(mode="json") == b.model_dump(mode="json")


class TestVersionStamp:
    def test_migrated_state_has_current_schema_version(self):
        state, _ = migrate_ui_config({})
        assert state.version == SCHEMA_VERSION


class TestLocalConfigMigration:
    def test_empty_input_returns_default(self):
        local, warnings = migrate_local_config({})
        assert local.server.port == 5000
        assert warnings == []

    def test_passes_through_typed_input(self):
        local, _ = migrate_local_config({"server": {"port": 6001}})
        assert local.server.port == 6001
