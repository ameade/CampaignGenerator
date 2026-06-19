"""Shared utilities for CampaignGenerator scripts.

All file I/O, API calls, clipboard, and logging live here so individual
scripts only contain their own logic.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .constants import DEFAULT_MODEL
from .textproc import (
    strip_base64_images, chunk_text, chunk_by_chapters,
    annotate_chunks_with_pov, prepare_chunks,
)
from .config import (
    find_default_config, load_config, load_file, load_file_optional,
    _clear_prompt_cache, load_agent_prompt, assemble_docs,
)
from .util import copy_to_clipboard, save_log
from .api.client import make_client, call_api, call_api_with_tools, stream_api
from .api.batch import (
    build_batch_request, submit_batch, poll_batch, collect_batch,
    write_batch_sidecar, read_batch_sidecar, utc_now_iso, format_batch_progress,
)

from .npc import (
    parse_dossier, normalize_npc_key, build_alias_normalizer, load_alias_map,
    extract_player_character_map, normalize_vtt_speakers, format_npc_roster,
)
from .scenes import (
    parse_gmassist_scenes, snapshot_scene_for_rerun, run_scene_extraction,
    format_scene_output, build_scene_extraction_system_prompt, plan_scene_extraction,
)
from .pipelines import run_extract_pipeline, run_synthesize_pipeline
