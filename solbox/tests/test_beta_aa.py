"""베타 AA: 명함 파일 가져오기 (.vcf · .csv · .xlsx). 파일은 모두 이 시험이 만든 가짜 값이다 (실제 사람·번호·회사가 아니다)."""
import dataclasses
import io
import re

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import cardfile, cardplan, intake as intake_lib, ontology, origin
from app.main import create_app
from app.models import AIJob, Customer, Intake, Partner
from tests.conftest import _PageForm, nloc

HEADER = ["이름", "회사", "부서", "직책", "휴대폰", "전화", "이메일"]


def qp(text: str) -> str:
    return "".join(f"={b:02X}" for b in text.encode("utf-8"))


def csv_bytes(rows, header=HEADER, encoding="utf-8-sig") -> bytes:
    lines = [",".join(header)] + [",".join(r) for r in rows]
    return ("\r\n".join(lines) + "\r\n").encode(encoding)


def xlsx_bytes(rows, sheet="Sheet1") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


REMEMBER_REAL = ["회사", "이름", "부서", "직함", "전자 메일 주소", "근무지 주소 번지", "근무처 전화", "근무처 팩스", "휴대폰", "명함 등록일", "명함첩 이름", "그룹", "메모"]


def remember_real_headers_xlsx() -> bytes:
    rows = [REMEMBER_REAL,
            ["(주)가나다전자", "김가짜", "영업팀", "과장", "kim.gajja@example.com", "서울시 가짜구 가짜로 1", "02-000-0001", "02-000-0002", "010-0000-0001", "2026-01-02", "내 명함첩", "거래처", None],
            ["가나다전자", "이모조", "", "", "lee.mojo@example.com", "", "", "", "010-0000-0002", "2026-01-03", "내 명함첩", "", None],
            ["라마바시스템", "박허구", "개발", "팀장", "park.hogu@example.com", "부산시 가짜구 2", "051-000-0003", "", "010-0000-0003", "2026-02-01", "내 명함첩", "", None]]
    return xlsx_bytes(rows, sheet="Remember")


def remember_like_xlsx() -> bytes:
    rows = [["회사명", "이름(한글)", "부서", "직급", "휴대전화", "회사전화", "이메일 주소", "회사주소", "홈페이지", "메모", "등록일"],
            ["루미텍(주)", "최가상", "개발팀", "책임", "010-0000-0010", "031-000-0010", "choi@example.com", "경기도 가짜시", "example.com", "", "2026-03-01"]]
    return xlsx_bytes(rows)


VCF21 = (
    "BEGIN:VCARD\r\nVERSION:2.1\r\n"
    f"N;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:{qp('홍')};{qp('길동')};;;\r\n"
    f"FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:{qp('홍길동')}\r\n"
    f"ORG;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:{qp('한빛센서')};{qp('영업팀')}\r\n"
    f"TITLE;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:{qp('팀장')}\r\n"
    "TEL;CELL:010-0000-0001\r\nTEL;WORK;VOICE:02-000-0002\r\nTEL;WORK;FAX:02-000-0003\r\nTEL;HOME:02-000-0004\r\n"
    "EMAIL;INTERNET:gildong@example.com\r\n"
    + "NOTE;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:" + qp("긴메모입니다 ")[:36] + "=\r\n" + qp("긴메모입니다 ")[36:] + qp("끝") + "\r\n"
    "END:VCARD\r\n"
).encode("ascii")

VCF30 = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:김하늘\r\nN:김;하늘;;;\r\nORG:라마바시스템;개발\\;연구소\r\nTITLE:책임연구원\r\n"
    "TEL;TYPE=CELL,VOICE:+82 10-0000-0011\r\nTEL;TYPE=WORK:02-000-0012\r\n"
    "item1.EMAIL;type=INTERNET:haneul@example.com\r\n"
    "ADR;TYPE=WORK:;;가짜로\\, 3길;서울;;00000;대한민국\r\n"
    "NOTE:첫줄\\n둘째줄 이름이 아주 아주 아주 아주 아주 아주 아주 아주 아주\r\n  긴 메모를 접어 쓴 줄\r\nEND:VCARD\r\n"
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Jane Roe\r\nORG:Acme Example\r\nTEL;TYPE=cell:010-0000-0012\r\nEND:VCARD\r\n"
    "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:이사랑\r\nORG:다라마\r\nTEL;VALUE=uri;TYPE=\"cell,voice\":tel:+82-10-0000-0013\r\nEND:VCARD\r\n"
).encode("utf-8")


