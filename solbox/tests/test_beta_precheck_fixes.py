"""베타 사전 점검(2026-11-27)에서 찾은 것들을 고친 뒤의 동작."""
from datetime import date
from pathlib import Path

from app import records
from app.models import Customer, CustomerNeed, Report

CSS = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text(encoding="utf-8")


def test_manual_activity_form_marks_the_raw_memo_field(user_client):
    """칸을 직접 채우는 폼에서 원문 메모를 비우면 위쪽 알림과 함께 그 칸 옆에도 오류가 보이고 칸이 펼쳐진다."""
    r = user_client.post("/activities", data={"kind": "meeting", "date": "2026-11-27", "title": "제목만", "summary_md": "정리본만", "raw_text": ""})
    assert r.status_code == 400
    memo = r.text.split('id="raw-memo"')[1].split("</details>")[0]
    assert memo.startswith(" open") and 'aria-invalid="true"' in memo and 'class="field-err" id="e-raw_text">메모를 입력하세요.' in memo


def test_report_confirm_warns_about_an_unapplied_ai_draft(user_client, db):
    r = Report(unit="week", period_start=date(2026, 11, 23), period_end=date(2026, 11, 29), status="draft",
               plan_draft={"direction": "", "items": [{"task": "새 일", "owner": "", "due": None, "customer": "", "note": "", "carried_from": None}]})
    db.add(r)
    db.commit()
    page = user_client.get(f"/reports/{r.id}?step=plan").text
    head = page.split('<header class="page-header')[1].split("</header>")[0]
    assert "표에 옮기지 않은 AI 초안이 있어요" in head and "계획 확정" in head
    assert "\"계획 확정\"은 아래 표대로만 확정합니다" in page
    r.plan_draft = None
    db.commit()
    assert "표에 옮기지 않은 AI 초안" not in user_client.get(f"/reports/{r.id}?step=plan").text


def test_opportunity_from_a_need_gets_a_starting_name(user_client, db):
    c = Customer(name="한빛병원")
    db.add(c)
    db.flush()
    n = CustomerNeed(customer_id=c.id, customer_name="한빛병원", content="냉방 전력비를 줄이고 싶다\n둘째 줄")
    db.add(n)
    db.commit()
    page = user_client.get(f"/opportunities/new?customer_id={c.id}&need_id={n.id}").text
    assert 'value="냉방 전력비를 줄이고 싶다"' in page, "고객 이름은 넣지 않는다 (화면마다 따로 붙는다)"
    plain = user_client.get(f"/opportunities/new?customer_id={c.id}").text
    assert 'name="name" type="text" value=""' in plain, "니즈 없이 열면 이름 칸은 비어 있다"


def test_auto_title_does_not_cut_words():
    assert records.first_line("짧은 제목") == "짧은 제목"
    long = "한빛병원 시설팀 김부장 통화 냉방 설비 교체 견적 요청 받음 다음 주까지 회신"
    t = records.first_line(long)
    assert t.endswith("…") and len(t) <= 40 and long.startswith(t[:-1]) and long[len(t) - 1] == " "
    assert records.first_line("가" * 60).endswith("…") and len(records.first_line("가" * 60)) == 40, "띄어쓰기가 없으면 글자에서"


def test_layout_fixes_from_the_precheck():
    assert ".sumband dd.num { white-space: normal; }" in CSS, "영업기회 요약 칸의 남은 날이 휴대폰에서 넘치지 않게"
    assert ".sr-only { position: absolute; left: 0; top: 0;" in CSS, "숨은 글이 가로 넘침을 만들지 않게"
    assert ".kind-opts { display: flex;" in CSS and ".kind-opts .kind-opt { flex: 1 1 200px; margin: 0; gap: 10px; padding: 10px 12px;" in CSS


