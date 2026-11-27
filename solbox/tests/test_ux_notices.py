"""UX 개선 1·2: 결과 알림(한 곳의 코드 표)과 삭제 확인 단계."""
import re
import time

import pytest

from app import notices, ontology
from app.models import Activity, Customer, Intake, Opportunity, Partner, Project, Proposal, ResearchRun, ResearchTopic, Solution, SolutionFile
from tests.conftest import make_user, route_table, nloc


def nloc_s(url: str) -> str:
    return re.sub(r"&nt=\d+", "", url)


# ---- 알림 코드 표 ----

def test_known_codes_have_korean_text_and_tone():
    assert notices.lookup("saved").text == "저장했습니다."
    assert notices.lookup("deleted").text == "삭제했습니다."
    for code, n in notices.NOTICES.items():
        assert n.tone in ("ok", "warn", "error") and n.text, code


def test_valued_codes_use_the_ontology_labels():
    assert notices.lookup("stage.tapping").text == "발굴 단계를 '사전 태핑'으로 바꿨습니다."
    assert notices.lookup("stage.boxed").text == "발굴 단계를 '박스 등록'으로 바꿨습니다."
    assert notices.lookup("opportunity_stage.won").text == "영업기회 단계를 '수주'로 바꿨습니다."
    assert notices.lookup("opportunity_stage.negotiating").text == "영업기회 단계를 '협상'으로 바꿨습니다."


@pytest.mark.parametrize("bad", ["", "nope", "stage.", "stage.nope", "unknown.x", "stage.tapping.x", "x" * 100, "<script>", "stage.<b>"])
def test_unknown_or_malformed_codes_are_ignored(bad):
    assert notices.lookup(bad) is None


def test_with_notice_keeps_query_and_fragment():
    assert nloc_s(notices.with_notice("/a?x=1#h", "saved")) == "/a?x=1&notice=saved#h"
    assert nloc_s(notices.with_notice("/a?notice=old&nt=1", "saved")) == "/a?notice=saved"
    assert nloc_s(notices.redirect("/a", "saved").headers["location"]) == "/a?notice=saved"


def test_page_shows_notice_with_dismiss_link_and_ignores_garbage(user_client, db):
    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    page = user_client.get(notices.with_notice(f"/customers/{c.id}", "saved")).text
    assert 'class="flash ok"' in page and "저장했습니다." in page
    assert re.search(r'<a class="flash-x" href="/customers/%d" aria-label="알림 닫기">' % c.id, page)
    for bad in ("nope", "%3Cscript%3Ealert(1)%3C/script%3E", "stage.%3Cb%3E"):
        html = user_client.get(f"/customers/{c.id}?notice={bad}&nt={int(time.time())}").text
        assert 'class="flash' not in html and "<script>alert(1)" not in html


def test_dismiss_link_keeps_other_query(user_client):
    page = user_client.get(notices.with_notice("/customers?q=가", "saved")).text
    assert re.search(r'class="flash-x" href="/customers\?q=%EA%B0%80"', page)


def test_error_tone_notice_is_an_alert(user_client, db):
    from app.models import User

    it = Intake(note="n", status="applied", created_by_id=db.query(User).filter_by(username="kim").one().id)
    db.add(it)
    db.commit()
    page = user_client.get(notices.with_notice(f"/intake/{it.id}", "not_ready")).text
    assert 'class="flash error"' in page and 'role="alert"' in page and "지금 저장할 수 없는 상태" in page  # 실패처럼 읽히는 "이미 저장했거나…" 문구는 더 쓰지 않는다


def test_post_error_pages_do_not_show_a_notice(user_client):
    r = user_client.post("/customers?notice=saved", data={"name": ""})
    assert r.status_code == 400 and 'class="flash' not in r.text


# ---- 세 가지 이상 대상에서 저장 뒤 알림 ----

def _follow(client, r):
    assert r.status_code == 303
    return client.get(r.headers["location"]).text


