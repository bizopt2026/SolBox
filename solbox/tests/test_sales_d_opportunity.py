"""영업 D 단계: 영업기회에 담당자 잇기 (폼·상세·AI 분석 프롬프트)."""
from datetime import date

from sqlalchemy import select

from app import opportunity_ai
from app.models import Contact, Customer, Opportunity


def _world(db):
    a, b = Customer(name="한빛"), Customer(name="다른")
    db.add_all([a, b])
    db.commit()
    kim = Contact(name="김부장", title="구매팀 부장", role="decision", phone="010-1111-2222", email="kim@hanbit.kr", customer=a)
    lee = Contact(name="이대리", role="user", customer=a)
    other = Contact(name="박과장", customer=b)
    db.add_all([kim, lee, other])
    db.commit()
    return a, b, kim, lee, other


def test_form_lists_only_this_customers_contacts(user_client, db):
    a, b, kim, lee, other = _world(db)
    page = user_client.get(f"/opportunities/new?customer_id={a.id}").text
    picker = page.split('data-field="contact_ids"')[1].split("</fieldset>")[0]
    assert "김부장 · 구매팀 부장 · 의사결정자" in picker and "이대리 · 실무자" in picker and "박과장" not in picker
    assert 'name="contact_field" value="1"' in page


def test_create_links_contacts_and_ignores_other_customer(user_client, db):
    a, b, kim, lee, other = _world(db)
    r = user_client.post("/opportunities", data={"name": "ESL 도입", "customer_id": str(a.id), "contact_field": "1",
                                                "contact_ids": [str(kim.id), str(other.id)]}, follow_redirects=False)
    assert r.status_code == 303
    opp = db.scalar(select(Opportunity))
    assert [c.name for c in opp.contacts] == ["김부장"]  # 다른 고객의 담당자 번호는 잇지 않는다
    detail = user_client.get(f"/opportunities/{opp.id}").text
    assert f'<a href="/contacts/{kim.id}">김부장</a>(의사결정자)' in detail and "고객 담당자" in detail
    assert "ESL 도입" in user_client.get(f"/contacts/{kim.id}").text


def test_update_changes_and_clears_contacts(user_client, db):
    a, b, kim, lee, other = _world(db)
    opp = Opportunity(name="건", customer_id=a.id, stage="discovery")
    opp.contacts = [kim]
    db.add(opp)
    db.commit()
    page = user_client.get(f"/opportunities/{opp.id}/edit").text
    assert f'name="contact_ids" value="{kim.id}" checked' in page
    user_client.post(f"/opportunities/{opp.id}", data={"name": "건", "customer_id": str(a.id), "contact_field": "1", "contact_ids": [str(lee.id)]})
    db.refresh(opp)
    assert [c.name for c in opp.contacts] == ["이대리"]
    user_client.post(f"/opportunities/{opp.id}", data={"name": "건", "customer_id": str(a.id), "contact_field": "1"})
    db.refresh(opp)
    assert opp.contacts == []


def test_old_form_without_field_keeps_contacts_but_customer_change_drops_them(user_client, db):
    a, b, kim, lee, other = _world(db)
    opp = Opportunity(name="건", customer_id=a.id, stage="discovery")
    opp.contacts = [kim]
    db.add(opp)
    db.commit()
    user_client.post(f"/opportunities/{opp.id}", data={"name": "건 2", "customer_id": str(a.id)})
    db.refresh(opp)
    assert [c.name for c in opp.contacts] == ["김부장"]
    user_client.post(f"/opportunities/{opp.id}", data={"name": "건 2", "customer_id": str(b.id)})
    db.refresh(opp)
    assert opp.customer_id == b.id and opp.contacts == []


def test_ai_prompt_has_names_titles_roles_but_no_phone_or_email(db):
    a, b, kim, lee, other = _world(db)
    opp = Opportunity(name="건", customer_id=a.id, stage="discovery")
    opp.contacts = [kim, lee]
    db.add(opp)
    db.commit()
    prompt, _ = opportunity_ai.build_prompt(db, opp, date.today())
    assert "고객 담당자: 김부장 구매팀 부장 의사결정자 · 이대리 실무자" in prompt
    assert "010-1111-2222" not in prompt and "kim@hanbit.kr" not in prompt


def test_deleting_contact_unlinks_opportunity(user_client, db):
    a, b, kim, lee, other = _world(db)
    opp = Opportunity(name="건", customer_id=a.id, stage="discovery")
    opp.contacts = [kim]
    db.add(opp)
    db.commit()
    db.delete(kim)
    db.commit()
    db.refresh(opp)
    assert opp.contacts == [] and db.get(Opportunity, opp.id) is not None
