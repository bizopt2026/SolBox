"""업무 보고 계획 항목: 합치기 미리보기·지우기 영향·백업·지식 사본 제외 목록."""
from datetime import date, datetime

from app import impact, merge, ontology
from app.backup import run_backup, verify
from app.models import Customer, Opportunity, Project, Report, ReportPlanItem
from tests.conftest import make_user


def _item(db, **kw):
    r = Report(unit="week", period_start=date(2026, 11, 23), period_end=date(2026, 11, 29))
    db.add(r)
    db.commit()
    item = ReportPlanItem(report_id=r.id, task="견적 보내기", **kw)
    db.add(item)
    db.commit()
    return item


def test_customer_merge_moves_plan_links(db, settings, monkeypatch):
    monkeypatch.setattr(merge, "take_backup", lambda s: "fake")
    admin = make_user(db, "boss", role="admin")
    keep, absorb = Customer(name="한빛"), Customer(name="한빛병원")
    db.add_all([keep, absorb])
    db.commit()
    item = _item(db, customer_id=absorb.id)
    pv = merge.preview(db, "customer", keep, absorb)
    assert ("업무 보고 계획 항목", "R38", 1, "") in pv.counts
    merge.execute(db, "customer", keep.id, absorb.id, admin, settings)
    db.expire_all()
    assert item.customer_id == keep.id and item.task == "견적 보내기"


def test_delete_impact_lists_plan_links(db):
    c = Customer(name="한빛병원")
    db.add(c)
    db.flush()
    o = Opportunity(name="기회", customer_id=c.id)
    p = Project(name="사업", customer_id=c.id)
    db.add_all([o, p])
    db.commit()
    _item(db, customer_id=c.id, opportunity_id=o.id, project_id=p.id)
    for obj in (c, o, p):
        assert "업무 보고 계획 항목 1건" in " ".join(impact.sentences(db, impact.type_code(obj), obj))


def test_delete_impact_without_plan_links_has_no_line(db):
    c = Customer(name="빈고객")
    db.add(c)
    db.commit()
    assert "업무 보고" not in " ".join(impact.sentences(db, "customer", c))


def test_backup_includes_reports_folder(app, db, settings):
    folder = settings.reports_dir / "7"
    folder.mkdir(parents=True)
    (folder / "보고.md").write_text("내용", encoding="utf-8")
    path = run_backup(settings, now=datetime(2026, 11, 18, 2, 0, 0))
    assert (path / "reports" / "7" / "보고.md").read_text(encoding="utf-8") == "내용"
    assert verify(path) == []


def test_unmirrored_keys_are_real_relations_with_reason():
    keys = {r.key for r in ontology.RELATIONS}
    assert ontology.UNMIRRORED
    for key, reason in ontology.UNMIRRORED.items():
        assert key in keys, key
        assert isinstance(reason, str) and reason.strip(), key
