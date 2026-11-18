from app.labels import ACTIVITY_KINDS, FILE_KINDS, STAGES
from app.models import Activity, Business, Customer, Solution


def test_create_app_creates_folders_and_tables(app, settings, db):
    for d in (settings.uploads_dir, settings.knowledge_dir, settings.decks_dir):
        assert d.is_dir()
    c = Customer(name="한빛병원"); db.add(c); db.flush()
    b = Business(name="설비 유지보수", customer_id=c.id); db.add(b)
    s = Solution(name="AI 에너지 절감"); db.add(s)
    a = Activity(raw_text="메모", title="회의"); a.solutions.append(s); a.customers.append(c)
    db.add(a); db.commit()
    assert s.stage == "intake" and a.kind == "meeting"
    assert s.activities == [a] and c.businesses == [b]
    assert a.follow_ups == [] and a.demand_signals == []


def test_labels_cover_spec_values():
    assert list(STAGES) == ["intake", "tapping", "survey", "boxed", "hold"]
    assert STAGES["boxed"] == "박스 등록"
    assert list(ACTIVITY_KINDS) == ["meeting", "partner", "tapping", "survey", "other"]
    assert FILE_KINDS == {"intro": "소개", "spec": "스펙", "related": "관련"}
