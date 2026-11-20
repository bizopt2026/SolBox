from datetime import date

from app.knowledge import KnowledgeMirror, safe_name
from app.models import Activity, Project, Customer, Partner, Solution, SolutionFile
from tests.conftest import fu_rows, need_rows


def _read(path):
    return path.read_text(encoding="utf-8")


def test_solution_card_written_with_korean_labels(db, tmp_path):
    m = KnowledgeMirror(tmp_path)
    p = Partner(name="그린테크"); db.add(p); db.flush()
    s = Solution(name="AI 에너지 절감", summary="전력 10% 절감", stage="boxed", partner_id=p.id, tags="에너지,AI")
    db.add(s); db.commit()
    m.sync(s)
    text = (tmp_path / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")
    assert text.startswith("# AI 에너지 절감")
    assert "박스 등록" in text and "그린테크" in text and f"partners/{p.id}.md" in text


def test_activity_moves_when_year_changes(db, tmp_path):
    m = KnowledgeMirror(tmp_path)
    a = Activity(raw_text="원문", title="킥오프", date=date(2026, 12, 30)); db.add(a); db.commit()
    m.sync(a)
    a.date = date(2027, 1, 2); db.commit(); m.sync(a)
    assert not (tmp_path / "activities" / "2026" / f"{a.id}.md").exists()
    assert "## 원문" in (tmp_path / "activities" / "2027" / f"{a.id}.md").read_text(encoding="utf-8")


def test_remove_solution_deletes_folder_and_is_idempotent(db, tmp_path):
    m = KnowledgeMirror(tmp_path)
    s = Solution(name="솔루션"); db.add(s); db.commit()
    f = SolutionFile(solution_id=s.id, original_name="제안서.pptx"); db.add(f); db.commit()
    m.sync(s)
    m.write_file_text(f, "본문 텍스트")
    file_md = tmp_path / "solutions" / str(s.id) / "files" / f"{f.id}-제안서.pptx.md"
    assert _read(file_md) == "본문 텍스트"
    m.remove_file_text(f)
    assert not file_md.exists()
    m.remove_file_text(f)  # 없어도 오류 아님
    m.write_file_text(f, "again")
    m.remove(s)
    assert not (tmp_path / "solutions" / str(s.id)).exists()
    m.remove(s)  # 두 번째 호출도 예외 없음


def test_safe_name():
    assert safe_name('제안서: "최종"?.pptx') == "제안서_ _최종__.pptx"
    assert safe_name("  ..  ") == "file"
    assert len(safe_name("가" * 200)) == 80


def test_customer_project_partner_paths_and_links(db, tmp_path):
    m = KnowledgeMirror(tmp_path)
    c = Customer(name="한빛병원", industry="의료"); db.add(c); db.flush()
    b = Project(name="스마트 병동", customer_id=c.id, status="진행"); db.add(b); db.commit()
    m.sync(c); m.sync(b)
    assert m.path_for(c) == tmp_path / "customers" / f"{c.id}.md"
    assert m.path_for(b) == tmp_path / "projects" / f"{b.id}.md"
    ctext = _read(m.path_for(c))
    assert ctext.startswith("# 한빛병원") and "- 업종: 의료" in ctext
    assert f"스마트 병동 (projects/{b.id}.md)" in ctext
    assert f"- 고객: 한빛병원 (customers/{c.id}.md)" in _read(m.path_for(b))


def test_activity_md_sections_and_links(db, tmp_path):
    m = KnowledgeMirror(tmp_path)
    c = Customer(name="한빛병원"); s = Solution(name="AI 에너지 절감")
    db.add_all([c, s]); db.flush()
    a = Activity(
        kind="tapping", date=date(2026, 5, 3), title="태핑", raw_text="원문 메모",
        summary_md="요약 내용",
        follow_ups=fu_rows(("자료 송부", "김", "2026-05-10", False)),
        needs=need_rows(("한빛병원", "절감 관심")),
        customers=[c], solutions=[s],
    )
    db.add(a); db.commit()
    m.sync(a)
    text = _read(tmp_path / "activities" / "2026" / f"{a.id}.md")
    assert text.startswith("# 태핑") and "고객 태핑" in text
    assert f"한빛병원 (customers/{c.id}.md)" in text
    assert f"AI 에너지 절감 (solutions/{s.id}/card.md)" in text
    order = [text.index(h) for h in ("## 정리본", "## 후속 조치", "## 고객 니즈", "## 원문")]
    assert order == sorted(order)
    assert "자료 송부" in text and "절감 관심" in text and "요약 내용" in text


def test_linked_activities_section_newest_first_and_undated(db, tmp_path):
    m = KnowledgeMirror(tmp_path)
    s = Solution(name="S"); c = Customer(name="C"); db.add_all([s, c]); db.flush()
    a1 = Activity(raw_text="x", title="옛", date=date(2025, 1, 1), solutions=[s], customers=[c])
    a2 = Activity(raw_text="x", title="새", date=date(2026, 2, 2), solutions=[s], customers=[c])
    a3 = Activity(raw_text="x", title="무일자", solutions=[s], customers=[c])
    db.add_all([a1, a2, a3]); db.commit()
    m.sync(a3)
    assert (tmp_path / "activities" / "undated" / f"{a3.id}.md").exists()
    for obj in (s, c):
        m.sync(obj)
        text = _read(m.path_for(obj))
        assert "## 활동 기록" in text
        i2, i1 = text.index(f"activities/2026/{a2.id}.md"), text.index(f"activities/2025/{a1.id}.md")
        i3 = text.index(f"activities/undated/{a3.id}.md")
        assert i2 < i1 < i3
        assert "2026-02-02" in text and "새" in text
    p = Partner(name="P"); db.add(p); db.commit(); m.sync(p)
    assert "## 활동 기록" not in _read(m.path_for(p))
