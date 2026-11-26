"""AI 코워크 ②: AI 동료 칸 (대화 저장·작업·맥락·제안 반영·옮기기·지우기)."""
import json
import re

from sqlalchemy import select

from app import cowork
from app.ai.queue import process_next
from app.models import AIMessage, AIThread, Initiative, Opportunity, Customer, Solution, Strategy, User, WorkspaceChange
from tests.conftest import nloc


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def _tok(client, url):
    m = re.search(r'name="_tok" value="([^"]+)"', client.get(url).text)
    return m.group(1) if m else ""


def _space(db):
    s = Strategy(title="공공 에너지 우선", status="agreed", consensus="지자체·공공기관 우선, 민간 제조는 기존 고객만", instructions="숫자 먼저")
    item = Initiative(title="실증 추진", strategy=s, instructions="짧게 답하기", progress="부지 후보 3곳")
    db.add_all([s, item])
    db.commit()
    return s, item


def test_new_thread_runs_job_with_context_and_suggestions(app, user_client, db, settings, fake_claude):
    s, item = _space(db)
    answer = "부지 확정이 먼저입니다. 상태를 진행 중으로 바꾸길 제안해요."
    sug = [{"kind": "status", "value": "doing", "evidence": "부지 후보 3곳"},
           {"kind": "status", "value": "nope", "evidence": "x"},
           {"kind": "next_step", "value": "과제에는 없는 종류", "evidence": "x"},
           {"kind": "due", "value": "2026-12-15", "evidence": "지어낸 근거"}]
    fake_claude.set(mode="stream", chunks=[answer, "\n<<<SUGGEST\n", json.dumps(sug, ensure_ascii=False), "\nSUGGEST>>>"])
    page = user_client.get(f"/initiatives/{item.id}").text
    assert 'id="cowork"' in page and "+ 새 대화" in page
    r = user_client.post(f"/cowork/initiative/{item.id}/threads", data={"message": "다음에 뭘 하면 돼?", "_tok": _tok(user_client, f"/initiatives/{item.id}")},
                         follow_redirects=False)
    assert r.status_code == 303
    t = db.scalars(select(AIThread)).one()
    assert nloc(r) == f"/initiatives/{item.id}?thread={t.id}#cowork" and t.title == "다음에 뭘 하면 돼?"
    assert "대기 중" in user_client.get(f"/cowork/threads/{t.id}/live").text
    _run(app, settings)
    call = fake_claude.calls()[0]
    assert call["argv"][call["argv"].index("--tools") + 1] == ""
    for text in ("지자체·공공기관 우선", "숫자 먼저", "짧게 답하기", "부지 후보 3곳", "다음에 뭘 하면 돼?"):
        assert text in call["stdin"], text
    db.expire_all()
    t = db.get(AIThread, t.id)
    assert [(m.role, m.content) for m in t.messages] == [("user", "다음에 뭘 하면 돼?"), ("assistant", answer)]
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    assert t.messages[0].user_id == kim.id and t.messages[1].user_id == kim.id and t.active_job_id is None
    sugs = t.messages[1].suggestions
    assert [(x["kind"], x["value"], x["verified"]) for x in sugs] == [("status", "doing", True), ("due", "2026-12-15", False)]
    live = user_client.get(f"/cowork/threads/{t.id}/live")
    assert live.headers.get("HX-Retarget") == "#cowork-thread"
    page = user_client.get(f"/initiatives/{item.id}?thread={t.id}").text
    assert "상태: 진행 중" in page and "원문에서 근거를 찾지 못했어요" in page and "<<<SUGGEST" not in page


def test_apply_and_dismiss_suggestion(user_client, db):
    s, item = _space(db)
    t = AIThread(target_type="initiative", target_id=item.id, title="t")
    db.add(t)
    db.flush()
    m = AIMessage(thread_id=t.id, role="assistant", content="a",
                  suggestions=cowork.clean_suggestions([{"kind": "status", "value": "doing", "evidence": "부지 후보 3곳"},
                                                        {"kind": "progress", "value": "현장 확인 완료", "evidence": ""}], "initiative", "부지 후보 3곳"))
    db.add(m)
    db.commit()
    page = user_client.get(f"/initiatives/{item.id}?thread={t.id}").text
    rev = re.search(r'suggestions/1/apply">\s*<input type="hidden" name="_rev" value="([^"]+)"', page).group(1)
    r = user_client.post(f"/cowork/threads/{t.id}/messages/{m.id}/suggestions/1/apply", data={"_rev": rev}, follow_redirects=False)
    assert "cowork_applied" in nloc(r)
    db.expire_all()
    assert db.get(Initiative, item.id).status == "doing"
    assert db.scalars(select(WorkspaceChange).where(WorkspaceChange.initiative_id == item.id, WorkspaceChange.new_value == "doing")).first() is not None
    assert "cowork_decided" in nloc(user_client.post(f"/cowork/threads/{t.id}/messages/{m.id}/suggestions/1/apply", data={"_rev": rev}, follow_redirects=False))
    # 그 사이 바뀐 기록에는 반영하지 않는다 (옛 고침 표시)
    r = user_client.post(f"/cowork/threads/{t.id}/messages/{m.id}/suggestions/2/apply", data={"_rev": rev}, follow_redirects=False)
    assert "cowork_stale" in nloc(r)
    r = user_client.post(f"/cowork/threads/{t.id}/messages/{m.id}/suggestions/2/dismiss", follow_redirects=False)
    assert "cowork_dismissed" in nloc(r)
    db.expire_all()
    assert [x["state"] for x in db.get(AIMessage, m.id).suggestions] == ["applied", "dismissed"]
    assert "현장 확인 완료" not in (db.get(Initiative, item.id).progress or "")


