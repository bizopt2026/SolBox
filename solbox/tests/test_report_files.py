"""팀 업무 보고 파일 만들기 (app/report_files.py): MD·워드·PPT 와 만들기·내려받기 라우트."""
import re
from datetime import date
from urllib.parse import unquote

import pytest
from docx import Document
from docx.oxml.ns import qn
from pptx import Presentation

from app import report_files, reports
from app.models import Report, ReportPlanItem
from tests.conftest import nloc

W1 = (date(2026, 11, 16), date(2026, 11, 22))
W2 = (date(2026, 11, 23), date(2026, 11, 29))
STEM = "업무보고_주간_2026-11-23~29"


def _confirmed(db, unit="week", period=W2, plan=3, status="confirmed"):
    prev = Report(unit=unit, period_start=period[0] - (period[1] - period[0]) - __import__("datetime").timedelta(days=1), period_end=period[0] - __import__("datetime").timedelta(days=1), status="confirmed")
    db.add(prev)
    db.commit()
    db.add_all([ReportPlanItem(report_id=prev.id, position=i, task=f"지난 {i}", owner="김", result="done" if i == 0 else "doing", result_note="메모|표") for i in range(3)])
    r = Report(unit=unit, period_start=period[0], period_end=period[1], status="planning", summary_md="- 수주 1건\n- 신규 발굴 2건", highlights_md="- 예산 지연", direction="수주 착수")
    db.add(r)
    db.commit()
    db.add_all([ReportPlanItem(report_id=r.id, position=i, task=f"할 일 {i}", owner="이", due=date(2026, 12, 4)) for i in range(plan)])
    db.commit()
    if status == "confirmed":
        reports.confirm(db, r, None)
        db.commit()
    return r


def _content(db, **kw):
    return reports.report_content(db, _confirmed(db, **kw))


def test_content_shape(db):
    c = _content(db)
    assert c["title"] == "주간 업무 보고" and [k[0] for k in c["kpis"]][:2] == ["활동 기록", "새 고객 니즈"] and len(c["kpis"]) == 8
    assert c["done_count"] == 1 and len(c["check"]) == 3 and c["summary"] == ["수주 1건", "신규 발굴 2건"]
    assert set(c["notes"]) == {"kpi", "opps", "proposals", "check", "plan", "issues"}
    assert c["plan"][0][2] == "12-04"


def test_md_has_notes_before_tables(db, tmp_path):
    path = report_files.write(_content(db), "md", tmp_path, STEM)
    assert path.name == STEM + ".md" and not list(tmp_path.glob("*.part"))
    lines = path.read_text(encoding="utf-8").splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    assert len(heads) >= 6
    for i in heads:
        first = next(ln for ln in lines[i + 1:] if ln.strip())
        assert not first.startswith("|"), lines[i]
    assert "메모\\|표" in path.read_text(encoding="utf-8")


def test_docx_opens_and_has_sections(db, tmp_path):
    path = report_files.write(_content(db), "docx", tmp_path, STEM)
    doc = Document(str(path))
    heads = [p.text for p in doc.paragraphs if p.style.name == "Heading 2"]
    assert len(heads) == 6 and heads[3].startswith("4. 지난 계획 점검 (완료 1/3)")
    assert any(t.rows[0]._tr.trPr is not None and t.rows[0]._tr.trPr.find(qn("w:tblHeader")) is not None for t in doc.tables)
    assert any(p.text.startswith("○ ") for p in doc.paragraphs)


def _texts(prs):
    return [sh.text_frame.text for sl in prs.slides for sh in sl.shapes if sh.has_text_frame]


def test_pptx_six_slides_and_split(db, tmp_path):
    prs = Presentation(str(report_files.write(_content(db), "pptx", tmp_path, STEM)))
    assert len(prs.slides) == 6
    big = Presentation(str(report_files.write(_content(db, period=(date(2026, 12, 21), date(2026, 12, 27)), plan=15), "pptx", tmp_path / "b", "x")))
    assert len(big.slides) == 7 and any("(계속)" in t for t in _texts(big))


