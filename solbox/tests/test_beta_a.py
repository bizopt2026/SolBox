"""닫힌 베타 지적 A: 계정·데이터 보호 (고침 표시, 한 번 쓰는 폼 표, 비밀번호, 주소 번호 404, 알림 한 번만, 새로 고침 405, 고친 사람)."""
import re
import threading
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import guards, notices
from app.models import (
    BM, Activity, Customer, CustomerNeed, FollowUp, FormToken, Intake, Opportunity, Partner, Project, Proposal, Solution, User,
)
from tests.conftest import form_from_page, make_user, nloc, ready_intake, route_table

REV = re.compile(r'name="_rev" value="([^"]+)"')


def rev_of_page(client, url):
    m = REV.search(client.get(url).text)
    assert m, f"{url} 에 고침 표시가 없습니다"
    return m.group(1)


def token_of_page(client, url):
    m = re.search(r'name="_tok" value="([^"]+)"', client.get(url).text)
    assert m, f"{url} 에 폼 표가 없습니다"
    return m.group(1)


def login(app, username="kim", password="pw1234"):
    c = TestClient(app)
    assert c.post("/login", data={"username": username, "password": password}, follow_redirects=False).status_code == 303
    return c


# ---- A1: 고침 표시 ----

def _customer(db, name="한빛병원"):
    c = Customer(name=name, industry="병원")
    db.add(c)
    db.commit()
    return c


CASES = {  # (만들기, 수정 주소, 화면에 남아야 할 폼 값, 다른 곳에서 바꾸는 법, 올리는 칸, 바뀐 값 확인)
    "customer": dict(make=lambda db: Customer(name="한빛병원"), url="/customers/{id}", data={"name": "내 고객 이름"}),
    "partner": dict(make=lambda db: Partner(name="루미텍"), url="/partners/{id}", data={"name": "내 파트너 이름"}),
    "solution": dict(make=lambda db: Solution(name="AI 공조", stage="boxed"), url="/solutions/{id}",
                     data={"name": "내 솔루션 이름", "stage": "boxed"}),
    "bm": dict(make=lambda db: BM(name="구독형"), url="/bms/{id}", data={"name": "내 BM 이름"}),
    "project": dict(make=lambda db: Project(name="설비", customer_id=_customer(db, "프로젝트 고객").id), url="/projects/{id}",
                    data={"name": "내 프로젝트 이름", "customer_id": "{customer_id}"}),
    "opportunity": dict(make=lambda db: Opportunity(name="1차", customer_id=_customer(db, "영업 고객").id), url="/opportunities/{id}",
                        data={"name": "내 영업기회 이름", "customer_id": "{customer_id}"}),
    "activity": dict(make=lambda db: Activity(title="미팅", raw_text="원문", date=date(2026, 11, 1)), url="/activities/{id}",
                     data={"title": "내 활동 제목", "raw_text": "원문", "kind": "meeting", "date": "2026-11-01"}),
}


def _make(db, kind):
    obj = CASES[kind]["make"](db)
    db.add(obj)
    db.commit()
    return obj


def _data(kind, obj):
    return {k: v.format(customer_id=getattr(obj, "customer_id", "")) if isinstance(v, str) else v for k, v in CASES[kind]["data"].items()}


def _other_tab_changes(db, kind, obj):
    """다른 탭에서 저장한 것처럼 이름(활동 기록은 제목)을 바꾼다."""
    field = "title" if kind == "activity" else "name"
    setattr(obj, field, "다른 탭에서 바꾼 값")
    db.commit()


@pytest.mark.parametrize("kind", list(CASES))
def test_edit_form_carries_a_revision_stamp(user_client, db, kind):
    obj = _make(db, kind)
    stamp = rev_of_page(user_client, CASES[kind]["url"].format(id=obj.id) + "/edit")
    assert stamp == guards.rev_of(obj) and re.fullmatch(r"[0-9a-f]{10}\.[0-9a-f]{10}", stamp)  # 뜻을 알 수 없는 짧은 글


@pytest.mark.parametrize("kind", list(CASES))
def test_stale_edit_is_not_saved_and_keeps_input(user_client, db, kind):
    obj = _make(db, kind)
    base = CASES[kind]["url"].format(id=obj.id)
    stamp = rev_of_page(user_client, base + "/edit")
    _other_tab_changes(db, kind, obj)
    r = user_client.post(base, data={**_data(kind, obj), "_rev": stamp}, follow_redirects=False)
    assert r.status_code == 409
    assert "다른 곳에서 이 기록이 바뀌었습니다" in r.text
    assert f'href="{base}" target="_blank"' in r.text  # 현재 버전을 새 탭으로
    assert 'name="_force" value="1"' in r.text and "그래도 내 내용으로 저장" in r.text
    assert "내 " in r.text and ("내 활동 제목" if kind == "activity" else f"내 {dict(customer='고객', partner='파트너', solution='솔루션', bm='BM', project='프로젝트', opportunity='영업기회')[kind]} 이름") in r.text  # 쓴 내용이 그대로
    assert REV.search(r.text).group(1) == stamp  # 옛 표시를 그대로 다시 보낸다
    db.expire_all()
    assert (obj.title if kind == "activity" else obj.name) == "다른 탭에서 바꾼 값"  # 저장하지 않았다


