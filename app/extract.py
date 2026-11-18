"""업로드한 자료 파일에서 텍스트를 뽑는다 (pptx/pdf/docx/xlsx/txt)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExtractResult:
    status: str  # ok / failed / unsupported
    text: str


def decode_text(data: bytes) -> str:
    """UTF-8(BOM 허용) → cp949 → UTF-8 대체 문자 순으로 해석한다."""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _plain(path: Path) -> str:
    return decode_text(path.read_bytes())


def _pptx(path: Path) -> str:
    from pptx import Presentation

    parts: list[str] = []
    for number, slide in enumerate(Presentation(str(path)).slides, start=1):
        parts.append(f"## 슬라이드 {number}")
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text
            if notes.strip():
                parts.append(f"[노트] {notes}")
    return "\n".join(parts)


def _docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _xlsx(path: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        parts: list[str] = []
        for ws in wb.worksheets:
            parts.append(f"## {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    parts.append(" | ".join(cells).rstrip(" |"))
        return "\n".join(parts)
    finally:
        wb.close()


def _pdf(path: Path) -> str:
    from pypdf import PdfReader

    with path.open("rb") as f:
        reader = PdfReader(f)
        parts: list[str] = []
        for number, page in enumerate(reader.pages, start=1):
            parts.append(f"## {number}쪽")
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


_PLAIN = {".txt", ".md", ".csv"}
_READERS = {".pptx": _pptx, ".docx": _docx, ".xlsx": _xlsx, ".pdf": _pdf}


def _has_content(text: str) -> bool:
    """`## ` 머리글을 빼고 남는 글자가 있는지."""
    return any(
        line.strip() and not line.startswith("## ") for line in text.splitlines()
    )


def extract_text(path: Path) -> ExtractResult:
    suffix = path.suffix.lower()
    if suffix in _PLAIN:
        reader, check = _plain, lambda t: bool(t.strip())
    elif suffix in _READERS:
        reader, check = _READERS[suffix], _has_content
    else:
        return ExtractResult("unsupported", "")
    try:
        text = reader(path)
    except Exception:
        return ExtractResult("failed", "")
    if not check(text):
        return ExtractResult("failed", "")
    return ExtractResult("ok", text.strip())
