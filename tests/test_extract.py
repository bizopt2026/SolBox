from pptx import Presentation
from pptx.util import Inches
from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter

from app.extract import ExtractResult, extract_text


def test_pptx_text_and_notes(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "에너지 절감 솔루션"
    table = slide.shapes.add_table(1, 1, Inches(1), Inches(3), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "표 셀 값"
    slide.notes_slide.notes_text_frame.text = "발표자 메모"
    p = tmp_path / "a.pptx"
    prs.save(p)
    r = extract_text(p)
    assert r.status == "ok"
    assert "## 슬라이드 1" in r.text
    assert "에너지 절감 솔루션" in r.text
    assert "표 셀 값" in r.text
    assert "발표자 메모" in r.text


def test_docx_paragraphs_and_table(tmp_path):
    doc = Document()
    doc.add_paragraph("도입 효과")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "연 10%"
    p = tmp_path / "a.docx"
    doc.save(p)
    r = extract_text(p)
    assert r.status == "ok"
    assert "도입 효과" in r.text
    assert "연 10%" in r.text


def test_xlsx_rows(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "스펙"
    ws.append(["전력", 220])
    p = tmp_path / "a.xlsx"
    wb.save(p)
    wb.close()
    r = extract_text(p)
    assert r.status == "ok"
    assert "## 스펙" in r.text
    assert "전력 | 220" in r.text


def test_cp949_text_file(tmp_path):
    p = tmp_path / "memo.txt"
    p.write_bytes("한글 메모".encode("cp949"))
    assert extract_text(p) == ExtractResult("ok", "한글 메모")


def test_unsupported_extension(tmp_path):
    p = tmp_path / "a.hwp"
    p.write_bytes(b"x")
    assert extract_text(p) == ExtractResult("unsupported", "")


def test_corrupt_file_is_failed_not_exception(tmp_path):
    p = tmp_path / "x.pptx"
    p.write_bytes(b"not a zip")
    assert extract_text(p).status == "failed"


def test_pdf_without_text_is_failed(tmp_path):
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    p = tmp_path / "scan.pdf"
    with p.open("wb") as f:
        w.write(f)
    assert extract_text(p).status == "failed"


def test_uppercase_extension(tmp_path):
    p = tmp_path / "NOTE.TXT"
    p.write_text("hello", encoding="utf-8")
    assert extract_text(p) == ExtractResult("ok", "hello")