@pytest.mark.parametrize("kind", list(CASES))
def test_force_saves_when_changed_elsewhere(user_client, db, kind):
    obj = _make(db, kind)
    base = CASES[kind]["url"].format(id=obj.id)
    stamp = rev_of_page(user_client, base + "/edit")
    _other_tab_changes(db, kind, obj)
    r = user_client.post(base, data={**_data(kind, obj), "_rev": stamp, "_force": "1"}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == f"{base}?notice=saved"
    db.expire_all()
    assert (obj.title if kind == "activity" else obj.name).startswith("내 ")


@pytest.mark.parametrize("kind", list(CASES))
def test_fresh_stamp_and_missing_stamp_save(user_client, db, kind):
    obj = _make(db, kind)
    base = CASES[kind]["url"].format(id=obj.id)
    stamp = rev_of_page(user_client, base + "/edit")
    assert user_client.post(base, data={**_data(kind, obj), "_rev": stamp}, follow_redirects=False).status_code == 303
    # 표시가 없는 저장(업그레이드 전에 열어 둔 화면·브라우저가 아닌 클라이언트)은 지금처럼 한 번 받아 준다
    r = user_client.post(base, data=_data(kind, obj), follow_redirects=False)
    assert r.status_code == 303


@pytest.mark.parametrize("kind", ["customer", "solution", "activity", "project"])
def test_reused_id_is_a_different_record_and_cannot_be_forced(user_client, db, kind):
    obj = _make(db, kind)
    old_id = obj.id
    base = CASES[kind]["url"].format(id=old_id)
    stamp = rev_of_page(user_client, base + "/edit")
    db.delete(obj)
    db.commit()
    new = CASES[kind]["make"](db)
    new.id = old_id  # 번호 재사용은 이제 막혀 있지만(app/idmarks.py), 표시가 생기기 전에 지워진 번호는 다시 쓰일 수 있다: 같은 번호로 직접 넣어 그 경우를 만든다
    db.add(new)
    db.commit()
    assert new.id == old_id
    r = user_client.post(base, data={**_data(kind, new), "_rev": stamp, "_force": "1"}, follow_redirects=False)  # 강제 저장도 안 된다
    assert r.status_code == 409
    assert "삭제되고 같은 번호로 새 기록이 만들어졌습니다" in r.text
    assert 'name="_force"' not in r.text and f'href="{base}" target="_blank"' in r.text
    db.expire_all()
    assert (new.title if kind == "activity" else new.name) != "내 활동 제목" and "내 " not in (new.title if kind == "activity" else new.name)


def test_every_write_path_moves_the_stamp(user_client, db):
    """후속 조치 완료 표시·단계 변경·연결 변경이 모두 고침 표시를 바꾼다 (두 탭이 서로의 변경을 조용히 되돌리지 않게)."""
    a = Activity(title="미팅", raw_text="x", follow_ups=[FollowUp(position=0, task="견적", owner="", done=False)],
                 needs=[CustomerNeed(position=0, customer_name="한빛병원", content="절감")])
    db.add(a)
    db.commit()
    before = guards.rev_of(a)
    fu = a.follow_ups[0]
    assert user_client.post(f"/follow-ups/{fu.id}/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert guards.rev_of(a) != before  # 후속 조치 줄만 바뀌어도 활동 기록의 표시가 바뀐다
    s = Solution(name="AI", stage="intake")
    db.add(s)
    db.commit()
    s_before = guards.rev_of(s)
    assert user_client.post(f"/solutions/{s.id}/stage", data={"stage": "tapping"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert guards.rev_of(s) != s_before
    bm = BM(name="구독")
    db.add(bm)
    db.commit()
    bm_before = guards.rev_of(bm)
    bm.solutions = [s]  # 연결 표만 바뀐다
    db.commit()
    assert guards.rev_of(bm) != bm_before
    bm_before = guards.rev_of(bm)
    a.needs[0].content = "다시 쓴 니즈"  # 니즈 줄만 바뀐다
    a_before = guards.rev_of(a)
    db.commit()
    assert guards.rev_of(a) != a_before


def test_two_tabs_followups_are_not_silently_reverted(user_client, db):
    a = Activity(title="미팅", raw_text="x", follow_ups=[FollowUp(position=0, task="견적 보내기", owner="", done=False)])
    db.add(a)
    db.commit()
    stamp = rev_of_page(user_client, f"/activities/{a.id}/edit")
    fu_id = a.follow_ups[0].id
    user_client.post(f"/follow-ups/{fu_id}/toggle")  # 탭 1: 완료로 표시
    r = user_client.post(f"/activities/{a.id}", data={"title": "탭 2 제목", "raw_text": "x", "kind": "meeting", "date": "2026-11-01",
                                                       "fu_id": str(fu_id), "fu_task": "견적 보내기", "_rev": stamp}, follow_redirects=False)
    assert r.status_code == 409 and "견적 보내기" in r.text
    db.expire_all()
    assert a.title == "미팅" and a.follow_ups[0].done is True  # 탭 1 의 완료 표시가 그대로


def test_proposal_target_form_uses_the_stamp(user_client, db):
    c = _customer(db)
    p = Proposal(customer_id=c.id, request_memo="처음 메모")
    db.add(p)
    db.commit()
    stamp = REV.search(user_client.get(f"/proposals/{p.id}").text).group(1)
    p.request_memo = "다른 탭 메모"
    db.commit()
    r = user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "내 메모", "_rev": stamp}, follow_redirects=False)
    assert r.status_code == 409 and "내 메모" in r.text and "다른 곳에서 이 기록이 바뀌었습니다" in r.text and "그래도 내 내용으로 저장" in r.text
    db.expire_all()
    assert p.request_memo == "다른 탭 메모"
    assert user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "내 메모", "_rev": stamp, "_force": "1"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert p.request_memo == "내 메모"


def test_proposal_stamp_ignores_ai_job_churn(db):
    c = _customer(db)
    p = Proposal(customer_id=c.id, request_memo="m")
    db.add(p)
    db.commit()
    before = guards.rev_of(p)
    p.status = "directions"  # AI 작업이 올리는 값: 대상 폼과 상관없다
    p.deck_name = "x.pptx"
    db.commit()
    assert guards.rev_of(p) == before
    p.request_memo = "다름"
    db.commit()
    assert guards.rev_of(p) != before


def test_admin_user_row_conflict(admin_client, db):
    u = make_user(db, "lee")
    stamp = REV.search(admin_client.get("/admin/users").text.split('action="/admin/users/%d"' % u.id)[1]).group(1)
    u.name = "다른 곳에서 바꾼 이름"
    db.commit()
    r = admin_client.post(f"/admin/users/{u.id}", data={"name": "내 이름", "role": "user", "is_active": "on", "_rev": stamp})
    assert r.status_code == 409 and "다른 곳에서 바뀌었" in r.text
    db.expire_all()
    assert u.name == "다른 곳에서 바꾼 이름"


# ---- A9: 고친 사람 ----

def test_updated_by_is_recorded_and_shown(app, user_client, db):
    owner = make_user(db, "owner", name="소유자")
    c = Customer(name="한빛병원", created_by_id=owner.id)
    db.add(c)
    db.commit()
    assert c.updated_by_id is None
    assert "고친 사람" not in user_client.get(f"/customers/{c.id}").text  # 옛 기록: 아직 모름
    user_client.post(f"/customers/{c.id}", data={"name": "한빛병원(고침)"})
    db.expire_all()
    kim = db.query(User).filter_by(username="kim").one()
    assert c.updated_by_id == kim.id and c.created_by_id == owner.id  # 권한은 그대로: 누구나 고칠 수 있다
    page = user_client.get(f"/customers/{c.id}").text
    assert "고친 사람" in page and "<dd>kim</dd>" in page


@pytest.mark.parametrize("kind", list(CASES))
def test_updated_by_on_every_core_type(user_client, db, kind):
    obj = _make(db, kind)
    assert obj.updated_by_id is None
    user_client.post(CASES[kind]["url"].format(id=obj.id), data=_data(kind, obj))
    db.expire_all()
    assert obj.updated_by_id == db.query(User).filter_by(username="kim").one().id


def test_updated_by_added_by_migration(tmp_path):
    """옛 DB 에 `updated_by_id`·`must_change_password` 열이 더해진다 (기존 행은 그대로)."""
    import sqlite3

    from sqlalchemy import create_engine, inspect

    from app.migrate import add_missing_columns

    path = tmp_path / "app.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, name TEXT, password_hash TEXT, role TEXT, is_active BOOLEAN, created_at DATETIME)")
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, industry TEXT, contact TEXT, memo TEXT, created_by_id INTEGER, created_at DATETIME, updated_at DATETIME, origin_type TEXT, origin_id INTEGER)")
    conn.execute("INSERT INTO customers (id, name) VALUES (1, '옛 고객')")
    conn.commit()
    conn.close()
    engine = create_engine(f"sqlite:///{path}")
    added = add_missing_columns(engine, path)
    assert {"customers.updated_by_id", "users.must_change_password"} <= set(added)
    insp = inspect(engine)
    assert "updated_by_id" in {c["name"] for c in insp.get_columns("customers")}
    assert "must_change_password" in {c["name"] for c in insp.get_columns("users")}
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT name, updated_by_id FROM customers").one() == ("옛 고객", None)
    engine.dispose()


