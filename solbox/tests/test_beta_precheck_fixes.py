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
    assert 'value="한빛병원 · 냉방 전력비를 줄이고 싶다"' in page
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
