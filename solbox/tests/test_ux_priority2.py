"""UX 2순위 (2026-11-27): 화면마다 주 동작 하나와 정보 우선순위.

홈의 주 버튼·올리기 카드는 tests/test_home.py, 목록 머리 버튼은 tests/test_intake_pages.py 가 본다.
여기서는 ① 홈 "내 할 일" 0 칸 흐리게 ② 활동 기록 목록 "연결" 칸의 고객 이름 ③ 영업기회 목록의 진행 중 먼저·합계 한 줄
④ 상세 화면 옆 칸에서 "연결된 것" 이 AI 칸보다 위.
"""
import re
from datetime import date

from sqlalchemy import event

from app.models import Activity, Customer, Opportunity, Partner, Solution


# ① 홈 0 칸
def test_home_zero_work_cells_are_dimmed(user_client):
    strip = user_client.get("/").text.split('aria-label="내 할 일"')[1].split("</nav>")[0]
    cells = re.findall(r'<a class="work-cell([^"]*)"', strip)
    assert len(cells) == 6 and all("is-zero" in c for c in cells), "모두 0 이면 모두 흐리게 (칸은 그대로)"


# ② 활동 기록 목록의 고객 이름
def test_activity_list_link_cell_names_customers(user_client, db):
    cs = [Customer(name=n) for n in ("한빛", "가람", "다솜")]
    s = Solution(name="AI 관제")
    a = Activity(kind="meeting", date=date.today(), title="세 고객 미팅", raw_text="x", customers=cs, solutions=[s])
    b = Activity(kind="call", date=date.today(), title="연결 없는 통화", raw_text="y")
    db.add_all([a, b])
    db.commit()
    page = user_client.get("/activities").text
    row = page.split('class="row-title">세 고객 미팅</a>')[1].split("</tr>")[0]
    cell = row.split("</td>")[-3]  # 끝에서 둘째 칸이 "연결" (마지막은 일자)
    assert f'<a href="/customers/{cs[1].id}">가람</a>, <a href="/customers/{cs[2].id}">다솜</a> 외 1' in cell, "이름순 둘 + 외 n"
    assert "고객 3" not in cell and "솔루션 1" in cell
    meta = row.split('class="row-meta"')[1].split("</div>")[0]
    assert "가람, 다솜 외 1" in meta and "<a href=\"/customers" not in meta, "휴대폰 보조 줄은 글자만"
    empty = page.split('class="row-title">연결 없는 통화</a>')[1].split("</tr>")[0]
    assert 'aria-label="없음"' in empty


def test_activity_list_customer_names_take_one_query(user_client, db, app):
    def count_for(n):
        db.add_all([Activity(kind="meeting", date=date.today(), title=f"미팅{n}-{i}", raw_text="x", customers=[Customer(name=f"고객{n}-{i}")]) for i in range(n)])
        db.commit()
        seen = []
        engine = db.get_bind()

        def before(conn, cursor, statement, *a):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", before)
        try:
            assert user_client.get("/activities").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return len(seen)

    assert count_for(2) == count_for(6), "고객 이름은 쪽 단위 질의 하나 (줄 수와 무관)"


# ③ 영업기회 목록
def test_opportunity_list_puts_open_deals_first_and_sums_them(user_client, db):
    c = Customer(name="한빛")
    db.add(c)
    db.flush()
    won = Opportunity(name="수주 건", customer=c, stage="won", amount=900_000_000)
    hold = Opportunity(name="보류 건", customer=c, stage="hold")
    a = Opportunity(name="발굴 건", customer=c, stage="discovery", amount=300_000_000)
    b = Opportunity(name="협상 건", customer=c, stage="negotiating")
    db.add_all([won, a, b, hold])
    db.commit()
    db.add(Opportunity(name="실패 건", customer=c, stage="lost", amount=5))  # 가장 최근 수정이지만 닫힌 건
    db.commit()
    page = user_client.get("/opportunities").text
    order = [page.index(f'class="row-title">{n}</a>') for n in ("협상 건", "발굴 건", "실패 건", "보류 건", "수주 건")]
    assert order == sorted(order), "진행 중이 위, 그 안에서는 최근 수정순"
    s = page.split('class="list-sum"')[1].split("</p>")[0]
    assert "진행 중 <b>2건</b>" in s and "3억" in s and "금액 없는 1건 빼고" in s
    by_name = user_client.get("/opportunities?sort=name").text
    assert by_name.index("row-title\">보류 건") < by_name.index("row-title\">협상 건"), "다른 정렬은 그대로"
    assert 'class="list-sum"' not in user_client.get("/opportunities?stage=won").text, "닫힌 단계만 볼 때는 합계 줄 없음"


# ④ 상세 옆 칸 순서
def _aside(html):
    return html.split('<aside class="daside"')[1].split("</aside>")[0]


def test_related_card_comes_before_ai_dock(user_client, db):
    c = Customer(name="한빛")
    p = Partner(name="딥비전")
    s = Solution(name="AI 관제", partner=p)
    o = Opportunity(name="한빛 관제", customer=c)
    db.add_all([c, p, s, o, Activity(kind="meeting", date=date.today(), title="미팅", raw_text="x", customers=[c], solutions=[s], opportunities=[o])])
    db.commit()
    for url in (f"/solutions/{s.id}", f"/partners/{p.id}", f"/opportunities/{o.id}"):
        aside = _aside(user_client.get(url).text)
        assert 'class="ai-dock"' in aside, url
        rel = aside.find('class="card rel-card') if 'class="card rel-card' in aside else aside.find("연결된 것")
        assert 0 <= rel < aside.index('class="ai-dock"'), url


# ⑤ 활동 기록 상세 (UX 다듬기 2026-11-27): 제목 밑 한 줄과 겹치는 "기본 정보" 카드 없음, 다음 동작 하나
def test_activity_detail_drops_duplicate_card_and_offers_next_step(user_client, db):
    from app.models import Contact, CustomerNeed

    c = Customer(name="한빛")
    o = Opportunity(name="한빛 관제", customer=c)
    db.add_all([c, o])
    db.flush()
    a = Activity(kind="meeting", date=date.today(), title="한빛 미팅", raw_text="x", customers=[c])
    db.add(a)
    db.commit()
    page = user_client.get(f"/activities/{a.id}").text
    head = page.split('<header class="page-header')[1].split("</header>")[0]
    assert "한빛" in head and "primary" not in head, "니즈가 없으면 주 버튼 없음"
    assert "기본 정보" not in page and 'id="h-attendees"' not in page

    linked = CustomerNeed(activity_id=a.id, position=0, customer_name="한빛", customer_id=c.id, content="이미 묶인 니즈", opportunity_id=o.id)
    nameless = CustomerNeed(activity_id=a.id, position=1, customer_name="모름", content="고객 없는 니즈")
    open_need = CustomerNeed(activity_id=a.id, position=2, customer_name="한빛", customer_id=c.id, content="새 니즈")
    hong = Contact(name="홍길동", title="부장", customer_id=c.id)
    db.add_all([hong, linked, nameless, open_need])
    db.flush()
    a.contacts = [hong]
    db.commit()
    page = user_client.get(f"/activities/{a.id}").text
    head = page.split('<header class="page-header')[1].split("</header>")[0]
    assert f'class="button primary" href="/opportunities/new?customer_id={c.id}&amp;need_id={open_need.id}">영업기회 만들기</a>' in head
    card = page.split('id="h-attendees"')[1].split("</section>")[0]
    assert "홍길동" in card and "부장" in card and "1명" in card
