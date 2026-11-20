from sqlalchemy import inspect

from app.models import AIJob, Project, Customer, Proposal, ProposalMessage, Solution, User
from app.proposals import candidate_solutions, display_title

# HEAD(ba8ab7c) 모델에서 읽은 기존 테이블의 칼럼 이름.
EXISTING_COLUMNS = {
    "activities": ['created_at', 'created_by_id', 'date', 'demand_signals', 'follow_ups', 'id', 'kind', 'origin_id', 'origin_type', 'raw_text', 'summary_md', 'title', 'updated_at'],
    "activity_businesses": ['activity_id', 'business_id'],
    "activity_customers": ['activity_id', 'customer_id'],
    "activity_partners": ['activity_id', 'partner_id'],
    "activity_solutions": ['activity_id', 'solution_id'],
    "ai_jobs": ['created_at', 'error', 'error_kind', 'finished_at', 'id', 'input', 'kind', 'priority', 'requester_id', 'result', 'started_at', 'status'],
    "businesses": ['bm_id', 'created_at', 'created_by_id', 'customer_id', 'description', 'id', 'memo', 'name', 'period', 'status', 'updated_at'],
    "customers": ['contact', 'created_at', 'created_by_id', 'id', 'industry', 'memo', 'name', 'origin_id', 'origin_type', 'updated_at'],
    "partners": ['contact', 'created_at', 'created_by_id', 'customer_id', 'description', 'id', 'memo', 'name', 'origin_id', 'origin_type', 'updated_at'],
    "solution_files": ['created_at', 'created_by_id', 'extract_status', 'id', 'kind', 'original_name', 'size', 'solution_id', 'stored_name', 'updated_at'],
    "solutions": ['applicable_types', 'created_at', 'created_by_id', 'description', 'id', 'name', 'origin_id', 'origin_item', 'origin_type', 'partner_id', 'stage', 'summary', 'tags', 'updated_at', 'usage_direction'],
    "users": ['created_at', 'id', 'is_active', 'name', 'password_hash', 'role', 'username'],
}


def _customer_project(db, cname="한빛병원", bname="설비 유지보수"):
    c = Customer(name=cname)
    db.add(c)
    db.flush()
    b = Project(name=bname, customer_id=c.id)
    db.add(b)
    db.commit()
    return c, b


def test_create_proposal(user_client, db):
    c, b = _customer_project(db)
    r = user_client.post("/proposals", data={"customer_id": c.id, "project_id": b.id, "request_memo": "에너지 절감 쪽으로"},
                         follow_redirects=False)
    p = db.query(Proposal).one()
    assert r.headers["location"] == f"/proposals/{p.id}?notice=created"
    assert (p.status, p.directions, p.selected_index, p.outline, p.extra_solution_ids) == ("new", [], None, None, [])
    assert p.request_memo == "에너지 절감 쪽으로"
    assert p.created_by_id is not None
    assert display_title(p) == "한빛병원 · 설비 유지보수 제안"
    page = user_client.get(f"/proposals/{p.id}")
    assert page.status_code == 200
    assert "한빛병원" in page.text and "에너지 절감 쪽으로" in page.text
    for section in ("directions", "chat", "outline", "deck"):
        assert f'id="{section}"' in page.text


def test_display_title_variants(db):
    c = Customer(name="고객")
    db.add(c)
    db.flush()
    assert display_title(Proposal(customer=c)) == "고객 제안"
    assert display_title(Proposal(customer=c, title="내 제목")) == "내 제목"


def test_business_must_belong_to_customer(user_client, db):
    c, b = _customer_project(db)
    other = Customer(name="다른고객")
    db.add(other)
    db.commit()
    r = user_client.post("/proposals", data={"customer_id": other.id, "project_id": b.id})
    assert r.status_code == 400
    assert "선택한 고객의 프로젝트가 아닙니다." in r.text
    assert db.query(Proposal).count() == 0


def test_customer_required(user_client, db):
    r = user_client.post("/proposals", data={"customer_id": "", "request_memo": "x"})
    assert r.status_code == 400
    assert "고객을 선택하세요." in r.text
    assert user_client.post("/proposals", data={"customer_id": "999"}).status_code == 400
    assert db.query(Proposal).count() == 0


def test_new_form_prefills_and_lists_only_hold_for_manual_add(user_client, db):
    c, b = _customer_project(db)
    db.add_all([Solution(name="보류솔루션", stage="hold"), Solution(name="조사솔루션", stage="survey"), Solution(name="박스솔루션", stage="boxed")])
    db.commit()
    r = user_client.get(f"/proposals/new?customer_id={c.id}&project_id={b.id}")
    assert r.status_code == 200
    assert "한빛병원 – 설비 유지보수" in r.text
    assert "보류솔루션" in r.text and "조사솔루션" not in r.text and "박스솔루션" not in r.text  # 보류가 아니면 이미 후보다
    assert "보류 단계 솔루션도 직접 추가하기" in r.text and "박스 등록이 아닌" not in r.text


