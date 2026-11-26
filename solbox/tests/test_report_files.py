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