# ---------------------------------------------------------------- 파서

def test_vcf21_quoted_printable_utf8_with_soft_break():
    cards = cardfile.parse("a.vcf", VCF21).cards
    assert len(cards) == 1
    c = cards[0]
    assert (c.person, c.company, c.department, c.role) == ("홍길동", "한빛센서", "영업팀", "팀장")
    assert c.mobiles == ["010-0000-0001"] and c.phones == ["02-000-0002"] and c.faxes == ["02-000-0003"]
    assert c.emails == ["gildong@example.com"] and "집 02-000-0004" in c.note and c.note.startswith("긴메모입니다 끝")


def test_vcf30_multiple_cards_folding_escapes_and_versions():
    cards = cardfile.parse("a.vcf", VCF30).cards
    assert [c.person for c in cards] == ["김하늘", "Jane Roe", "이사랑"]
    a = cards[0]
    assert (a.company, a.department, a.role) == ("라마바시스템", "개발;연구소", "책임연구원")
    assert a.mobiles == ["+82 10-0000-0011"] and a.phones == ["02-000-0012"] and a.emails == ["haneul@example.com"]
    assert a.address == "대한민국 서울 가짜로, 3길 (00000)"
    assert a.note.startswith("첫줄 둘째줄 이름이") and a.note.endswith("긴 메모를 접어 쓴 줄")
    assert cards[1].mobiles == ["010-0000-0012"] and cards[2].mobiles == ["+82-10-0000-0013"]


def test_vcf_name_from_n_when_fn_missing_and_charset_euckr():
    raw = "BEGIN:VCARD\r\nVERSION:2.1\r\nN;ENCODING=QUOTED-PRINTABLE;CHARSET=EUC-KR:" + "".join(f"={b:02X}" for b in "박;하늘".encode("euc-kr")) + "\r\nORG:테스트\r\nEND:VCARD\r\n"
    c = cardfile.parse("a.vcf", raw.encode("utf-8")).cards[0]
    assert c.person == "박하늘" and c.company == "테스트"


def test_csv_utf8_bom_and_cp949_and_semicolon():
    rows = [["김가짜", "(주)가나다전자", "영업", "과장", "010-0000-0001", "02-000-0002", "a@example.com"]]
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        p = cardfile.parse("a.csv", csv_bytes(rows, encoding=enc))
        assert not p.needs_mapping and p.cards[0].person == "김가짜" and p.cards[0].company == "(주)가나다전자", enc
    semi = ("이름;회사;이메일\n김가짜;가나다;a@example.com\n").encode("utf-8")
    assert cardfile.parse("a.csv", semi).cards[0].emails == ["a@example.com"]
    with pytest.raises(cardfile.CardFileError, match="글자"):
        cardfile.parse("a.csv", b"\xff\xfe\x81\x00\x00\xff")


def test_xlsx_header_on_second_row_and_excel_dropped_zero():
    data = xlsx_bytes([["명함 목록 (가짜)", None, None], HEADER, ["김가짜", "가나다전자", "", "", 1012345678, "", "a@example.com"]])
    p = cardfile.parse("a.xlsx", data)
    assert p.header_row == 1 and not p.needs_mapping
    assert p.cards[0].mobiles == ["01012345678"] and p.cards[0].row == 3


def test_xlsx_unknown_headers_need_mapping_then_mapping_works():
    data = xlsx_bytes([["A열", "B열", "C열", "D열"], ["김가짜", "가나다전자", "010-0000-0001", "x@example.com"]])
    p = cardfile.parse("a.xlsx", data)
    assert p.needs_mapping and p.cards == [] and p.headers == ["A열", "B열", "C열", "D열"]
    mapping = cardfile.build_mapping(p.headers, {"person": "0", "company": "1", "mobile": "2", "email": "3", "note": "9", "fax": "x"})
    assert mapping == {"person": 0, "company": 1, "mobile": 2, "email": 3}  # 범위 밖·숫자가 아닌 값은 버린다
    q = cardfile.parse("a.xlsx", data, mapping=mapping, header_row=0)
    assert q.cards[0].person == "김가짜" and q.cards[0].emails == ["x@example.com"]


