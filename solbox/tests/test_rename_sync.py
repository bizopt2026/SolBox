from datetime import date

from app.knowledge import KnowledgeMirror
from app.models import Activity, Project, Customer, Partner, Solution


def _read(settings, *parts):
    return (settings.knowledge_dir.joinpath(*parts)).read_text(encoding="utf-8")


def _activity(db, relation, obj):
    a = Activity(raw_text="x", title="미팅", date=date(2026, 11, 1))
    getattr(a, relation).append(obj)
    db.add(a)
    db.commit()
    return a


def _sync_all(settings, *objs):
    mirror = KnowledgeMirror(settings.knowledge_dir)
    for o in objs:
        mirror.sync(o)


def test_customer_rename_updates_business_and_activity_mirrors(user_client, db, settings):
    c = Customer(name="한빛병원"); db.add(c); db.flush()
    b = Project(name="유지보수", customer_id=c.id); db.add(b)
    a = Activity(raw_text="x", title="미팅", date=date(2026, 11, 1)); a.customers.append(c); db.add(a); db.commit()
    _sync_all(settings, c, b, a)
    user_client.post(f"/customers/{c.id}", data={"name": "한빛종합병원"})
    assert "한빛종합병원" in _read(settings, "projects", f"{b.id}.md")
    assert "한빛종합병원" in _read(settings, "activities", "2026", f"{a.id}.md")


def test_solution_rename_updates_partner_and_activity_mirrors(user_client, db, settings):
    p = Partner(name="파트너"); db.add(p); db.flush()
    s = Solution(name="옛솔루션", partner_id=p.id); db.add(s); db.commit()
    a = _activity(db, "solutions", s)
    _sync_all(settings, p, s, a)
    user_client.post(f"/solutions/{s.id}", data={"name": "새솔루션", "partner_id": str(p.id)})
    assert "새솔루션" in _read(settings, "partners", f"{p.id}.md")
    assert "새솔루션" in _read(settings, "activities", "2026", f"{a.id}.md")


def test_partner_rename_updates_solution_cards(user_client, db, settings):
    p = Partner(name="옛파트너"); db.add(p); db.flush()
    s = Solution(name="솔루션", partner_id=p.id); db.add(s); db.commit()
    a = _activity(db, "partners", p)
    _sync_all(settings, p, s, a)
    user_client.post(f"/partners/{p.id}", data={"name": "새파트너"})
    assert "새파트너" in _read(settings, "solutions", str(s.id), "card.md")
    assert "새파트너" in _read(settings, "activities", "2026", f"{a.id}.md")


def test_business_rename_updates_customer_and_activity_mirrors(user_client, db, settings):
    c = Customer(name="고객"); db.add(c); db.flush()
    b = Project(name="옛사업", customer_id=c.id); db.add(b); db.commit()
    a = _activity(db, "projects", b)
    _sync_all(settings, c, b, a)
    user_client.post(f"/projects/{b.id}", data={"name": "새사업", "customer_id": str(c.id)})
    assert "새사업" in _read(settings, "customers", f"{c.id}.md")
    assert "새사업" in _read(settings, "activities", "2026", f"{a.id}.md")
