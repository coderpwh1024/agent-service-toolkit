import asyncio
import re
from collections.abc import AsyncIterator
from time import monotonic


class SpeechText:
    FIRST_CHUNK_CHARS = 24
    FIRST_CHUNK_DELAY = 0.18
    CHUNK_DELAY = 0.4

    def __init__(self, max_chars: int = 160):
        self.buffer = ""
        self.max_chars = max_chars
        self.in_code = False
        self.started = False

    def feed(self, text: str, *, final: bool = False, flush: bool = False) -> list[str]:
        self.buffer += text
        result = []
        while self.buffer:
            limit = self.max_chars if self.started else min(self.max_chars, self.FIRST_CHUNK_CHARS)
            boundaries = [
                match
                for match in re.finditer(r"[。！？!?；;\n]|[，,：:]\s*", self.buffer)
                if match.end() <= limit
                and (match.end() >= 12 or final or match.group()[0] in "。！？!?\n")
            ]
            boundary = boundaries[-1 if self.started else 0] if boundaries else None
            if boundary:
                cut = boundary.end()
            elif len(self.buffer) >= limit:
                cut = limit
            elif final or flush:
                cut = len(self.buffer)
            else:
                break
            if not final:
                fence = re.search(r"(?<!`)`{1,2}$", self.buffer[:cut])
                if fence and (cut == len(self.buffer) or self.buffer[cut] == "`"):
                    cut = fence.start()
                if cut == 0:
                    break
            chunk, self.buffer = self.buffer[:cut], self.buffer[cut:]
            cleaned = self.clean(chunk)
            if cleaned:
                self.started = True
                result.append(cleaned)
        return result

    async def stream(self, queue: asyncio.Queue[str | None]) -> AsyncIterator[str]:
        pending_since: float | None = None
        while True:
            delay = self.CHUNK_DELAY if self.started else self.FIRST_CHUNK_DELAY
            timeout = max(0, pending_since + delay - monotonic()) if pending_since else None
            try:
                text = (
                    queue.get_nowait()
                    if not queue.empty()
                    else await asyncio.wait_for(queue.get(), timeout=timeout)
                )
            except TimeoutError:
                phrases = self.feed("", flush=True)
                pending_since = monotonic() if self.buffer else None
            else:
                final = text is None
                tokens = [text or ""]
                while not final and not queue.empty():
                    token = queue.get_nowait()
                    final = token is None
                    tokens.append(token or "")
                phrases = self.feed("".join(tokens), final=final)
                if self.buffer and pending_since is None:
                    pending_since = monotonic()
                elif not self.buffer:
                    pending_since = None
                if final:
                    for phrase in phrases:
                        yield phrase
                    return
            for phrase in phrases:
                yield phrase

    def clean(self, text: str) -> str:
        parts = text.split("```")
        spoken = []
        for i, part in enumerate(parts):
            if i:
                self.in_code = not self.in_code
            if not self.in_code:
                spoken.append(part)
        text = "".join(spoken)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"[`*#>|]", "", text).strip()
        return text if any(char.isalnum() for char in text) else ""
