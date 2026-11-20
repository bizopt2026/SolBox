"""파트너 ↔ 고객 "같은 회사" 연결, 올린 파일 대상 정리."""
import re

from app import ontology
from app.ai.intake_tasks import IntakePlanOut
from app.intake import clean_plan
from app.knowledge import KNOWLEDGE_FORMAT
from app.models import Customer, IntakeFile, Partner, Solution
from tests.conftest import ready_bundle


def _pair(db, pname="루미텍", cname="한빛병원"):
    p, c = Partner(name=pname), Customer(name=cname)
    db.add_all([p, c])
    db.commit()
    return p, c


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ---- 정의 ----

def test_same_company_relation_defined():
    r = {x.key: x for x in ontology.RELATIONS}["R25"]
    assert (r.label, r.source, r.target, r.cardinality, r.storage, r.physical) == (
        "같은 회사다", ("partner",), ("customer",), "1:1", "fk", "partners.customer_id")
    assert ontology.prop("partner", "customer") == "같은 회사인 고객"
    assert ontology.prop("customer", "partner") == "같은 회사인 파트너"
    assert KNOWLEDGE_FORMAT >= 4


# ---- 양쪽에서 연결 ----

def test_link_from_partner_form_and_show_on_both_pages(user_client, db):
    p, c = _pair(db)
    page = user_client.get(f"/partners/{p.id}/edit").text
    assert 'name="customer_id"' in page and "같은 회사인 고객" in page
    r = user_client.post(f"/partners/{p.id}", data={"name": "루미텍", "customer_id": str(c.id)}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(p)
    assert p.customer_id == c.id
    assert "같은 회사 고객" in _text(user_client.get(f"/partners/{p.id}").text)
    assert f'href="/customers/{c.id}"' in user_client.get(f"/partners/{p.id}").text
    cp = user_client.get(f"/customers/{c.id}").text
    assert "같은 회사 파트너" in _text(cp) and f'href="/partners/{p.id}"' in cp
    assert f'<option value="{p.id}" selected' in user_client.get(f"/customers/{c.id}/edit").text


def test_link_from_customer_form_and_clear_from_either_side(user_client, db):
    p, c = _pair(db)
    assert user_client.post(f"/customers/{c.id}", data={"name": "한빛병원", "partner_id": str(p.id)}, follow_redirects=False).status_code == 303
    db.refresh(p)
    assert p.customer_id == c.id
    user_client.post(f"/customers/{c.id}", data={"name": "한빛병원", "partner_id": ""})  # 비우면 연결이 풀린다
    db.refresh(p)
    assert p.customer_id is None
    user_client.post(f"/partners/{p.id}", data={"name": "루미텍", "customer_id": str(c.id)})
    user_client.post(f"/partners/{p.id}", data={"name": "루미텍", "customer_id": ""})
    db.refresh(p)
    assert p.customer_id is None


def test_create_partner_with_customer_selected(user_client, db):
    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    assert user_client.post("/partners", data={"name": "새 파트너", "customer_id": str(c.id)}, follow_redirects=False).status_code == 303
    assert db.query(Partner).one().customer_id == c.id


def test_counterpart_already_linked_elsewhere_is_rejected_naming_the_link(user_client, db):
    p1, c1 = _pair(db)
    p2, c2 = Partner(name="다른 파트너"), Customer(name="다른 고객")
    db.add_all([p2, c2])
    db.commit()
    p1.customer_id = c1.id
    db.commit()
    r = user_client.post(f"/partners/{p2.id}", data={"name": "다른 파트너", "customer_id": str(c1.id)})
    assert r.status_code == 400 and "이미 연결되어 있습니다" in r.text and "루미텍" in r.text and "한빛병원" in r.text
    r = user_client.post(f"/customers/{c2.id}", data={"name": "다른 고객", "partner_id": str(p1.id)})
    assert r.status_code == 400 and "루미텍" in r.text and "한빛병원" in r.text
    db.expire_all()
    assert p1.customer_id == c1.id and p2.customer_id is None
    # 저장이 거절되면 다른 칸도 저장되지 않는다
    assert db.get(Partner, p2.id).name == "다른 파트너"
    r = user_client.post(f"/partners/{p2.id}", data={"name": "이름 바꿈", "customer_id": str(c1.id)})
    db.expire_all()
    assert r.status_code == 400 and db.get(Partner, p2.id).name == "다른 파트너"


def test_unknown_counterpart_is_rejected(user_client, db):
    p, _ = _pair(db)
    r = user_client.post(f"/partners/{p.id}", data={"name": "루미텍", "customer_id": "9999"})
    assert r.status_code == 400 and "고객" in r.text
    r = user_client.post(f"/customers/1", data={"name": "x", "partner_id": "abc"})
    assert r.status_code in (400, 404)


# ---- 이름이 같을 때 안내 ----

def test_same_name_shows_hint_without_auto_link_and_one_click_links(user_client, db):
    p, c = _pair(db, "한빛 병원", "한빛병원")
    page = user_client.get(f"/partners/{p.id}").text
    assert "이름이 같은 고객 '한빛병원'이 있습니다" in _text(page) and f'action="/partners/{p.id}/same-company"' in page
    assert "이름이 같은 파트너 '한빛 병원'이 있습니다" in _text(user_client.get(f"/customers/{c.id}").text)
    db.refresh(p)
    assert p.customer_id is None  # 자동 연결 없음
    r = user_client.post(f"/partners/{p.id}/same-company", data={"customer_id": str(c.id)}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(p)
    assert p.customer_id == c.id
    assert "이름이 같은" not in user_client.get(f"/partners/{p.id}").text


def test_customer_side_one_click_link(user_client, db):
    p, c = _pair(db, "한빛병원", "한빛병원")
    assert user_client.post(f"/customers/{c.id}/same-company", data={"partner_id": str(p.id)}, follow_redirects=False).status_code == 303
    db.refresh(p)
    assert p.customer_id == c.id


def test_no_hint_when_ambiguous_linked_or_different(user_client, db):
    p, c = _pair(db, "한빛병원", "한빛병원")
    c2 = Customer(name="한빛 병원")
    db.add(c2)
    db.commit()
    assert "이름이 같은" not in user_client.get(f"/partners/{p.id}").text  # 같은 이름 고객이 둘
    c2.name = "다른 고객"
    other = Partner(name="한빛병원", customer_id=c.id)  # 고객이 이미 다른 파트너와 연결
    db.add(other)
    db.commit()
    assert "이름이 같은" not in user_client.get(f"/partners/{p.id}").text
    assert "이름이 같은" not in user_client.get(f"/customers/{c.id}").text
    r = user_client.post(f"/partners/{p.id}/same-company", data={"customer_id": str(c.id)})
    assert r.status_code == 400 and "이미 연결되어 있습니다" in r.text


def test_partner_linked_elsewhere_cannot_take_second_link_by_hint_route(user_client, db):
    p, c = _pair(db)
    c2 = Customer(name="두번째")
    db.add(c2)
    db.commit()
    p.customer_id = c.id
    db.commit()
    r = user_client.post(f"/partners/{p.id}/same-company", data={"customer_id": str(c2.id)})
    assert r.status_code == 400 and "이미 연결되어 있습니다" in r.text
    db.refresh(p)
    assert p.customer_id == c.id


def test_unique_index_rejects_two_partners_for_one_customer(app, db):
    import pytest
    from sqlalchemy.exc import IntegrityError

    p, c = _pair(db)
    p.customer_id = c.id
    db.add(Partner(name="두번째", customer_id=c.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---- 지우기 ----

def test_deleting_customer_clears_partner_link_and_partner_deletion_clears_customer_view(user_client, admin_client, db, settings):
    p, c = _pair(db)
    p.customer_id = c.id
    db.commit()
    assert admin_client.post(f"/customers/{c.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Partner, p.id) is not None and db.get(Partner, p.id).customer_id is None
    assert "같은 회사" not in _text(user_client.get(f"/partners/{p.id}").text).replace("같은 회사로 연결", "")
    assert "한빛병원" not in (settings.knowledge_dir / "partners" / f"{p.id}.md").read_text(encoding="utf-8")
    c2 = Customer(name="다시")
    db.add(c2)
    db.commit()
    p.customer_id = c2.id
    db.commit()
    assert admin_client.post(f"/partners/{p.id}/delete", follow_redirects=False).status_code == 303
    assert "같은 회사" not in _text(user_client.get(f"/customers/{c2.id}").text)


def test_customer_delete_rules_unchanged(admin_client, db):
    from app.models import Project

    p, c = _pair(db)
    p.customer_id = c.id
    db.add(Project(name="프로젝트", customer_id=c.id))
    db.commit()
    r = admin_client.post(f"/customers/{c.id}/delete")
    assert r.status_code == 400 and "프로젝트" in r.text
    db.refresh(p)
    assert p.customer_id == c.id


# ---- 지식 사본 ----

def test_mirror_mentions_counterpart_with_path(user_client, db, settings):
    p, c = _pair(db)
    user_client.post(f"/partners/{p.id}", data={"name": "루미텍", "customer_id": str(c.id)})
    pm = (settings.knowledge_dir / "partners" / f"{p.id}.md").read_text(encoding="utf-8")
    cm = (settings.knowledge_dir / "customers" / f"{c.id}.md").read_text(encoding="utf-8")
    assert f"- 같은 회사: 고객 한빛병원 (customers/{c.id}.md)" in pm
    assert f"- 같은 회사: 파트너 루미텍 (partners/{p.id}.md)" in cm
    user_client.post(f"/customers/{c.id}", data={"name": "한빛병원", "partner_id": ""})  # 풀면 양쪽 사본이 갱신된다
    assert "같은 회사" not in (settings.knowledge_dir / "partners" / f"{p.id}.md").read_text(encoding="utf-8")
    assert "같은 회사" not in (settings.knowledge_dir / "customers" / f"{c.id}.md").read_text(encoding="utf-8")


def test_rename_resyncs_counterpart_mirror(user_client, db, settings):
    p, c = _pair(db)
    user_client.post(f"/partners/{p.id}", data={"name": "루미텍", "customer_id": str(c.id)})
    user_client.post(f"/customers/{c.id}", data={"name": "한빛의료원", "partner_id": str(p.id)})
    assert "한빛의료원" in (settings.knowledge_dir / "partners" / f"{p.id}.md").read_text(encoding="utf-8")


# ---- 올리기: 자동 연결 없음, 이름 맞추기 ----

def test_customer_only_company_as_new_partner_is_still_created_as_partner(db, settings, user_client):
    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    it = ready_bundle(db, settings, [("a.txt", b"x")], partners=[{"name": "한빛 병원", "files": [1], "reason": "명함", "contact": "02-123-4567"}])
    kinds = [i["kind"] for i in it.plan["items"]]
    assert "new_partner" in kinds and "add_contact" not in kinds


def test_intake_created_partner_is_not_auto_linked(app, user_client, db, settings):
    from tests.conftest import form_from_page

    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    it = ready_bundle(db, settings, [("a.txt", b"x")], partners=[{"name": "한빛병원", "files": [1], "reason": "r"}])
    assert user_client.post(f"/intake/{it.id}/apply", data=form_from_page(user_client, it), follow_redirects=False).status_code == 303
    p = db.query(Partner).one()
    assert p.customer_id is None and db.query(Customer).count() == 1
    assert "이름이 같은 고객 '한빛병원'이 있습니다" in _text(user_client.get(f"/partners/{p.id}").text)


# ---- 올린 파일 대상 정리 (R21) ----

def _files(db, settings, kind, target_id):
    it = ready_bundle(db, settings, [("a.txt", b"x"), ("b.txt", b"y")], solutions=[])
    a, b = it.files
    a.target_type, a.target_id = kind, target_id
    b.target_type, b.target_id = kind, target_id + 100  # 다른 대상을 가리키는 파일은 그대로
    db.commit()
    return a, b


def test_deleting_solution_partner_customer_clears_file_targets(user_client, admin_client, db, settings):
    s, (p, c) = Solution(name="솔"), _pair(db)
    db.add(s)
    db.commit()
    for kind, obj, url in (("solution", s, "solutions"), ("partner", p, "partners"), ("customer", c, "customers")):
        oid = obj.id
        a, b = _files(db, settings, kind, oid)
        assert admin_client.post(f"/{url}/{oid}/delete", follow_redirects=False).status_code == 303
        db.expire_all()
        a, b = db.get(IntakeFile, a.id), db.get(IntakeFile, b.id)
        assert (a.target_type, a.target_id) == ("", None), kind
        assert (b.target_type, b.target_id) == (kind, oid + 100), kind


def test_deleting_other_kind_does_not_touch_files(user_client, admin_client, db, settings):
    p, c = _pair(db)
    cid = c.id
    a, _ = _files(db, settings, "partner", cid)  # 번호가 같아도 종류가 다르면 건드리지 않는다
    assert admin_client.post(f"/customers/{cid}/delete", follow_redirects=False).status_code == 303
    db.refresh(a)
    assert (a.target_type, a.target_id) == ("partner", cid)
