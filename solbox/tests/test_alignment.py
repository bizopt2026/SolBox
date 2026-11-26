"""AI 코워크 ③: 사업 방향 정렬 점검 (자동·수동 작업, 저장, 화면 카드, 목록 걸러 보기, 보고 요약)."""
import json
import re
from datetime import date

from sqlalchemy import select

from app import alignment, reports
from app.ai.queue import process_next
from app.models import AIJob, AlignmentNote, Customer, Initiative, Opportunity, Solution, Strategy
from tests.conftest import nloc


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def _tok(client, url):
    m = re.search(r'name="_tok" value="([^"]+)"', client.get(url).text)
    return m.group(1) if m else ""


def _agreed(db, consensus="지자체·공공기관 우선, 민간 제조는 기존 고객만"):
    s = Strategy(title="공공 에너지 우선", status="agreed", consensus=consensus)
    db.add(s)
    db.commit()
    return s


def _jobs(db):
    return db.scalars(select(AIJob).where(AIJob.kind == "alignment_check").order_by(AIJob.id)).all()


def test_no_auto_check_without_agreed_strategy(user_client, db, worker_on):
    user_client.post("/initiatives", data={"title": "실증", "_tok": _tok(user_client, "/initiatives/new")})
    assert _jobs(db) == []
    page = user_client.get("/initiatives/1").text
    assert "합의된 사업 방향이 없어 점검하지 않습니다" in page


def test_save_queues_one_low_priority_check_and_job_saves_note(app, user_client, db, settings, fake_claude, worker_on):
    s = _agreed(db)
    user_client.post("/initiatives", data={"title": "민간 제조 신규 영업", "description": "민간 제조사 신규 고객 개척", "_tok": _tok(user_client, "/initiatives/new")})
    item = db.scalars(select(Initiative)).one()
    rev = re.search(r'name="_rev" value="([^"]+)"', user_client.get(f"/initiatives/{item.id}/edit").text).group(1)
    user_client.post(f"/initiatives/{item.id}", data={"title": "민간 제조 신규 영업", "description": "민간 제조사 신규 고객 개척", "_rev": rev})
    jobs = _jobs(db)
    assert len(jobs) == 1 and jobs[0].priority == 20 and jobs[0].input == {"target_type": "initiative", "target_id": item.id}
    assert "점검 대기 중" in user_client.get(f"/initiatives/{item.id}").text
    fake_claude.set(result=json.dumps({"verdict": "misaligned", "strategy_id": s.id, "reason": "방향은 민간 제조를 기존 고객만으로 둡니다.",
                                       "evidence": "민간 제조사 신규 고객 개척"}, ensure_ascii=False))
    _run(app, settings)
    call = fake_claude.calls()[0]
    assert call["argv"][call["argv"].index("--tools") + 1] == "" and "지자체·공공기관 우선" in call["stdin"] and "민간 제조사 신규 고객 개척" in call["stdin"]
    db.expire_all()
    note = db.scalars(select(AlignmentNote)).one()
    assert (note.verdict, note.strategy_id, note.verified) == ("misaligned", s.id, True)
    page = user_client.get(f"/initiatives/{item.id}").text
    assert "방향 밖" in page and "민간 제조를 기존 고객만으로" in page and "다시 점검" in page
    lst = user_client.get("/initiatives?align=off").text
    assert "민간 제조 신규 영업" in lst
    # 방향이 바뀌면 다시 점검 필요 표시
    s = db.get(Strategy, s.id)
    s.consensus = "공공 우선"
    db.commit()
    assert "다시 점검이 필요해요" in user_client.get(f"/initiatives/{item.id}").text


def test_manual_check_and_guards(user_client, db, worker_on):
    c = Customer(name="한빛")
    o = Opportunity(name="한빛 ESS", customer=c)
    sol = Solution(name="보류 솔루션", stage="hold")
    db.add_all([c, o, sol])
    db.commit()
    r = user_client.post(f"/alignment/opportunity/{o.id}/check", follow_redirects=False)
    assert "align_no_strategy" in nloc(r)
    _agreed(db)
    r = user_client.post(f"/alignment/opportunity/{o.id}/check", follow_redirects=False)
    assert "align_started" in nloc(r) and _jobs(db)[-1].priority == 0
    assert "align_waiting" in nloc(user_client.post(f"/alignment/opportunity/{o.id}/check", follow_redirects=False))
    assert "align_not_target" in nloc(user_client.post(f"/alignment/solution/{sol.id}/check", follow_redirects=False))
    assert user_client.post("/alignment/bm/1/check").status_code == 404