def test_create_update_delete_show_notices(user_client, db):
    r = user_client.post("/customers", data={"name": "신규 고객"}, follow_redirects=False)
    assert "notice=created" in nloc(r)
    assert "등록했습니다." in _follow(user_client, r)
    cid = db.query(Customer).one().id
    r = user_client.post(f"/customers/{cid}", data={"name": "신규 고객 2"}, follow_redirects=False)
    assert "저장했습니다." in _follow(user_client, r)
    r = user_client.post(f"/customers/{cid}/delete", follow_redirects=False)
    assert nloc(r) == "/customers?notice=deleted"
    assert "삭제했습니다." in _follow(user_client, r)


def test_stage_change_notices_for_solution_and_opportunity(user_client, db):
    c = Customer(name="고객")
    s = Solution(name="솔루션")
    db.add_all([c, s])
    db.flush()
    o = Opportunity(name="기회", customer_id=c.id)
    db.add(o)
    db.commit()
    r = user_client.post(f"/solutions/{s.id}/stage", data={"stage": "tapping"}, follow_redirects=False)
    assert "발굴 단계를 &#39;사전 태핑&#39;으로 바꿨습니다." in _follow(user_client, r)
    r = user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": "other"}, follow_redirects=False)
    assert "영업기회 단계를 &#39;수주&#39;로 바꿨습니다." in _follow(user_client, r)


def test_every_post_route_is_triaged_in_the_notice_table(app):
    """모든 POST 라우터가 알림 표에 올라 있다 (새 POST 가 생기면 여기서 알림 코드나 "알림 없음" 이유를 정해야 한다)."""
    posts = sorted({p for p, m in route_table(app) if "POST" in m})
    assert len(posts) > 60
    untriaged = [p for p in posts if p not in POST_NOTICES and re.sub(r"\{[^}]+\}", "{}", p) not in {re.sub(r"\{[^}]+\}", "{}", k) for k in POST_NOTICES}]
    assert untriaged == []


def test_post_notice_codes_exist_in_the_table():
    for path, code in POST_NOTICES.items():
        if code is None or code.startswith("("):
            continue
        for c in (c.strip() for c in code.split(",")):
            if not c.startswith("("):
                assert notices.lookup(c.replace("{stage}", "tapping")) is not None, (path, c)


