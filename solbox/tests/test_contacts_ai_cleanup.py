"""담당자 관리 부담 줄이기 ③: 관리 › 연락처 메모 정리, 상세의 옮긴 줄 접기."""
import re

from sqlalchemy import select

from app.models import Contact, Customer, Partner
from tests.conftest import nloc

MEMO = "대표번호 02-123-4567\n김민수 부장 010-1234-5678\n이영희 lee@hanbit.kr\n박철수 과장"


def _world(db):
    c = Customer(name="한빛", contact=MEMO)
    p = Partner(name="그린", contact="정기사 010-5555-6666\n정기사 jung@green.kr")
    db.add_all([c, p])
    db.commit()
    db.add(Contact(name="김민수", phone="01012345678", customer=c))
    db.commit()
    return c, p


def test_admin_only(user_client, db):
    _world(db)
    assert user_client.get("/admin/contacts-cleanup").status_code == 403
    assert user_client.post("/admin/contacts-cleanup").status_code == 403


def test_preview_and_apply_once(admin_client, db):
    c, p = _world(db)
    page = admin_client.get("/admin/contacts-cleanup").text
    assert "옮길 수 있는 줄 2줄" in page and "회사 2곳" in page and 'name="_tok"' in page
    tok = re.search(r'name="_tok" value="([^"]+)"', page).group(1)
    r = admin_client.post("/admin/contacts-cleanup", data={"_tok": tok}, follow_redirects=False)
    assert nloc(r) == "/admin/contacts-cleanup?notice=contacts_cleanup"
    admin_client.post("/admin/contacts-cleanup", data={"_tok": tok}, follow_redirects=False)  # 두 번 눌러도 한 벌
    names = sorted(x.name for x in db.scalars(select(Contact)))
    assert names == ["김민수", "이영희", "정기사"]  # 박철수(번호·이메일 없음)는 남기고, 같은 글 안의 정기사는 한 번만
    db.refresh(c)
    assert c.contact == MEMO  # 메모 글은 그대로
    assert "옮길 줄이 없어요" in admin_client.get("/admin/contacts-cleanup").text
    r = admin_client.post("/admin/contacts-cleanup", follow_redirects=False)
    assert "notice=contacts_cleanup_none" in nloc(r)


def test_detail_folds_lines_already_contacts(user_client, db):
    c, p = _world(db)
    page = user_client.get(f"/customers/{c.id}").text
    assert "담당자로 옮긴 1줄 보기" in page
    shown = page.split('class="memo-text">')[1].split("</span>")[0]
    assert "대표번호" in shown and "김민수" not in shown and "박철수" in shown


def test_duplicates_page_links_cleanup(admin_client, db):
    assert 'href="/admin/contacts-cleanup"' in admin_client.get("/admin/duplicates").text