def test_opportunity_next_step_suggestion(user_client, db):
    c = Customer(name="한빛병원")
    o = Opportunity(name="한빛 ESS", customer=c)
    db.add_all([c, o])
    db.commit()
    t = AIThread(target_type="opportunity", target_id=o.id, title="t")
    db.add(t)
    db.flush()
    m = AIMessage(thread_id=t.id, role="assistant", content="a",
                  suggestions=cowork.clean_suggestions([{"kind": "next_step", "value": "견적 보내기", "due": "2026-12-01", "evidence": ""}], "opportunity", ""))
    db.add(m)
    db.commit()
    page = user_client.get(f"/opportunities/{o.id}?thread={t.id}").text
    assert 'id="cowork"' in page and "다음 할 일: 견적 보내기 (2026-12-01)" in page
    user_client.post(f"/cowork/threads/{t.id}/messages/{m.id}/suggestions/1/apply")
    db.expire_all()
    o = db.get(Opportunity, o.id)
    assert o.next_step == "견적 보내기" and o.next_step_due.isoformat() == "2026-12-01"


def test_strategy_and_solution_take_no_suggestions():
    assert cowork.clean_suggestions([{"kind": "status", "value": "doing"}], "strategy", "") == []
    assert cowork.clean_suggestions([{"kind": "memo", "value": "x"}], "solution", "") == []
    assert len(cowork.clean_suggestions([{"kind": "memo", "value": "x"}], "partner", "")) == 1


def test_empty_and_busy_messages_keep_text(user_client, db):
    s, item = _space(db)
    r = user_client.post(f"/cowork/initiative/{item.id}/threads", data={"message": "  ", "_tok": _tok(user_client, f"/initiatives/{item.id}")})
    assert r.status_code == 400 and "메시지를 입력하세요" in r.text
    long = "가" * (cowork.MAX_MESSAGE + 1)
    r = user_client.post(f"/cowork/initiative/{item.id}/threads", data={"message": long, "_tok": _tok(user_client, f"/initiatives/{item.id}")})
    assert r.status_code == 400 and long in r.text
    user_client.post(f"/cowork/initiative/{item.id}/threads", data={"message": "첫 질문", "_tok": _tok(user_client, f"/initiatives/{item.id}")})
    t = db.scalars(select(AIThread)).one()
    r = user_client.post(f"/cowork/threads/{t.id}/messages", data={"message": "또 질문"})
    assert r.status_code == 409 and "또 질문" in r.text and "답하는 중" in r.text
    assert user_client.post("/cowork/bm/1/threads", data={"message": "x"}).status_code == 404


def test_move_thread_to_initiative_and_delete(user_client, db):
    sol = Solution(name="공조 제어")
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    item = Initiative(title="실증 추진")
    db.add_all([sol, item])
    db.commit()
    t = AIThread(target_type="solution", target_id=sol.id, title="솔루션 평가", created_by_id=kim.id)
    db.add(t)
    db.commit()
    page = user_client.get(f"/solutions/{sol.id}?thread={t.id}").text
    assert "이 대화를 과제로 옮기기" in page and "실증 추진" in page
    r = user_client.post(f"/cowork/threads/{t.id}/move", data={"initiative_id": str(item.id)}, follow_redirects=False)
    assert "cowork_moved" in nloc(r) and nloc(r).startswith(f"/initiatives/{item.id}?thread={t.id}")
    db.expire_all()
    t = db.get(AIThread, t.id)
    assert (t.target_type, t.target_id, t.origin_type, t.origin_id) == ("initiative", item.id, "solution", sol.id)
    assert [s.id for s in db.get(Initiative, item.id).solutions] == [sol.id]
    assert "솔루션에서 옮겨 온 대화" in user_client.get(f"/initiatives/{item.id}?thread={t.id}").text
    assert user_client.get(f"/cowork/threads/{t.id}/delete").status_code == 200
    tid = t.id
    assert user_client.post(f"/cowork/threads/{tid}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(AIThread, tid) is None


def test_deleting_target_deletes_its_threads(user_client, db):
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    item = Initiative(title="실증 추진", created_by_id=kim.id)
    db.add(item)
    db.commit()
    t = AIThread(target_type="initiative", target_id=item.id, title="t")
    db.add(t)
    db.flush()
    db.add(AIMessage(thread_id=t.id, role="user", content="q"))
    db.commit()
    iid = item.id
    user_client.post(f"/initiatives/{iid}/delete")
    db.expire_all()
    assert db.scalars(select(AIThread)).all() == [] and db.scalars(select(AIMessage)).all() == []


def test_context_caps_files(db, settings):
    from app.knowledge import KnowledgeMirror
    from app.models import WorkspaceFile

    s, item = _space(db)
    mirror = KnowledgeMirror(settings.knowledge_dir)
    files = []
    for n in range(4):
        f = WorkspaceFile(initiative_id=item.id, original_name=f"f{n}.txt", stored_name=f"{n}.txt", extract_status="ok")
        db.add(f)
        db.flush()
        mirror.write_file_text(f, "가" * 20000)
        files.append(f)
    db.commit()
    db.refresh(item)
    ctx = cowork.build_context(db, mirror, item)
    assert len(ctx.files) < cowork.FILES_CAP + 2000 and "잘렸습니다" in ctx.files and "넣지 못했습니다" in ctx.files


def test_composer_starts_empty(user_client, db):
    s, item = _space(db)
    html = user_client.get(f"/initiatives/{item.id}").text
    box = html.split('id="cowork-message"')[1].split("</textarea>")[0]
    assert box.endswith(">")  # 쓴 글이 없으면 빈 입력칸