def test_remember_like_korean_headers_and_ignored_columns():
    p = cardfile.parse("a.xlsx", remember_like_xlsx())
    view = {m["field"]: m["column"] for m in p.mapping_view()}
    assert view == {"person": "이름(한글)", "company": "회사명", "department": "부서", "role": "직급", "mobile": "휴대전화", "phone": "회사전화",
                    "email": "이메일 주소", "address": "회사주소", "note": "메모"}
    c = p.cards[0]
    assert c.company == "루미텍(주)" and c.address == "경기도 가짜시" and c.mobiles == ["010-0000-0010"] and "example.com" not in c.note
    plan_values = cardplan.contact_values(c)
    assert "2026-03-01" not in "".join(plan_values.values())


def test_remember_real_headers_fixture_and_parse_card_file(tmp_path):
    path = tmp_path / "remember.xlsx"
    path.write_bytes(remember_real_headers_xlsx())
    mapping, cards, problems = cardfile.parse_card_file(path)
    assert problems == []
    assert mapping == {"company": "회사", "person": "이름", "department": "부서", "role": "직함", "email": "전자 메일 주소", "address": "근무지 주소 번지",
                       "phone": "근무처 전화", "fax": "근무처 팩스", "mobile": "휴대폰", "note": "메모"}
    assert len(cards) == 3 and cards[0].faxes == ["02-000-0002"] and cards[0].phones == ["02-000-0001"]
    joined = " ".join(cardplan.contact_values(c)["note"] + cardplan.contact_values(c)["person"] for c in cards)
    assert "2026-01" not in joined and "내 명함첩" not in joined and "거래처" not in joined  # 등록일·명함첩·그룹은 연락처 줄에 넣지 않는다
    assert cardfile.parse_card_file(tmp_path / "nope.xlsx")[2]


@pytest.mark.parametrize("text,field", [
    ("이름", "person"), ("성명", "person"), ("Name", "person"), ("이름(한글)", "person"), ("회사", "company"), ("회사명", "company"), ("소속", "company"),
    ("Company", "company"), ("Organization", "company"), ("부서", "department"), ("Department", "department"), ("직함", "role"), ("직책", "role"), ("직위", "role"),
    ("Title", "role"), ("Position", "role"), ("휴대폰", "mobile"), ("휴대전화", "mobile"), ("모바일", "mobile"), ("핸드폰", "mobile"), ("Mobile", "mobile"), ("Cell", "mobile"),
    ("전화", "phone"), ("유선", "phone"), ("사무실", "phone"), ("Tel", "phone"), ("Phone", "phone"), ("Work", "phone"), ("팩스", "fax"), ("Fax", "fax"),
    ("이메일", "email"), ("메일", "email"), ("E-mail", "email"), ("Email", "email"), ("전자메일주소", "email"), ("전자 메일 주소", "email"), ("주소", "address"),
    ("Address", "address"), ("메모", "note"), ("Note", "note"), ("홈페이지", None), ("등록일", None), ("명함 등록일", None), ("이메일2", "email"),
])
def test_header_synonyms(text, field):
    assert cardfile.header_field(text) == field


