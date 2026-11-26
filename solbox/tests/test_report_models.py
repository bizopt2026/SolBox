"""팀 업무 보고 모델(보고 건·계획 항목·대화)과 온톨로지 등록."""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app import ontology
from app.models import Customer, Opportunity, Project, Report, ReportMessage, ReportPlanItem


def _report(db, unit="week", start=date(2026, 11, 23), end=date(2026, 11, 29)):
    r = Report(unit=unit, period_start=start, period_end=end)
    db.add(r)
    db.commit()
    return r


def test_report_defaults(db):
    r = _report(db)
    assert r.status == "draft" and r.status in ontology.REPORT_STATUSES
    assert r.summary_md == "" and r.highlights_md == "" and r.direction == ""
    assert r.section_notes == {} and r.file_names == {}
    assert r.snapshot is None and r.plan_draft is None and r.confirmed_at is None
    assert r.unit in ontology.REPORT_UNITS and ontology.REPORT_UNIT_DEFAULT == "week"


def test_report_unique_unit_period(db):
    _report(db)
    db.add(Report(unit="week", period_start=date(2026, 11, 23), period_end=date(2026, 11, 29)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    _report(db, unit="day", start=date(2026, 11, 23), end=date(2026, 11, 23))  # 단위가 다르면 같은 시작일도 된다


def test_report_children_order_and_cascade(db):
    r = _report(db)
    r.plan_items = [ReportPlanItem(task="둘째", position=1), ReportPlanItem(task="첫째", position=0)]
    r.messages = [ReportMessage(role="user", content="안녕")]
    db.commit()
    db.expire_all()
    assert [p.task for p in r.plan_items] == ["첫째", "둘째"]
    assert r.plan_items[0].result == "" and r.plan_items[0].owner == "" and r.plan_items[0].note == ""
    db.delete(r)
    db.commit()
    assert db.query(ReportPlanItem).count() == 0 and db.query(ReportMessage).count() == 0


def test_plan_item_link_nulls_on_customer_delete(db):
    c = Customer(name="한빛병원")
    db.add(c)
    db.flush()
    o = Opportunity(name="기회", customer_id=c.id)
    p = Project(name="사업", customer_id=c.id)
    db.add_all([o, p])
    db.flush()
    r = _report(db)
    item = ReportPlanItem(report_id=r.id, task="견적 보내기", customer_id=c.id, opportunity_id=o.id, project_id=p.id)
    db.add(item)
    db.commit()
    db.delete(o)
    db.delete(p)
    db.commit()
    db.delete(c)
    db.commit()
    db.expire_all()
    assert (item.customer_id, item.opportunity_id, item.project_id) == (None, None, None)
    assert item.task == "견적 보내기"


def test_checked_in_report_nulls_on_report_delete(db):
    first = _report(db)
    second = _report(db, start=date(2026, 11, 30), end=date(2026, 12, 6))
    item = ReportPlanItem(report_id=first.id, task="a", result="done", checked_in_report_id=second.id)
    db.add(item)
    db.commit()
    db.delete(second)
    db.commit()
    db.expire_all()
    assert item.checked_in_report_id is None and item.result == "done"


def test_carried_from_unique(db):
    first = _report(db)
    second = _report(db, start=date(2026, 11, 30), end=date(2026, 12, 6))
    src = ReportPlanItem(report_id=first.id, task="a")
    db.add(src)
    db.commit()
    db.add(ReportPlanItem(report_id=second.id, task="a", carried_from_id=src.id))
    db.commit()
    db.add(ReportPlanItem(report_id=second.id, task="a 또", carried_from_id=src.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_report_in_nav_today(app):
    today = next(g for g in ontology.nav_groups() if g.label == "오늘")
    paths = [i.path for i in today.items]
    assert "reports" in paths
    assert paths.index("reports") == paths.index("activities") + 1


def test_report_routes_minimal(user_client, db):
    assert user_client.get("/reports").status_code == 200
    r = _report(db)
    assert user_client.get(f"/reports/{r.id}").status_code == 200
    assert user_client.get("/reports/99999").status_code == 404


def test_customer_merge_moves_plan_item_link(db, settings, monkeypatch):
    from app import merge
    from tests.conftest import make_user

    monkeypatch.setattr(merge, "take_backup", lambda s: "fake")
    admin = make_user(db, "boss", role="admin")
    keep, absorb = Customer(name="한빛"), Customer(name="한빛병원")
    db.add_all([keep, absorb])
    db.commit()
    r = _report(db)
    item = ReportPlanItem(report_id=r.id, task="견적", customer_id=absorb.id)
    db.add(item)
    db.commit()
    merge.execute(db, "customer", keep.id, absorb.id, admin, settings)
    db.expire_all()
    assert item.customer_id == keep.id and item.task == "견적"
