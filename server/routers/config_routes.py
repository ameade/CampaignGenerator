"""Config API routes — load/save ui_config, path validation, status."""

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.config import (
    DEFAULT_MODEL,
    MODELS,
    api_key_present,
    derive_campaign_paths,
    derive_session_paths,
    find_ui_config,
    load_ui_config,
    load_ui_config_raw,
    path_exists,
    save_ui_config,
    save_ui_config_raw,
)

router = APIRouter()


@router.get("/")
def get_config():
    """Return the full ui_config.yaml contents."""
    cfg = load_ui_config()
    return cfg


class ConfigUpdate(BaseModel):
    values: dict


@router.put("/")
def put_config(update: ConfigUpdate):
    """Merge values into ui_config.yaml."""
    save_ui_config(update.values)
    return {"ok": True}


@router.get("/raw")
def get_config_raw():
    """Return the raw YAML text and file path."""
    return {"text": load_ui_config_raw(), "path": str(find_ui_config())}


class ConfigRawUpdate(BaseModel):
    text: str


@router.put("/raw")
def put_config_raw(update: ConfigRawUpdate):
    """Overwrite ui_config.yaml with raw YAML text."""
    try:
        save_ui_config_raw(update.text)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/campaign-paths")
def get_campaign_paths(campaign_dir: str, session_dir: str):
    """Derive all paths from campaign directory + session directory."""
    return derive_campaign_paths(campaign_dir, session_dir)


@router.get("/session-paths")
def get_session_paths(session_dir: str):
    """Derive sub-paths from a session directory (legacy)."""
    return derive_session_paths(session_dir)


@router.get("/path-status")
def get_path_status(path: str):
    """Check if a file or directory exists."""
    return {"exists": path_exists(path)}


@router.get("/party-yaml")
def get_party_yaml(path: str):
    """Load a party config YAML.

    Returns the parsed `characters` list (with arc_score=null preserved as
    null) and the resolved file path. If the file does not exist, returns
    an empty character list so the UI can offer a 'create new' flow.
    """
    p = Path(path).expanduser().resolve() if path else None
    if not p or not path:
        raise HTTPException(status_code=400, detail="path is required")
    if not p.exists():
        return {"path": str(p), "exists": False, "characters": []}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"invalid YAML: {e}")
    chars = raw.get("characters") or []
    if not isinstance(chars, list):
        raise HTTPException(status_code=400,
                            detail="'characters' must be a list")

    # Normalize: arc_score key always present (null when trackless / unspecified).
    out = []
    for c in chars:
        if not isinstance(c, dict):
            continue
        out.append({
            "name": c.get("name", ""),
            "sheet": c.get("sheet", ""),
            "backstory": c.get("backstory") or "",
            # Distinguish three cases for arc_score on the wire:
            #   present + null → trackless=True
            #   present + path → trackless=False, arc_score=path
            #   absent         → trackless=False, arc_score=""
            "arc_score": c.get("arc_score") if c.get("arc_score") else "",
            "trackless": "arc_score" in c and c.get("arc_score") is None,
        })
    return {"path": str(p), "exists": True, "characters": out}


class PartyYamlSave(BaseModel):
    path: str
    characters: list[dict]


@router.put("/party-yaml")
def put_party_yaml(update: PartyYamlSave):
    """Write a party config YAML.

    Each character dict must contain `name` and `sheet`. `backstory` is
    optional. `arc_score` is three-state:
        trackless=True            → emits `arc_score: null`
        arc_score truthy          → emits `arc_score: <path>`
        otherwise                 → arc_score key omitted entirely
    Validates only the shape of the YAML — does NOT verify referenced
    files exist (party.py's loader does that at run time).
    """
    p = Path(update.path).expanduser().resolve()
    if not update.characters:
        raise HTTPException(status_code=400,
                            detail="characters list cannot be empty")

    out_chars = []
    for i, c in enumerate(update.characters):
        name = (c.get("name") or "").strip()
        sheet = (c.get("sheet") or "").strip()
        if not name or not sheet:
            raise HTTPException(
                status_code=400,
                detail=f"character #{i + 1}: 'name' and 'sheet' are required",
            )
        entry: dict = {"name": name, "sheet": sheet}
        backstory = (c.get("backstory") or "").strip()
        if backstory:
            entry["backstory"] = backstory
        if c.get("trackless"):
            entry["arc_score"] = None
        else:
            arc_score = (c.get("arc_score") or "").strip()
            if arc_score:
                entry["arc_score"] = arc_score
        out_chars.append(entry)

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"characters": out_chars}, sort_keys=False),
                 encoding="utf-8")
    return {"ok": True, "path": str(p)}


@router.get("/models")
def get_models():
    """Return the list of available Claude models."""
    return {"models": MODELS, "default": DEFAULT_MODEL}


@router.get("/status")
def get_status():
    """Return API key status and working directory."""
    import os
    return {
        "api_key_present": api_key_present(),
        "cwd": os.getcwd(),
    }
