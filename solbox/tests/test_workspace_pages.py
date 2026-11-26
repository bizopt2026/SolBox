"""AI 코워크 ①: 사업 방향·과제 화면, 공간 자료 올리기."""
import re

from sqlalchemy import select

from app.models import Initiative, Solution, Strategy, User, WorkspaceChange, WorkspaceFile
from tests.conftest import nloc


def _tok(client, url):
    m = re.search(r'name="_tok" value="([^"]+)"', client.get(url).text)
    return m.group(1) if m else ""


def _rev(client, url):
    m = re.search(r'name="_rev" value="([^"]+)"', client.get(url).text)
    return m.group(1)


# ---- 사업 방향 ----

def test_strategy_create_detail_edit_delete(user_client, db):
    r = user_client.post("/strategies", data={"title": "", "_tok": _tok(user_client, "/strategies/new")})
    assert r.status_code == 400 and "제목을 입력하세요" in r.text
    r = user_client.post("/strategies", data={"title": "공공 에너지 우선", "consensus": "지자체 우선", "status": "agreed",
                                              "_tok": _tok(user_client, "/strategies/new")}, follow_redirects=False)
    assert r.status_code == 303
    s = db.scalars(select(Strategy)).one()
    page = user_client.get(f"/strategies/{s.id}").text
    assert "지자체 우선" in page and "잣대로 쓰는 중" in page and "+ 과제 만들기" in page
    # 목록의 상태 탭
    lst = user_client.get("/strategies?status=agreed").text
    assert "공공 에너지 우선" in lst
    assert "공공 에너지 우선" not in user_client.get("/strategies?status=draft").text.split("</thead>")[-1]
    # 고치기: 틀린 상태, 기간 거꾸로, 충돌
    rev = _rev(user_client, f"/strategies/{s.id}/edit")
    assert user_client.post(f"/strategies/{s.id}", data={"title": "x", "status": "nope", "_rev": rev}).status_code == 400
    r = user_client.post(f"/strategies/{s.id}", data={"title": "x", "status": "agreed", "period_start": "2026-12-01", "period_end": "2026-11-01", "_rev": rev})
    assert r.status_code == 400 and "기간의 끝이" in r.text
    assert user_client.post(f"/strategies/{s.id}", data={"title": "공공 에너지 우선", "consensus": "지자체·공공기관 우선", "status": "agreed", "_rev": rev},
                            follow_redirects=False).status_code == 303
    assert user_client.post(f"/strategies/{s.id}", data={"title": "옛 값", "status": "agreed", "_rev": rev}).status_code == 409
    db.expire_all()
    assert [c.field for c in db.scalars(select(WorkspaceChange).where(WorkspaceChange.strategy_id == s.id))].count("consensus") == 2
    assert "합의 내용" in user_client.get(f"/strategies/{s.id}").text  # 변경 이력 카드
    # 지우기 확인 화면, 지우기
    conf = user_client.get(f"/strategies/{s.id}/delete")
    assert conf.status_code == 200 and "과제는 남고" in conf.text
    r = user_client.post(f"/strategies/{s.id}/delete", follow_redirects=False)
    assert nloc(r).startswith("/strategies")
    db.expire_all()
    assert db.scalars(select(Strategy)).all() == []


# ---- 과제 ----

def test_initiative_create_with_links_and_prefill(user_client, db):
    s = Strategy(title="공공 에너지 우선", status="agreed")
    sol = Solution(name="공조 제어")
    db.add_all([s, sol])
    db.commit()
    form = user_client.get(f"/initiatives/new?strategy_id={s.id}&solution_id={sol.id}").text
    assert f'value="{s.id}" selected' in form.replace("selected>", "selected") or re.search(rf'<option value="{s.id}"\s+selected', form)
    assert re.search(rf'name="solution_ids" value="{sol.id}" checked', form)
    assert user_client.get("/initiatives/new?strategy_id=999").status_code == 200  # 없는 번호는 무시
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    r = user_client.post("/initiatives", data={"title": "실증 추진", "kind": "issue", "status": "open", "owner_user_id": str(kim.id),
                                                "strategy_id": str(s.id), "solution_ids": [str(sol.id)], "_tok": _tok(user_client, "/initiatives/new")},
                         follow_redirects=False)
    assert r.status_code == 303
    item = db.scalars(select(Initiative)).one()
    assert item.kind == "issue" and item.owner_user_id == kim.id and [x.id for x in item.solutions] == [sol.id]
    page = user_client.get(f"/initiatives/{item.id}").text
    assert "공공 에너지 우선" in page and "공조 제어" in page and "상태 바꾸기" in page
    assert "실증 추진" in user_client.get(f"/solutions/{sol.id}").text  # 솔루션 상세의 연결에 과제
    assert "실증 추진" in user_client.get(f"/strategies/{s.id}").text  # 방향 상세의 이 방향의 과제
    bad = user_client.post("/initiatives", data={"title": "x", "owner_user_id": "999", "_tok": _tok(user_client, "/initiatives/new")})
    assert bad.status_code == 400 and "담당은" in bad.text