def test_proposal_title_and_subtitle_do_not_repeat_the_customer(user_client, db):
    from app.models import Opportunity, Project, Proposal
    from app.proposals import display_title

    c = Customer(name="한빛병원")
    db.add(c)
    db.flush()
    pj = Project(name="한빛병원 냉방 전력비 절감", customer_id=c.id)
    other = Project(name="설비 유지보수", customer_id=c.id)
    db.add_all([pj, other])
    db.flush()
    o = Opportunity(name="한빛병원 냉방 전력비 절감", customer_id=c.id, project_id=pj.id)
    db.add(o)
    db.flush()
    p = Proposal(customer_id=c.id, project_id=pj.id, opportunity_id=o.id)
    db.add(p)
    db.commit()
    assert display_title(p) == "한빛병원 냉방 전력비 절감 제안"
    p2 = Proposal(customer_id=c.id, project_id=other.id)
    db.add(p2)
    db.commit()
    assert display_title(p2) == "한빛병원 · 설비 유지보수 제안", "고객 이름이 없는 프로젝트는 그대로 앞에 붙인다"
    head = user_client.get(f"/proposals/{p.id}").text.split('<header class="page-header')[1].split("</header>")[0]
    sub = head.split('class="ph-sub"')[1].split("</p>")[0]
    assert sub.count("냉방 전력비 절감") == 1 and ">영업기회</a>·<a" in sub


def test_unknown_address_keeps_the_menu_for_a_logged_in_user(user_client, client):
    """없는 주소: 로그인한 사람에게는 메뉴가 있는 한국어 404 화면 (베타 점검 2026-11-27: 메뉴가 사라지고 영어 "Not Found"가 떴다)."""
    r = user_client.get("/no-such-page")
    assert r.status_code == 404 and "Not Found" not in r.text and "항목을 찾을 수 없습니다" in r.text
    assert 'class="top-tabs"' in r.text and 'action="/logout"' in r.text
    anon = client.get("/no-such-page")
    assert anon.status_code == 404 and 'class="top-tabs"' not in anon.text and "Not Found" not in anon.text


def test_huge_amount_is_a_form_error_not_a_server_error(user_client, db):
    from app import money

    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    r = user_client.post("/opportunities", data={"name": "큰 건", "customer_id": str(c.id), "progress": "1", "amount": "9999999999999억"})
    assert r.status_code == 400 and "금액이 너무 큽니다" in r.text
    assert money.parse_amount("3억") == 300_000_000


# ---- QA 2차 (AI·파일) ----

def test_research_job_links_to_the_research_run(app, db, settings):
    from app.main import job_link
    from app.models import AIJob
    from tests.conftest import finished_run

    run = finished_run(db, settings)
    rep = next(r for r in run.reports if r.kind == "suggest")
    job = AIJob(kind="research_suggest", input={"report_id": rep.id}, status="done")
    assert job_link(app.state.session_factory, job) == f"/research/runs/{run.id}", "업무 보고(/reports/…)가 아니라 리서치 실행"
    assert job_link(app.state.session_factory, AIJob(kind="research_area", input={"report_id": 99999}, status="done")) == "/research"


def test_partly_failed_research_run_names_the_failed_part(admin_client, user_client, db, settings):
    from tests.conftest import finished_run

    run = finished_run(db, settings)
    page = admin_client.get(f"/research/runs/{run.id}").text
    box = page.split('id="h-failed"')[1].split("</section>")[0]
    assert "조사하지 못한 항목 1개" in box and "정책" in box and "/retry" in box
    staff = user_client.get(f"/research/runs/{run.id}").text.split('id="h-failed"')[1].split("</section>")[0]
    assert "/retry" not in staff and "관리자가 할 수 있어요" in staff


def test_too_long_field_message_is_korean():
    from app.main import TOO_LONG_FIELD

    assert "30만 자" in TOO_LONG_FIELD and "Part exceeded" not in TOO_LONG_FIELD
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "FIELD_MAX_BYTES = 1000 * 1024" in js


def test_failed_opportunity_review_is_shown_on_the_briefing(user_client, db):
    from datetime import datetime

    from app import opportunity_ai as oai
    from app.models import AIJob, Opportunity

    c = Customer(name="한빛")
    db.add(c)
    db.flush()
    o = Opportunity(name="한빛 관제", customer_id=c.id)
    db.add(o)
    db.flush()
    db.add(AIJob(kind=oai.REVIEW_KIND, input={"opportunity_id": o.id}, status="failed", error_kind="auth", error="Invalid API key", finished_at=datetime.now()))
    db.commit()
    page = user_client.get(f"/opportunities/{o.id}").text
    assert "AI 분석을 하지 못했습니다." in page and "Invalid API key" not in page


