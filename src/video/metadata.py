"""Derive display metadata without changing the original generation request."""

import re


def derive_video_title(prompt: str) -> str:
    sentences = re.split(r"[\n。！？!?；;]|\.(?:\s+|$)", prompt.strip())
    first = next((sentence for sentence in sentences if sentence.strip()), prompt)
    return " ".join(first.split())[:80]
