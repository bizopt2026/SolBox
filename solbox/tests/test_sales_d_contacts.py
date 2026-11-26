"""영업 D 단계: 같은 사람 판단·나누기 파서·담당자 폼·상세·회사 카드·나누기 화면."""
import pytest
from sqlalchemy import select

from app import contacts as ct
from app.models import Contact, Customer, Partner, User
from tests.conftest import nloc


@pytest.mark.parametrize("line,expect", [
    ("김민수 부장 · 010-1234-5678 · kim@hanbit.co.kr (명함, 2026-11-20 올리기)",
     {"name": "김민수", "title": "부장", "phone": "010-1234-5678", "office_phone": "", "email": "kim@hanbit.co.kr", "note": "명함, 2026-11-20 올리기"}),
    ("이영희", {"name": "이영희", "title": "", "phone": "", "email": "", "note": ""}),
    ("대표번호 02-123-4567", {"name": "", "title": "", "phone": "", "office_phone": "02-123-4567"}),
    ("02-555-1234", {"name": "", "office_phone": "02-555-1234"}),
    ("John Smith Sales Manager john@acme.com +82 10-9999-8888", {"name": "John Smith", "title": "Sales Manager", "phone": "+82 10-9999-8888", "email": "john@acme.com"}),
    ("박대리 영업팀 과장 010 2222 3333, 02-777-8888 · 서울시 강남구", {"name": "박대리", "title": "영업팀 과장", "phone": "010 2222 3333", "office_phone": "02-777-8888", "note": "서울시 강남구"}),
    ("영업팀 최과장 010-1111-2222", {"name": "", "phone": "010-1111-2222"}),
])
def test_parse_line(line, expect):
    got = ct.parse_line(line)
    for k, v in expect.items():
        assert got[k] == v, (k, got)
    assert got["role"] == ""


def test_parse_text_skips_blank_lines():
    rows = ct.parse_text("김민수 010-1234-5678\n\n  \n대표 02-1234-5678\r\n")
    assert [r["name"] for r in rows] == ["김민수", ""] and rows[0]["line"] == "김민수 010-1234-5678"


class _C:
    def __init__(self, name, phone="", email=""):
        self.name, self.phone, self.email = name, phone, email


def test_find_same_rules():
    people = [_C("김민수", "010-1234-5678", "Kim@Hanbit.co.kr"), _C("이 영희")]
    assert ct.find_same(people, "다른이름", "01012345678") is people[0]  # 휴대폰 숫자
    assert ct.find_same(people, "다른이름", "+82 10 1234 5678") is people[0]
    assert ct.find_same(people, "", email="kim@hanbit.co.kr ") is people[0]  # 이메일 소문자
    assert ct.find_same(people, "이영희") is people[1]  # 번호·이메일 둘 다 없으면 이름(공백 무시)
    assert ct.find_same(people, "이영희", "010-0000-0000") is None  # 번호가 있으면 이름만으로는 같지 않다
    assert ct.find_same(people, "김민수", "010-1234-5678", exclude=people[0]) is None


def _customer(db, contact=""):
    c = Customer(name="한빛", contact=contact)
    db.add(c)
    db.commit()
    return c


def test_create_contact_from_customer_and_card(user_client, db):
    c = _customer(db)
    page = user_client.get(f"/contacts/new?customer_id={c.id}").text
    assert 'name="_tok"' in page and "의사결정자" in page and "한빛" in page
    r = user_client.post("/contacts", data={"customer_id": str(c.id), "name": " 김민수 ", "title": "부장", "role": "decision", "phone": "010-1234-5678",
                                           "email": "kim@hanbit.co.kr"}, follow_redirects=False)
    assert nloc(r) == f"/customers/{c.id}?notice=created"
    p = db.scalar(select(Contact))
    me = db.scalar(select(User).where(User.username == "kim"))
    assert (p.name, p.customer_id, p.partner_id, p.role, p.created_by_id, p.origin_type) == ("김민수", c.id, None, "decision", me.id, "manual")
    detail = user_client.get(f"/customers/{c.id}").text
    card = detail.split('id="contacts"')[1].split("</section>")[0]
    assert "담당자 1" in card and f'href="/contacts/{p.id}"' in card and "의사결정자" in card and "010-1234-5678" in card
    assert f'href="/contacts/new?customer_id={c.id}"' in card
    assert "연락처 메모" in detail