def test_row_limit_and_other_errors():
    rows = [[f"사람{i}", f"회사{i % 5}", "", "", "", "", ""] for i in range(cardfile.MAX_ROWS)]
    assert len(cardfile.parse("a.csv", csv_bytes(rows)).cards) == cardfile.MAX_ROWS
    with pytest.raises(cardfile.CardFileError, match="2,000"):
        cardfile.parse("a.csv", csv_bytes(rows + [["하나더", "회사", "", "", "", "", ""]]))
    with pytest.raises(cardfile.CardFileError, match="2,000"):
        cardfile.parse("a.vcf", ("BEGIN:VCARD\r\nFN:가\r\nORG:나\r\nEND:VCARD\r\n" * (cardfile.MAX_ROWS + 1)).encode())
    with pytest.raises(cardfile.CardFileError):
        cardfile.parse("a.xls", b"x")
    with pytest.raises(cardfile.CardFileError, match="내용"):
        cardfile.parse("a.csv", b"\n\n")
    with pytest.raises(cardfile.CardFileError, match="엑셀"):
        cardfile.parse("a.xlsx", b"not a zip")
    with pytest.raises(cardfile.CardFileError, match="명함을 찾지"):
        cardfile.parse("a.vcf", b"hello")


# ---------------------------------------------------------------- 화면·저장

def _post_cards(client, name, data, kind="customer", tok=None):
    form = {"default_kind": kind}
    if tok:
        form["_tok"] = tok
    return client.post("/intake/cards", data=form, files={"file": (name, data, "application/octet-stream")}, follow_redirects=False)


def _form(client, intake_id):
    r = client.get(f"/intake/{intake_id}")
    assert r.status_code == 200
    parser = _PageForm(f"/intake/{intake_id}/apply")
    parser.feed(r.text)
    return r, parser.fields


def _world(db):
    c = Customer(name="한빛병원", contact="김원장 010-0000-0099")
    p = Partner(name="루미텍(주)", contact="")
    db.add_all([c, p])
    db.commit()
    return c, p


WORLD_ROWS = [
    ["김가짜", "한빛병원", "원무과", "과장", "010-0000-0001", "02-000-0001", "kim@example.com"],       # 등록된 고객
    ["이기존", "한빛병원", "", "", "010-0000-0099", "", ""],                                          # 이미 연락처에 있는 휴대폰
    ["박파트", "㈜루미텍", "개발", "팀장", "010-0000-0002", "", "park@example.com"],                  # 등록된 파트너
    ["최신규", "새로운(주)", "", "대표", "010-0000-0003", "", "choi@example.com"],                    # 새 회사 (둘이 한 항목)
    ["정신규", "새로운", "", "이사", "010-0000-0004", "", ""],
    ["최신규", "새로운(주)", "", "", "010-0000-0003", "", ""],                                        # 같은 사람이 파일 안에 두 번
    ["무회사", "", "", "", "010-0000-0005", "", "none@example.com"],                                 # 회사 없음
]


def test_import_builds_plan_without_ai(user_client, db, settings):
    c, p = _world(db)
    r = _post_cards(user_client, "cards.csv", csv_bytes(WORLD_ROWS))
    assert r.status_code == 303 and "notice=cards_read" in r.headers["location"]
    it = db.query(Intake).one()
    assert (it.status, it.source, it.job_id) == ("ready", "cards", None)
    assert db.query(AIJob).count() == 0  # AI 작업을 넣지 않았다
    assert it.files[0].original_name == "cards.csv" and it.files[0].read_status == "ok"
    assert intake_lib.stored_path(settings, it.files[0]).is_file()  # 올린 파일은 다른 올리기처럼 보관된다
    items = it.plan["items"]
    kinds = [(i["kind"], i.get("target_name") or i.get("name")) for i in items]
    assert kinds == [("new_customer", "새로운(주)"), ("add_contact", "한빛병원"), ("add_contact", "한빛병원"), ("add_contact", "루미텍(주)"), ("unplaced", None)] or \
        [k for k, _ in kinds] == ["new_customer", "add_contact", "add_contact", "add_contact", "unplaced"]
    new = items[0]
    assert new["name"] == "새로운(주)" and new["card_count"] == 2 and new["from_cards"]  # 같은 새 회사는 한 항목
    assert new["contact"].count("\n") == 1 and "최신규" in new["contact"] and "정신규" in new["contact"] and "(명함 파일, " in new["contact"]
    assert it.plan["merged"] == 1 and it.plan["cards"] == 7
    contacts = [i for i in items if i["kind"] == "add_contact"]
    assert [x["person"] for x in contacts] == ["김가짜", "이기존", "박파트"]
    assert contacts[0]["role"] == "원무과 과장" and contacts[0]["phone"] == "010-0000-0001 / 02-000-0001" and contacts[0]["target_id"] == c.id
    assert contacts[2]["target_type"] == "partner" and contacts[2]["target_id"] == p.id
    assert contacts[1]["skip_default"] and "휴대폰" in contacts[1]["skip_note"] and "skip_default" not in contacts[0]
    unplaced = items[-1]
    assert unplaced["kind"] == "unplaced" and any("무회사" in line and "회사 이름이 없어요" in line for line in unplaced["lines"])
    r = user_client.get(f"/intake/{it.id}")
    assert "AI 생성" not in r.text and "AI 를 쓰지 않았어요" in r.text and "정리하지 못한 명함" in r.text
    assert "명함 7장을 읽어" in r.text and "<b>이름</b> ← 이름" in r.text  # 읽은 열 요약
    assert "다시 정리" not in r.text and "정리 시작" not in r.text
    assert 'name="c1__as"' in r.text and "모두 제외" in r.text and "모두 포함" in r.text