def test_day_and_month_wording(db, tmp_path):
    day = _content(db, unit="day", period=(date(2026, 11, 23), date(2026, 11, 23)))
    month = _content(db, unit="month", period=(date(2026, 11, 1), date(2026, 11, 30)))
    assert any("오늘 한눈에" in t for t in _texts(Presentation(str(report_files.write(day, "pptx", tmp_path, "d")))))
    assert any("이번 달 한눈에" in t for t in _texts(Presentation(str(report_files.write(month, "pptx", tmp_path, "m")))))


def test_bad_format(db, tmp_path):
    with pytest.raises(ValueError):
        report_files.write(_content(db), "pdf", tmp_path, STEM)


TOK = re.compile(r'name="_tok" value="([^"]+)"')


def test_build_and_download(user_client, db, app):
    r = _confirmed(db)
    resp = user_client.post(f"/reports/{r.id}/files", data={"fmt": "md"}, follow_redirects=False)
    assert resp.status_code == 303 and "notice=report_file_built" in nloc(resp)
    db.refresh(r)
    assert r.file_names == {"md": STEM + ".md"} and r.files_built_at is not None
    assert STEM + ".md" in user_client.get(f"/reports/{r.id}?step=files").text
    dl = user_client.get(f"/reports/{r.id}/files/md")
    assert dl.status_code == 200 and dl.text.startswith("# 주간 업무 보고")
    assert unquote(re.search(r"filename\*=UTF-8''(\S+)", dl.headers["content-disposition"]).group(1)) == STEM + ".md"
    assert user_client.get(f"/reports/{r.id}/files/pptx").status_code == 404
    assert user_client.post(f"/reports/{r.id}/files", data={"fmt": "pdf"}).status_code == 400


def test_download_requires_confirmed(user_client, db):
    r = _confirmed(db, status="planning")
    resp = user_client.post(f"/reports/{r.id}/files", data={"fmt": "md"}, follow_redirects=False)
    assert "notice=report_not_confirmed" in nloc(resp)
    assert "notice=report_not_confirmed" in nloc(user_client.get(f"/reports/{r.id}/files/md", follow_redirects=False))


def test_download_filename_korean(user_client, db):
    r = _confirmed(db)
    user_client.post(f"/reports/{r.id}/files", data={"fmt": "docx"})
    cd = user_client.get(f"/reports/{r.id}/files/docx").headers["content-disposition"]
    assert "filename*=UTF-8''" + "%EC%97%85%EB%AC%B4" in cd and 'filename="report-' in cd


def test_unconfirm_drops_files(user_client, db, app):
    r = _confirmed(db)
    user_client.post(f"/reports/{r.id}/files", data={"fmt": "md"})
    folder = app.state.settings.data_dir / "reports" / str(r.id)
    assert folder.exists()
    user_client.post(f"/reports/{r.id}/unconfirm")
    db.refresh(r)
    assert r.file_names == {} and not folder.exists()


# ---- 형식 규칙 고정 (설계 §5.6, 시안) ----

def _cell_fill(cell):
    shd = cell._tc.tcPr.find(qn("w:shd"))
    return shd.get(qn("w:fill")) if shd is not None else None


def _table_with_head(doc, first):
    return next(t for t in doc.tables if t.rows[0].cells[0].text == first)


def test_md_headings_exact(db, tmp_path):
    c = _content(db)
    text = report_files.write(c, "md", tmp_path, STEM).read_text(encoding="utf-8")
    heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert heads == [
        "## 1. 요약 숫자", "## 2. 주요 성과", "## 3. 영업기회 단계 변화", "## 4. 제안 진행",
        "## 5. 지난 계획 점검 (완료 1/3)", f"## 6. 다음 주 계획 ({c['next_period']})", "## 7. 이슈·건의",
    ]
    assert text.startswith("# 주간 업무 보고\n")


