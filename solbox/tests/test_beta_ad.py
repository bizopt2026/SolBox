"""베타 AD: 솔루션 분야 (사용자 결정 2026-11-24).
분야는 관리자가 고치는 목록(표 `solution_fields`)이고 솔루션은 분야를 하나 가질 수 있다(`solutions.field_id`). 목록은 분야·발굴 단계·사업방식·파트너로 묶어 볼 수 있다.
AI 는 분야를 제안만 하고 저장하지 않는다."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import ontology, solution_fields as sfields
from app.ai.queue import process_next
from app.main import create_app
from app.models import AppSetting, Partner, Solution, SolutionBusinessType, SolutionField
from tests.conftest import form_from_page, make_user, nloc, ready_bundle
from tests.test_beta_w import _old_db

SEED = ["에너지 효율", "신재생·발전", "안전·방재", "통신·네트워크", "시설 관리", "환경"]


def _fields(db):
    db.expire_all()
    return [(f.name, f.sort_order, f.active) for f in sfields.all_fields(db)]


def _fid(db, name):
    db.expire_all()
    return db.query(SolutionField).filter_by(name=name).one().id


def _sol(db, name="AI 에너지 절감", field=None, **kw):
    s = Solution(name=name, stage=kw.pop("stage", "boxed"), field_id=_fid(db, field) if field else None, **kw)
    db.add(s)
    db.commit()
    return s


def _form(name="AI 에너지 절감", **over):
    data = {"name": name, "stage": "boxed", **over}
    if "field_id" in data:
        data["field_present"] = "1"  # 폼의 숨은 표시 (분야 칸이 실려 왔다)
    return data


def _card(settings, s):
    return (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


# ---- 처음 채우기 ----

def test_seeded_once_in_order(db):
    assert _fields(db) == [(n, i + 1, True) for i, n in enumerate(SEED)]
    assert list(ontology.SOLUTION_FIELD_SEED) == SEED  # 목록은 ontology 한 곳
    assert db.get(AppSetting, sfields.SEEDED_KEY) is not None


def test_not_reseeded_after_admin_deleted_values(settings):
    app = create_app(settings)
    try:
        s = app.state.session_factory()
        s.query(SolutionField).filter(SolutionField.name.in_(["환경", "안전·방재"])).delete()
        s.commit()
        s.close()
    finally:
        app.state.engine.dispose()
    app = create_app(settings)  # 다시 시작
    try:
        s = app.state.session_factory()
        assert [f.name for f in sfields.all_fields(s)] == ["에너지 효율", "신재생·발전", "통신·네트워크", "시설 관리"]
        s.query(SolutionField).delete()
        s.commit()
        s.close()
    finally:
        app.state.engine.dispose()
    app = create_app(settings)  # 모두 지워도 다시 채우지 않는다
    try:
        s = app.state.session_factory()
        assert sfields.all_fields(s) == []
        s.close()
    finally:
        app.state.engine.dispose()


# ---- 관리 화면 ----

def test_admin_page_lists_with_counts_and_is_in_admin_nav(admin_client, db):
    _sol(db, "A", "에너지 효율")
    _sol(db, "B", "에너지 효율")
    page = admin_client.get("/admin/solution-fields").text
    assert "솔루션 분야" in page and all(n in page for n in SEED)
    assert re.search(r'href="/solutions\?group=field#field-\d+">2건', page)
    assert 'href="/admin/solution-fields"' in page and "분야가 없는 솔루션은 0건" in page
    paths = [i.path for i in ontology.ADMIN_NAV_ITEMS]
    assert "admin/solution-fields" in paths and paths.index("admin/solution-fields") < paths.index("admin/system")


def test_non_admin_gets_403_everywhere(user_client, db):
    fid = _fid(db, "환경")
    assert user_client.get("/admin/solution-fields").status_code == 403
    assert user_client.get(f"/admin/solution-fields/{fid}/delete").status_code == 403
    for url, data in (("", {"name": "새 분야"}), (f"/{fid}", {"name": "x"}), (f"/{fid}/move", {"direction": "up"}),
                      (f"/{fid}/active", {"active": "0"}), (f"/{fid}/delete", {})):
        assert user_client.post("/admin/solution-fields" + url, data=data).status_code == 403, url
    assert _fields(db) == [(n, i + 1, True) for i, n in enumerate(SEED)]
    assert "/admin/solution-fields" not in user_client.get("/").text


def test_anonymous_cannot_use_admin_page(client):
    r = client.post("/admin/solution-fields", data={"name": "x"}, follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403)


def test_add_rename_validation_never_500(admin_client, db):
    r = admin_client.post("/admin/solution-fields", data={"name": "  스마트   팜 "}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == "/admin/solution-fields?notice=field_created"
    assert _fields(db)[-1] == ("스마트 팜", 7, True)  # 맨 뒤, 공백 한 칸으로
    for bad, text in (("", "입력"), ("   ", "입력"), ("가" * 31, "30자"), ("환경", "이미 있는"), (" 환경 ", "이미 있는"), ("스마트 팜", "이미 있는")):
        r = admin_client.post("/admin/solution-fields", data={"name": bad})
        assert r.status_code == 400 and text in r.text, bad
    assert admin_client.post("/admin/solution-fields", data={"name": "가" * 30}, follow_redirects=False).status_code == 303  # 30자는 된다
    assert admin_client.post("/admin/solution-fields", data={}).status_code == 400  # 칸이 아예 없어도
    fid = _fid(db, "환경")
    r = admin_client.post(f"/admin/solution-fields/{fid}", data={"name": "환경·ESG"}, follow_redirects=False)
    assert r.status_code == 303 and "환경·ESG" in [f[0] for f in _fields(db)]
    r = admin_client.post(f"/admin/solution-fields/{fid}", data={"name": "시설 관리"})  # 다른 분야와 같은 이름
    assert r.status_code == 400 and "이미 있는" in r.text and 'value="시설 관리"' in r.text
    r = admin_client.post(f"/admin/solution-fields/{fid}", data={"name": ""})
    assert r.status_code == 400
    assert admin_client.post(f"/admin/solution-fields/{fid}", data={"name": "환경·ESG"}, follow_redirects=False).status_code == 303  # 자기 이름은 그대로 저장돼도 된다
    for bad_id in (9999, "abc"):
        assert admin_client.post(f"/admin/solution-fields/{bad_id}", data={"name": "x"}).status_code in (404, 422)
    assert admin_client.post(f"/admin/solution-fields/9999/delete").status_code == 404


def test_move_up_down_and_edges(admin_client, db):
    ids = {n: _fid(db, n) for n in SEED}
    admin_client.post(f"/admin/solution-fields/{ids['안전·방재']}/move", data={"direction": "up"})
    assert [f[0] for f in _fields(db)][:3] == ["에너지 효율", "안전·방재", "신재생·발전"]
    admin_client.post(f"/admin/solution-fields/{ids['에너지 효율']}/move", data={"direction": "down"})
    assert [f[0] for f in _fields(db)][:3] == ["안전·방재", "에너지 효율", "신재생·발전"]
    assert [f[1] for f in _fields(db)] == [1, 2, 3, 4, 5, 6]
    admin_client.post(f"/admin/solution-fields/{ids['안전·방재']}/move", data={"direction": "up"})  # 맨 위에서 더 못 감: 그대로
    assert [f[0] for f in _fields(db)][0] == "안전·방재"
    assert admin_client.post(f"/admin/solution-fields/{ids['환경']}/move", data={"direction": "sideways"}).status_code == 400


def test_deactivate_hides_from_new_choices_but_existing_keeps_it(admin_client, user_client, db, settings):
    s = _sol(db, "A", "시설 관리")
    fid = _fid(db, "시설 관리")
    r = admin_client.post(f"/admin/solution-fields/{fid}/active", data={"active": "0"}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r).endswith("notice=field_off")
    new_page = user_client.get("/solutions/new").text
    assert "시설 관리" not in new_page and "에너지 효율" in new_page
    edit = user_client.get(f"/solutions/{s.id}/edit").text
    assert f'<option value="{fid}" selected>시설 관리 (사용 안 함)</option>' in edit  # 이 솔루션이 가진 것은 표시와 함께 남는다
    other = _sol(db, "B")
    assert "시설 관리" not in user_client.get(f"/solutions/{other.id}/edit").text
    assert user_client.post("/solutions", data=_form("N", field_id=str(fid))).status_code == 400  # 새로 고를 수 없다
    assert user_client.post(f"/solutions/{other.id}", data=_form("B", field_id=str(fid))).status_code == 400
    r = user_client.post(f"/solutions/{s.id}", data=_form("A", field_id=str(fid), summary="고침"), follow_redirects=False)
    assert r.status_code == 303  # 이미 가진 것은 그대로 저장된다
    assert "시설 관리" in user_client.get(f"/solutions/{s.id}").text and "(사용 안 함)" in user_client.get(f"/solutions/{s.id}").text
    assert "(사용 안 함)" in admin_client.get("/admin/solution-fields").text
    admin_client.post(f"/admin/solution-fields/{fid}/active", data={"active": "1"})
    assert "시설 관리" in user_client.get("/solutions/new").text
    assert admin_client.post(f"/admin/solution-fields/{fid}/active", data={"active": "x"}).status_code == 400


def test_delete_blocked_while_in_use_and_allowed_when_unused(admin_client, db):
    _sol(db, "A", "환경")
    _sol(db, "B", "환경")
    fid = _fid(db, "환경")
    r = admin_client.post(f"/admin/solution-fields/{fid}/delete")
    assert r.status_code == 400 and "2건" in r.text and "사용을 끄" in r.text
    assert "환경" in [f[0] for f in _fields(db)]
    page = admin_client.get(f"/admin/solution-fields/{fid}/delete").text  # 확인 화면도 이유를 보인다
    assert "2건" in page and "지울 수 없습니다" in page
    unused = _fid(db, "안전·방재")
    assert "정말" in admin_client.get(f"/admin/solution-fields/{unused}/delete").text or "삭제" in admin_client.get(f"/admin/solution-fields/{unused}/delete").text
    r = admin_client.post(f"/admin/solution-fields/{unused}/delete", follow_redirects=False)
    assert r.status_code == 303 and nloc(r).endswith("notice=field_deleted")
    assert "안전·방재" not in [f[0] for f in _fields(db)]


def test_admin_page_escapes_names(admin_client, db):
    admin_client.post("/admin/solution-fields", data={"name": "<b>굵게</b>&"})
    page = admin_client.get("/admin/solution-fields").text
    assert "<b>굵게</b>" not in page and "&lt;b&gt;굵게&lt;/b&gt;&amp;" in page


# ---- 솔루션 폼·상세·지식 사본 ----

def test_form_detail_and_knowledge_line(user_client, db, settings):
    page = user_client.get("/solutions/new").text
    assert 'name="field_id"' in page and "(분야 미지정)" in page and all(n in page for n in SEED)
    fid = _fid(db, "에너지 효율")
    r = user_client.post("/solutions", data=_form(field_id=str(fid)), follow_redirects=False)
    assert r.status_code == 303
    s = db.query(Solution).one()
    assert s.field_id == fid
    assert "- 분야: 에너지 효율" in _card(settings, s)
    detail = user_client.get(f"/solutions/{s.id}").text
    assert "분야" in detail and "에너지 효율" in detail
    assert f'<option value="{fid}" selected>' in user_client.get(f"/solutions/{s.id}/edit").text
    # 비우기
    assert user_client.post(f"/solutions/{s.id}", data=_form(field_id=""), follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Solution, s.id).field_id is None and "- 분야:" not in _card(settings, s)


def test_invalid_field_value_is_400_and_keeps_form(user_client, db):
    for bad in ("999999", "abc", "-1", "1; DROP", "9" * 40):
        r = user_client.post("/solutions", data=_form(summary="적은 글", field_id=bad))
        assert r.status_code == 400 and "알 수 없는 분야" in r.text and "적은 글" in r.text, bad
    assert db.query(Solution).count() == 0
    s = _sol(db, "A", "환경")
    r = user_client.post(f"/solutions/{s.id}", data=_form("A", field_id="abc"))
    assert r.status_code == 400
    db.expire_all()
    assert db.get(Solution, s.id).field_id == _fid(db, "환경")


def test_old_forms_without_field_leave_it_alone(user_client, db):
    s = _sol(db, "A", "환경")
    assert user_client.post(f"/solutions/{s.id}", data=_form("A", summary="x"), follow_redirects=False).status_code == 303  # field_id 칸 없음
    db.expire_all()
    assert db.get(Solution, s.id).field_id == _fid(db, "환경")


def test_concurrent_edit_guard_includes_field(user_client, db):
    s = _sol(db, "A", "환경")
    page = user_client.get(f"/solutions/{s.id}/edit").text
    rev = re.search(r'name="_rev" value="([^"]+)"', page).group(1)
    s2 = db.get(Solution, s.id)
    s2.field_id = _fid(db, "에너지 효율")  # 다른 곳에서 분야를 바꿨다
    db.commit()
    r = user_client.post(f"/solutions/{s.id}", data={**_form("A", field_id=str(_fid(db, "시설 관리"))), "_rev": rev})
    assert r.status_code == 409 and "다른 곳에서 이 기록이 바뀌었습니다" in r.text
    assert "분야" in r.text and "에너지 효율" in r.text  # 저장된 값(발췌)과 함께 어느 칸이 다른지 보인다
    db.expire_all()
    assert db.get(Solution, s.id).field_id == _fid(db, "에너지 효율")


def test_rename_and_deactivate_rebuild_knowledge_copies(admin_client, user_client, db, settings):
    s1 = _sol(db, "A", "환경")
    s2 = _sol(db, "B", "환경")
    s3 = _sol(db, "C", "시설 관리")
    from app.knowledge import KnowledgeMirror

    mirror = KnowledgeMirror(settings.knowledge_dir)
    for s in (s1, s2, s3):
        mirror.sync(s)
    assert "- 분야: 환경" in _card(settings, s1)
    fid = _fid(db, "환경")
    assert admin_client.post(f"/admin/solution-fields/{fid}", data={"name": "환경·ESG"}, follow_redirects=False).status_code == 303
    assert "- 분야: 환경·ESG" in _card(settings, s1) and "- 분야: 환경·ESG" in _card(settings, s2)
    assert "- 분야: 시설 관리" in _card(settings, s3)  # 다른 분야는 그대로
    assert admin_client.post(f"/admin/solution-fields/{fid}/active", data={"active": "0"}, follow_redirects=False).status_code == 303
    assert "- 분야: 환경·ESG" in _card(settings, s1)  # 사용을 꺼도 붙은 솔루션의 사본은 분야를 유지한다


def test_knowledge_format_bumped(app, db, settings):
    from app.knowledge import KNOWLEDGE_FORMAT, KnowledgeMirror

    assert KNOWLEDGE_FORMAT >= 10
    s = _sol(db, "A", "환경")
    KnowledgeMirror(settings.knowledge_dir).rebuild_all(db, settings.uploads_dir)
    assert "- 분야: 환경" in _card(settings, s)


# ---- 온톨로지 ----

def test_ontology_registration():
    t = ontology.TYPES["solution_field"]
    assert (t.model_name, t.table, t.list_url, t.group) == ("SolutionField", "solution_fields", "/admin/solution-fields", "부속")
    assert ontology.TYPES["solution"].props["field"] == "분야"
    r = {x.key: x for x in ontology.RELATIONS}["R36"]
    assert (r.source, r.target, r.cardinality, r.storage, r.physical) == (("solution_field",), ("solution",), "1:N", "fk", "solutions.field_id")
    assert "R36" in ontology.UNTRAVERSED
    assert ontology.SOLUTION_GROUPS == {"field": "분야", "stage": "발굴 단계", "business_type": "사업방식", "partner": "파트너"}


def test_docs_mention_new_rules():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    doc = (root / "docs" / "ontology.md").read_text(encoding="utf-8")
    assert "| R36 |" in doc and "2026-11-24" in doc and "솔루션 분야" in doc
    assert "베타 AD 묶음이 바꾼 규칙" in (root / "docs" / "rules" / "대상과-데이터.md").read_text(encoding="utf-8")
    assert "솔루션 분야" in (root / "docs" / "운영가이드.md").read_text(encoding="utf-8")
    assert "묶어 보기" in (root / "app" / "templates" / "help.html").read_text(encoding="utf-8")


# ---- AI 제안: 저장하지 않는다 ----

def test_solution_draft_suggests_active_field_name_only(app, admin_client, user_client, db, settings, fake_claude, worker_on):
    s = _sol(db, "A", summary="기존")
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("소개.txt", "태양광 발전 설비".encode(), "text/plain"))])
    fake_claude.set(mode="ok", result=json.dumps({"summary": "태양광", "description": "설명", "tags": [], "field": "신재생·발전"}, ensure_ascii=False))
    url = user_client.post(f"/solutions/{s.id}/draft", follow_redirects=False).headers["location"]
    _run(app, settings)
    page = user_client.get(url).text
    fid = _fid(db, "신재생·발전")
    assert f'<option value="{fid}" selected>신재생·발전</option>' in page and "AI 가 이 분야를 제안했어요" in page
    prompt = fake_claude.calls()[0]["stdin"]
    assert all(f"- {n}" in prompt for n in SEED) and "field" in prompt
    db.expire_all()
    assert db.get(Solution, s.id).field_id is None  # 저장하지 않았다
    # 사용자가 저장해야 반영
    assert user_client.post(f"/solutions/{s.id}", data=_form("A", field_id=str(fid)), follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Solution, s.id).field_id == fid


def test_draft_unknown_or_bad_field_is_ignored_and_inactive_not_offered(app, admin_client, user_client, db, settings, fake_claude, worker_on):
    s = _sol(db, "A")
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("소개.txt", b"x", "text/plain"))])
    admin_client.post(f"/admin/solution-fields/{_fid(db, '환경')}/active", data={"active": "0"})
    for bad in ("없는 분야", "환경", "", 7, ["에너지 효율"], None, {"name": "환경"}):
        fake_claude.set(mode="ok", result=json.dumps({"summary": "한 줄", "field": bad}, ensure_ascii=False))
        url = user_client.post(f"/solutions/{s.id}/draft", follow_redirects=False).headers["location"]
        _run(app, settings)
        page = user_client.get(url).text
        assert "AI 초안입니다" in page and not re.search(r'<option value="\d+" selected', re.search(r'<select id="f-field_id".*?</select>', page, re.S).group(0)), bad
    assert "- 환경" not in fake_claude.calls()[-1]["stdin"]  # 사용 안 하는 분야는 목록에 주지 않는다
    fake_claude.set(mode="ok", result=json.dumps({"summary": "한 줄"}))  # field 칸이 아예 없어도 된다
    url = user_client.post(f"/solutions/{s.id}/draft", follow_redirects=False).headers["location"]
    _run(app, settings)
    assert "AI 초안입니다" in user_client.get(url).text


def test_draft_does_not_override_existing_field(app, user_client, db, settings, fake_claude, worker_on):
    s = _sol(db, "A", "시설 관리")
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("소개.txt", b"x", "text/plain"))])
    fake_claude.set(mode="ok", result=json.dumps({"summary": "한 줄", "field": "환경"}, ensure_ascii=False))
    url = user_client.post(f"/solutions/{s.id}/draft", follow_redirects=False).headers["location"]
    _run(app, settings)
    assert f'<option value="{_fid(db, "시설 관리")}" selected>' in user_client.get(url).text


def test_intake_suggests_field_and_apply_saves_only_what_user_keeps(user_client, db, settings):
    files = [("소개서.txt", "태양광".encode()), ("소개서2.txt", "조명".encode())]
    plan = {"solutions": [{"name": "태양광 모니터링", "files": [1], "reason": "r", "field": " 신재생·발전 "},
                          {"name": "스마트 조명", "files": [2], "reason": "r", "field": "없는 분야"}]}
    it = ready_bundle(db, settings, files, **plan)
    sun, light = it.plan["items"][0], it.plan["items"][1]
    fid = _fid(db, "신재생·발전")
    assert sun["field_id"] == fid and light["field_id"] is None  # 모르는 이름은 버린다
    page = user_client.get(f"/intake/{it.id}").text
    assert f'name="{sun["key"]}__field"' in page and f'<option value="{fid}" selected>신재생·발전</option>' in page and "AI 제안</span>" in page
    form = form_from_page(user_client, it)
    assert form[f'{sun["key"]}__field'] == [str(fid)] and form[f'{light["key"]}__field'] == [""]
    form[f'{light["key"]}__field'] = [str(_fid(db, "환경"))]  # 사용자가 직접 골랐다
    assert user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(Solution).filter_by(name="태양광 모니터링").one().field_id == fid
    assert db.query(Solution).filter_by(name="스마트 조명").one().field_id == _fid(db, "환경")
    assert "- 분야: 신재생·발전" in _card(settings, db.query(Solution).filter_by(name="태양광 모니터링").one())


def test_intake_user_can_clear_suggestion_and_garbage_value_is_dropped(user_client, db, settings):
    it = ready_bundle(db, settings, [("a.txt", b"x"), ("b.txt", b"y")],
                      solutions=[{"name": "하나", "files": [1], "reason": "r", "field": "환경"}, {"name": "둘", "files": [2], "reason": "r", "field": "환경"}])
    form = form_from_page(user_client, it)
    keys = [i["key"] for i in it.plan["items"]]
    form[f"{keys[0]}__field"] = [""]
    form[f"{keys[1]}__field"] = ["999999"]  # 없는 번호는 조용히 비운다
    assert user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(Solution).filter_by(name="하나").one().field_id is None and db.query(Solution).filter_by(name="둘").one().field_id is None


def test_intake_without_field_suggestion_is_unchanged(user_client, db, settings):
    it = ready_bundle(db, settings, [("a.txt", b"x")], solutions=[{"name": "조명", "files": [1], "reason": "r"}])
    assert it.plan["items"][0]["field_id"] is None
    user_client.post(f"/intake/{it.id}/apply", data=form_from_page(user_client, it), follow_redirects=False)
    assert db.query(Solution).filter_by(name="조명").one().field_id is None


# ---- 묶어 보기 ----

def _world(db):
    """분야·단계·사업방식·파트너가 섞인 솔루션들 (이름은 목록이 보이는 순서와 상관없는 글자)."""
    p_b = Partner(name="바다테크")
    p_a = Partner(name="가람전자")
    db.add_all([p_b, p_a])
    db.commit()
    a = _sol(db, "솔라A", "신재생·발전", stage="boxed", partner_id=p_b.id)
    b = _sol(db, "에너지B", "에너지 효율", stage="tapping", partner_id=p_a.id)
    c = _sol(db, "에너지C", "에너지 효율", stage="intake")
    d = _sol(db, "무분야D", None, stage="boxed", partner_id=p_a.id)
    e = _sol(db, "환경E", "환경", stage="hold")
    for s, codes in ((a, ["invest", "build"]), (b, ["lease"]), (c, ["build"])):
        for code in codes:
            s.business_types.append(SolutionBusinessType(business_type=code, memo=""))
    db.commit()
    return a, b, c, d, e


def _groups(html):
    """묶음 머리와 그 안의 솔루션 이름들: [(이름, 건수 글, [솔루션])]"""
    out = []
    for m in re.finditer(r'<details class="group-sec"[^>]*>(.*?)</details>', html, re.S):
        body = m.group(1)
        head = re.search(r'<b class="group-name[^"]*">(.*?)</b> <span class="group-count">(.*?)</span>', body)
        out.append((head.group(1), head.group(2), re.findall(r'class="row-title">(.*?)</a>', body)))
    return out


def test_group_by_field_order_and_unassigned_last(user_client, db):
    _world(db)
    g = _groups(user_client.get("/solutions?group=field").text)
    assert [x[0] for x in g] == ["에너지 효율", "신재생·발전", "환경", "분야 미지정"]  # 분야 순서, 미지정은 맨 뒤, 빈 분야(안전·방재 …)는 숨김
    assert [x[1] for x in g] == ["2건", "1건", "1건", "1건"]
    assert sorted(g[0][2]) == ["에너지B", "에너지C"] and g[3][2] == ["무분야D"]


def test_group_by_field_follows_admin_order_and_marks_inactive(admin_client, user_client, db):
    _world(db)
    admin_client.post(f"/admin/solution-fields/{_fid(db, '환경')}/move", data={"direction": "up"})
    admin_client.post(f"/admin/solution-fields/{_fid(db, '환경')}/move", data={"direction": "up"})
    admin_client.post(f"/admin/solution-fields/{_fid(db, '환경')}/move", data={"direction": "up"})
    admin_client.post(f"/admin/solution-fields/{_fid(db, '환경')}/move", data={"direction": "up"})
    admin_client.post(f"/admin/solution-fields/{_fid(db, '환경')}/move", data={"direction": "up"})
    admin_client.post(f"/admin/solution-fields/{_fid(db, '신재생·발전')}/active", data={"active": "0"})
    g = _groups(user_client.get("/solutions?group=field").text)
    assert [x[0] for x in g] == ["환경", "에너지 효율", "신재생·발전 (사용 안 함)", "분야 미지정"]


def test_group_by_stage_in_stage_order(user_client, db):
    _world(db)
    g = _groups(user_client.get("/solutions?group=stage").text)
    assert [x[0] for x in g] == [ontology.STAGES[c] for c in ("intake", "tapping", "boxed", "hold")]
    assert [x[1] for x in g] == ["1건", "1건", "2건", "1건"]


def test_group_by_business_type_multi_type_note_and_none_last(user_client, db):
    _world(db)
    html = user_client.get("/solutions?group=business_type").text
    g = _groups(html)
    assert [x[0] for x in g] == ["구축형", "임대형", "투자형", "사업방식 미지정"]  # 코드 순서
    assert sorted(g[0][2]) == ["솔라A", "에너지C"] and g[2][2] == ["솔라A"]  # 솔라A 는 두 묶음에 모두
    assert sorted(g[3][2]) == ["무분야D", "환경E"]
    assert "여러 사업방식을 가진 솔루션은 해당하는 묶음마다 나타나서" in html
    assert "여러 사업방식을 가진" not in user_client.get("/solutions?group=field").text  # 다른 기준에는 없다


def test_group_by_partner_name_order_and_none_last(user_client, db):
    _world(db)
    g = _groups(user_client.get("/solutions?group=partner").text)
    assert [x[0] for x in g] == ["가람전자", "바다테크", "파트너 없음"]  # 이름순 (바다테크를 먼저 만들었어도)
    assert sorted(g[0][2]) == ["무분야D", "에너지B"] and g[1][2] == ["솔라A"] and sorted(g[2][2]) == ["에너지C", "환경E"]


def test_group_is_collapsible_details_open_without_js_and_same_row_markup(user_client, db):
    _world(db)
    html = user_client.get("/solutions?group=field").text
    assert html.count('<details class="group-sec" open') == 4
    flat = user_client.get("/solutions").text
    assert 'class="list-table grouped"' in html and html.count('class="row-title"') == 5 == flat.count('class="row-title"')
    assert 'class="row-meta"' in html  # 좁은 화면 카드 줄 (같은 title_cell)


def test_group_with_filters_search_and_sort(user_client, db):
    _world(db)
    g = _groups(user_client.get("/solutions?group=field&stage=boxed").text)  # 단계 탭
    assert [(x[0], x[2]) for x in g] == [("신재생·발전", ["솔라A"]), ("분야 미지정", ["무분야D"])]
    g = _groups(user_client.get("/solutions?group=field&business_type=build").text)  # 사업방식 걸러 보기
    assert sorted(x[0] for x in g) == ["신재생·발전", "에너지 효율"] and [x[1] for x in g] == ["1건", "1건"]
    g = _groups(user_client.get("/solutions?group=stage&q=에너지").text)  # 검색
    assert [(x[0], x[2]) for x in g] == [(ontology.STAGES["intake"], ["에너지C"]), (ontology.STAGES["tapping"], ["에너지B"])]
    g = _groups(user_client.get("/solutions?group=field&sort=name").text)  # 정렬은 묶음 안에서
    assert g[0][2] == ["에너지B", "에너지C"]
    assert _groups(user_client.get("/solutions?group=field&origin=research").text) == []  # 맞는 것이 없으면 묶음도 없다
    assert "조건에 맞는 항목이 없습니다" in user_client.get("/solutions?group=field&origin=research").text


def test_group_control_links_keep_other_conditions(user_client, db):
    _world(db)
    html = user_client.get("/solutions?stage=boxed&q=솔라&group=field").text
    nav = re.search(r'<nav class="seg" aria-label="묶어 보기">(.*?)</nav>', html, re.S).group(1)
    links = re.findall(r'<a href="([^"]+)"( aria-current="page")?>(.*?)</a>', nav)
    assert [l[2] for l in links] == ["없음", "분야", "발굴 단계", "사업방식", "파트너"]
    assert [bool(l[1]) for l in links] == [False, True, False, False, False]
    assert "group=none" in links[0][0] and "stage=boxed" in links[0][0] and "group=partner" in links[4][0] and "page=" not in links[4][0]


def test_grouped_view_shows_all_rows_without_pagination(user_client, db):
    for i in range(55):
        _sol(db, f"대량{i:02d}", "환경" if i % 2 else "에너지 효율")
    html = user_client.get("/solutions?group=field").text
    assert html.count('class="row-title"') == 55 and 'class="pager"' not in html and "55건을 모두" in html
    flat = user_client.get("/solutions?group=none").text
    assert flat.count('class="row-title"') == 50 and 'class="pager"' in flat  # 묶지 않은 목록은 그대로 쪽을 나눈다
    assert user_client.get("/solutions?group=field&page=2").text.count('class="row-title"') == 55


def test_group_memory_cookie_and_none_clears(user_client, db):
    _world(db)
    r = user_client.get("/solutions?group=field")
    assert "solution_group=field" in r.headers.get("set-cookie", "")
    assert _groups(user_client.get("/solutions").text) and [x[0] for x in _groups(user_client.get("/solutions").text)][0] == "에너지 효율"  # 주소에 없어도 기억한 값
    assert [x[0] for x in _groups(user_client.get("/solutions?q=솔라").text)] == ["신재생·발전"]
    r = user_client.get("/solutions?group=stage")  # 다른 기준으로 바꾸면 그것을 기억한다
    assert "solution_group=stage" in r.headers.get("set-cookie", "")
    r = user_client.get("/solutions?group=none")
    assert _groups(r.text) == [] and 'class="row-title"' in r.text
    assert _groups(user_client.get("/solutions").text) == []  # 지워졌다
    assert user_client.cookies.get("solution_group") is None


def test_unknown_group_value_is_none_and_never_500(user_client, db):
    _world(db)
    user_client.get("/solutions?group=field")
    for bad in ("bogus", "", "FIELD", "field%00", "' OR 1=1"):
        r = user_client.get("/solutions", params={"group": bad})
        assert r.status_code == 200 and _groups(r.text) == [], bad
    user_client.cookies.set("solution_group", "<script>")  # 쿠키가 엉터리여도 무시
    assert user_client.get("/solutions").status_code == 200


def test_group_memory_is_per_browser_not_stored_in_db(app, user_client, admin_client, db):
    _world(db)
    user_client.get("/solutions?group=field")
    assert _groups(admin_client.get("/solutions").text) == []  # 다른 사용자(브라우저)는 영향 없음
    from app.models import User

    assert all("group" not in c.name for c in User.__table__.columns)  # 사용자 표에 기억 칸을 두지 않았다


def test_group_headings_and_list_escape_html(user_client, admin_client, db):
    admin_client.post("/admin/solution-fields", data={"name": "<i>분야</i>"})
    s = Solution(name="<u>이름</u>", stage="boxed", field_id=_fid(db, "<i>분야</i>"))
    db.add(s)
    db.commit()
    for url in ("/solutions?group=field", "/solutions", f"/solutions/{s.id}", f"/solutions/{s.id}/edit"):
        page = user_client.get(url).text
        assert "<i>분야</i>" not in page and "&lt;i&gt;분야&lt;/i&gt;" in page, url
    p = Partner(name="<b>파트너</b>")
    db.add(p)
    db.commit()
    s.partner_id = p.id
    db.commit()
    page = user_client.get("/solutions?group=partner").text
    assert "<b>파트너</b>" not in page and "&lt;b&gt;파트너&lt;/b&gt;" in page


def test_empty_solution_list_with_group_still_ok(user_client):
    r = user_client.get("/solutions?group=field")
    assert r.status_code == 200 and "아직 등록된 솔루션이 없습니다" in r.text


def test_list_shows_field_column(user_client, db):
    _sol(db, "A", "환경")
    page = user_client.get("/solutions").text
    assert "<th class=\"col-sec\">분야</th>" in page and "환경" in page


# ---- 옛 DB ----

def test_migration_on_old_database_adds_table_and_column_once_and_keeps_rows(settings):
    import sqlite3

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _old_db(settings.db_path)
    con = sqlite3.connect(settings.db_path)
    assert "solution_fields" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "field_id" not in {r[1] for r in con.execute("PRAGMA table_info(solutions)")}
    before = con.execute("SELECT id, name, summary, description, stage, usage_direction, applicable_types, tags FROM solutions ORDER BY id").fetchall()
    con.close()
    app = create_app(settings)
    try:
        con = sqlite3.connect(settings.db_path)
        info = {r[1]: r for r in con.execute("PRAGMA table_info(solutions)")}
        assert info["field_id"][3] == 0 and info["field_id"][4] is None  # NULL 허용, 기본값 없음 (옛 솔루션은 분야 미지정)
        assert con.execute("SELECT name FROM solution_fields ORDER BY sort_order").fetchall() == [(n,) for n in SEED]
        assert con.execute("SELECT id, name, summary, description, stage, usage_direction, applicable_types, tags FROM solutions ORDER BY id").fetchall() == before
        assert con.execute("SELECT COUNT(*) FROM solutions WHERE field_id IS NOT NULL").fetchone()[0] == 0
        con.close()
        c = TestClient(app)
        assert c.post("/login", data={"username": "kim", "password": "pw1234"}, follow_redirects=False).status_code == 303
        for url in ("/solutions", "/solutions?group=field", "/solutions/1", "/solutions/1/edit", "/solutions/new"):
            assert c.get(url).status_code == 200, url
        con = sqlite3.connect(settings.db_path)
        fid = con.execute("SELECT id FROM solution_fields WHERE name='환경'").fetchone()[0]
        con.close()
        assert c.post("/solutions/1", data=_form("AI 공조", field_id=str(fid)), follow_redirects=False).status_code == 303
    finally:
        app.state.engine.dispose()
    app = create_app(settings)  # 두 번째 시작: 다시 채우지 않고 값도 그대로
    try:
        con = sqlite3.connect(settings.db_path)
        assert con.execute("SELECT COUNT(*) FROM solution_fields").fetchone()[0] == 6
        assert con.execute("SELECT field_id FROM solutions WHERE id=1").fetchone()[0] == fid
        con.close()
    finally:
        app.state.engine.dispose()


def test_nothing_else_defines_the_seed_names():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    for path in sorted(root.rglob("*.py")):
        if path.name == "ontology.py":
            continue
        text = path.read_text(encoding="utf-8")
        for name in SEED[:5]:  # "환경" 은 흔한 낱말이라 뺀다
            assert name not in text, f"{name!r} 가 ontology.py 밖에 있다: {path.name}"