def test_initiative_status_change_and_list_filters(user_client, db):
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    a = Initiative(title="열린 이슈", kind="issue", owner_user_id=kim.id)
    b = Initiative(title="끝난 과제", kind="task", status="done")
    c = Initiative(title="남의 과제", kind="task")
    db.add_all([a, b, c])
    db.commit()
    lst = user_client.get("/initiatives").text
    assert "열린 이슈" in lst and "남의 과제" in lst and "끝난 과제" not in lst  # 기본은 열린 것만
    assert "끝난 과제" in user_client.get("/initiatives?open=0").text
    mine = user_client.get("/initiatives?who=mine").text
    assert "열린 이슈" in mine and "남의 과제" not in mine
    issues = user_client.get("/initiatives?kind=issue").text
    assert "열린 이슈" in issues and "남의 과제" not in issues
    rev = _rev(user_client, f"/initiatives/{a.id}")
    r = user_client.post(f"/initiatives/{a.id}/status", data={"status": "doing", "progress_line": "현장 확인", "_rev": rev}, follow_redirects=False)
    assert r.status_code == 303 and "notice=saved" in nloc(r)
    db.expire_all()
    a = db.get(Initiative, a.id)
    assert a.status == "doing" and "현장 확인" in a.progress
    rows = db.scalars(select(WorkspaceChange).where(WorkspaceChange.initiative_id == a.id, WorkspaceChange.field == "status")).all()
    assert (rows[-1].old_value, rows[-1].new_value) == ("open", "doing")
    assert user_client.post(f"/initiatives/{a.id}/status", data={"status": "zzz"}).status_code == 400
    assert user_client.post(f"/initiatives/{a.id}/status", data={"status": "done", "_rev": rev}).status_code == 409  # 옛 표시


def test_initiative_delete_keeps_linked_records(user_client, db):
    sol = Solution(name="공조 제어")
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    item = Initiative(title="실증 추진", created_by_id=kim.id)
    item.solutions = [sol]
    db.add_all([sol, item])
    db.commit()
    item_id, sol_id = item.id, sol.id
    assert "올린 자료 파일과 대화가" in user_client.get(f"/initiatives/{item_id}/delete").text
    assert user_client.post(f"/initiatives/{item_id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Initiative, item_id) is None and db.get(Solution, sol_id) is not None


# ---- 공간 자료 ----

def test_workspace_file_upload_duplicate_download_delete(user_client, db, settings):
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    item = Initiative(title="실증 추진", created_by_id=kim.id)
    db.add(item)
    db.commit()
    body = "실증 부지 후보: 세 곳".encode("utf-8")
    r = user_client.post(f"/initiatives/{item.id}/files", files=[("files", ("회의록.txt", body, "text/plain"))], follow_redirects=False)
    assert r.status_code == 303 and "files_uploaded" in nloc(r)
    f = db.scalars(select(WorkspaceFile)).one()
    raw = settings.uploads_dir / "workspaces" / "initiative" / str(item.id) / f.stored_name
    copy = settings.knowledge_dir / f"workspaces/initiative/{item.id}/files/{f.id}-회의록.txt.md"
    assert raw.is_file() and copy.is_file() and "세 곳" in copy.read_text(encoding="utf-8")
    assert "회의록.txt" in (settings.knowledge_dir / f"initiatives/{item.id}.md").read_text(encoding="utf-8")
    r = user_client.post(f"/initiatives/{item.id}/files", files=[("files", ("다시.txt", body, "text/plain"))])
    assert r.status_code == 200 and "이미 같은 내용의 자료" in r.text
    assert len(db.scalars(select(WorkspaceFile)).all()) == 1
    d = user_client.get(f"/workspace-files/{f.id}/download")
    assert d.status_code == 200 and d.content == body and "UTF-8''" in d.headers["content-disposition"]
    assert user_client.get(f"/workspace-files/{f.id}/delete").status_code == 200  # 확인 화면
    r = user_client.post(f"/workspace-files/{f.id}/delete", follow_redirects=False)
    assert r.status_code == 303 and nloc(r).startswith(f"/initiatives/{item.id}")
    assert not raw.exists() and not copy.exists()


def test_workspace_file_too_big_and_owner_delete_removes_files(user_client, db, settings):
    settings.max_upload_mb = 0
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    s = Strategy(title="공공 에너지 우선", created_by_id=kim.id)
    db.add(s)
    db.commit()
    r = user_client.post(f"/strategies/{s.id}/files", files=[("files", ("큰.txt", b"x" * 10, "text/plain"))])
    assert r.status_code == 200 and "크기 제한" in r.text
    assert db.scalars(select(WorkspaceFile)).all() == []
    settings.max_upload_mb = 5
    user_client.post(f"/strategies/{s.id}/files", files=[("files", ("방향.txt", "지자체 우선".encode(), "text/plain"))])
    f = db.scalars(select(WorkspaceFile)).one()
    raw = settings.uploads_dir / "workspaces" / "strategy" / str(s.id) / f.stored_name
    assert raw.is_file()
    sid = s.id
    user_client.post(f"/strategies/{sid}/delete")
    db.expire_all()
    assert not raw.exists() and db.scalars(select(WorkspaceFile)).all() == []
    assert not (settings.knowledge_dir / f"workspaces/strategy/{sid}").exists()


def test_edit_keeps_link_to_closed_strategy(user_client, db):
    s = Strategy(title="지난 방향", status="closed")
    item = Initiative(title="실증 추진", strategy=s)
    db.add_all([s, item])
    db.commit()
    form = user_client.get(f"/initiatives/{item.id}/edit").text
    assert re.search(rf'<option value="{s.id}"\s+selected', form) and "(종료)" in form


def test_status_conflict_keeps_typed_values(user_client, db):
    item = Initiative(title="실증 추진")
    db.add(item)
    db.commit()
    rev = _rev(user_client, f"/initiatives/{item.id}")
    item.title = "다른 곳에서 고침"
    db.commit()
    r = user_client.post(f"/initiatives/{item.id}/status", data={"status": "blocked", "progress_line": "인허가 대기", "_rev": rev})
    assert r.status_code == 409
    assert re.search(r'<option value="blocked"\s+selected', r.text) and 'value="인허가 대기"' in r.text