def test_docx_format_rules(db, tmp_path):
    from docx.shared import Pt

    doc = Document(str(report_files.write(_content(db), "docx", tmp_path, STEM)))
    sec = doc.sections[0]
    cm = lambda v: round(v.cm, 2)  # noqa: E731 - 쪽 크기는 저장할 때 twip 단위로 반올림된다
    assert (cm(sec.page_width), cm(sec.page_height)) == (21.0, 29.7)
    assert (cm(sec.left_margin), cm(sec.right_margin), cm(sec.top_margin), cm(sec.bottom_margin)) == (2.0, 2.0, 1.8, 1.8)
    title = doc.paragraphs[0]
    assert title.text == "주간 업무 보고" and title.runs[0].font.size == Pt(20)
    assert _cell_fill(doc.tables[0].rows[0].cells[0]) == "F1F5F9"  # 핵심 요약 상자
    head_run = [p for p in doc.paragraphs if p.style.name == "Heading 2"][0].runs[0]
    assert head_run.font.size == Pt(12.5) and str(head_run.font.color.rgb) == "1F3A8A"
    check = _table_with_head(doc, "계획")
    assert _cell_fill(check.rows[0].cells[0]) == "E8EDF8"
    assert check.rows[0]._tr.trPr.find(qn("w:tblHeader")) is not None and check.autofit is False
    colors = {row.cells[2].text: str(next(r for r in row.cells[2].paragraphs[0].runs if r.text).font.color.rgb) for row in check.rows[1:]}
    assert colors["완료"] == "15803D" and colors["진행 중"] == "B45309"
    normal = doc.styles["Normal"]
    assert normal.font.name == "맑은 고딕" and normal.element.rPr.rFonts.get(qn("w:eastAsia")) == "맑은 고딕"
    assert next(r for r in check.rows[1].cells[0].paragraphs[0].runs if r.text)._element.rPr.rFonts.get(qn("w:eastAsia")) == "맑은 고딕"


def test_docx_issues_note_before_bullets(db, tmp_path):
    doc = Document(str(report_files.write(_content(db), "docx", tmp_path, STEM)))
    texts = [p.text for p in doc.paragraphs]
    i = next(k for k, t in enumerate(texts) if t == "6. 이슈·건의")
    assert texts[i + 1].startswith("○ ") and texts[i + 2] == "• 예산 지연"


@pytest.mark.parametrize("unit,period,this,last,nxt", [
    ("day", (date(2026, 11, 23), date(2026, 11, 23)), "오늘", "지난 보고", "다음 평일"),
    ("week", W2, "이번 주", "지난 주", "다음 주"),
    ("month", (date(2026, 11, 1), date(2026, 11, 30)), "이번 달", "지난 달", "다음 달"),
])
def test_unit_wording_everywhere(db, tmp_path, unit, period, this, last, nxt):
    c = _content(db, unit=unit, period=period)
    md = report_files.write(c, "md", tmp_path, "w").read_text(encoding="utf-8")
    assert f"## 6. {nxt} 계획 (" in md
    doc = Document(str(report_files.write(c, "docx", tmp_path, "w")))
    assert any(p.text.startswith(f"5. {nxt} 계획 (") for p in doc.paragraphs)
    texts = _texts(Presentation(str(report_files.write(c, "pptx", tmp_path, "w"))))
    assert any(t == f"{this} 한눈에" for t in texts)
    assert any(t.startswith(f"{last} 계획 점검 — 완료") for t in texts)
    assert any(t.startswith(f"{nxt} 계획 (") for t in texts)
    assert not any("지난 일간" in t for t in texts)


def test_pptx_nothing_overlaps_footer(db, tmp_path):
    from pptx.util import Inches

    def prs_of(**kw):
        c = _content(db, **kw)
        c["opps"] = [(f"기회{i}", "고객", "발굴") for i in range(9)]
        c["summary"] = [f"성과 {i}" for i in range(8)]
        return Presentation(str(report_files.write(c, "pptx", tmp_path / str(len(kw)), "f")))

    for prs in (prs_of(), prs_of(period=(date(2026, 12, 21), date(2026, 12, 27)), plan=15)):
        for n, sl in enumerate(prs.slides):
            if n == 0:
                continue  # 표지는 바닥글이 없다
            footer = min(sh.top for sh in sl.shapes if sh.has_text_frame and sh.text_frame.text.startswith("주간 업무 보고 ·"))
            assert footer == Inches(6.95)
            for sh in sl.shapes:
                if sh.top < footer:
                    assert sh.top + sh.height <= footer, (n + 1, sh.shape_type, sh.top + sh.height)
