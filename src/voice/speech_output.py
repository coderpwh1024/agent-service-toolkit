import re


class SpeechText:
    def __init__(self, max_chars: int = 160):
        self.buffer = ""
        self.max_chars = max_chars
        self.in_code = False

    def feed(self, text: str, *, final: bool = False) -> list[str]:
        self.buffer += text
        result = []
        while self.buffer:
            boundary = re.search(r"[。！？!?；;\n]|[，,：:]\s*", self.buffer)
            if boundary and (boundary.end() >= 12 or final or boundary.group()[0] in "。！？!?\n"):
                cut = boundary.end()
            elif len(self.buffer) >= self.max_chars:
                cut = self.max_chars
            elif final:
                cut = len(self.buffer)
            else:
                break
            chunk, self.buffer = self.buffer[:cut], self.buffer[cut:]
            cleaned = self.clean(chunk)
            if cleaned:
                result.append(cleaned)
        return result

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
        return re.sub(r"[`*#>|]", "", text).strip()