def test_create_needs_name_and_company(user_client, db):
    c = _customer(db)
    r = user_client.post("/contacts", data={"customer_id": str(c.id), "name": "  "})
    assert r.status_code == 400 and db.scalar(select(Contact)) is None
    assert user_client.post("/contacts", data={"customer_id": "999", "name": "김"}).status_code == 404
    assert user_client.post("/contacts", data={"name": "김"}).status_code == 404
    assert user_client.get("/contacts/new").status_code == 404


def test_same_person_warning_then_save_anyway(user_client, db):
    c = _customer(db)
    db.add(Contact(name="김민수", customer=c, phone="010-1234-5678"))
    db.commit()
    r = user_client.post("/contacts", data={"customer_id": str(c.id), "name": "김 민수", "phone": "01012345678"})
    assert r.status_code == 200 and "같은 사람일 수 있어요" in r.text and "김민수" in r.text
    assert len(db.scalars(select(Contact)).all()) == 1
    r = user_client.post("/contacts", data={"customer_id": str(c.id), "name": "김 민수", "phone": "01012345678", "confirm_dup": "1"}, follow_redirects=False)
    assert r.status_code == 303 and len(db.scalars(select(Contact)).all()) == 2


def test_partner_contact_edit_and_detail(user_client, db):
    p = Partner(name="그린")
    db.add(p)
    db.commit()
    user_client.post("/contacts", data={"partner_id": str(p.id), "name": "박기사", "role": "nope"})
    person = db.scalar(select(Contact))
    assert person.partner_id == p.id and person.role == ""
    page = user_client.get(f"/contacts/{person.id}/edit").text
    assert 'name="_rev"' in page and 'value="park"' not in page
    r = user_client.post(f"/contacts/{person.id}", data={"name": "박기사", "title": "책임", "role": "user", "email": "park@green.kr"}, follow_redirects=False)
    assert nloc(r) == f"/contacts/{person.id}?notice=saved"
    db.refresh(person)
    assert (person.title, person.role, person.email) == ("책임", "user", "park@green.kr")
    detail = user_client.get(f"/contacts/{person.id}").text
    assert "실무자" in detail and f'href="/partners/{p.id}"' in detail and "그린" in detail and "park@green.kr" in detail
    assert "담당자 1" in user_client.get(f"/partners/{p.id}").text


def test_delete_contact_goes_back_to_company(user_client, db):
    c = _customer(db)
    user_client.post("/contacts", data={"customer_id": str(c.id), "name": "김민수"})
    person = db.scalar(select(Contact))
    confirm = user_client.get(f"/contacts/{person.id}/delete")
    assert confirm.status_code == 200 and "김민수" in confirm.text
    r = user_client.post(f"/contacts/{person.id}/delete", follow_redirects=False)
    assert nloc(r) == f"/customers/{c.id}?notice=deleted" and db.scalar(select(Contact)) is None


def test_split_link_counts_new_named_lines(user_client, db):
    c = _customer(db, "김민수 부장 · 010-1234-5678\n대표번호 02-123-4567\n이영희 대리 lee@hanbit.co.kr")
    db.add(Contact(name="김민수", customer=c, phone="01012345678"))
    db.commit()
    page = user_client.get(f"/customers/{c.id}").text
    assert f'href="/customers/{c.id}/contacts/split"' in page and "담당자로 나누기 (1줄)" in page