def test_apply_creates_records_with_import_origin_and_contact_lines(user_client, db, settings):
    c, p = _world(db)
    _post_cards(user_client, "cards.csv", csv_bytes(WORLD_ROWS))
    it = db.query(Intake).one()
    r, form = _form(user_client, it.id)
    assert "skip_ct2" in form and "skip_ct1" not in form  # 이미 있는 연락처는 처음부터 제외로
    r = user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    assert r.status_code == 303 and "notice=intake_applied" in r.headers["location"]
    db.expire_all()
    hanbit, lumi = db.get(Customer, c.id), db.get(Partner, p.id)
    assert hanbit.contact.splitlines()[0] == "김원장 010-0000-0099"  # 기존 글은 그대로
    assert "김가짜 원무과 과장 · 010-0000-0001 / 02-000-0001 · kim@example.com (명함 파일, " in hanbit.contact and "이기존" not in hanbit.contact
    assert "박파트 개발 팀장" in lumi.contact
    new = db.query(Customer).filter_by(name="새로운(주)").one()
    assert "최신규 대표 · 010-0000-0003" in new.contact and "정신규 이사 · 010-0000-0004" in new.contact
    assert (new.origin_type, new.origin_id) == (ontology.ORIGIN_IMPORT, it.id)
    assert not ontology.is_ai_generated(new) and ontology.ai_generated_note(new) is None
    assert db.query(Customer).count() == 2 and db.query(AIJob).count() == 0
    view = origin.view(db, db.query(__import__("app.models", fromlist=["User"]).User).filter_by(username="kim").one(), new)
    assert view["ai"] is False and view["href"] == f"/intake/{it.id}" and "파일 가져오기" in view["label"]
    page = user_client.get(f"/customers/{new.id}").text
    assert "AI 생성" not in page and "파일 가져오기" in page
    assert "AI 생성" not in user_client.get("/customers").text and "파일 가져오기" in user_client.get("/customers").text
    # 올리기의 같은 파일 한 개가 한 회사에 묶이지 않는다
    assert it.files[0].target_type == ""
    # 올리기 유래 걸러 보기에 함께 센다
    assert str(new.id) and "올리기" in user_client.get("/customers?origin=intake").text


def test_double_apply_saves_once(user_client, db, settings):
    c, _ = _world(db)
    _post_cards(user_client, "cards.csv", csv_bytes(WORLD_ROWS))
    it = db.query(Intake).one()
    _, form = _form(user_client, it.id)
    first = user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    second = user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    assert first.status_code == second.status_code == 303
    db.expire_all()
    assert db.get(Customer, c.id).contact.count("김가짜") == 1 and db.query(Customer).filter_by(name="새로운(주)").count() == 1


def test_double_submit_of_the_upload_form_makes_one_batch(user_client, db):
    page = user_client.get("/intake/cards").text
    tok = re.search(r'name="_tok" value="([^"]+)"', page).group(1)
    data = csv_bytes(WORLD_ROWS)
    a = _post_cards(user_client, "cards.csv", data, tok=tok)
    b = _post_cards(user_client, "cards.csv", data, tok=tok)
    assert a.status_code == b.status_code == 303 and nloc(a) == nloc(b)
    assert db.query(Intake).count() == 1