def test_save_clean_bad_values_and_trim(db):
    s = _agreed(db)
    note = alignment.save(db, "initiative", 1, {"verdict": "nonsense", "strategy_id": 999, "reason": "x", "evidence": "없는 말"}, "기록 글", {s.id}, "b", None)
    assert (note.verdict, note.strategy_id, note.verified) == ("unclear", None, False)
    for _ in range(12):
        alignment.save(db, "initiative", 1, {"verdict": "aligned"}, "", {s.id}, "b", None)
    db.commit()
    assert len(db.scalars(select(AlignmentNote)).all()) == 10


def test_deleting_target_or_strategy_cleans_notes(user_client, db):
    s = _agreed(db)
    from app.models import User

    kim = db.scalars(select(User).where(User.username == "kim")).one()
    item = Initiative(title="x", created_by_id=kim.id)
    s.created_by_id = kim.id
    db.add(item)
    db.commit()
    alignment.save(db, "initiative", item.id, {"verdict": "aligned", "strategy_id": s.id}, "", {s.id}, "b", None)
    alignment.save(db, "opportunity", 77, {"verdict": "aligned", "strategy_id": s.id}, "", {s.id}, "b", None)
    db.commit()
    user_client.post(f"/initiatives/{item.id}/delete")
    user_client.post(f"/strategies/{s.id}/delete")
    db.expire_all()
    rows = db.scalars(select(AlignmentNote)).all()
    assert [(n.target_type, n.strategy_id) for n in rows] == [("opportunity", None)]


def test_report_alignment_summary(db):
    s = _agreed(db)
    c = Customer(name="한빛")
    o1, o2 = Opportunity(name="방향 밖 건", customer=c), Opportunity(name="점검 전 건", customer=c)
    db.add_all([c, o1, o2])
    db.commit()
    alignment.save(db, "opportunity", o1.id, {"verdict": "misaligned", "reason": "민간 신규"}, "", {s.id}, "b", None)
    db.commit()
    today = date.today()
    w = reports.aggregate(db, "day", today, today)["workspace"]["alignment"]
    assert w["checked"] == 1 and w["unchecked"] == 1 and [m["title"] for m in w["misaligned"]] == ["방향 밖 건"]


# ---- 검토 반영 ----

def test_lock_failure_on_check_request_keeps_save_successful(user_client, db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.ai import queue

    _agreed(db)

    def boom(*a, **k):
        raise OperationalError("insert", {}, Exception("database is locked"))

    monkeypatch.setattr(queue, "enqueue", boom)
    r = user_client.post("/initiatives", data={"title": "실증", "_tok": _tok(user_client, "/initiatives/new")}, follow_redirects=False)
    assert r.status_code == 303 and "created" in nloc(r)
    assert db.scalars(select(Initiative)).one().title == "실증"


def test_progress_swaps_card_without_page_refresh(user_client, db):
    s = _agreed(db)
    item = Initiative(title="x")
    db.add(item)
    db.commit()
    alignment.save(db, "initiative", item.id, {"verdict": "aligned", "strategy_id": s.id}, "", {s.id}, alignment.current_basis(db), None)
    db.commit()
    r = user_client.get(f"/alignment/initiative/{item.id}/progress")
    assert "HX-Refresh" not in r.headers and 'id="alignment"' in r.text and "hx-get" not in r.text


def test_closed_records_leave_the_off_chip_and_no_stale_without_strategies(user_client, db):
    s = _agreed(db)
    c = Customer(name="한빛")
    o = Opportunity(name="닫힌 건", customer=c, stage="lost")
    db.add_all([c, o])
    db.commit()
    alignment.save(db, "opportunity", o.id, {"verdict": "misaligned"}, "", {s.id}, "old", None)
    db.commit()
    assert alignment.misaligned_ids(db, "opportunity") == set()
    item = Initiative(title="x")
    db.add(item)
    db.commit()
    alignment.save(db, "initiative", item.id, {"verdict": "aligned"}, "", {s.id}, "old", None)
    s.status = "closed"
    db.commit()
    assert not alignment.view(db, item)["stale"]
