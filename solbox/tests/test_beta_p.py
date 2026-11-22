"""베타 P: 프로젝트 상태는 정해진 목록에서 고른다 (옛 자유 글은 고치지 않고 "(이전 입력)" 으로 보인다)."""
import re

import pytest

from app import ontology
from app.models import Customer, Project

LIST = ["준비", "진행", "완료", "중단", "유지보수"]
LEGACY = "진행 중 (11월까지)"


@pytest.fixture
def cust(db):
    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    return c


def _options(page: str) -> list[tuple[str, bool]]:
    select = re.search(r'<select id="f-status".*?</select>', page, re.S).group(0)
    return [(v, "selected" in rest) for v, rest in re.findall(r'<option value="([^"]*)"([^>]*)>', select)]


def _legacy(db, cust, text=LEGACY, name="옛 프로젝트"):
    p = Project(name=name, customer_id=cust.id, status=text)
    db.add(p)
    db.commit()
    return p


def _stored(db, pid):
    db.expire_all()
    return db.get(Project, pid).status


def test_list_is_defined_once_in_order():
    assert list(ontology.PROJECT_STATUSES) == LIST
    assert list(ontology.CODE_LISTS["project_status"].items) == LIST
    assert ontology.COLUMN_CODES[("businesses", "status")] == "project_status"


def test_form_has_select_with_empty_option_in_order(user_client):
    opts = _options(user_client.get("/projects/new").text)
    assert opts == [("", True)] + [(s, False) for s in LIST]  # 새 폼은 "선택 안 함" 이 골라져 있다
    assert "선택 안 함" in user_client.get("/projects/new").text


@pytest.mark.parametrize("status", LIST + [""])
def test_create_and_edit_with_each_value(user_client, db, cust, status):
    r = user_client.post("/projects", data={"name": "새", "customer_id": str(cust.id), "status": status}, follow_redirects=False)
    assert r.status_code == 303
    p = db.query(Project).filter_by(name="새").one()
    assert p.status == status
    other = "중단" if status != "중단" else "준비"
    r = user_client.post(f"/projects/{p.id}", data={"name": "새", "customer_id": str(cust.id), "status": other}, follow_redirects=False)
    assert r.status_code == 303 and _stored(db, p.id) == other
    edit = _options(user_client.get(f"/projects/{p.id}/edit").text)
    assert (other, True) in edit and len(edit) == 6
    r = user_client.post(f"/projects/{p.id}", data={"name": "새", "customer_id": str(cust.id), "status": status}, follow_redirects=False)
    assert r.status_code == 303 and _stored(db, p.id) == status


@pytest.mark.parametrize("bad", ["진행중", "아무거나", "준비 ", "<b>", "ㅇ" * 500])
def test_invalid_value_is_a_form_error_not_500(user_client, db, cust, bad):
    r = user_client.post("/projects", data={"name": "틀림", "customer_id": str(cust.id), "status": bad})
    assert r.status_code == 400 and "알 수 없는 상태입니다" in r.text or bad == "준비 "
    if bad != "준비 ":  # 앞뒤 공백만 다른 목록 값은 다듬어 받는다
        assert db.query(Project).filter_by(name="틀림").count() == 0
    assert 'aria-invalid="true"' in r.text or bad == "준비 "
    p = _legacy(db, cust, "준비", "고칠 것")
    r = user_client.post(f"/projects/{p.id}", data={"name": "고칠 것", "customer_id": str(cust.id), "status": "없는 값"})
    assert r.status_code == 400 and "알 수 없는 상태입니다" in r.text
    assert _stored(db, p.id) == "준비"


def test_legacy_value_preserved_on_unrelated_edit(user_client, db, cust):
    p = _legacy(db, cust)
    page = user_client.get(f"/projects/{p.id}/edit").text
    opts = _options(page)
    assert opts[-1] == (LEGACY, True) and len(opts) == 7
    assert f"{LEGACY} (이전 입력)" in page
    r = user_client.post(f"/projects/{p.id}", data={"name": "옛 프로젝트", "customer_id": str(cust.id), "status": LEGACY, "memo": "메모만 바꿈"}, follow_redirects=False)
    assert r.status_code == 303 and _stored(db, p.id) == LEGACY
    assert db.get(Project, p.id).memo == "메모만 바꿈"


