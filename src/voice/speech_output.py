import asyncio
import re
from collections.abc import AsyncIterator
from time import monotonic

URL_PATTERN = re.compile(r"(?i)(?<!\w)(?:https?://|https?:|www\.)[^\s，。！？；、）】<>]*")
URL_PREFIX_PATTERN = re.compile(r"(?i)(?<!\w)(?:h|ht|htt|http|https|w|ww|www)$")
MARKDOWN_LINK_START = re.compile(r"!?\[")
HTML_TAG_START = re.compile(r"</?[A-Za-z]")
HTML_TAG_PREFIX = re.compile(r"</?$")
EMOJI_PATTERN = re.compile(
    r"[\U0001F000-\U0001FAFF\u2600-\u27BF]\ufe0f?"
    r"|[0-9#*]\ufe0f?\u20e3"
    r"|[\u00a9\u00ae\u203c\u2049\u2122\u2139]\ufe0f?"
)


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
            cut = self._safe_cut(cut, final=final)
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

    def _safe_cut(self, cut: int, *, final: bool) -> int:
        """Keep URLs and markup intact until their readable text can be extracted."""
        spans: list[tuple[int, int, bool]] = []
        for match in URL_PATTERN.finditer(self.buffer):
            spans.append((match.start(), match.end(), match.end() == len(self.buffer)))
        if not final:
            for match in URL_PREFIX_PATTERN.finditer(self.buffer):
                spans.append((match.start(), match.end(), True))
        for match in MARKDOWN_LINK_START.finditer(self.buffer):
            label_end = self.buffer.find("]", match.end())
            if label_end < 0 or (label_end + 1 == len(self.buffer) and not final):
                spans.append((match.start(), len(self.buffer), True))
            elif self.buffer[label_end + 1 : label_end + 2] == "(":
                target_end = self.buffer.find(")", label_end + 2)
                spans.append(
                    (
                        match.start(),
                        target_end + 1 if target_end >= 0 else len(self.buffer),
                        target_end < 0,
                    )
                )
        for match in HTML_TAG_START.finditer(self.buffer):
            tag_end = self.buffer.find(">", match.end())
            spans.append(
                (match.start(), tag_end + 1 if tag_end >= 0 else len(self.buffer), tag_end < 0)
            )
        if not final:
            for match in HTML_TAG_PREFIX.finditer(self.buffer):
                spans.append((match.start(), match.end(), True))
        for start, end, incomplete in sorted(spans):
            if start < cut < end or (cut == end and incomplete and not final):
                if start:
                    return start
                return 0 if incomplete and not final else end
        return cut

    def clean(self, text: str) -> str:
        parts = text.split("```")
        spoken = []
        for i, part in enumerate(parts):
            if i:
                self.in_code = not self.in_code
            if not self.in_code:
                spoken.append(part)
        text = "".join(spoken)
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
        text = URL_PATTERN.sub("", text)
        text = EMOJI_PATTERN.sub("", text)
        text = text.replace("\u200d", "").replace("\ufe0f", "")
        text = re.sub(r"[`*#>|]", "", text).strip()
        return text if any(char.isalnum() for char in text) else ""
