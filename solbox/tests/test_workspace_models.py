"""AI 코워크 ①: 사업 방향·과제 모델, 관계, 이력, 지식 사본."""
from datetime import date, timedelta

from sqlalchemy import select

from app import ontology
from app.knowledge import KnowledgeMirror
from app.models import Initiative, Opportunity, Customer, Solution, Strategy, WorkspaceChange


def _changes(db, **where):
    stmt = select(WorkspaceChange).order_by(WorkspaceChange.id)
    for k, v in where.items():
        stmt = stmt.where(getattr(WorkspaceChange, k) == v)
    return db.scalars(stmt).all()


def test_initiative_links_roundtrip(db):
    c = Customer(name="한빛병원")
    s = Solution(name="ESS")
    o = Opportunity(name="한빛 ESS", customer=c)
    i = Initiative(title="실증 추진")
    i.solutions = [s]
    i.opportunities = [o]
    db.add_all([c, s, o, i])
    db.commit()
    groups = ontology.related(db, s)
    assert any(g.target_type == "initiative" and g.total == 1 for g in groups), groups
    back = ontology.path_query(db, i, ["R42"])
    assert [x.id for x in back] == [s.id]
    assert [x.id for x in ontology.path_query(db, o, ["R45"])] == [i.id]


def test_status_change_history(db):
    i = Initiative(title="실증 추진", kind="issue", due=date(2026, 12, 1))
    db.add(i)
    db.commit()
    made = {(c.field, c.old_value, c.new_value) for c in _changes(db, initiative_id=i.id)}
    assert ("status", "", "open") in made and ("kind", "", "issue") in made and ("due", "", "2026-12-01") in made
    assert not any(f == "owner_user_id" for f, _, _ in made)
    before = len(_changes(db, initiative_id=i.id))
    i.status = "doing"
    db.commit()
    rows = _changes(db, initiative_id=i.id)
    assert len(rows) == before + 1
    assert (rows[-1].field, rows[-1].old_value, rows[-1].new_value) == ("status", "open", "doing")
    i.status = "doing"
    i.progress = "부지 후보 3곳"
    db.commit()
    assert len(_changes(db, initiative_id=i.id)) == before + 1


def test_strategy_history_and_active_rule(db):
    today = date(2026, 11, 26)
    s = Strategy(title="공공 에너지 우선", consensus="지자체 우선")
    db.add(s)
    db.commit()
    assert s.status == "draft"
    assert not ontology.strategy_is_active(s, today)
    s.status = "agreed"
    s.consensus = "지자체·공공기관 우선"
    db.commit()
    fields = [c.field for c in _changes(db, strategy_id=s.id)]
    assert fields.count("status") == 2 and fields.count("consensus") == 2
    assert ontology.strategy_is_active(s, today)
    s.period_start, s.period_end = today - timedelta(days=30), today - timedelta(days=1)
    assert not ontology.strategy_is_active(s, today)
    s.period_end = today
    assert ontology.strategy_is_active(s, today)


def test_delete_strategy_keeps_initiatives(db):
    s = Strategy(title="공공 에너지 우선")
    i = Initiative(title="실증 추진", strategy=s)
    db.add_all([s, i])
    db.commit()
    db.delete(s)
    db.commit()
    db.expire_all()
    i = db.get(Initiative, i.id)
    assert i is not None and i.strategy_id is None


def test_delete_linked_solution_keeps_initiative(db):
    s = Solution(name="ESS")
    i = Initiative(title="실증 추진")
    i.solutions = [s]
    db.add_all([s, i])
    db.commit()
    db.delete(s)
    db.commit()
    db.expire_all()
    assert db.get(Initiative, i.id).solutions == []


def test_mirror_writes_strategy_and_initiative(db, settings):
    mirror = KnowledgeMirror(settings.knowledge_dir)
    s = Strategy(title="공공 에너지 우선", consensus="지자체 우선", status="agreed")
    i = Initiative(title="실증 추진", kind="issue", strategy=s, progress="부지 후보 3곳")
    db.add_all([s, i])
    db.commit()
    mirror.sync(s)
    mirror.sync(i)
    st = (settings.knowledge_dir / f"strategies/{s.id}.md").read_text(encoding="utf-8")
    it = (settings.knowledge_dir / f"initiatives/{i.id}.md").read_text(encoding="utf-8")
    assert "공공 에너지 우선" in st and "지자체 우선" in st and "합의" in st
    assert "실증 추진" in it and "이슈" in it and "부지 후보 3곳" in it and f"strategies/{s.id}.md" in it