def test_create_with_extra_solutions(user_client, db):
    c, _ = _customer_project(db)
    s = Solution(name="조사솔루션", stage="survey")
    db.add(s)
    db.commit()
    user_client.post("/proposals", data={"customer_id": c.id, "extra_solution_ids": [s.id, 9999]})
    p = db.query(Proposal).one()
    assert p.extra_solution_ids == [s.id]
    assert p.solutions == []  # 방향을 고를 때 채워진다


def test_candidate_solutions_all_but_hold_plus_extra(db):
    """결정 Q7: 보류를 뺀 모든 단계 + 제안 건에 직접 추가한 솔루션 (보류도 직접 추가하면 후보)."""
    a = Solution(name="가", stage="boxed"); b = Solution(name="나", stage="survey"); c = Solution(name="다", stage="hold")
    d = Solution(name="라", stage="intake"); e = Solution(name="마", stage="hold")
    cust = Customer(name="고객"); db.add_all([a, b, c, d, e, cust]); db.flush()
    p = Proposal(customer_id=cust.id); db.add(p); db.commit()
    assert [s.name for s in candidate_solutions(db, p)] == ["가", "나", "라"]
    p.extra_solution_ids = [c.id, a.id]; db.commit()
    assert [s.name for s in candidate_solutions(db, p)] == ["가", "나", "다", "라"]


def test_update_target(user_client, db):
    c, _ = _customer_project(db)
    s = Solution(name="조사솔루션", stage="hold")
    db.add(s)
    db.commit()
    p = Proposal(customer_id=c.id, request_memo="옛")
    db.add(p)
    db.commit()
    r = user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "새 메모", "extra_solution_ids": [s.id]},
                         follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/proposals/{p.id}?notice=target_saved"
    db.refresh(p)
    assert p.request_memo == "새 메모" and p.extra_solution_ids == [s.id]
    assert [x.id for x in candidate_solutions(db, p)] == [s.id]
    user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "비움"})
    db.refresh(p)
    assert p.extra_solution_ids == []


def test_update_target_keeps_direction_solutions_and_boxed_extras(user_client, db):
    c, _ = _customer_project(db)
    chosen = Solution(name="방향솔루션", stage="boxed")
    was_extra = Solution(name="이제박스", stage="boxed")
    db.add_all([chosen, was_extra]); db.flush()
    p = Proposal(customer_id=c.id, solutions=[chosen], extra_solution_ids=[was_extra.id])
    db.add(p); db.commit()
    user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "수정"})
    db.refresh(p)
    assert [s.id for s in p.solutions] == [chosen.id]
    assert p.extra_solution_ids == [was_extra.id]


def test_non_ascii_digit_ids_do_not_crash(user_client, db):
    c, _ = _customer_project(db)
    assert user_client.post("/proposals", data={"customer_id": "²"}).status_code == 400
    assert user_client.post("/proposals", data={"customer_id": str(c.id), "project_id": "²"}).status_code == 400
    r = user_client.post("/proposals", data={"customer_id": str(c.id), "extra_solution_ids": ["²", "1" * 30]},
                         follow_redirects=False)
    assert r.status_code == 303
    assert db.query(Proposal).one().extra_solution_ids == []


def test_busy_notice(user_client, db):
    c, _ = _customer_project(db)
    p = Proposal(customer_id=c.id)
    db.add(p)
    db.commit()
    assert "이미 AI 작업이 진행 중입니다." in user_client.get(f"/proposals/{p.id}?notice=busy").text
    assert "이미 AI 작업이 진행 중입니다." not in user_client.get(f"/proposals/{p.id}").text


def test_active_job_relationship(db):
    c = Customer(name="고객")
    db.add(c)
    db.flush()
    job = AIJob(kind="x")
    db.add(job)
    db.flush()
    p = Proposal(customer_id=c.id, active_job_id=job.id)
    db.add(p)
    db.commit()
    assert p.active_job.id == job.id


