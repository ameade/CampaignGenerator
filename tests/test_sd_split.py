"""Phase 4 smoke tests for the session_doc carve-up.

Verifies:
  - Each new helper module (session_doc_*.py) imports cleanly
  - session_doc.py still exposes every previously-public name via re-export
  - Each new CLI shim (sd_*.py) exits 0 on --help (argparse setup is sane)
"""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HELPERS = [
    "session_doc_io",
    "session_doc_voice",
    "session_doc_roster",
    "session_doc_examples",
    "session_doc_narrate",
]

# (Public name still importable from session_doc, source helper module)
REEXPORTS = [
    ("extract_character_roster",        "session_doc_roster"),
    ("load_voice_files",                "session_doc_voice"),
    ("get_voice_note",                  "session_doc_voice"),
    ("extract_contrast_sample",         "session_doc_voice"),
    ("get_char_examples",               "session_doc_examples"),
    ("load_extractions",                "session_doc_io"),
    ("load_scene_extractions",          "session_doc_io"),
    ("_split_scene_body",               "session_doc_io"),
    ("_SCENE_FRONTMATTER_RE",           "session_doc_io"),
    ("extract_scene_text",              "session_doc_io"),
    ("parse_plan",                      "session_doc_io"),
    ("format_extractions",              "session_doc_io"),
    ("build_narrate_system",            "session_doc_narrate"),
    ("build_narrate_prompt",            "session_doc_narrate"),
    ("estimate_narration_tokens",       "session_doc_narrate"),
    ("NARRATE_SYSTEM_BASE",             "session_doc_narrate"),
    ("EXAMPLES_BLOCK",                  "session_doc_narrate"),
    ("PER_CHAR_EXAMPLES_BLOCK",         "session_doc_narrate"),
    ("VOICE_SPEC_BLOCK",                "session_doc_narrate"),
    ("PREV_VOICE_CONTRAST_BLOCK",       "session_doc_narrate"),
    ("DIALOGUE_INSTRUCTION_FULL",       "session_doc_narrate"),
    ("DIALOGUE_INSTRUCTION_CONDITIONAL","session_doc_narrate"),
    ("PROSE_MODE_INSTRUCTION",          "session_doc_narrate"),
    ("SCENE_ANCHORED_DIRECTIVE",        "session_doc_narrate"),
]

CLI_SHIMS = ["sd_consistency.py", "sd_plan.py", "sd_narrate.py"]


@pytest.mark.parametrize("module_name", HELPERS)
def test_helper_module_imports(module_name):
    mod = importlib.import_module(module_name)
    assert mod is not None


@pytest.mark.parametrize("name,source_module", REEXPORTS)
def test_session_doc_reexport_matches_source(name, source_module):
    """session_doc.X must be the same object as the helper module's X
    (re-exports are pure aliases, not local copies)."""
    sd = importlib.import_module("session_doc")
    src = importlib.import_module(source_module)
    assert getattr(sd, name) is getattr(src, name), (
        f"session_doc.{name} is not the same object as {source_module}.{name}"
    )


@pytest.mark.parametrize("shim", CLI_SHIMS)
def test_cli_shim_help_exits_zero(shim):
    """argparse --help should exit 0 — verifies the CLI is importable and
    the argument set is internally consistent."""
    result = subprocess.run(
        [sys.executable, str(ROOT / shim), "--help"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (
        f"{shim} --help exited {result.returncode}\n"
        f"stdout:\n{result.stdout[:400]}\n"
        f"stderr:\n{result.stderr[:400]}"
    )
    # Sanity: the help output mentions the shim's name (vs. random failure mode).
    assert shim.replace(".py", "") in result.stdout.lower(), (
        f"{shim} --help output didn't mention the shim name"
    )
