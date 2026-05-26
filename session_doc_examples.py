"""Per-character example file routing for session_doc and the sd_* CLIs."""


def get_char_examples(per_char_examples: dict[str, str], narrator: str) -> str | None:
    """Look up per-character style examples by case-insensitive first-name match."""
    key = narrator.lower().split()[0]
    return per_char_examples.get(key) or per_char_examples.get(narrator.lower())