def test_any_user_can_open_but_only_owner_or_admin_deletes(user_client, admin_client, app, db, settings):
    c, _ = _customer_project(db)
    owner = db.query(User).filter_by(username="kim").one()
    other = User(username="lee", name="lee", password_hash="x")
    db.add(other)
    db.commit()
    p = Proposal(customer_id=c.id, created_by_id=other.id)
    p.messages.append(ProposalMessage(role="user", content="안녕"))
    db.add(p)
    db.commit()
    pid = p.id
    deck_dir = settings.decks_dir / str(pid)
    deck_dir.mkdir(parents=True)
    (deck_dir / "a.pptx").write_bytes(b"x")
    assert user_client.get(f"/proposals/{pid}").status_code == 200
    assert user_client.post(f"/proposals/{pid}/delete").status_code == 403
    assert db.get(Proposal, pid) is not None and deck_dir.exists()
    r = admin_client.post(f"/proposals/{pid}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/proposals?notice=deleted"
    db.expire_all()
    assert db.get(Proposal, pid) is None
    assert db.query(ProposalMessage).count() == 0
    assert not deck_dir.exists()
    # 본인 건은 본인이 지운다
    mine = Proposal(customer_id=c.id, created_by_id=owner.id)
    db.add(mine)
    db.commit()
    assert user_client.post(f"/proposals/{mine.id}/delete", follow_redirects=False).status_code == 303
    assert user_client.get(f"/proposals/{mine.id}").status_code == 404


def test_list_filters(user_client, db):
    c1 = Customer(name="한빛병원"); c2 = Customer(name="푸른학교")
    db.add_all([c1, c2]); db.flush()
    db.add_all([
        Proposal(customer_id=c1.id, status="refining", title="가제안"),
        Proposal(customer_id=c2.id, status="new", title="나제안"),
    ])
    db.commit()
    both = user_client.get("/proposals").text
    assert "가제안" in both and "나제안" in both
    r = user_client.get("/proposals?status=refining").text
    assert "가제안" in r and "나제안" not in r
    r = user_client.get("/proposals?q=한빛").text
    assert "가제안" in r and "나제안" not in r
    r = user_client.get("/proposals?q=나제").text
    assert "나제안" in r and "가제안" not in r


def test_list_orders_by_recent_update(user_client, db):
    from datetime import datetime
    c = Customer(name="고객")
    db.add(c); db.flush()
    db.add_all([
        Proposal(customer_id=c.id, title="오래된건", updated_at=datetime(2026, 1, 1)),
        Proposal(customer_id=c.id, title="최근건", updated_at=datetime(2026, 6, 1)),
    ])
    db.commit()
    t = user_client.get("/proposals").text
    assert t.index("최근건") < t.index("오래된건")


def test_customer_detail_lists_proposals_and_new_link(user_client, db):
    c, b = _customer_project(db)
    db.add(Proposal(customer_id=c.id, project_id=b.id, title="연결된제안"))
    db.commit()
    t = user_client.get(f"/customers/{c.id}").text
    assert "연결된제안" in t and f"/proposals/new?customer_id={c.id}" in t
    t = user_client.get(f"/projects/{b.id}").text
    assert "연결된제안" in t and f"/proposals/new?customer_id={c.id}&amp;project_id={b.id}" in t


def test_solution_detail_lists_proposals(user_client, db):
    c, _ = _customer_project(db)
    s = Solution(name="솔루션")
    db.add(s); db.flush()
    p = Proposal(customer_id=c.id, title="들어간제안", solutions=[s])
    db.add(p); db.commit()
    assert "들어간제안" in user_client.get(f"/solutions/{s.id}").text


def test_customer_with_proposals_cannot_be_deleted(user_client, db):
    c = Customer(name="고객", created_by_id=db.query(User).filter_by(username="kim").one().id)
    db.add(c); db.flush()
    db.add(Proposal(customer_id=c.id)); db.commit()
    r = user_client.post(f"/customers/{c.id}/delete")
    assert r.status_code == 400
    assert "제안 건이 있는 고객은 삭제할 수 없습니다." in r.text
    assert db.get(Customer, c.id) is not None


def test_deleting_business_clears_proposal_business(user_client, db):
    c, b = _customer_project(db)
    b.created_by_id = db.query(User).filter_by(username="kim").one().id
    p = Proposal(customer_id=c.id, project_id=b.id)
    db.add(p); db.commit()
    assert user_client.post(f"/projects/{b.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Proposal, p.id).project_id is None


def test_deleting_solution_keeps_proposal(user_client, db):
    c, _ = _customer_project(db)
    s = Solution(name="솔루션", created_by_id=db.query(User).filter_by(username="kim").one().id)
    db.add(s); db.flush()
    p = Proposal(customer_id=c.id, solutions=[s], extra_solution_ids=[s.id])
    db.add(p); db.commit()
    assert user_client.post(f"/solutions/{s.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Proposal, p.id).solutions == []


def test_nav_has_proposals(user_client):
    assert 'href="/proposals"' in user_client.get("/").text


def test_new_tables_do_not_alter_existing(app):
    insp = inspect(app.state.engine)
    for table, cols in EXISTING_COLUMNS.items():
        assert sorted(c["name"] for c in insp.get_columns(table)) == sorted(cols), table
    assert {"proposals", "proposal_messages", "proposal_solutions"} <= set(insp.get_table_names())