# ---- A2: 한 번 쓰는 폼 표 ----

def _race(app, n, post, username="kim"):
    clients = [login(app, username) for _ in range(n)]
    barrier = threading.Barrier(n)
    results, errors = [], []

    def go(c):
        try:
            barrier.wait(timeout=20)
            results.append(post(c))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors, errors
    return results


def test_solution_create_with_same_token_makes_one(app, user_client, db):
    tok = token_of_page(user_client, "/solutions/new")
    results = _race(app, 5, lambda c: c.post("/solutions", data={"name": "연타 솔루션", "stage": "intake", "_tok": tok}, follow_redirects=False))
    assert all(r.status_code == 303 for r in results)
    assert db.query(Solution).filter_by(name="연타 솔루션").count() == 1
    sol = db.query(Solution).one()
    assert {r.headers["location"].split("?")[0] for r in results} == {f"/solutions/{sol.id}"}  # 모두 처음 결과로
    assert all("notice=created" in r.headers["location"] for r in results)  # 모두 성공 알림이 보인다


def test_activity_raw_save_with_same_token_makes_one(app, user_client, db):
    tok = token_of_page(user_client, "/activities/new")
    results = _race(app, 4, lambda c: c.post("/activities/raw", data={"raw_text": "한빛병원 미팅", "_tok": tok}, follow_redirects=False))
    assert all(r.status_code == 303 for r in results)
    assert db.query(Activity).count() == 1


