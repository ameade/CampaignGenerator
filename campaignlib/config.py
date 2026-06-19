"""Config loading, file I/O, document assembly, and agent-prompt templating."""

import os
import sys
from pathlib import Path


def find_default_config(script_file: str) -> str:
    """Return CWD/config.yaml if it exists, else <script_dir>/config/config.yaml."""
    cwd_config = Path.cwd() / "config.yaml"
    if cwd_config.exists():
        return str(cwd_config)
    return str(Path(script_file).resolve().parent / "config" / "config.yaml")


def load_config(config_path: str) -> tuple[dict, Path]:
    """Load a YAML config file. Returns (config_dict, config_directory)."""
    try:
        import yaml
    except ImportError:
        print("Error: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    p = Path(config_path).expanduser().resolve()
    with open(p) as f:
        return yaml.safe_load(f), p.parent


# ── File I/O ──────────────────────────────────────────────────────────────────

def load_file(path: str, base_dir: Path | None = None) -> str:
    """Read a file. Relative paths are resolved against base_dir if given."""
    p = Path(path).expanduser()
    if not p.is_absolute() and base_dir:
        p = base_dir / p
    if not p.exists():
        print(f"Error: file not found: {p}", file=sys.stderr)
        sys.exit(1)
    return p.read_text(encoding="utf-8")


def load_file_optional(path: str | Path, label: str = "file") -> str | None:
    """Read a file, returning None (with a stderr warning) if it does not exist."""
    p = Path(path).expanduser()
    if not p.exists():
        print(f"  Warning: {label} not found: {p}", file=sys.stderr)
        return None
    return p.read_text(encoding="utf-8")


# ── Agent prompt loader ───────────────────────────────────────────────────────

_PROMPT_CACHE: dict[Path, str] = {}


def _clear_prompt_cache() -> None:
    """Wipe the in-process prompt cache. Test-only helper."""
    _PROMPT_CACHE.clear()


def load_agent_prompt(
    name: str,
    base_dir: Path | None = None,
    placeholders: dict[str, str] | None = None,
) -> str:
    """Load a prompt template from ``config/agents/<name>.md``.

    Resolution order: ``base_dir`` (or CWD if unset) → repo's ``config/agents/``.
    A campaign that wants to override a prompt drops its own file in either
    location and the loader picks it up without forking the repo.

    Placeholder substitution is opt-in (``placeholders=None`` returns the
    template verbatim) and strict on both sides: every ``{key}`` in the
    template must appear in ``placeholders``, and every key in
    ``placeholders`` must appear in the template. Either mismatch raises
    ``ValueError`` so prompt drift surfaces loudly instead of silently
    producing a malformed prompt.

    File contents are cached per absolute path for the life of the process.
    """
    rel = Path("config/agents") / f"{name}.md"
    override_root = Path(base_dir) if base_dir is not None else Path.cwd()
    # This module lives at campaignlib/config.py, so the repo root (which holds
    # config/agents/) is two levels up, not one.
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [override_root / rel, repo_root / rel]

    chosen: Path | None = None
    for cand in candidates:
        if cand.is_file():
            chosen = cand.resolve()
            break
    if chosen is None:
        tried = "\n  ".join(str(c) for c in candidates)
        raise FileNotFoundError(
            f"Agent prompt '{name}' not found. Looked in:\n  {tried}"
        )

    if chosen not in _PROMPT_CACHE:
        _PROMPT_CACHE[chosen] = chosen.read_text(encoding="utf-8")
    template = _PROMPT_CACHE[chosen]

    if placeholders is None:
        return template

    from string import Formatter
    template_keys: set[str] = set()
    for _literal, field, _spec, _conv in Formatter().parse(template):
        if field:
            template_keys.add(field)
    provided_keys = set(placeholders)

    missing = template_keys - provided_keys
    if missing:
        raise ValueError(
            f"Agent prompt '{name}' has unfilled placeholder(s): "
            f"{sorted(missing)}"
        )
    extra = provided_keys - template_keys
    if extra:
        raise ValueError(
            f"Agent prompt '{name}' was passed unused placeholder(s): "
            f"{sorted(extra)}"
        )

    return template.format(**placeholders)


def assemble_docs(config: dict, doc_labels: list[str], base_dir: Path | None = None) -> str:
    """Load the requested document labels from config and join them with separators.

    Documents with no path set are skipped with a warning.
    Raises SystemExit if a requested label is not in the config at all,
    or if no documents with a path could be loaded.
    """
    available = {d["label"]: d.get("path") for d in config.get("documents", [])}
    parts = []
    for label in doc_labels:
        if label not in available:
            print(
                f"Error: document '{label}' not found in config. "
                f"Available: {[k for k, v in available.items() if v]}",
                file=sys.stderr,
            )
            sys.exit(1)
        if not available[label]:
            print(f"Skipping '{label}': no path set in config.", file=sys.stderr)
            continue
        content = load_file(available[label], base_dir)
        parts.append(f"## {label}\n\n{content.strip()}")
    if not parts:
        print("Error: no documents with a path to load.", file=sys.stderr)
        sys.exit(1)
    return "\n\n---\n\n".join(parts)
