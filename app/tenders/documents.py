"""Turn a lot's downloaded attachments into text for Gemini.

Handles PDF (text layer via pypdf; scanned / badly-OCR'd PDFs are sent to Gemini as the file
itself), DOCX, DOC/RTF (macOS `textutil`), and RAR/ZIP/7z archives (extracted with `bsdtar`,
nested archives too). Archives come from third parties: each is extracted into its own empty
folder, with size / file-count limits, and nothing extracted is ever executed.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

KIND_ORDER = {"tz": 0, "tech_doc": 1, "additional": 2, "contract_draft": 3}
SKIP_KINDS = {"expertise"}  # state-expertise stamps: scans with no useful text
ARCHIVES = {"rar", "zip", "7z"}
MAX_EXTRACT_BYTES = 200 * 1024 * 1024
MAX_EXTRACT_FILES = 200
MAX_PDF_PAGES = 150
TZ_NAME_HINT = re.compile(r"техн|texnik|тз|\btz\b|задани|topshiriq|топшириқ", re.I)
_WEIRD = set("{}[]|\\@#$%^~<>")


@dataclass
class DocText:
    label: str
    filename: str
    text: str = ""
    mode: str = "text"  # text | pdf_file (scanned → uploaded to Gemini as a file) | skipped
    note: str = ""
    path: Path | None = None
    size: int = 0
    pages: int = 0


def _ext(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def text_quality_ok(text: str) -> bool:
    """False for empty or garbage OCR layers like 'YTBEP}I(AAK)'."""
    sample = text[:20000]
    letters = sum(ch.isalpha() for ch in sample)
    if letters < 200:
        return False
    weird = sum(ch in _WEIRD for ch in sample)
    words = re.findall(r"\w{3,}", sample)
    mixed = sum(bool(re.search(r"[A-Za-z]", w) and re.search(r"[А-Яа-яЁёЎўҚқҒғҲҳ]", w)) for w in words)
    return weird / max(letters, 1) < 0.01 and mixed / max(len(words), 1) < 0.05


def pdf_text(path: Path) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = len(reader.pages)
    parts = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # broken page — keep the rest
            parts.append("")
    return "\n".join(parts), pages


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(a, b)
    return text


def textutil_text(path: Path) -> str | None:
    if not shutil.which("textutil"):  # macOS only
        return None
    out = subprocess.run(
        ["textutil", "-convert", "txt", "-stdout", str(path)],
        capture_output=True, timeout=60, check=False,
    )
    return out.stdout.decode("utf-8", "ignore") if out.returncode == 0 else None


def extract_archive(path: Path, dest: Path) -> list[Path]:
    """bsdtar refuses absolute paths and '..' entries by default; we also cap size and count."""
    if not dest.exists():
        tar = shutil.which("bsdtar") or shutil.which("tar")
        if not tar:
            raise RuntimeError("bsdtar not installed")
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        res = subprocess.run([tar, "-xf", str(path), "-C", str(tmp)], capture_output=True, timeout=120, check=False)
        files = [p for p in tmp.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        if len(files) > MAX_EXTRACT_FILES or total > MAX_EXTRACT_BYTES:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"archive too big: {len(files)} files, {total // 1_048_576} MB")
        if res.returncode != 0 and not files:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(res.stderr.decode("utf-8", "ignore")[:200] or "extract failed")
        tmp.rename(dest)
    return sorted(
        (p for p in dest.rglob("*") if p.is_file() and not p.name.startswith(".")),
        key=lambda p: (not TZ_NAME_HINT.search(p.name), p.name),
    )


def _one_file(path: Path, label: str, workdir: Path, depth: int) -> list[DocText]:
    ext = _ext(path)
    size = path.stat().st_size if path.exists() else 0
    doc = DocText(label=label, filename=path.name, path=path, size=size)
    try:
        if ext == "pdf":
            text, pages = pdf_text(path)
            doc.pages = pages
            if text_quality_ok(text):
                doc.text = text
                if pages > MAX_PDF_PAGES:
                    doc.note = f"faqat birinchi {MAX_PDF_PAGES} sahifa o'qildi ({pages} dan)"
            else:
                doc.mode, doc.text = "pdf_file", ""
                doc.note = f"skaner yoki sifatsiz matn qatlami ({pages} sahifa)"
        elif ext == "docx":
            doc.text = docx_text(path)
        elif ext in {"doc", "rtf", "odt"}:
            text = textutil_text(path)
            if text is None:
                doc.mode, doc.note = "skipped", f".{ext} fayl o'qilmadi (konverter yo'q)"
            else:
                doc.text = text
        elif ext in {"txt", "csv"}:
            doc.text = path.read_text("utf-8", "ignore")
        elif ext in ARCHIVES:
            if depth >= 2:
                return [DocText(label, path.name, mode="skipped", note="ichma-ich arxiv juda chuqur")]
            inner_dir = workdir / "extracted" / (path.stem if depth == 0 else f"{path.parent.name}-{path.stem}")
            out: list[DocText] = []
            for inner in extract_archive(path, inner_dir):
                out.extend(_one_file(inner, f"{label} › {inner.name}", inner_dir, depth + 1))
            return out or [DocText(label, path.name, mode="skipped", note="arxiv bo'sh")]
        else:
            doc.mode, doc.note = "skipped", f".{ext} formati qo'llab-quvvatlanmaydi"
    except Exception as e:
        log.warning("documents.extract_failed", extra={"file": str(path), "error": str(e)[:200]})
        doc.mode, doc.text, doc.note = "skipped", "", f"o'qib bo'lmadi: {type(e).__name__}"
    doc.text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", doc.text)).strip()
    if doc.mode == "text" and not doc.text:
        doc.mode, doc.note = "skipped", doc.note or "matn yo'q"
    return [doc]


def extract_lot_documents(
    files: list[dict], workdir: Path, *, max_chars: int, scan_pdf_max_bytes: int, scan_pdf_max_pages: int
) -> list[DocText]:
    """Return docs in priority order (TZ first), trimmed to a shared character budget.

    At most one scanned PDF (the highest-priority one within the size/page limits) is kept for
    uploading to Gemini, which reads scans itself.
    """
    ordered = sorted(
        (f for f in files if f.get("kind") not in SKIP_KINDS),
        key=lambda f: KIND_ORDER.get(f.get("kind"), 9),
    )
    docs: list[DocText] = []
    for f in ordered:
        local = f.get("local")
        label = f"{f.get('label') or f.get('kind')}"
        if not local or not Path(local).exists():
            docs.append(DocText(label, f.get("name") or f.get("path", "?"), mode="skipped",
                                note="yuklab olinmagan (hajmi katta yoki xato)"))
            continue
        docs.extend(_one_file(Path(local), label, workdir, 0))

    budget = max_chars
    file_used = False
    for d in docs:
        if d.mode == "pdf_file":
            if file_used:
                d.mode, d.note = "skipped", d.note + "; faqat bitta skaner fayl yuboriladi"
            elif d.size > scan_pdf_max_bytes or d.pages > scan_pdf_max_pages:
                d.mode, d.note = "skipped", d.note + "; hajmi yoki sahifalari limitdan katta"
            else:
                file_used = True
        elif d.mode == "text":
            if budget <= 0:
                d.mode, d.note, d.text = "skipped", "token limiti tugadi", ""
            elif len(d.text) > budget:
                d.note = f"qisqartirildi: {budget} / {len(d.text)} belgi"
                d.text = d.text[:budget]
                budget = 0
            else:
                budget -= len(d.text)
    return docs