def test_customer_create_with_same_token_makes_one(app, user_client, db):
    tok = token_of_page(user_client, "/customers/new")
    results = _race(app, 5, lambda c: c.post("/customers", data={"name": "동시 고객", "_tok": tok}, follow_redirects=False))
    assert all(r.status_code == 303 for r in results)
    assert db.query(Customer).count() == 1


@pytest.mark.parametrize("url, data, model", [
    ("/partners", {"name": "파트너"}, Partner),
    ("/bms", {"name": "BM"}, BM),
])
def test_other_creates_use_the_token(user_client, db, url, data, model):
    tok = token_of_page(user_client, url + "/new")
    first = user_client.post(url, data={**data, "_tok": tok}, follow_redirects=False)
    second = user_client.post(url, data={**data, "_tok": tok}, follow_redirects=False)
    assert first.status_code == second.status_code == 303 and db.query(model).count() == 1
    assert second.headers["location"].split("?")[0] == first.headers["location"].split("?")[0]


def test_project_opportunity_proposal_activity_and_intake_creates_use_the_token(user_client, db):
    c = _customer(db)
    for url, data in (("/projects", {"name": "프로젝트", "customer_id": c.id}), ("/opportunities", {"name": "기회", "customer_id": c.id}),
                      ("/proposals", {"customer_id": c.id}), ("/activities", {"raw_text": "원문", "title": "제목", "kind": "meeting", "date": "2026-11-01"})):
        page = {"/activities": "/activities/new?manual=1", "/opportunities": f"/opportunities/new?customer_id={c.id}"}.get(url, url + "/new")
        tok = token_of_page(user_client, page)
        a = user_client.post(url, data={**data, "_tok": tok}, follow_redirects=False)
        b = user_client.post(url, data={**data, "_tok": tok}, follow_redirects=False)
        assert a.status_code == b.status_code == 303 and a.headers["location"].split("?")[0] == b.headers["location"].split("?")[0], url
    assert (db.query(Project).count(), db.query(Opportunity).count(), db.query(Proposal).count(), db.query(Activity).count()) == (1, 1, 1, 1)
    tok = token_of_page(user_client, "/intake/new")
    files = [("files", ("메모.txt", b"hello", "text/plain"))]
    a = user_client.post("/intake", data={"note": "", "_tok": tok}, files=files, follow_redirects=False)
    b = user_client.post("/intake", data={"note": "", "_tok": tok}, files=files, follow_redirects=False)
    assert a.status_code == b.status_code == 303 and db.query(Intake).count() == 1


def test_token_is_per_user_and_released_on_validation_error(app, user_client, db):
    make_user(db, "lee")
    other = login(app, "lee")
    tok = token_of_page(user_client, "/customers/new")
    assert user_client.post("/customers", data={"name": "", "_tok": tok}).status_code == 400  # 오류면 표를 풀어 고쳐서 다시 낼 수 있다
    assert db.query(FormToken).count() == 0
    assert user_client.post("/customers", data={"name": "고객", "_tok": tok}, follow_redirects=False).status_code == 303
    r = other.post("/customers", data={"name": "고객", "_tok": tok, "confirm_dup": "1"}, follow_redirects=False)  # 다른 사용자의 같은 글자 표는 따로다 (같은 이름 경고는 확인했다)
    assert r.status_code == 303 and db.query(Customer).count() == 2


def test_missing_token_is_accepted_as_before(user_client, db):
    assert user_client.post("/customers", data={"name": "표 없음"}, follow_redirects=False).status_code == 303
    assert user_client.post("/customers", data={"name": "표 없음", "confirm_dup": "1"}, follow_redirects=False).status_code == 303  # 같은 이름 경고를 확인했다
    assert db.query(Customer).count() == 2


def test_old_tokens_are_purged(app, user_client, db):
    db.add_all([FormToken(user_id=1, token="old", created_at=datetime.now() - timedelta(hours=3)),
                FormToken(user_id=1, token="new", created_at=datetime.now())])
    db.commit()
    assert guards.purge_tokens(db) == 1
    assert [t.token for t in db.query(FormToken)] == ["new"]
    tok = token_of_page(user_client, "/customers/new")  # 쓸 때도 오래된 것은 지운다
    db.add(FormToken(user_id=1, token="ancient", created_at=datetime.now() - timedelta(days=2)))
    db.commit()
    user_client.post("/customers", data={"name": "고객", "_tok": tok})
    assert "ancient" not in [t.token for t in db.query(FormToken)]


def test_create_forms_render_a_token_and_submit_button_script_is_shipped(user_client, db):
    for url in ("/customers/new", "/partners/new", "/solutions/new", "/projects/new", "/bms/new", "/opportunities/new?customer_id=%d" % _customer(db).id, "/proposals/new", "/intake/new", "/activities/new", "/activities/new?manual=1"):
        assert 'name="_tok"' in user_client.get(url).text, url
    js = user_client.get("/static/app.js").text
    assert "data-busy" in js and "pageshow" in js