def test_switch_new_customer_to_partner_and_skip(user_client, db):
    _post_cards(user_client, "cards.csv", csv_bytes([["최신규", "새로운(주)", "", "대표", "010-0000-0003", "", "c@example.com"],
                                                       ["박둘째", "다른회사", "", "", "010-0000-0008", "", ""]]))
    it = db.query(Intake).one()
    _, form = _form(user_client, it.id)
    assert form["c1__as"] == ["customer"]
    form["c1__as"] = ["partner"]
    form["skip_c2"] = ["on"]
    r = user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    partner = db.query(Partner).filter_by(name="새로운(주)").one()
    assert "최신규 대표" in partner.contact and (partner.origin_type, partner.origin_id) == ("import", it.id)
    assert db.query(Customer).count() == 0  # 고객으로는 만들지 않았고, 제외한 회사도 없다


def test_default_kind_partner_and_only_company_unknown_kind_value(user_client, db):
    _post_cards(user_client, "cards.csv", csv_bytes([["최신규", "새로운(주)", "", "", "010-0000-0003", "", ""]]), kind="partner")
    it = db.query(Intake).one()
    assert it.plan["items"][0]["kind"] == "new_partner" and it.plan["default_kind"] == "partner"
    _post_cards(user_client, "cards2.csv", csv_bytes([["최신규", "새로운(주)", "", "", "010-0000-0003", "", ""]]), kind="evil")
    assert db.query(Intake).order_by(Intake.id.desc()).first().plan["items"][0]["kind"] == "new_customer"


def test_company_matching_both_customer_and_partner_defaults_to_customer(user_client, db):
    db.add_all([Customer(name="같은이름(주)"), Partner(name="같은이름")])
    db.commit()
    _post_cards(user_client, "cards.csv", csv_bytes([["김가짜", "같은이름", "", "", "010-0000-0001", "", ""]]))
    items = db.query(Intake).one().plan["items"]
    assert [(i["target_type"], bool(i.get("skip_default"))) for i in items] == [("customer", False), ("partner", True)]


def test_escaping_of_file_values(user_client, db):
    evil = "<script>alert(1)</script>"
    _post_cards(user_client, "cards.csv", csv_bytes([[f"<b>{evil}", f"회사{evil}", "", "", "010-0000-0003", "", ""], [evil, "", "", "", "", "", "a@example.com"]]))
    it = db.query(Intake).one()
    r = user_client.get(f"/intake/{it.id}")
    assert "<script>alert(1)" not in r.text and "&lt;script&gt;alert(1)" in r.text
    assert "<b><script>" not in r.text


def test_cards_without_contact_info_are_unplaced_and_all_unplaced_cannot_save(user_client, db):
    _post_cards(user_client, "cards.csv", csv_bytes([["", "한빛병원", "", "", "", "", ""], ["무회사", "", "", "", "", "", ""]]))
    it = db.query(Intake).one()
    kinds = [i["kind"] for i in it.plan["items"]]
    assert kinds == ["new_customer", "unplaced"]  # 연락처 없는 새 회사는 회사만 만들 수 있다
    assert len(it.plan["items"][-1]["lines"]) == 1 and "무회사" in it.plan["items"][-1]["lines"][0]


