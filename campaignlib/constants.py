"""Shared constants for CampaignGenerator.

Module-level so every script and submodule reads the same default.
"""

import os

# Unified default Claude model for every CLI script. Override per-environment
# with the CAMPAIGN_MODEL env var; the server forwards the UI's sidebar pick
# as an explicit --model, which takes precedence over this default.
DEFAULT_MODEL = os.environ.get("CAMPAIGN_MODEL") or "claude-sonnet-4-6"