def test_delete_confirm_counts_contacts_and_does_not_repeat(admin_client, db):
    import re

    from app.models import Activity, Contact, FollowUp, Partner

    p = Partner(name="딥비전")
    db.add(p)
    db.flush()
    db.add(Contact(name="홍길동", partner_id=p.id))
    a = Activity(kind="meeting", date=date.today(), title="미팅", raw_text="x")
    db.add(a)
    db.flush()
    db.add(FollowUp(activity_id=a.id, task="견적", position=0))
    db.commit()
    page = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", admin_client.get(f"/partners/{p.id}/delete").text))
    assert "담당자 1" in page and "영향이 없습니다" not in page, page[page.find("삭제"):][:400]
    act = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", admin_client.get(f"/activities/{a.id}/delete").text))
    assert act.count("후속 조치") == 1, "후속 조치 지움 문장이 두 번 나오지 않는다"



def test_need_conflict_lists_a_changed_customer(user_client, db):
    """다른 탭이 니즈의 고객을 바꿨으면 충돌 표에 "고객"이 나온다 (강제 저장이 몰래 되돌리지 않게, 베타 점검 3차)."""
    import re

    a, b = Customer(name="한빛병원"), Customer(name="대한물산")
    db.add_all([a, b])
    db.flush()
    n = CustomerNeed(customer_id=a.id, customer_name=a.name, content="냉방")
    db.add(n)
    db.commit()
    page = user_client.get(f"/needs/{n.id}/edit").text
    rev = re.search(r'name="_rev" value="([^"]+)"', page).group(1)
    assert user_client.post(f"/needs/{n.id}", data={"customer_id": str(b.id), "content": "냉방", "status": "new", "_rev": rev}, follow_redirects=False).status_code == 303
    r = user_client.post(f"/needs/{n.id}", data={"customer_id": str(a.id), "content": "냉방 고침", "status": "new", "_rev": rev})
    assert r.status_code == 409
    diff = r.text.split('class="conflict-diff"')[1].split("</ul>")[0]
    assert "고객" in diff and "대한물산" in diff


def test_opportunity_made_from_a_need_counts_the_need_activity(db):
    from app import opportunity_ai as oai
    from app.models import Activity, Opportunity

    c = Customer(name="한빛")
    db.add(c)
    db.flush()
    o = Opportunity(name="한빛 관제", customer_id=c.id)
    a = Activity(kind="meeting", date=date.today(), title="미팅", raw_text="x")
    db.add_all([o, a])
    db.flush()
    assert not oai.has_activities(db, o)
    db.add(CustomerNeed(customer_id=c.id, customer_name="한빛", content="관제", activity_id=a.id, opportunity_id=o.id))
    db.commit()
    assert oai.has_activities(db, o), "니즈가 나온 활동 기록도 분석 재료"


def test_top_status_refreshes_only_while_a_job_runs(user_client, db):
    from sqlalchemy import select

    from app.ai import queue as ai_queue
    from app.models import User

    home = user_client.get("/").text
    assert 'id="top-status"' in home and 'hx-get="/ui/top-status"' not in home, "할 일 없을 때는 다시 부르지 않는다"
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    ai_queue.enqueue(db, "solution_draft", {}, kim.id)
    home = user_client.get("/").text
    assert 'hx-get="/ui/top-status"' in home and 'class="jobtray"' in home
    part = user_client.get("/ui/top-status")
    assert part.status_code == 200 and part.text.lstrip().startswith("{#") is False and 'id="top-status"' in part.text and "<html" not in part.text


def test_system_page_backup_text_follows_the_scheduler(admin_client, settings):
    assert "매일 자동 백업이 만들어지지 않으니" in admin_client.get("/admin/system").text  # 시험 서버는 예약 작업 꺼짐 (베타 서버처럼)
    settings.scheduler_enabled = True
    assert "매일 자동으로도 만들어집니다" in admin_client.get("/admin/system").text