def test_mapping_step_for_unknown_headers_xlsx(user_client, db):
    data = xlsx_bytes([["A열", "B열", "C열", "D열"], ["김가짜", "가나다전자", "010-0000-0001", "x@example.com"], ["박허구", "가나다전자", "010-0000-0002", ""]])
    r = _post_cards(user_client, "cards.xlsx", data)
    assert r.status_code == 303 and "notice=cards_need_map" in r.headers["location"]
    it = db.query(Intake).one()
    assert (it.status, it.plan.get("items")) == ("uploaded", None) and db.query(AIJob).count() == 0
    page = user_client.get(f"/intake/{it.id}")
    assert page.status_code == 200 and "어느 열이 무엇인지" in page.text and "정리 시작" not in page.text and 'name="map_person"' in page.text
    # 필수 열을 비우면 다시 고르게 한다
    bad = user_client.post(f"/intake/{it.id}/cards-map", data={"header_row": "1", "map_mobile": "2"}, follow_redirects=False)
    assert bad.status_code == 400 and "이름, 회사" in bad.text
    dup = user_client.post(f"/intake/{it.id}/cards-map", data={"header_row": "1", "map_person": "0", "map_company": "0"}, follow_redirects=False)
    assert dup.status_code == 400 and "같은 열" in dup.text
    ok = user_client.post(f"/intake/{it.id}/cards-map", data={"header_row": "1", "map_person": "0", "map_company": "1", "map_mobile": "2", "map_email": "3"},
                          follow_redirects=False)
    assert ok.status_code == 303 and "notice=cards_read" in ok.headers["location"]
    db.expire_all()
    it = db.get(Intake, it.id)
    assert it.status == "ready" and [i["kind"] for i in it.plan["items"]] == ["new_customer"] and it.plan["items"][0]["card_count"] == 2
    again = user_client.post(f"/intake/{it.id}/cards-map", data={"header_row": "1", "map_person": "0", "map_company": "1"}, follow_redirects=False)
    assert "notice=not_ready" in again.headers["location"]  # 연타·다시 제출은 아무것도 바꾸지 않는다
    page = user_client.get(f"/intake/{it.id}").text
    assert "<b>이름</b> ← A열" in page and "<b>회사</b> ← B열" in page
    # 열이 틀렸으면 다시 고르기
    back = user_client.post(f"/intake/{it.id}/cards-remap", follow_redirects=False)
    assert back.status_code == 303
    db.expire_all()
    assert db.get(Intake, it.id).status == "uploaded"
    assert 'name="map_person"' in user_client.get(f"/intake/{it.id}").text


def test_detected_mapping_is_shown_above_the_review(user_client, db):
    _post_cards(user_client, "remember.xlsx", remember_real_headers_xlsx())
    it = db.query(Intake).one()
    r = user_client.get(f"/intake/{it.id}")
    for label, column in (("이름", "이름"), ("회사", "회사"), ("이메일", "전자 메일 주소"), ("주소", "근무지 주소 번지"), ("전화(유선)", "근무처 전화"), ("휴대폰", "휴대폰")):
        assert f"<b>{label}</b> ← {column}" in r.text
    assert "쓰지 않은 열: 명함 등록일, 명함첩 이름, 그룹" in r.text
    assert r.text.index("읽은 방법") < r.text.index('id="plan-cards"')
    assert "열 다시 고르기" in r.text


def test_vcf_upload_end_to_end(user_client, db):
    r = _post_cards(user_client, "export.vcf", VCF30)
    assert r.status_code == 303
    it = db.query(Intake).one()
    assert it.plan["format"] == "vcf" and it.plan["cards"] == 3 and [i["name"] for i in it.plan["items"]] == ["라마바시스템", "Acme Example", "다라마"]
    assert "vCard 파일이라" in user_client.get(f"/intake/{it.id}").text
    assert "열 다시 고르기" not in user_client.get(f"/intake/{it.id}").text


def test_upload_errors_leave_no_batch(user_client, db, settings):
    assert _post_cards(user_client, "x.txt", b"hello").status_code == 400
    assert "만 가져올 수" in _post_cards(user_client, "x.pdf", b"hello").text
    r = _post_cards(user_client, "x.csv", b"")
    assert r.status_code == 400
    r = _post_cards(user_client, "x.csv", "\n\n".encode())
    assert r.status_code == 400 and "내용이 없어요" in r.text
    r = _post_cards(user_client, "x.xlsx", b"junk")
    assert r.status_code == 400 and "엑셀" in r.text
    r = user_client.post("/intake/cards", data={}, follow_redirects=False)
    assert r.status_code == 400 and "파일을 골라" in r.text
    assert db.query(Intake).count() == 0
    assert not (settings.uploads_dir / "intake").exists() or not any((settings.uploads_dir / "intake").iterdir())
    two = user_client.post("/intake/cards", files=[("file", ("a.csv", b"x", "text/csv")), ("file", ("b.csv", b"y", "text/csv"))], follow_redirects=False)
    assert two.status_code == 400 and "하나만" in two.text