def test_daily_job_purges_tokens(app, db):
    from app import scheduler

    db.add(FormToken(user_id=1, token="old", created_at=datetime.now() - timedelta(days=1)))
    db.commit()
    scheduler._form_token_job(app)
    assert db.query(FormToken).count() == 0


# ---- A3: 내 정보·비밀번호 ----

def test_account_page_shows_identity_and_is_linked_from_sidebar(user_client):
    page = user_client.get("/account").text
    assert "내 정보" in page and "kim" in page and "일반 사용자" in page and "비밀번호 바꾸기" in page
    assert 'href="/account"' in user_client.get("/").text


def test_account_requires_login(client):
    assert client.get("/account", follow_redirects=False).headers["location"] == "/login"


def test_change_password_flow(app, user_client, db):
    other = login(app)  # 다른 곳(다른 기기)의 로그인
    assert other.get("/", follow_redirects=False).status_code == 200
    r = user_client.post("/account/password", data={"current_password": "pw1234", "new_password": "새로운비번1234", "confirm_password": "새로운비번1234"},
                         follow_redirects=False)
    assert r.status_code == 303 and "notice=password_changed" in r.headers["location"]
    assert user_client.get("/", follow_redirects=False).status_code == 200  # 지금 로그인은 그대로
    assert other.get("/", follow_redirects=False).status_code == 303  # 다른 곳의 로그인은 끝났다
    assert TestClient(app).post("/login", data={"username": "kim", "password": "pw1234"}).status_code == 401
    assert TestClient(app).post("/login", data={"username": "kim", "password": "새로운비번1234"}, follow_redirects=False).status_code == 303


@pytest.mark.parametrize("current, new, confirm, text", [
    ("틀림", "abcdefgh1", "abcdefgh1", "현재 비밀번호가 맞지 않습니다"),
    ("pw1234", "short1", "short1", "8자 이상"),
    ("pw1234", "kim", "kim", "8자 이상"),
    ("pw1234", "KIM", "KIM", "8자 이상"),
    ("pw1234", "aaaaaaaaaa", "aaaaaaaaaa", "같은 글자 하나로만"),
    ("pw1234", "abcdefgh1", "다르게", "서로 다릅니다"),
])
def test_change_password_validation(user_client, db, current, new, confirm, text):
    r = user_client.post("/account/password", data={"current_password": current, "new_password": new, "confirm_password": confirm})
    assert r.status_code == 400 and text in r.text
    db.expire_all()
    from app.security import verify_password

    assert verify_password("pw1234", db.query(User).filter_by(username="kim").one().password_hash)


def test_password_equal_to_username_is_rejected(app, db):
    make_user(db, "kimkimkim", password="oldpass99")
    c = login(app, "kimkimkim", "oldpass99")
    r = c.post("/account/password", data={"current_password": "oldpass99", "new_password": "kimkimkim", "confirm_password": "kimkimkim"})
    assert r.status_code == 400 and "아이디와 같은 비밀번호" in r.text


