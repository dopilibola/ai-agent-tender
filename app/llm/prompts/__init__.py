"""Versioned prompt files. First line of each .md may be `<!-- version: N -->`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
_VERSION_RE = re.compile(r"^<!--\s*version:\s*([\w.-]+)\s*-->\s*\n?")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    template: str

    def render(self, **values: str) -> str:
        out = self.template
        for key, value in values.items():
            out = out.replace("{{" + key + "}}", value)
        missing = re.findall(r"\{\{(\w+)\}\}", out)
        if missing:
            raise KeyError(f"Prompt {self.name} missing values: {missing}")
        return out


@cache
def load_prompt(name: str) -> Prompt:
    raw = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    m = _VERSION_RE.match(raw)
    version = m.group(1) if m else "0"
    return Prompt(name=name, version=f"{name}@{version}", template=raw[m.end():] if m else raw)
