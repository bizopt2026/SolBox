"""영업 D 단계: 전체 검색·지식 사본에 담당자."""
from app import knowledge, search
from app.models import Contact, Customer, Opportunity, Partner


def _world(db):
    c, p = Customer(name="한빛"), Partner(name="그린")
    db.add_all([c, p])
    db.commit()
    kim = Contact(name="김민수", title="구매팀 부장", role="decision", phone="010-1234-5678", email="minsu@hanbit.kr", note="골프 좋아함", customer=c)
    park = Contact(name="박기사", partner=p, office_phone="02-777-8888")
    db.add_all([kim, park])
    o = Opportunity(name="ESL", customer=c, stage="discovery")
    db.add(o)
    db.commit()
    o.contacts = [kim]
    db.commit()
    return c, p, kim, park, o


def test_search_finds_contacts_by_name_email_phone_and_note(user_client, db):
    c, p, kim, park, o = _world(db)
    for q in ("김민수", "minsu@", "1234-5678", "골프"):
        res = search.search(db, q)
        group = next(g for g in res["groups"] if g["code"] == "contact")
        row = group["rows"][0]
        assert row["label"] == "김민수" and row["url"] == f"/contacts/{kim.id}" and "고객 한빛" in row["sub"] and row["badge"] == ("contact_role", "decision")
    group = next(g for g in search.search(db, "777-8888")["groups"] if g["code"] == "contact")
    assert group["rows"][0]["sub"] == "파트너 그린" and group["more_url"] is None
    page = user_client.get("/search?q=김민수").text
    assert f'href="/contacts/{kim.id}"' in page


def test_company_and_opportunity_copies_list_contacts(app, db):
    c, p, kim, park, o = _world(db)
    mirror = app.state.mirror
    for obj in (c, p, o):
        mirror.sync(obj)
    text = mirror.path_for(c).read_text(encoding="utf-8")
    assert "## 담당자\n\n- 김민수 · 구매팀 부장 · 의사결정자 · 010-1234-5678 · minsu@hanbit.kr" in text and "골프" not in text
    assert "## 담당자\n\n- 박기사" in mirror.path_for(p).read_text(encoding="utf-8")
    assert "- 고객 담당자: 김민수 구매팀 부장 의사결정자" in mirror.path_for(o).read_text(encoding="utf-8")
    assert knowledge.KNOWLEDGE_FORMAT == 15