def test_admin_create_and_reset_follow_the_rule_and_force_a_change(app, admin_client, db):
    r = admin_client.post("/admin/users", data={"username": "lee", "password": "x", "role": "user"})
    assert r.status_code == 400 and "8자 이상" in r.text and db.query(User).filter_by(username="lee").count() == 0
    r = admin_client.post("/admin/users", data={"username": "lee", "name": "이대리", "password": "temp12345", "role": "user"}, follow_redirects=False)
    assert r.status_code == 303
    lee = db.query(User).filter_by(username="lee").one()
    assert lee.must_change_password is True
    # 로그인하면 비밀번호 바꾸기 화면으로: 다른 화면은 바꾸기 전까지 막힌다 (로그아웃은 된다)
    c = TestClient(app)
    login_r = c.post("/login", data={"username": "lee", "password": "temp12345"}, follow_redirects=False)
    assert login_r.status_code == 303 and login_r.headers["location"] == "/account"
    page = c.get("/account")
    assert page.status_code == 200 and "비밀번호를 먼저 바꿔 주세요" in page.text
    for url in ("/", "/customers", "/solutions/new", "/search?q=a"):
        r = c.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/account", url
    assert c.post("/customers", data={"name": "x"}, follow_redirects=False).headers["location"] == "/account"
    assert c.post("/account/password", data={"current_password": "temp12345", "new_password": "temp12345", "confirm_password": "temp12345"}).status_code == 400  # 같은 비밀번호는 안 된다
    ok = c.post("/account/password", data={"current_password": "temp12345", "new_password": "내가정한비번99", "confirm_password": "내가정한비번99"}, follow_redirects=False)
    assert ok.status_code == 303
    db.expire_all()
    assert not lee.must_change_password
    assert c.get("/customers", follow_redirects=False).status_code == 200
    # 비밀번호 다시 정하기: 규칙 + 다시 바꾸게 한다
    row = admin_client.get("/admin/users").text.split('action="/admin/users/%d"' % lee.id)[1]
    stamp = REV.search(row).group(1)
    r = admin_client.post(f"/admin/users/{lee.id}", data={"name": "이대리", "role": "user", "is_active": "on", "password": "abc", "_rev": stamp})
    assert r.status_code == 400 and "8자 이상" in r.text
    r = admin_client.post(f"/admin/users/{lee.id}", data={"name": "이대리", "role": "user", "is_active": "on", "password": "reset12345", "_rev": stamp}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert lee.must_change_password is True
    assert c.get("/customers", follow_redirects=False).status_code == 303  # 비밀번호가 바뀌어 기존 로그인도 끝난다


def test_forced_change_lets_logout_through(app, db):
    u = make_user(db, "park", password="temp12345")
    u.must_change_password = True
    db.commit()
    c = login(app, "park", "temp12345")
    assert c.post("/logout", follow_redirects=False).headers["location"] == "/login"


def test_admin_changing_own_password_keeps_session_and_no_forced_change(admin_client, db):
    me = db.query(User).filter_by(username="admin").one()
    row = admin_client.get("/admin/users").text.split('action="/admin/users/%d"' % me.id)[1]
    r = admin_client.post(f"/admin/users/{me.id}", data={"name": "관리자", "role": "admin", "is_active": "on", "password": "adminnew99", "_rev": REV.search(row).group(1)},
                          follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert not me.must_change_password
    assert admin_client.get("/admin/users", follow_redirects=False).status_code == 200


def test_legacy_short_password_still_logs_in_with_a_notice(app, db):
    make_user(db, "old", password="pw1234")  # 규칙이 생기기 전의 짧은 비밀번호
    c = TestClient(app)
    r = c.post("/login", data={"username": "old", "password": "pw1234"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/account?notice=weak_password")
    page = c.get(r.headers["location"]).text
    assert "새 규칙" in page and "비밀번호 바꾸기" in page
    assert c.get("/customers", follow_redirects=False).status_code == 200  # 잠기지 않는다


def test_add_user_form_defaults_to_plain_user_and_explains(admin_client):
    page = admin_client.get("/admin/users").text
    form = page.split('action="/admin/users"')[1].split("</form>")[0]
    assert re.search(r'<option value="user" selected>일반 사용자</option>', form) and not re.search(r'<option value="admin" selected', form)
    assert "8자 이상" in page and "처음 로그인한 뒤 새 비밀번호로 바꿔야" in page


def test_login_messages(app, db):
    make_user(db, "gone", password="pw123456")
    db.query(User).filter_by(username="gone").one().is_active = False
    db.commit()
    c = TestClient(app)
    ok = c.post("/login", data={"username": "gone", "password": "pw123456"})
    assert ok.status_code == 401 and "사용이 중지된 계정입니다. 관리자에게 문의하세요." in ok.text
    wrong = c.post("/login", data={"username": "gone", "password": "틀린비밀번호"})
    nobody = c.post("/login", data={"username": "nobody", "password": "아무거나12345"})
    for r in (wrong, nobody):  # 계정이 있는지 드러나지 않는 일반 문구
        assert r.status_code == 401 and "아이디 또는 비밀번호가 올바르지 않습니다." in r.text and "중지" not in r.text


def test_help_page_has_a_password_how_to(user_client):
    page = user_client.get("/help").text
    assert "비밀번호 바꾸기" in page and 'href="/account"' in page


# ---- A4: 주소 번호가 숫자가 아닐 때 ----

def _detail_get_routes(app):
    out = []
    for path, methods in route_table(app):
        if "GET" in methods and re.search(r"\{\w+\}", path) and ":path" not in path and not path.startswith(("/static", "/businesses")):
            out.append(path)
    return sorted(set(out))


def test_non_integer_ids_show_the_friendly_404_on_every_detail_route(app, admin_client):
    user_client = admin_client  # 관리자 전용 주소까지
    routes = _detail_get_routes(app)
    assert len(routes) > 25
    checked = 0
    for path in routes:
        for bad in ("abc", "1.5"):
            url = re.sub(r"\{\w+\}", bad, path)
            r = user_client.get(url, headers={"accept": "text/html"}, follow_redirects=False)
            if r.status_code in (301, 303, 307):  # 번호 자리가 아닌 글자 경로
                continue
            assert r.status_code == 404, (url, r.status_code)
            assert r.headers["content-type"].startswith("text/html") and "항목을 찾을 수 없습니다" in r.text and '{"detail"' not in r.text, url
            checked += 1
    assert checked >= 40


def test_validation_errors_elsewhere_keep_their_behaviour(app, user_client):
    # HTMX 요청: 기존 JSON
    r = user_client.get("/customers/abc", headers={"HX-Request": "true"})
    assert r.status_code == 422 and r.json()["detail"][0]["type"] == "int_parsing"
    # POST 의 경로 오류도 지금 그대로
    assert user_client.post("/customers/abc/delete").status_code == 422
    # 로그인하지 않으면 로그인 화면
    assert TestClient(app).get("/customers/abc", follow_redirects=False).headers["location"] == "/login"


# ---- A5: 올리기 저장 뒤 알림 ----

def _apply(client, it, form):
    return client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)


def test_apply_double_click_ends_on_the_success_notice(app, user_client, db, settings):
    it, s1, c1 = ready_intake(db, settings)
    form = form_from_page(user_client, it)
    assert form["_tok"]
    first = _apply(user_client, it, form)
    second = _apply(user_client, it, form)  # 연타로 진 쪽의 요청: 같은 폼 표
    assert nloc(first) == nloc(second) == f"/intake/{it.id}?notice=intake_applied"
    page = user_client.get(second.headers["location"]).text
    assert "정리안을 저장했습니다." in page and "이미 저장" not in page


def test_apply_loser_without_token_right_after_saving_is_still_success(app, user_client, db, settings):
    it, s1, c1 = ready_intake(db, settings)
    form = form_from_page(user_client, it)
    assert _apply(user_client, it, form).status_code == 303
    loser = _apply(user_client, it, {k: v for k, v in form.items() if k != "_tok"})  # 표 없는 옛 화면의 요청
    assert nloc(loser) == f"/intake/{it.id}?notice=intake_applied"


def test_apply_later_on_an_applied_bundle_says_already_applied_neutrally(app, user_client, db, settings):
    it, s1, c1 = ready_intake(db, settings)
    form = form_from_page(user_client, it)
    assert _apply(user_client, it, form).status_code == 303
    it.applied_at = datetime.now() - timedelta(minutes=10)
    db.commit()
    r = _apply(user_client, it, {k: v for k, v in form.items() if k != "_tok"})
    assert nloc(r) == f"/intake/{it.id}?notice=already_applied"
    text = user_client.get(r.headers["location"]).text
    assert "이미 저장된 묶음입니다." in text and "저장할 수 없는 상태" not in text
    assert notices.lookup("already_applied").tone == "warn"


def test_apply_concurrent_ends_every_request_on_success(app, user_client, db, settings):
    it, s1, c1 = ready_intake(db, settings)
    form = form_from_page(user_client, it)
    results = _race(app, 5, lambda c: _apply(c, it, form))
    assert {nloc(r) for r in results} == {f"/intake/{it.id}?notice=intake_applied"}
    assert db.query(Activity).count() == 1


# ---- A6: 알림은 한 번만 ----

def test_notice_needs_a_fresh_stamp(user_client, db):
    c = _customer(db)
    url = f"/customers/{c.id}"
    assert 'class="flash' not in user_client.get(f"{url}?notice=deleted").text  # 옛 북마크: 시각 표시가 없다
    now = int(__import__("time").time())
    assert "삭제했습니다." in user_client.get(f"{url}?notice=deleted&nt={now}").text
    assert 'class="flash' not in user_client.get(f"{url}?notice=deleted&nt={now - 60}").text  # 오래됐다
    for bad in ("abc", "", "-5", "9" * 30, str(now + 3600)):
        assert 'class="flash' not in user_client.get(f"{url}?notice=deleted&nt={bad}").text, bad
    r = user_client.post(f"/customers/{c.id}", data={"name": "고침"}, follow_redirects=True)
    assert "저장했습니다." in r.text  # 방금 한 일의 알림은 보인다
    assert re.search(r'class="flash-x" href="/customers/%d"' % c.id, r.text)  # 닫기 링크는 notice 와 nt 를 모두 뺀다


def test_with_notice_adds_stamp_and_refresh_restamps():
    url = notices.with_notice("/a?x=1#h", "saved")
    assert re.fullmatch(r"/a\?x=1&notice=saved&nt=\d+#h", url)
    stale = "/a?notice=saved&nt=1"
    assert notices.fresh(re.search(r"nt=(\d+)", notices.refresh(stale)).group(1))
    assert notices.refresh("/a") == "/a"


# ---- A7: 서버 재시작으로 중단된 AI 작업 ----

RESTART = "서버가 다시 시작되어 작업이 중단되었습니다. 다시 시도해 주세요."


def test_stale_running_job_gets_its_own_error_kind(app, user_client, db):
    from app.ai.queue import fail_stale_running
    from app.models import AIJob

    kim = db.query(User).filter_by(username="kim").one()
    job = AIJob(kind="activity_structure", status="running", requester_id=kim.id, input={"raw_text": "메모"})
    db.add(job)
    db.commit()
    assert fail_stale_running(db) == 1
    db.refresh(job)
    assert job.status == "failed" and job.error_kind == "restart"
    page = user_client.get(f"/activities/draft/{job.id}").text
    assert RESTART in page and "알 수 없는 오류" not in page
    assert 'action="/activities/draft/%d/retry"' % job.id in page  # 다시 시도 조작은 그대로
    assert RESTART in user_client.get(f"/jobs/{job.id}/status", headers={"HX-Request": "true"}).text


def test_old_stale_rows_stored_as_unknown_read_the_same(app, admin_client, user_client, db):
    """이미 `unknown` 으로 남은 옛 중단 작업도 같은 안내로 읽는다 (행은 고쳐 쓰지 않는다)."""
    from app.models import AIJob

    kim = db.query(User).filter_by(username="kim").one()
    admin = db.query(User).filter_by(username="admin").one()
    job = AIJob(kind="activity_structure", status="failed", error_kind="unknown", error="서버가 재시작되어 중단되었습니다.", requester_id=kim.id,
                input={"raw_text": "메모"}, finished_at=datetime.now())
    db.add(job)
    db.commit()
    assert RESTART in user_client.get(f"/activities/draft/{job.id}").text
    assert RESTART in admin_client.get("/admin/jobs").text
    db.refresh(job)
    assert job.error_kind == "unknown"


def test_restart_message_everywhere_job_errors_show(app, db):
    from app import intake as intake_lib
    from app.models import AIJob

    old = AIJob(kind="x", status="failed", error_kind="restart", error="서버가 재시작되어 중단되었습니다.")
    assert intake_lib.job_failure_message(old) == RESTART
    from app.research import ERROR_KINDS

    assert ERROR_KINDS["restart"] == RESTART
    from app.labels import ERROR_KINDS as labels_kinds

    assert labels_kinds["restart"] == RESTART


# ---- A8: 오류 화면이 뜬 주소를 새로 열어도 405 가 아니다 ----

def _post_only_paths(app):
    table = route_table(app)
    get_paths = {p for p, m in table if "GET" in m}
    return sorted({p for p, m in table if "POST" in m and p not in get_paths and not p.startswith("/businesses")})


def test_post_only_addresses_do_not_answer_405_to_a_get(app, admin_client):
    user_client = admin_client  # 관리자 전용 주소까지
    paths = _post_only_paths(app)
    assert len(paths) > 30
    for path in paths:
        url = re.sub(r"\{\w+\}", "1", path)
        r = user_client.get(url, follow_redirects=False)
        if r.status_code == 200:  # 삭제 확인 화면이 있는 주소는 항목이 있으면 "정말 지울까요?" 를 보인다 (베타 AD 의 솔루션 분야는 처음부터 1번이 있다)
            from app.routers.confirm import SPECS as _confirm_specs

            assert any(re.sub(r"\{\w+\}", "1", sp.path) == url for sp in _confirm_specs), (url, r.status_code)
            assert r.headers["content-type"].startswith("text/html"), url
            continue
        assert r.status_code in (303, 404), (url, r.status_code)  # 405 도 JSON 도 아니다
        if r.status_code == 303:
            assert r.headers["location"] != url


def test_get_fallback_targets_the_proper_pages(user_client, db):
    admin = TestClient(user_client.app)
    make_user(db, "boss", role="admin")
    admin.post("/login", data={"username": "boss", "password": "pw1234"})
    assert admin.get("/admin/users/1", follow_redirects=False).headers["location"] == "/admin/users"
    assert user_client.get("/activities/raw", follow_redirects=False).headers["location"] == "/activities/new"
    assert user_client.get("/activities/draft", follow_redirects=False).headers["location"] == "/activities/new"
    s = Solution(name="s", stage="intake")
    db.add(s)
    db.commit()
    assert user_client.get(f"/solutions/{s.id}/stage", follow_redirects=False).headers["location"] == f"/solutions/{s.id}"
    assert user_client.get("/files/3/kind", follow_redirects=False).headers["location"] == "/"


def test_failed_post_page_keeps_input_and_refresh_goes_to_the_form(user_client):
    r = user_client.post("/activities/raw", data={"raw_text": ""})
    assert r.status_code == 400 and "메모를 입력하세요" in r.text
    assert user_client.get("/activities/raw", follow_redirects=True).status_code == 200


def test_method_not_allowed_for_other_methods_is_unchanged(user_client):
    assert user_client.put("/customers/1").status_code == 405


# ---- 구성안 슬라이드 편집도 같은 표시를 쓴다 ----

def test_outline_slide_edit_uses_the_stamp(user_client, db, settings):
    from tests.conftest import outlined_proposal

    p = outlined_proposal(db, settings)
    base = f"/proposals/{p.id}/outline"
    page = user_client.get(f"{base}?open=2").text
    stamp = re.search(r'action="%s/slides/2" class="slide-form fgrid">\s*<div class="wide">\s*<input type="hidden" name="_rev" value="([^"]+)"' % re.escape(base), page).group(1)
    title = p.outline["slides"][2]["title"]
    # 다른 탭이 같은 장을 먼저 고쳤다
    other = user_client.post(f"{base}/slides/2", data={"kind": "bullets", "title": "다른 탭 제목", "_rev": stamp}, follow_redirects=False)
    assert other.status_code == 303
    r = user_client.post(f"{base}/slides/2", data={"kind": "bullets", "title": "내 제목", "bullets": "내 글머리", "_rev": stamp}, follow_redirects=False)
    assert r.status_code == 409 and "다른 곳에서 이 기록이 바뀌었습니다" in r.text and "내 제목" in r.text and "내 글머리" in r.text
    assert 'name="_force" value="1"' in r.text
    db.expire_all()
    assert db.get(Proposal, p.id).outline["slides"][2]["title"] == "다른 탭 제목" and title != "다른 탭 제목"
    forced = user_client.post(f"{base}/slides/2", data={"kind": "bullets", "title": "내 제목", "_rev": stamp, "_force": "1"}, follow_redirects=False)
    assert forced.status_code == 303
    db.expire_all()
    assert db.get(Proposal, p.id).outline["slides"][2]["title"] == "내 제목"
    # 다른 탭이 장 순서를 바꿔 그 번호의 장이 달라졌다
    stamp3 = re.search(r'slides/3" class="slide-form fgrid">\s*<div class="wide">\s*<input type="hidden" name="_rev" value="([^"]+)"', user_client.get(f"{base}?open=3").text).group(1)
    user_client.post(f"{base}/slides/3/move", data={"dir": "up"})
    moved = user_client.post(f"{base}/slides/3", data={"kind": "bullets", "title": "엉뚱한 장을 덮으려는 값", "_rev": stamp3}, follow_redirects=False)
    assert moved.status_code == 409
    # 표시 없는 저장은 지금처럼
    assert user_client.post(f"{base}/slides/3", data={"kind": "bullets", "title": "표시 없음"}, follow_redirects=False).status_code == 303
