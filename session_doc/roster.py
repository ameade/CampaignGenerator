"""Party roster parsing for session_doc and the sd_* CLIs."""

import re


def extract_character_roster(party_text: str) -> str:
    """Parse party.md and return a compact name → class list for prompt injection.

    Expects sections like:
        ## Soma
        **Tortle Druid 5, Player: Wade**

    Outputs:
        - Soma (Wade): Tortle Druid 5
    """
    roster = []
    current_name: str | None = None
    for line in party_text.splitlines():
        m = re.match(r'^## (.+)$', line.strip())
        if m:
            current_name = m.group(1).strip()
        elif current_name:
            cm = re.match(r'^\*\*(.+\d+.+)\*\*$', line.strip())
            if cm:
                class_info = cm.group(1)
                pm = re.search(r',\s*Player:\s*(.+)', class_info)
                if pm:
                    player = pm.group(1).strip().rstrip('*')
                    class_only = class_info[:pm.start()].strip()
                    roster.append(f"- {current_name} ({player}): {class_only}")
                else:
                    roster.append(f"- {current_name}: {class_info}")
                current_name = None
    return "\n".join(roster)