def test_split_page_and_save_checked_rows_only(user_client, db):
    text = "김민수 부장 · 010-1234-5678\n대표번호 02-123-4567\n이영희 대리 lee@hanbit.co.kr\n박철수 010-3333-4444"
    c = _customer(db, text)
    db.add(Contact(name="김민수", customer=c, phone="01012345678"))
    db.commit()
    page = user_client.get(f"/customers/{c.id}/contacts/split").text
    assert page.count('type="checkbox"') == 4 and "이미 있음" in page and 'name="_tok"' in page
    assert 'name="pick" value="0"' in page and 'name="pick" value="0" checked' not in page  # 이미 있음은 처음부터 넣지 않음
    assert 'name="pick" value="1"' in page and 'name="pick" value="1" checked' not in page and "이름 없음" in page
    assert 'name="pick" value="2" checked' in page
    data = {"pick": ["1", "2"], "name_1": "", "phone_1": "", "office_phone_1": "02-123-4567",
            "name_2": "이영희", "title_2": "대리", "role_2": "user", "email_2": "lee@hanbit.co.kr", "phone_2": "", "office_phone_2": "", "note_2": "",
            "name_3": "박철수", "phone_3": "010-3333-4444"}
    r = user_client.post(f"/customers/{c.id}/contacts/split", data=data, follow_redirects=False)
    assert nloc(r) == f"/customers/{c.id}?notice=contacts_split"
    names = sorted(p.name for p in db.scalars(select(Contact)))
    assert names == ["김민수", "이영희"]  # 이름 없는 줄은 넣을 수 없다, 체크 안 한 줄은 넣지 않는다
    lee = db.scalar(select(Contact).where(Contact.name == "이영희"))
    assert lee.role == "user" and lee.email == "lee@hanbit.co.kr" and lee.origin_type == "manual"
    db.refresh(c)
    assert c.contact == text  # 연락처 글은 건드리지 않는다


def test_split_skips_rows_that_became_same_person(user_client, db):
    c = _customer(db, "이영희 대리 lee@hanbit.co.kr")
    db.add(Contact(name="이영희", customer=c, email="LEE@hanbit.co.kr"))
    db.commit()
    user_client.post(f"/customers/{c.id}/contacts/split", data={"pick": ["0"], "name_0": "이영희", "email_0": "lee@hanbit.co.kr"})
    assert len(db.scalars(select(Contact)).all()) == 1


def test_partner_split_route(user_client, db):
    p = Partner(name="그린", contact="박기사 010-5555-6666")
    db.add(p)
    db.commit()
    assert user_client.get(f"/partners/{p.id}/contacts/split").status_code == 200
    user_client.post(f"/partners/{p.id}/contacts/split", data={"pick": ["0"], "name_0": "박기사", "phone_0": "010-5555-6666"})
    assert db.scalar(select(Contact)).partner_id == p.id


@pytest.mark.parametrize("line", ["서울시 강남구 테헤란로 123", "경기도 성남시 분당구", "월요일 방문 예정", "대표전화 1588-1234",
                                  "Tel 02-555-1234", "Email kim@x.com", "Mobile 010-1234-5678", "Seoul Office 02-1234-5678", "Fax 02-111-2222"])
def test_parser_does_not_take_places_or_labels_as_names(line):
    assert ct.parse_line(line)["name"] == ""


def test_new_company_split_needs_phone_or_email_and_keeps_skipped_lines():
    people, rest = ct.split_for_new_company("김민수 부장 010-1234-5678\n삼성전자 구매팀\n김민수 02-555-0000\n이영희 lee@x.com")
    assert [p["name"] for p in people] == ["김민수", "이영희"]
    assert rest == "삼성전자 구매팀\n김민수 02-555-0000"  # 번호·이메일 없는 줄, 같은 글 안 같은 사람 줄은 글에 남는다


def test_split_page_does_not_precheck_name_only_lines(user_client, db):
    c = _customer(db, "삼성전자 구매팀")
    page = user_client.get(f"/customers/{c.id}/contacts/split").text
    assert 'name="pick" value="0" checked' not in page
    assert "담당자로 나누기" not in user_client.get(f"/customers/{c.id}").text


def test_email_field_accepts_several_addresses_and_manual_same_person_uses_any(user_client, db):
    c = _customer(db)
    db.add(Contact(name="김민수", customer=c, email="a@x.kr, b@y.kr"))
    db.commit()
    page = user_client.get(f"/contacts/new?customer_id={c.id}").text
    assert 'type="email"' not in page
    r = user_client.post("/contacts", data={"customer_id": str(c.id), "name": "다른 이름", "email": "B@y.kr"})
    assert "같은 사람일 수 있어요" in r.text
