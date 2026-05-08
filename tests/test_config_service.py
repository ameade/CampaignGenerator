"""Tests for server/config_service.py — the single-authority service.

The hard invariants this file freezes:

  - ``config.yaml`` is never opened for write.
  - Path resolution is uniform: relative → absolute against campaign_dir;
    absolute and ``~`` paths pass through.
  - Boot overrides are in-memory only; a second instance does not see them.
  - Atomic writes: a crash between ``tmp.write`` and ``os.replace`` leaves
    the original file untouched.
  - Concurrent updates serialize on the section lock.
  - Migration runs lazily, idempotently, and surfaces warnings.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config_service import (
    CampaignConfigService,
    ConfigError,
    LEGACY_UI_CONFIG_NAME,
    LOCAL_CONFIG_NAME,
    MIGRATION_MARKER_NAME,
    TRACKED_CONFIG_NAME,
    UI_STATE_NAME,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def fresh_campaign(tmp_path):
    """A campaign dir with only ``config.yaml``."""
    cfg = tmp_path / TRACKED_CONFIG_NAME
    _write(
        cfg,
        "# hand-written, do not touch\ndocuments:\n  - label: world_state\n    path: docs/world_state.md\n",
    )
    return tmp_path


@pytest.fixture
def legacy_campaign(tmp_path):
    """A campaign dir with ``config.yaml`` and a legacy ``ui_config.yaml``
    that contains the worked-example bug."""
    _write(
        tmp_path / TRACKED_CONFIG_NAME,
        "documents:\n  - label: world_state\n    path: docs/world_state.md\n",
    )
    _write(
        tmp_path / LEGACY_UI_CONFIG_NAME,
        "sd_narrate_tokens: '4000'\nsd_voice_dir: voice/\nsession_doc_voice_dir: legacy/voice/\nglobal_model: claude-opus-4-6\nweird_unknown_key: hello\n",
    )
    return tmp_path


# ── Construction ──────────────────────────────────────────────────────────


class TestConstruction:
    def test_missing_campaign_dir_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="does not exist"):
            CampaignConfigService(tmp_path / "nope")

    def test_missing_config_yaml_raises(self, tmp_path):
        # Empty dir, no config.yaml.
        with pytest.raises(ConfigError, match="no config.yaml"):
            CampaignConfigService(tmp_path)

    def test_invalid_config_yaml_raises(self, tmp_path):
        _write(tmp_path / TRACKED_CONFIG_NAME, "key: : :")
        with pytest.raises(ConfigError, match="not valid YAML"):
            CampaignConfigService(tmp_path)

    def test_invalid_ui_state_yaml_raises(self, tmp_path):
        _write(tmp_path / TRACKED_CONFIG_NAME, "documents: []\n")
        _write(tmp_path / UI_STATE_NAME, "version: 2\nui: : :")
        with pytest.raises(ConfigError, match="ui_state.yaml is not valid YAML"):
            CampaignConfigService(tmp_path)

    def test_local_yaml_syntax_error_does_not_block_startup(self, fresh_campaign):
        _write(fresh_campaign / LOCAL_CONFIG_NAME, "server: : :")
        # Hostile to refuse boot over a bad nav.last_page entry.
        svc = CampaignConfigService(fresh_campaign)
        assert any("could not be parsed" in w for w in svc.migration_warnings)


# ── config.yaml read-only invariant ──────────────────────────────────────


class TestConfigYamlNeverWritten:
    def test_config_yaml_unchanged_after_updates(self, fresh_campaign):
        original = (fresh_campaign / TRACKED_CONFIG_NAME).read_bytes()
        svc = CampaignConfigService(fresh_campaign)
        svc.update_section("session_doc", {"narrate_tokens": 12000})
        svc.update_local({"server": {"port": 6001}})
        assert (fresh_campaign / TRACKED_CONFIG_NAME).read_bytes() == original

    def test_service_module_has_no_config_yaml_writer(self):
        """Source-level guard: no function in config_service.py opens
        config.yaml for writing. The strongest enforcement of the
        'human-only' contract."""
        src = Path(__file__).resolve().parent.parent / "server" / "config_service.py"
        text = src.read_text(encoding="utf-8")
        # Writers go through _atomic_write or _persist_*. Make sure none
        # target the tracked config path.
        assert "config_path" not in text.split("_atomic_write")[1].split("def ")[0] if "_atomic_write" in text else True
        # The structural guarantee: only ui_state and local are persisted.
        assert "_persist_ui_state" in text
        assert "_persist_local" in text
        # And there should be no ``self.config_path.write`` style mutation.
        assert ".config_path.write" not in text


# ── Path resolution ──────────────────────────────────────────────────────


class TestPathResolution:
    def test_relative_resolves_against_campaign_dir(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        resolved = svc.resolve_path("docs/world_state.md")
        assert resolved == str((fresh_campaign / "docs" / "world_state.md").resolve())

    def test_absolute_passes_through(self, fresh_campaign, tmp_path):
        svc = CampaignConfigService(fresh_campaign)
        resolved = svc.resolve_path(str(tmp_path / "elsewhere"))
        assert resolved == str((tmp_path / "elsewhere").resolve())

    def test_tilde_expands(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        resolved = svc.resolve_path("~/file.md")
        assert resolved == str(Path.home() / "file.md")

    def test_empty_becomes_none(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        assert svc.resolve_path("") is None
        assert svc.resolve_path("   ") is None
        assert svc.resolve_path(None) is None


# ── Boot overrides ───────────────────────────────────────────────────────


class TestBootOverrides:
    def test_boot_override_visible_in_resolved(self, fresh_campaign):
        svc = CampaignConfigService(
            fresh_campaign,
            boot_overrides={"session_doc.narrate_tokens": 8000},
        )
        resolved = svc.resolved()
        assert resolved["ui"]["session_doc"]["narrate_tokens"] == 8000

    def test_boot_override_does_not_persist(self, fresh_campaign):
        # First instance with override.
        CampaignConfigService(
            fresh_campaign,
            boot_overrides={"session_doc.narrate_tokens": 8000},
        )
        # Second instance with no override sees the file's value (default).
        second = CampaignConfigService(fresh_campaign)
        resolved = second.resolved()
        # narrate_tokens default is 16000 from SessionDocSection.
        assert resolved["ui"]["session_doc"]["narrate_tokens"] == 16000

    def test_runtime_dotted_override_lands_in_runtime(self, fresh_campaign):
        svc = CampaignConfigService(
            fresh_campaign,
            boot_overrides={"runtime.session_dir": "/tmp/x"},
        )
        resolved = svc.resolved()
        assert resolved["runtime"]["session_dir"] == "/tmp/x"


# ── Migration ────────────────────────────────────────────────────────────


class TestMigration:
    def test_legacy_file_migrates_on_first_load(self, legacy_campaign):
        svc = CampaignConfigService(legacy_campaign)
        assert (legacy_campaign / UI_STATE_NAME).exists()
        assert (legacy_campaign / MIGRATION_MARKER_NAME).exists()
        # Stringy int coerced.
        assert svc.ui_state.ui.session_doc.narrate_tokens == 4000
        # Duplicate collapsed (sd_ wins over session_doc_).
        assert svc.ui_state.ui.session_doc.voice_dir == "voice/"
        # global_model rerouted.
        assert svc.ui_state.runtime.default_model == "claude-opus-4-6"
        # Unknown quarantined with a warning.
        assert "weird_unknown_key" in svc.ui_state.legacy.unmigrated
        assert any("weird_unknown_key" in w for w in svc.migration_warnings)

    def test_legacy_ui_config_left_untouched(self, legacy_campaign):
        before = (legacy_campaign / LEGACY_UI_CONFIG_NAME).read_bytes()
        CampaignConfigService(legacy_campaign)
        after = (legacy_campaign / LEGACY_UI_CONFIG_NAME).read_bytes()
        assert before == after

    def test_migration_idempotent(self, legacy_campaign):
        first = CampaignConfigService(legacy_campaign)
        first_dump = first.ui_state.model_dump(mode="json")
        second = CampaignConfigService(legacy_campaign)
        second_dump = second.ui_state.model_dump(mode="json")
        assert first_dump == second_dump
        # The second construction did NOT re-run migration (ui_state
        # already existed) — so warnings should be empty.
        assert second.migration_warnings == []

    def test_fresh_campaign_starts_with_empty_state(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        assert svc.migration_warnings == []
        # Defaults applied.
        assert svc.ui_state.ui.session_doc.narrate_tokens == 16000


# ── update_section / update_local ─────────────────────────────────────────


class TestUpdateSection:
    def test_update_section_persists(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        svc.update_section("session_doc", {"narrate_tokens": 12000})

        # Re-read from disk.
        on_disk = yaml.safe_load((fresh_campaign / UI_STATE_NAME).read_text())
        assert on_disk["ui"]["session_doc"]["narrate_tokens"] == 12000

    def test_update_section_merges_not_replaces(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        svc.update_section("session_doc", {"narrate_tokens": 12000})
        svc.update_section("session_doc", {"voice_dir": "voice/"})
        # Both fields present after the second call.
        assert svc.ui_state.ui.session_doc.narrate_tokens == 12000
        assert svc.ui_state.ui.session_doc.voice_dir == "voice/"

    def test_unknown_section_rejected(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        with pytest.raises(ValueError, match="unknown UI section"):
            svc.update_section("server", {"port": 6001})

    def test_update_local(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        svc.update_local({"server": {"port": 6001}})
        on_disk = yaml.safe_load((fresh_campaign / LOCAL_CONFIG_NAME).read_text())
        assert on_disk["server"]["port"] == 6001


# ── Atomic writes & concurrency ──────────────────────────────────────────


class TestAtomicWrites:
    def test_replace_failure_leaves_existing_file_intact(
        self, fresh_campaign, monkeypatch
    ):
        svc = CampaignConfigService(fresh_campaign)
        svc.update_section("session_doc", {"narrate_tokens": 11111})
        before = (fresh_campaign / UI_STATE_NAME).read_bytes()

        original_replace = os.replace

        def boom(*args, **kwargs):
            raise OSError("simulated mid-write crash")

        monkeypatch.setattr(os, "replace", boom)

        with pytest.raises(OSError, match="simulated"):
            svc.update_section("session_doc", {"narrate_tokens": 22222})

        # Original file unchanged.
        assert (fresh_campaign / UI_STATE_NAME).read_bytes() == before

        # Restore so cleanup works.
        monkeypatch.setattr(os, "replace", original_replace)

    def test_concurrent_writes_to_same_section_no_torn_file(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        errors: list[BaseException] = []

        def writer(value: int) -> None:
            try:
                svc.update_section("session_doc", {"narrate_tokens": value})
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(v,)) for v in range(100, 200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # File is parseable (no torn YAML) — that's the actual invariant.
        on_disk = yaml.safe_load((fresh_campaign / UI_STATE_NAME).read_text())
        assert isinstance(on_disk, dict)
        # Whatever the last writer was, it's a valid value from our range.
        assert 100 <= on_disk["ui"]["session_doc"]["narrate_tokens"] < 200


# ── Resolved view ────────────────────────────────────────────────────────


class TestResolvedView:
    def test_campaign_relative_path_resolves_against_campaign_dir(
        self, fresh_campaign
    ):
        # voice_dir is campaign-scoped (lives at <campaign>/voice/).
        svc = CampaignConfigService(fresh_campaign)
        svc.update_section("session_doc", {"voice_dir": "voice/"})
        resolved = svc.resolved()
        voice = resolved["ui"]["session_doc"]["voice_dir"]
        assert Path(voice).is_absolute()
        assert voice == str((fresh_campaign / "voice").resolve())

    def test_session_relative_path_resolves_against_session_dir(
        self, fresh_campaign
    ):
        # The user-reported bug: a relative `session_summary` was landing
        # in the campaign root instead of the session dir.
        svc = CampaignConfigService(fresh_campaign)
        svc.update_section("session_doc", {"session_summary": "session-summary.md"})
        svc.update_section("grounding", {"summaries": "summaries.md"})
        # Without a session_dir set, the legacy fallback to campaign_dir
        # still applies.
        no_sd = svc.resolved()
        assert no_sd["ui"]["session_doc"]["session_summary"] == \
            str((fresh_campaign / "session-summary.md").resolve())

        # With session_dir set, session-scoped paths resolve there.
        svc2 = CampaignConfigService(
            fresh_campaign,
            boot_overrides={"runtime.session_dir": "summaries/sess1"},
        )
        svc2.update_section(
            "session_doc", {"session_summary": "session-summary.md"}
        )
        svc2.update_section("grounding", {"summaries": "summaries.md"})
        with_sd = svc2.resolved()
        # session-scoped → resolves against session_dir
        assert with_sd["ui"]["session_doc"]["session_summary"] == str(
            (fresh_campaign / "summaries" / "sess1" / "session-summary.md").resolve()
        )
        # campaign-scoped → still campaign root, even with session_dir set
        assert with_sd["ui"]["grounding"]["summaries"] == str(
            (fresh_campaign / "summaries.md").resolve()
        )

    def test_absolute_path_overrides_base(self, fresh_campaign, tmp_path):
        # Absolute paths pass through regardless of base hint.
        elsewhere = tmp_path / "elsewhere" / "file.md"
        svc = CampaignConfigService(fresh_campaign)
        svc.update_section("session_doc", {"session_summary": str(elsewhere)})
        resolved = svc.resolved()
        assert resolved["ui"]["session_doc"]["session_summary"] == \
            str(elsewhere.resolve())

    def test_non_path_fields_pass_through(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        svc.update_section("session_doc", {"narrate_tokens": 12000, "prose_mode": "lyric"})
        resolved = svc.resolved()
        assert resolved["ui"]["session_doc"]["narrate_tokens"] == 12000
        assert resolved["ui"]["session_doc"]["prose_mode"] == "lyric"

    def test_resolved_includes_campaign_dir(self, fresh_campaign):
        svc = CampaignConfigService(fresh_campaign)
        resolved = svc.resolved()
        assert resolved["campaign_dir"] == str(fresh_campaign.resolve())
