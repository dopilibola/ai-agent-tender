"""Telegram-HTML → safe web HTML. Only Telegram's own tags survive; links only http(s); everything else is text."""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

ALLOWED = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote"}
VOID_LIKE: set[str] = set()


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ALLOWED:
            self.out.append(f"<{tag}>")
            self.stack.append(tag)
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith(("https://", "http://")):
                self.out.append(f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer nofollow">')
            else:
                self.out.append("<a>")
            self.stack.append("a")
        elif tag in ("tg-spoiler",) or (tag == "span" and ("class", "tg-spoiler") in attrs):
            self.out.append('<span class="spoiler">')
            self.stack.append("span")

    def handle_endtag(self, tag):
        tag = "span" if tag == "tg-spoiler" else tag
        if tag in self.stack:
            while self.stack:  # close everything opened after it (keeps output well-formed)
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))

    def result(self) -> str:
        self.close()
        return "".join(self.out) + "".join(f"</{t}>" for t in reversed(self.stack))


def to_web(text: str) -> str:
    p = _Sanitizer()
    p.feed(text or "")
    return p.result()
