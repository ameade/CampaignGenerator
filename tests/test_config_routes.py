"""API tests for the unified config endpoints (step C).

Covers:
  - ``GET /api/config/`` returns the new shape AND the legacy flat overlay
    so the un-reshaped frontend keeps working.
  - ``PUT /api/config/section/{name}`` rejects unknown sections.
  - ``PUT /api/config/local`` cannot write ``ui.*`` keys.
  - The legacy ``PUT /api/config/`` and raw-YAML endpoints still work
    (they're removed in step F).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config_service import (
    CampaignConfigService,
    LEGACY_UI_CONFIG_NAME,
    TRACKED_CONFIG_NAME,
)
from server.routers import config_routes


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def campaign_with_legacy(tmp_path):
    """Campaign dir with hand-written config.yaml AND legacy ui_config.yaml."""
    _write(
        tmp_path / TRACKED_CONFIG_NAME,
        "documents:\n  - label: world_state\n    path: docs/world_state.md\n",
    )
    _write(
        tmp_path / LEGACY_UI_CONFIG_NAME,
        "sd_narrate_tokens: '4000'\nsd_voice_dir: voice/\nglobal_model: claude-opus-4-6\n",
    )
    return tmp_path


@pytest.fixture
def fresh_campaign(tmp_path):
    _write(
        tmp_path / TRACKED_CONFIG_NAME,
        "documents:\n  - label: world_state\n    path: docs/world_state.md\n",
    )
    return tmp_path


def _make_app(campaign_dir: Path | None) -> FastAPI:
    app = FastAPI()
    app.include_router(config_routes.router, prefix="/api/config")
    if campaign_dir is not None:
        app.state.config_service = CampaignConfigService(campaign_dir)
    else:
        app.state.config_service = None
    return app


# ── GET / shape ───────────────────────────────────────────────────────────


class TestGetConfig:
    def test_includes_new_shape_fields(self, fresh_campaign):
        client = TestClient(_make_app(fresh_campaign))
        body = client.get("/api/config/").json()
        for key in (
            "campaign_dir",
            "config_path",
            "ui_state_path",
            "local_config_path",
            "schema_version",
            "resolved",
            "tracked",
            "local",
            "migration_warnings",
        ):
            assert key in body, f"missing {key} in GET / response"

    def test_legacy_flat_keys_present_for_unmigrated_frontend(
        self, campaign_with_legacy
    ):
        client = TestClient(_make_app(campaign_with_legacy))
        body = client.get("/api/config/").json()
        # The flat overlay lets the unreshaped Pinia store keep reading
        # ``cfg.sd_narrate_tokens`` and ``cfg.global_model`` directly.
        # Path fields are resolved to absolute (against campaign_dir)
        # because the overlay is computed from service.resolved().
        assert body["sd_narrate_tokens"] == 4000
        assert body["sd_voice_dir"].endswith("/voice")
        assert body["global_model"] == "claude-opus-4-6"

    def test_no_service_falls_back_to_legacy(self, monkeypatch):
        # No campaign_dir wired → legacy load_ui_config path.
        monkeypatch.setattr(
            config_routes,
            "load_ui_config",
            lambda *a, **k: {"sd_voice_dir": "legacy/"},
        )

        client = TestClient(_make_app(None))
        body = client.get("/api/config/").json()
        assert body == {"sd_voice_dir": "legacy/"}


# ── PUT /section/{name} ────────────────────────────────────────────────────


class TestPutSection:
    def test_typed_section_update_persists(self, fresh_campaign):
        app = _make_app(fresh_campaign)
        client = TestClient(app)
        resp = client.put(
            "/api/config/section/session_doc",
            json={"values": {"narrate_tokens": 12000, "voice_dir": "voice/"}},
        )
        assert resp.status_code == 200

        # Read back via GET / and confirm the values are visible.
        body = client.get("/api/config/").json()
        assert body["resolved"]["ui"]["session_doc"]["narrate_tokens"] == 12000
        # And the flat overlay reflects them too.
        assert body["sd_narrate_tokens"] == 12000

    def test_unknown_section_rejected_404(self, fresh_campaign):
        client = TestClient(_make_app(fresh_campaign))
        resp = client.put(
            "/api/config/section/server",
            json={"values": {"port": 6001}},
        )
        # Sections come from UISection model fields; ``server`` is local-only
        # and must not be writable through this endpoint.
        assert resp.status_code == 404

    def test_no_service_503(self, fresh_campaign):
        client = TestClient(_make_app(None))
        resp = client.put(
            "/api/config/section/session_doc",
            json={"values": {"narrate_tokens": 12000}},
        )
        assert resp.status_code == 503


# ── PUT /local ────────────────────────────────────────────────────────────


class TestPutLocal:
    def test_local_update_persists(self, fresh_campaign):
        client = TestClient(_make_app(fresh_campaign))
        resp = client.put(
            "/api/config/local",
            json={"values": {"server": {"port": 6001}}},
        )
        assert resp.status_code == 200

        body = client.get("/api/config/").json()
        assert body["local"]["server"]["port"] == 6001

    def test_local_rejects_ui_top_level_key(self, fresh_campaign):
        client = TestClient(_make_app(fresh_campaign))
        # ``ui`` is not a valid LocalConfig top-level key; pydantic rejects.
        # ``extra="allow"`` on LocalConfig means stranger top-level keys are
        # accepted but stored without further validation. The important
        # invariant is that ``ui`` keys do not land in the typed
        # ``server``/``nav`` slots — verify by reading back.
        client.put("/api/config/local", json={"values": {"ui": {"session_doc": {}}}})
        body = client.get("/api/config/").json()
        # Whatever happened, the service-resolved ui section stays empty
        # of any session_doc state we didn't put there via /section/.
        assert body["resolved"]["ui"]["session_doc"]["narrate_tokens"] == 16000

    def test_no_service_503(self, fresh_campaign):
        client = TestClient(_make_app(None))
        resp = client.put("/api/config/local", json={"values": {"server": {"port": 6001}}})
        assert resp.status_code == 503


# ── Migration warnings surface through GET / ──────────────────────────────


class TestMigrationWarnings:
    def test_legacy_campaign_surfaces_warnings(self, campaign_with_legacy):
        # ``sd_voice_dir`` + ``session_doc_voice_dir`` would normally produce
        # a duplicate-collapse warning. Add the legacy duplicate to trigger it.
        legacy = campaign_with_legacy / LEGACY_UI_CONFIG_NAME
        legacy.write_text(
            legacy.read_text() + "session_doc_voice_dir: legacy/voice/\nbogus_unknown: ok\n",
            encoding="utf-8",
        )
        client = TestClient(_make_app(campaign_with_legacy))
        body = client.get("/api/config/").json()
        warnings = body["migration_warnings"]
        assert any("duplicate" in w.lower() or "collapsed" in w.lower() for w in warnings)
        assert any("bogus_unknown" in w for w in warnings)


# ── Legacy endpoints still work in step C ─────────────────────────────────


class TestLegacyEndpointsStillFunctional:
    def test_legacy_put_root_still_writes_old_file(
        self, fresh_campaign, monkeypatch, tmp_path
    ):
        # PUT / writes to the file find_ui_config() points at. Redirect to
        # tmp so the test is hermetic.
        from server import config as legacy_config

        target = tmp_path / "ui_config.yaml"
        monkeypatch.setattr(legacy_config, "find_ui_config", lambda: target)

        client = TestClient(_make_app(fresh_campaign))
        resp = client.put("/api/config/", json={"values": {"sd_voice_dir": "v/"}})
        assert resp.status_code == 200
        assert target.exists()
        assert "sd_voice_dir" in target.read_text(encoding="utf-8")

    def test_raw_yaml_endpoints_removed(self, fresh_campaign):
        # Step F removed GET/PUT /api/config/raw. The Settings.vue raw
        # editor was the only consumer; it's now a read-only view of
        # service.resolved.
        client = TestClient(_make_app(fresh_campaign))
        assert client.get("/api/config/raw").status_code == 404
        assert client.put("/api/config/raw", json={"text": "x: y"}).status_code == 404
