"""Clipboard and timestamped-log helpers."""

from datetime import datetime
from pathlib import Path


def copy_to_clipboard(text: str) -> None:
    try:
        import pyperclip
        pyperclip.copy(text)
        print(f"Copied to clipboard ({len(text):,} chars).")
    except ImportError:
        print("pyperclip not installed. Run: pip install pyperclip", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Clipboard error: {e}", file=sys.stderr)
        print("On WSL you may need: sudo apt install xclip", file=sys.stderr)
        sys.exit(1)


# ── Logging ───────────────────────────────────────────────────────────────────

def save_log(log_dir: str, sections: list[tuple[str, str]], stem: str = "session") -> Path:
    """Save a markdown log file.

    sections — list of (heading, content) tuples
    stem     — filename prefix (timestamp is prepended automatically)
    """
    log_path = Path(log_dir).expanduser()
    log_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_file = log_path / f"{timestamp}_{stem}.md"
    lines = [f"# Session Log — {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    for heading, content in sections:
        lines += ["", "---", "", f"## {heading}", "", content.strip()]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_file
