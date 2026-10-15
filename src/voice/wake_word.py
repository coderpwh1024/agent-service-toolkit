"""Match wake prefixes without passing repeated calls to the agent."""

import re
from dataclasses import dataclass

from schema.voice import normalize_wake_word

SEPARATOR = r"[\s，,。！？!?、；;：:．.…]*"


@dataclass(frozen=True)
class WakeMatch:
    command: str
    complete: bool


def match_wake_prefix(text: str, keyword: str) -> WakeMatch | None:
    keyword = normalize_wake_word(keyword)
    if not keyword:
        return None
    unit, minimum = ("小美", 2) if keyword == "小美小美" else (keyword, 1)
    phrase = SEPARATOR.join(re.escape(char) for char in unit) + SEPARATOR
    remaining = re.sub(r"^" + SEPARATOR, "", text)
    repetitions = 0
    while match := re.match(phrase, remaining, flags=re.IGNORECASE):
        repetitions += 1
        remaining = remaining[match.end() :]
    if not repetitions:
        return None
    return WakeMatch(command=remaining.strip(), complete=repetitions >= minimum)
