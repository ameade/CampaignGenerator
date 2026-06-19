"""Anthropic Message Batches orchestration: build / submit / poll / collect."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .client import _is_retryable


# ── Batch API ─────────────────────────────────────────────────────────────────
#
# Anthropic's Message Batches API charges 50% of list price for any request
# that completes within its 24-hour SLA, and honours prompt caching the same
# way live calls do. Our session-prep workflow has a human review step after
# each LLM stage, so giving up live token streaming in exchange for the 50%
# discount is a clean trade — but only when the user explicitly asks (`--batch`).
#
# The helpers below are pure orchestration: they don't know what the prompts
# are, just how to build a Request, submit a batch, poll for completion, and
# stream the results back. Prompt assembly stays in the calling script.


def build_batch_request(
    *,
    custom_id: str,
    system: str,
    user: str,
    model: str,
    max_tokens: int = 8192,
    cache_system: bool = False,
) -> dict:
    """Build one Request entry for `client.messages.batches.create(requests=...)`.

    Mirrors the system/messages shape `stream_api` constructs, including the
    optional `cache_control: ephemeral` block on the system prompt so the
    cache breakpoint is identical between live and batched paths.
    """
    if cache_system:
        system_arg = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]
    else:
        system_arg = system

    return {
        "custom_id": custom_id,
        "params": {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_arg,
            "messages": [{"role": "user", "content": user}],
        },
    }


def submit_batch(client, requests: list[dict]) -> str:
    """Submit `requests` as a single Message Batch. Returns the batch ID.

    Retries on transient errors using the same predicate as the streaming path.
    """
    if not requests:
        raise ValueError("submit_batch: requests list is empty")
    delays = [10, 20, 40]
    for attempt, delay in enumerate([-1] + delays):
        if delay >= 0:
            print(f"\n  [Batch submit unavailable — waiting {delay}s before retry "
                  f"{attempt}/{len(delays)}...]", flush=True)
            time.sleep(delay)
        try:
            batch = client.messages.batches.create(requests=requests)
            return batch.id
        except Exception as e:
            if _is_retryable(e) and attempt < len(delays):
                continue
            raise


def poll_batch(client, batch_id: str, *, interval: int = 10, on_tick=None,
               max_wait: int | None = None):
    """Poll until the batch's `processing_status == 'ended'`.

    `on_tick(batch)` is called after each retrieve so the caller can print
    progress (`batch.request_counts.processing/succeeded/errored/...`).
    Returns the final batch object.

    Retries transient retrieve errors. `max_wait` is in seconds; None means
    wait up to the API's 24-hour SLA.
    """
    waited = 0
    delays = [10, 20, 40]
    while True:
        for attempt, delay in enumerate([-1] + delays):
            if delay >= 0:
                print(f"\n  [Batch retrieve unavailable — waiting {delay}s "
                      f"before retry {attempt}/{len(delays)}...]", flush=True)
                time.sleep(delay)
            try:
                batch = client.messages.batches.retrieve(batch_id)
                break
            except Exception as e:
                if _is_retryable(e) and attempt < len(delays):
                    continue
                raise
        if on_tick:
            try:
                on_tick(batch)
            except Exception:
                pass
        if getattr(batch, "processing_status", None) == "ended":
            return batch
        if max_wait is not None and waited >= max_wait:
            raise TimeoutError(
                f"Batch {batch_id} did not finish within {max_wait}s "
                f"(status: {batch.processing_status})"
            )
        time.sleep(interval)
        waited += interval


def collect_batch(client, batch_id: str) -> dict[str, dict]:
    """Stream the batch's results back into a dict keyed by `custom_id`.

    Each value: `{"status": "succeeded" | "errored" | "canceled" | "expired",
                  "text": str | None, "error": str | None,
                  "usage": dict | None}`.

    `text` is populated only for succeeded results. The caller is responsible
    for deciding what to do with non-succeeded entries (typically: print the
    error message and let the user re-run; sidecar files stay on disk so a
    subsequent `--collect` can retry).
    """
    delays = [10, 20, 40]
    for attempt, delay in enumerate([-1] + delays):
        if delay >= 0:
            print(f"\n  [Batch results unavailable — waiting {delay}s before retry "
                  f"{attempt}/{len(delays)}...]", flush=True)
            time.sleep(delay)
        try:
            stream = client.messages.batches.results(batch_id)
            break
        except Exception as e:
            if _is_retryable(e) and attempt < len(delays):
                continue
            raise

    out: dict[str, dict] = {}
    for entry in stream:
        custom_id = getattr(entry, "custom_id", None)
        result = getattr(entry, "result", None)
        if custom_id is None or result is None:
            continue
        result_type = getattr(result, "type", None)
        record: dict = {"status": result_type, "text": None,
                        "error": None, "usage": None}
        if result_type == "succeeded":
            message = getattr(result, "message", None)
            if message is not None:
                blocks = getattr(message, "content", []) or []
                text_parts = [getattr(b, "text", "") for b in blocks
                              if getattr(b, "type", None) == "text"]
                record["text"] = "".join(text_parts)
                usage = getattr(message, "usage", None)
                if usage is not None:
                    record["usage"] = {
                        "input_tokens": getattr(usage, "input_tokens", None),
                        "output_tokens": getattr(usage, "output_tokens", None),
                        "cache_creation_input_tokens":
                            getattr(usage, "cache_creation_input_tokens", None),
                        "cache_read_input_tokens":
                            getattr(usage, "cache_read_input_tokens", None),
                    }
        elif result_type == "errored":
            err = getattr(result, "error", None)
            record["error"] = (
                getattr(getattr(err, "error", None), "message", None)
                or str(err)
            )
        else:
            record["error"] = f"result type: {result_type}"
        out[custom_id] = record
    return out


def write_batch_sidecar(path: Path, payload: dict) -> None:
    """Persist batch metadata (id, model, custom_ids, etc.) for later --collect."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def read_batch_sidecar(path: Path) -> dict:
    """Read a sidecar previously written by `write_batch_sidecar`."""
    if not path.exists():
        print(f"Error: batch sidecar not found: {path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_batch_progress(batch) -> str:
    """One-line summary like '[batch ... | 4/8 succeeded | 1 processing]'."""
    counts = getattr(batch, "request_counts", None)
    if counts is None:
        return f"[batch {batch.id} | status: {batch.processing_status}]"
    succeeded = getattr(counts, "succeeded", 0) or 0
    errored = getattr(counts, "errored", 0) or 0
    canceled = getattr(counts, "canceled", 0) or 0
    expired = getattr(counts, "expired", 0) or 0
    processing = getattr(counts, "processing", 0) or 0
    total = succeeded + errored + canceled + expired + processing
    parts = [f"[batch {batch.id}", f"{succeeded}/{total} succeeded"]
    if processing:
        parts.append(f"{processing} processing")
    if errored:
        parts.append(f"{errored} errored")
    if canceled:
        parts.append(f"{canceled} canceled")
    if expired:
        parts.append(f"{expired} expired")
    return " | ".join(parts) + "]"