def test_legacy_value_with_odd_spacing_is_kept_byte_for_byte(user_client, db, cust):
    p = _legacy(db, cust, "진행  중 ")
    r = user_client.post(f"/projects/{p.id}", data={"name": "옛 프로젝트", "customer_id": str(cust.id), "status": "진행  중 "}, follow_redirects=False)
    assert r.status_code == 303 and _stored(db, p.id) == "진행  중 "


def test_legacy_can_change_to_list_value_or_empty_and_is_not_accepted_for_other_records(user_client, db, cust):
    p = _legacy(db, cust)
    r = user_client.post(f"/projects/{p.id}", data={"name": "옛 프로젝트", "customer_id": str(cust.id), "status": "완료"}, follow_redirects=False)
    assert r.status_code == 303 and _stored(db, p.id) == "완료"
    assert LEGACY not in user_client.get(f"/projects/{p.id}/edit").text  # 목록 값으로 바꾸면 옛 항목은 없어진다
    q = _legacy(db, cust, "다른 옛 글", "다른 프로젝트")
    r = user_client.post(f"/projects/{q.id}", data={"name": "다른 프로젝트", "customer_id": str(cust.id), "status": ""}, follow_redirects=False)
    assert r.status_code == 303 and _stored(db, q.id) == ""
    # 다른 기록의 옛 글을 새로 쓰려 하면 거절
    r = user_client.post("/projects", data={"name": "새것", "customer_id": str(cust.id), "status": LEGACY})
    assert r.status_code == 400 and "알 수 없는 상태입니다" in r.text


def test_error_rerender_of_legacy_record_keeps_legacy_option(user_client, db, cust):
    p = _legacy(db, cust)
    r = user_client.post(f"/projects/{p.id}", data={"name": "", "customer_id": str(cust.id), "status": LEGACY})
    assert r.status_code == 400
    assert _options(r.text)[-1] == (LEGACY, True)


def test_legacy_value_is_displayed_with_marker_on_screens_but_not_in_the_knowledge_copy(user_client, db, cust, settings):
    p = _legacy(db, cust)
    ok = Project(name="목록 값", customer_id=cust.id, status="진행")
    db.add(ok)
    db.commit()
    marked = f"{LEGACY} (이전 입력)"
    detail = user_client.get(f"/projects/{p.id}").text
    assert detail.count(marked) >= 2  # 제목 줄 배지 + 정보 카드
    listing = user_client.get("/projects").text
    assert marked in listing
    assert "진행 (이전 입력)" not in listing and "진행 (이전 입력)" not in user_client.get(f"/projects/{ok.id}").text
    assert marked in user_client.get(f"/customers/{cust.id}").text  # 고객 상세의 연결된 프로젝트 줄
    from app.knowledge import get_mirror  # noqa: F401  (지식 사본은 아래에서 직접 읽는다)
    user_client.post(f"/projects/{p.id}", data={"name": "옛 프로젝트", "customer_id": str(cust.id), "status": LEGACY}, follow_redirects=False)
    text = (settings.knowledge_dir / "projects" / f"{p.id}.md").read_text(encoding="utf-8")
    # 사용자 결정 2026-11-22: 지식 사본(AI 가 읽는 파일)에는 표지를 붙이지 않는다. 저장된 글 그대로 적는다 (베타 P 이전과 같다)
    assert f"- 상태: {LEGACY}\n" in text and "(이전 입력)" not in text
    assert db.get(Project, p.id).status == LEGACY  # 저장된 글에는 표지가 붙지 않는다


def test_legacy_value_is_html_escaped(user_client, db, cust):
    evil = "<script>alert(1)</script>"
    p = _legacy(db, cust, evil)
    for url in (f"/projects/{p.id}", "/projects", f"/projects/{p.id}/edit", f"/customers/{cust.id}"):
        page = user_client.get(url).text
        assert evil not in page, url
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page, url
    r = user_client.post(f"/projects/{p.id}", data={"name": "옛 프로젝트", "customer_id": str(cust.id), "status": evil}, follow_redirects=False)
    assert r.status_code == 303 and _stored(db, p.id) == evil


def test_converted_project_has_empty_status(user_client, db, cust):
    """영업기회 → 프로젝트 전환은 상태를 쓰지 않는다 (빈 값: 목록 규칙에 맞는다)."""
    from app.models import Opportunity

    o = Opportunity(name="수주건", customer_id=cust.id, stage="won")
    db.add(o)
    db.commit()
    r = user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "create", "name": "전환 프로젝트"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.query(Project).filter_by(name="전환 프로젝트").one().status == ""
