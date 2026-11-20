"""UX 개선 2: 지우기 확인 단계 (서버가 그린 GET 확인 화면 + 스크립트가 있으면 그 자리 되묻기용 링크)."""
import re
from pathlib import Path

import pytest

from app.models import (
    BM, Activity, Customer, Intake, Opportunity, Partner, Project, Proposal, ResearchRun, ResearchTopic, Solution, SolutionFile, User,
)
from tests.conftest import make_user, route_table


def test_every_delete_or_discard_post_has_a_confirm_page(app):
    routes = route_table(app)
    posts = [p for p, m in routes if "POST" in m and (p.endswith("/delete") or p.endswith("/discard")) and not p.startswith("/businesses")]
    assert len(posts) >= 12
    norm = lambda p: re.sub(r"\{[^}]+\}", "{}", p)  # noqa: E731  (경로 변수 이름은 달라도 같은 주소)
    gets = {norm(p) for p, m in routes if "GET" in m}
    assert [p for p in posts if norm(p) not in gets] == []


def test_templates_do_not_use_window_confirm():
    for path in Path("app/templates").rglob("*.html"):
        assert "confirm(" not in path.read_text(encoding="utf-8"), path
    assert "window.confirm" not in Path("app/static/app.js").read_text(encoding="utf-8")


def test_confirm_page_names_the_object_and_posts_to_the_same_address(user_client, db):
    kim = db.query(User).filter_by(username="kim").one()
    c = Customer(name="한빛병원", created_by_id=kim.id)
    db.add(c)
    db.commit()
    cid = c.id
    page = user_client.get(f"/customers/{cid}/delete").text
    assert "한빛병원" in page and "삭제할까요?" in page
    assert re.search(r'<form method="post" action="/customers/%d/delete"' % cid, page)
    assert "같은 회사로 이은 파트너 연결이 풀립니다" in page and f'href="/customers/{cid}"' in page  # 지울 때의 규칙과 취소 링크
    # 확인 화면을 열어 본다고 지워지지 않는다
    assert db.get(Customer, cid) is not None
    assert user_client.post(f"/customers/{cid}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Customer, cid) is None


def test_blocked_delete_shows_the_reason_without_a_delete_button(user_client, db):
    kim = db.query(User).filter_by(username="kim").one()
    c = Customer(name="고객", created_by_id=kim.id)
    db.add(c)
    db.flush()
    db.add(Project(name="프로젝트", customer_id=c.id))
    db.commit()
    page = user_client.get(f"/customers/{c.id}/delete").text
    assert "프로젝트가 있어 삭제할 수 없습니다" in page and "<form method=\"post\" action=\"/customers" not in page


def test_confirm_respects_permissions_and_missing_objects(app, user_client, db):
    other = make_user(db, "lee")
    c = Customer(name="남의 고객", created_by_id=other.id)
    db.add(c)
    db.commit()
    assert user_client.get(f"/customers/{c.id}/delete").status_code == 403
    assert user_client.get("/customers/9999/delete").status_code == 404
    from fastapi.testclient import TestClient

    assert TestClient(app).get(f"/customers/{c.id}/delete", follow_redirects=False).status_code == 303  # 로그인 필요


def test_admin_only_confirms(user_client, admin_client, db):
    t = ResearchTopic(name="주제", keywords="k", area="a")
    run = ResearchRun(trigger="manual", status="done")
    db.add_all([t, run])
    db.commit()
    assert user_client.get(f"/admin/research/topics/{t.id}/delete").status_code == 403
    assert user_client.get(f"/research/runs/{run.id}/delete").status_code == 403
    page = admin_client.get(f"/admin/research/topics/{t.id}/delete").text
    assert "주제" in page and "다음 조사부터 빠집니다" in page
    assert "보고서" in admin_client.get(f"/research/runs/{run.id}/delete").text
    run.status = "running"
    db.commit()
    assert "진행 중인 조사는 삭제할 수 없습니다" in admin_client.get(f"/research/runs/{run.id}/delete").text


def test_other_object_confirms(user_client, db):
    kim = db.query(User).filter_by(username="kim").one()
    s = Solution(name="솔루션", created_by_id=kim.id)
    db.add(s)
    db.flush()
    f = SolutionFile(solution_id=s.id, original_name="소개서.pdf", stored_name="a.pdf", kind="intro", created_by_id=kim.id)
    it = Intake(note="n", status="ready", created_by_id=kim.id)
    p = Partner(name="파트너", created_by_id=kim.id)
    a = Activity(kind="meeting", title="회의", raw_text="r", created_by_id=kim.id)
    bm = BM(name="모델", created_by_id=kim.id)
    c = Customer(name="고객", created_by_id=kim.id)
    db.add_all([f, it, p, a, bm, c])
    db.flush()
    o = Opportunity(name="기회", customer_id=c.id, created_by_id=kim.id)
    pr = Proposal(customer_id=c.id, created_by_id=kim.id, outline={"slides": [{"kind": "cover", "title": "표지"}, {"kind": "bullets", "title": "범위"}]})
    db.add_all([o, pr])
    db.commit()
    assert "소개서.pdf" in user_client.get(f"/files/{f.id}/delete").text
    page = user_client.get(f"/intake/{it.id}/discard").text
    assert "버릴까요?" in page and "올린 원본 파일은 남습니다" in page and f'action="/intake/{it.id}/discard"' in page
    assert "회의" in user_client.get(f"/activities/{a.id}/delete").text
    assert "파트너" in user_client.get(f"/partners/{p.id}/delete").text
    assert "모델" in user_client.get(f"/bms/{bm.id}/delete").text
    assert "묶은 니즈는 풀리고" in user_client.get(f"/opportunities/{o.id}/delete").text
    assert "만든 제안서 파일이 함께 지워집니다" in user_client.get(f"/proposals/{pr.id}/delete").text
    assert "범위" in user_client.get(f"/proposals/{pr.id}/outline/slides/1/delete").text
    assert "표지는 지울 수 없습니다" in user_client.get(f"/proposals/{pr.id}/outline/slides/0/delete").text
    assert user_client.get(f"/proposals/{pr.id}/outline/slides/9/delete").status_code == 404


def test_delete_buttons_are_links_to_the_confirm_page(user_client, db):
    kim = db.query(User).filter_by(username="kim").one()
    c = Customer(name="고객", created_by_id=kim.id)
    db.add(c)
    db.commit()
    page = user_client.get(f"/customers/{c.id}").text
    assert re.search(r'<a class="button ghost danger" href="/customers/%d/delete" data-confirm-post="/customers/%d/delete" data-ask="정말 삭제할까요\?" data-ok="삭제">삭제</a>' % (c.id, c.id), page)
    assert "onsubmit" not in page.split('<div class="dgrid">')[0]