# 라우트 -> 알림 코드 (None 또는 괄호 글은 알림이 없는 이유). 보고서의 표와 같다.
POST_NOTICES = {
    "/login": "(로그인: 홈으로)", "/logout": "(로그인 화면으로)", "/account/password": "password_changed", "/account/contact": "saved", "/follow-ups/{follow_up_id}/mine": "fu_mine",
    "/solutions": "created", "/solutions/{solution_id}/draft": "ai_off, (AI 초안 작업 진행 화면)", "/solutions/{solution_id}/stage": "stage.{stage}",
    "/solutions/{solution_id}": "saved", "/solutions/{solution_id}/delete": "deleted",
    "/solutions/{solution_id}/files": "files_uploaded", "/files/{file_id}/kind": "file_kind", "/files/{file_id}/delete": "file_deleted",
    "/partners/{record_id}/merge": "merged, merge_gone", "/customers/{record_id}/merge": "merged, merge_gone",
    "/admin/duplicates/ai/{kind}": "ai_off, dup_ai_started", "/admin/duplicates/dismiss": "dup_dismissed", "/admin/duplicates/undismiss": "dup_undismissed",
    "/admin/contacts-cleanup": "contacts_cleanup, contacts_cleanup_none",
    "/contacts": "created", "/contacts/{contact_id}": "saved", "/contacts/{contact_id}/delete": "deleted",
    "/customers/{company_id}/contacts/split": "contacts_split, contacts_split_none", "/partners/{company_id}/contacts/split": "contacts_split, contacts_split_none",
    "/partners": "created", "/partners/{partner_id}": "saved", "/partners/{partner_id}/same-company": "same_company", "/partners/{partner_id}/delete": "deleted",
    "/customers": "created", "/customers/{customer_id}": "saved", "/customers/{customer_id}/same-company": "same_company", "/customers/{customer_id}/delete": "deleted",
    "/projects": "created", "/projects/{project_id}": "saved", "/projects/{project_id}/delete": "deleted",
    "/businesses": "(옛 주소: 307 로 /projects 에 넘김)", "/businesses/{rest:path}": "(옛 주소: 307 로 /projects 에 넘김)",
    "/bms": "created", "/bms/{bm_id}": "saved", "/bms/{bm_id}/delete": "deleted",
    "/cowork/{code}/{target_id}/threads": "(새 대화로: 진행 표시가 알림을 대신한다)", "/cowork/threads/{thread_id}/messages": "(대화로: 진행 표시가 알림을 대신한다)",
    "/cowork/threads/{thread_id}/retry": "busy, (대화로: 진행 표시가 알림을 대신한다)",
    "/cowork/threads/{thread_id}/messages/{message_id}/suggestions/{sid}/apply": "cowork_applied, cowork_stale, cowork_decided, cowork_not_applicable",
    "/cowork/threads/{thread_id}/messages/{message_id}/suggestions/{sid}/dismiss": "cowork_dismissed, cowork_decided",
    "/cowork/threads/{thread_id}/move": "cowork_moved, cowork_pick_initiative", "/cowork/threads/{thread_id}/delete": "deleted",
    "/alignment/{code}/{target_id}/check": "align_started, align_waiting, align_no_strategy, align_not_target",
    "/strategies": "created", "/strategies/{strategy_id}": "saved", "/strategies/{strategy_id}/delete": "deleted",
    "/initiatives": "created", "/initiatives/{item_id}": "saved", "/initiatives/{item_id}/status": "saved", "/initiatives/{item_id}/delete": "deleted",
    "/strategies/{strategy_id}/files": "files_uploaded", "/initiatives/{initiative_id}/files": "files_uploaded", "/workspace-files/{file_id}/delete": "file_deleted",
    "/opportunities": "created", "/opportunities/{opportunity_id}": "saved", "/opportunities/{opportunity_id}/stage": "opportunity_stage.won, opportunity_stage_open.discovery.proposing.3", "/opportunities/{opportunity_id}/close": "opportunity_stage.won", "/opportunities/{opportunity_id}/ai/review": "ai_review_started, ai_review_waiting, ai_no_activity, ai_not_open, ai_off", "/opportunities/{opportunity_id}/ai/{insight_id}/{sid}/{what}": "ai_applied, ai_applied_changed, ai_dismissed, ai_stale, ai_stage_open.discovery.proposing.2", "/opportunities/analysis/ai": "ai_insight_started, ai_insight_waiting, ai_off", "/opportunities/{opportunity_id}/checks": "(알림 없음: 확인 항목 카드 자리로 돌아가거나 HTMX 로 카드만 바뀐다)",
    "/opportunities/{opportunity_id}/convert": "converted, linked_project", "/opportunities/{opportunity_id}/delete": "deleted",
    "/activities": "created", "/activities/raw": "created", "/activities/draft": "ai_off, (AI 초안 진행 화면)", "/activities/draft/{job_id}/retry": "ai_off, (AI 초안 진행 화면)",
    "/activities/{activity_id}": "saved", "/activities/{activity_id}/delete": "deleted",
    "/activities/{activity_id}/followups/{index}/toggle": "(되돌리기가 있는 기존 안내 fu_toggled)", "/follow-ups/{follow_up_id}/toggle": "(되돌리기가 있는 기존 안내 fu_toggled)",
    "/intake": "intake_uploaded, intake_kept_ai_off", "/intake/{intake_id}/analyze": "intake_analyzing, ai_off", "/intake/{intake_id}/apply": "intake_applied, superseded, not_ready",
    "/intake/{intake_id}/discard": "intake_discarded", "/intake/{intake_id}/recheck": "intake_rechecked",
    "/intake/cards": "cards_read, cards_need_map", "/intake/{intake_id}/cards-map": "cards_read, not_ready", "/intake/{intake_id}/cards-remap": "cards_remap",
    "/jobs/{job_id}/retry": "ai_off, (JSON 응답: 화면이 새 작업 번호로 이어 감)",
    "/research/runs/{run_id}/candidate": "candidate_registered, already_registered", "/research/runs/{run_id}/delete": "deleted",
    "/research/run": "research_started, research_busy, no_topics, ai_off", "/research/reports/{report_id}/retry": "research_retry, research_busy, ai_off",
    "/admin/solution-fields": "field_created", "/admin/solution-fields/{field_id}": "field_renamed", "/admin/solution-fields/{field_id}/move": "field_moved",
    "/admin/solution-fields/{field_id}/active": "field_on, field_off", "/admin/solution-fields/{field_id}/delete": "field_deleted",
    "/admin/users": "created", "/admin/users/{user_id}": "saved",
    "/admin/teams": "team_created", "/admin/teams/{team_id}": "team_saved", "/admin/teams/{team_id}/delete": "team_deleted", "/admin/research/topics": "created", "/admin/research/topics/{topic_id}": "saved",
    "/admin/research/topics/{topic_id}/delete": "deleted", "/admin/research/schedule": "saved", "/admin/research/keywords": "keywords_started, ai_off", "/admin/system/ai-check": "ai_check, ai_off", "/admin/system/backup": "backup_done",
    "/admin/system/update/check": "update_checked", "/admin/system/update/apply": "(진행 화면 /admin/system/update/progress 으로 이동)",
    "/proposals": "created, created_ai_off", "/proposals/{proposal_id}/directions": "busy, ai_off, (작업이 시작되면 진행 표시)", "/proposals/{proposal_id}/select": "direction_selected, busy",
    "/proposals/{proposal_id}/chat": "ai_off, (대화창에 내 글이 바로 보임)", "/proposals/{proposal_id}/chat/retry": "ai_off, (대화창에 진행이 보임)",
    "/proposals/{proposal_id}/outline": "busy, ai_off, (작업이 시작되면 진행 표시)", "/proposals/{proposal_id}/deck": "deck_built", "/proposals/{proposal_id}/target": "target_saved",
    "/proposals/{proposal_id}/delete": "deleted",
    "/proposals/{proposal_id}/outline/slides/{index}": "slide_saved", "/proposals/{proposal_id}/outline/slides/{index}/check": "(칸 하나만 바뀌는 확인 표시: 알림 없음)",
    "/proposals/{proposal_id}/outline/slides/{index}/check-all": "(칸 하나만 바뀌는 확인 표시: 알림 없음)", "/proposals/{proposal_id}/outline/slides/{index}/delete": "slide_deleted",
    "/proposals/{proposal_id}/outline/slides/{index}/move": "slide_moved", "/proposals/{proposal_id}/outline/slides": "slide_added",
    "/proposals/{proposal_id}/outline/slides/{index}/ai": "busy, ai_off, (작업이 시작되면 진행 상자)", "/proposals/{proposal_id}/outline/slides/{index}/undo": "slide_restored",
    "/proposals/{proposal_id}/deck/pdf": "pdf_built, pdf_failed, pdf_unavailable",
    "/reports": "report_created, report_exists", "/reports/{report_id}/summary": "saved", "/reports/{report_id}/notes": "saved",
    "/reports/{report_id}/check": "report_checked", "/reports/{report_id}/plan": "report_planned, report_planned_unlinked",
    "/reports/{report_id}/confirm": "report_confirmed", "/reports/{report_id}/unconfirm": "report_unconfirmed, report_unconfirm_blocked",
    "/reports/{report_id}/files": "report_file_built, report_not_confirmed", "/reports/{report_id}/delete": "deleted",
    "/reports/{report_id}/ai/summary": "busy, ai_off, (작업이 시작되면 진행 상자)", "/reports/{report_id}/ai/plan": "busy, ai_off, (작업이 시작되면 진행 상자)",
    "/reports/{report_id}/ai/notes": "busy, ai_off, (작업이 시작되면 진행 상자)", "/reports/{report_id}/chat": "ai_off, (대화창에 내 글이 바로 보임)",
    "/reports/{report_id}/chat/retry": "busy, ai_off, (대화창에 진행이 보임)", "/reports/{report_id}/plan/apply-draft": "report_draft_applied, report_planned_unlinked",
}
