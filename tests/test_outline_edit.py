from app.ai.proposal_tasks import Slide
from app.models import AIJob, Proposal
from app.proposals import slide_from_form, slide_to_form
from tests.conftest import make_proposal_world, outlined_proposal, sample_outline


def _slides(db, p):
    db.refresh(p)
    return p.outline["slides"]


def test_outline_page_shows_sources_and_needs_check(user_client, db, settings):
    p = outlined_proposal(db, settings)
    page = user_client.get(f"/proposals/{p.id}/outline").text
    assert "절감률 수치" in page and "solutions/1/card.md" in page and "확인 필요" in page
    assert f"/proposals/{p.id}" in page and 'id="slide-7"' in page


def test_outline_page_escapes_text(user_client, db, settings):
    p = outlined_proposal(db, settings)
    slides = [dict(s) for s in p.outline["slides"]]
    slides[1]["title"] = "<script>alert(1)</script>"
    p.outline = {**p.outline, "slides": slides}
    db.commit()
    page = user_client.get(f"/proposals/{p.id}/outline").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)" in page


def test_edit_slide_text_fields(user_client, db, settings):
    p = outlined_proposal(db, settings)
    before = p.outline_updated_at
    r = user_client.post(f"/proposals/{p.id}/outline/slides/1", data={
        "kind": "table", "title": "비교", "key_message": "연 10% 절감",
        "columns": "항목 | 현재 | 제안", "rows": "전력 | 100 | 90\n인력 | 3",
        "needs_check": "수치 근거\n"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/proposals/{p.id}/outline#slide-1"
    db.refresh(p)
    s = p.outline["slides"][1]
    assert (s["kind"], s["columns"]) == ("table", ["항목", "현재", "제안"])
    assert s["rows"] == [["전력", "100", "90"], ["인력", "3", ""]] and s["needs_check"] == ["수치 근거"]
    assert s["bullets"] == [] and set(s) == set(Slide(kind="x").model_dump())
    assert p.outline_updated_at > before


def test_edit_cover_and_kind_rules(user_client, db, settings):
    p = outlined_proposal(db, settings)
    url = f"/proposals/{p.id}/outline/slides"
    assert user_client.post(f"{url}/0", data={"kind": "cover", "title": "새 제목", "subtitle": "부제"}).status_code == 200
    assert _slides(db, p)[0]["subtitle"] == "부제"
    assert user_client.post(f"{url}/0", data={"kind": "bullets", "title": "x"}).status_code == 400
    assert user_client.post(f"{url}/1", data={"kind": "cover", "title": "x"}).status_code == 400
    assert user_client.post(f"{url}/1", data={"kind": "nope", "title": "x"}).status_code == 400
    assert _slides(db, p)[1]["kind"] == "bullets"


def test_steps_parsing():
    s = slide_from_form("steps", {"title": "일정", "steps": "1단계: 진단: 2주\n2단계\n\n"})
    assert s["steps"] == [{"name": "1단계", "detail": "진단: 2주"}, {"name": "2단계", "detail": ""}]


def test_form_clears_other_kind_fields():
    s = slide_from_form("bullets", {"title": "t", "bullets": "a\n\nb", "columns": "x | y", "subtitle": "z"})
    assert s["bullets"] == ["a", "b"] and s["columns"] == [] and s["subtitle"] == ""
    assert set(s) == set(Slide(kind="x").model_dump())


def test_form_round_trip():
    slides = sample_outline(8)["slides"]
    full = [Slide.model_validate(x).model_dump() for x in slides]
    full[1].update(key_message="핵심", sources=["solutions/1/card.md", "a/b.md"], needs_check=["확인1", "확인2"])
    full[2].update(left=["a", "b"], right=["c"])
    kinds = {s["kind"] for s in full}
    assert kinds == {"cover", "bullets", "two_column", "table", "steps", "closing"}
    for s in full:
        assert slide_from_form(s["kind"], slide_to_form(s)) == s
    assert slide_from_form("steps", {"steps": "A: x\nB"})["steps"] == [{"name": "A", "detail": "x"}, {"name": "B", "detail": ""}]
    # 값에 | 나 : 가 있어도 허용되는 자리(글머리, 단계 설명)는 그대로 돌아온다
    t = Slide(kind="bullets", title="a|b: c", bullets=["x | y: z"]).model_dump()
    assert slide_from_form("bullets", slide_to_form(t)) == t
    st = Slide(kind="steps", steps=[{"name": "착수", "detail": "a: b | c"}]).model_dump()
    assert slide_from_form("steps", slide_to_form(st)) == st


def test_delete_move_add_rules(user_client, db, settings):
    p = outlined_proposal(db, settings)
    base = f"/proposals/{p.id}/outline/slides"
    titles = [s["title"] for s in _slides(db, p)]
    assert user_client.post(f"{base}/0/delete").status_code == 400
    assert user_client.post(f"{base}/1/move", data={"dir": "up"}).status_code == 400
    assert user_client.post(f"{base}/2/move", data={"dir": "up"}).status_code == 200
    assert [s["title"] for s in _slides(db, p)][1:3] == [titles[2], titles[1]]
    assert user_client.post(f"{base}/7/move", data={"dir": "down"}).status_code == 400
    assert user_client.post(f"{base}/0/move", data={"dir": "down"}).status_code == 400
    assert user_client.post(f"{base}/2/move", data={"dir": "sideways"}).status_code == 400
    r = user_client.post(f"{base}", data={"after": "3"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#slide-4")
    slides = _slides(db, p)
    assert len(slides) == 9 and slides[4]["title"] == "새 슬라이드" and slides[4]["kind"] == "bullets"
    assert user_client.post(f"{base}/4/delete").status_code == 200
    assert len(_slides(db, p)) == 8
    while len(_slides(db, p)) < 14:
        assert user_client.post(f"{base}", data={"after": "1"}).status_code == 200
    r = user_client.post(f"{base}", data={"after": "1"})
    assert r.status_code == 400 and "슬라이드는 14장까지 만들 수 있습니다." in r.text


def test_updated_at_strictly_increases(user_client, db, settings):
    p = outlined_proposal(db, settings)
    seen = [p.outline_updated_at]
    for _ in range(3):
        user_client.post(f"/proposals/{p.id}/outline/slides/1", data={"kind": "bullets", "title": "같은 내용"})
        db.refresh(p)
        seen.append(p.outline_updated_at)
    assert all(a < b for a, b in zip(seen, seen[1:]))


def test_out_of_range_and_no_outline(user_client, db, settings):
    p = outlined_proposal(db, settings)
    base = f"/proposals/{p.id}/outline"
    assert user_client.post(f"{base}/slides/8", data={"kind": "bullets"}).status_code == 404
    assert user_client.post(f"{base}/slides/8/delete").status_code == 404
    assert user_client.post(f"{base}/slides/8/move", data={"dir": "up"}).status_code == 404
    assert user_client.post(f"{base}/slides", data={"after": "8"}).status_code == 404
    assert user_client.post(f"{base}/slides", data={"after": "x"}).status_code == 404
    q, *_ = make_proposal_world(db, settings)
    assert q.outline is None
    assert user_client.get(f"/proposals/{q.id}/outline").status_code == 404
    assert user_client.post(f"/proposals/{q.id}/outline/slides/0", data={"kind": "cover"}).status_code == 404
    assert user_client.get("/proposals/9999/outline").status_code == 404


def test_edit_blocked_while_job_running(user_client, db, settings):
    p = outlined_proposal(db, settings)
    job = AIJob(kind="proposal_outline", status="running", input={"proposal_id": p.id})
    db.add(job)
    db.flush()
    p.active_job_id = job.id
    db.commit()
    base = f"/proposals/{p.id}/outline"
    before = [s["title"] for s in _slides(db, p)]
    for path, data in [("/slides/1", {"kind": "bullets", "title": "x"}), ("/slides/1/delete", {}),
                       ("/slides/2/move", {"dir": "up"}), ("/slides", {"after": "1"})]:
        r = user_client.post(base + path, data=data)
        assert r.status_code == 400 and "AI 작업이 끝난 뒤 수정하세요." in r.text
    assert [s["title"] for s in _slides(db, p)] == before
    assert user_client.get(base).status_code == 200