def test_row_limit_through_the_page(user_client, db):
    rows = [[f"사람{i}", f"회사{i}", "", "", "", "", ""] for i in range(cardfile.MAX_ROWS + 1)]
    r = _post_cards(user_client, "big.csv", csv_bytes(rows))
    assert r.status_code == 400 and "2,000장" in r.text and db.query(Intake).count() == 0


def test_size_limit(user_client, db, settings):
    tiny = create_app(dataclasses.replace(settings, max_upload_mb=0))
    try:
        c = TestClient(tiny)
        c.post("/login", data={"username": "kim", "password": "pw1234"})
        r = _post_cards(c, "cards.csv", csv_bytes(WORLD_ROWS))
        assert r.status_code == 400 and "크기 제한" in r.text
        db.expire_all()
        assert db.query(Intake).count() == 0
    finally:
        tiny.state.engine.dispose()


def test_card_batch_cannot_be_analyzed_by_ai(user_client, db):
    _post_cards(user_client, "cards.csv", csv_bytes(WORLD_ROWS))
    it = db.query(Intake).one()
    assert user_client.post(f"/intake/{it.id}/analyze", follow_redirects=False).status_code == 400
    assert intake_lib.start_analysis(db, it, db.query(__import__("app.models", fromlist=["User"]).User).first()) is False
    db.expire_all()
    assert db.get(Intake, it.id).status == "ready" and db.query(AIJob).count() == 0


def test_other_users_cannot_open_or_map_a_card_batch(user_client, db, app):
    from tests.conftest import _logged_in_client

    _post_cards(user_client, "cards.csv", csv_bytes(WORLD_ROWS))
    it = db.query(Intake).one()
    other = _logged_in_client(app, db, "lee", "user")
    assert other.get(f"/intake/{it.id}").status_code == 403
    assert other.post(f"/intake/{it.id}/cards-map", data={"map_person": "0", "map_company": "1"}, follow_redirects=False).status_code == 403
    assert other.post(f"/intake/{it.id}/cards-remap", follow_redirects=False).status_code == 403


def test_pages_and_links_and_paste_hint(user_client):
    page = user_client.get("/intake/cards")
    assert page.status_code == 200 and "명함 파일 가져오기" in page.text and 'accept=".vcf,.csv,.xlsx"' in page.text and "AI 를 쓰지 않아요" in page.text
    assert "/intake/cards" in user_client.get("/intake/new").text and "/intake/cards" in user_client.get("/intake").text
    assert "사진을 복사(Ctrl+C)한 뒤 여기에서 붙여넣기(Ctrl+V)해도 돼요." in user_client.get("/intake/new").text
    assert "사진을 복사(Ctrl+C)한 뒤 여기에서 붙여넣기(Ctrl+V)해도 돼요." in user_client.get("/").text  # 홈 카드
    assert user_client.get("/intake/cards", follow_redirects=False).status_code == 200


def test_existing_contact_listed_twice_is_not_added_again_at_apply(user_client, db):
    """화면에서 제외를 풀어도 저장할 때 기존 `CONTACT_EXISTS` 규칙이 한 번 더 건너뛴다."""
    c = Customer(name="한빛병원", contact="김가짜 010-0000-0001 kim@example.com")
    db.add(c)
    db.commit()
    _post_cards(user_client, "cards.csv", csv_bytes([["김가짜", "한빛병원", "", "", "010-0000-0001", "", "kim@example.com"],
                                                       ["이다른", "한빛병원", "", "", "010-0000-0007", "", ""]]))
    it = db.query(Intake).one()
    _, form = _form(user_client, it.id)
    form.pop("skip_ct1", None)
    user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    db.expire_all()
    assert db.get(Customer, c.id).contact.count("010-0000-0001") == 1 and "이다른" in db.get(Customer, c.id).contact
    assert any("이미 있는 연락처" in str(e.get("label")) for e in db.get(Intake, it.id).result)
