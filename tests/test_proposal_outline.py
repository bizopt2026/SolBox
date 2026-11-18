import json

import pytest

from app.ai.proposal_tasks import Outline, Slide, Step
from app.ai.queue import process_next
from app.models import AIJob, Proposal
from app.proposals import clean_outline, start_job
from tests.conftest import make_proposal_world, make_user, refining_proposal, sample_outline


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def test_outline_generation(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="ok", result=json.dumps(sample_outline(8), ensure_ascii=False))
    user_client.post(f"/proposals/{p.id}/outline", data={"instruction": "일정 강조"})
    _run(app, settings)
    db.refresh(p)
    assert p.status == "outlined" and len(p.outline["slides"]) == 8 and p.outline["slides"][0]["kind"] == "cover"
    assert p.outline_updated_at is not None and p.active_job_id is None
    assert "일정 강조" in fake_claude.calls()[0]["stdin"]
    assert "8장" in user_client.get(f"/proposals/{p.id}").text


def test_sample_outline_covers_all_kinds():
    kinds = {s["kind"] for s in sample_outline(8)["slides"]}
    assert kinds == {"cover", "bullets", "two_column", "table", "steps", "closing"}
    assert len(Outline.model_validate(sample_outline(14)).slides) == 14


def test_stored_slides_have_every_field(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="ok", result=json.dumps(sample_outline(8), ensure_ascii=False))
    user_client.post(f"/proposals/{p.id}/outline")
    _run(app, settings)
    db.refresh(p)
    assert set(p.outline["slides"][1]) == set(Slide.model_fields)


@pytest.mark.parametrize("n", [5, 15])
def test_slide_count_out_of_range_fails(app, user_client, db, settings, fake_claude, n):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="ok", result=json.dumps(sample_outline(n), ensure_ascii=False))
    user_client.post(f"/proposals/{p.id}/outline")
    _run(app, settings)
    db.refresh(p)
    assert p.outline is None and p.status == "refining"
    assert p.active_job.status == "failed" and p.active_job.error == "슬라이드 수가 범위를 벗어났습니다."


def test_clean_outline_rules(tmp_path):
    raw = Outline(title="제안", slides=[
        Slide(kind="bullets", title="현황", bullets=[" 가 ", ""]),
        Slide(kind="mystery", title="?", bullets=["x"]),
        Slide(kind="cover", title="두 번째 표지"),
        Slide(kind="table", title="표", columns=["항목", "값"], rows=[["전력"], ["a", "b", "c"], []]),
        Slide(kind="table", title="열 없음", rows=[["a", "b"]]),
        Slide(kind="steps", title="일정", steps=[Step(name=str(i)) for i in range(7)]),
        Slide(kind="bullets", title="출처", sources=["nope.md"])])
    out = clean_outline(raw, tmp_path)["slides"]
    assert [s["kind"] for s in out] == ["cover", "bullets", "bullets", "bullets", "table", "bullets", "steps", "bullets"]
    assert out[0]["title"] == "제안" and out[1]["bullets"] == ["가"]
    assert out[4]["rows"] == [["전력", ""], ["a", "b"]]
    assert out[5]["bullets"] == ["a / b"]
    assert len(out[6]["steps"]) == 5
    assert out[7]["sources"] == [] and out[7]["needs_check"] == ["출처 확인 불가: nope.md"]


def test_outline_requires_selected_direction(app, user_client, db, settings):
    p, *_ = make_proposal_world(db, settings)
    r = user_client.post(f"/proposals/{p.id}/outline", follow_redirects=False)
    assert r.status_code == 400
    assert db.query(AIJob).count() == 0


def test_outline_busy(app, user_client, db, settings):
    p = refining_proposal(db, settings)
    start_job(db, p, "proposal_chat", make_user(db, "kim2"))
    r = user_client.post(f"/proposals/{p.id}/outline", follow_redirects=False)
    assert r.headers["location"] == f"/proposals/{p.id}?notice=busy"
    assert db.query(AIJob).count() == 1


def test_outline_superseded_does_not_write(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="ok", result=json.dumps(sample_outline(8), ensure_ascii=False))
    user_client.post(f"/proposals/{p.id}/outline")
    db.refresh(p)
    p.active_job_id = None
    db.commit()
    _run(app, settings)
    db.refresh(p)
    assert p.outline is None and p.status == "refining"


def test_detail_has_outline_controls(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    html = user_client.get(f"/proposals/{p.id}").text
    assert f'action="/proposals/{p.id}/outline"' in html and "제안서 만들기" in html
    fake_claude.set(mode="ok", result=json.dumps(sample_outline(8), ensure_ascii=False))
    user_client.post(f"/proposals/{p.id}/outline")
    _run(app, settings)
    html = user_client.get(f"/proposals/{p.id}").text
    assert "구성안 검토·수정" in html and f'href="/proposals/{p.id}/outline"' in html and "다시 만들기" in html


def test_failed_outline_offers_retry(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="error", stderr="Please run /login", exit_code=1)
    user_client.post(f"/proposals/{p.id}/outline")
    _run(app, settings)
    assert f'action="/proposals/{p.id}/outline"' in user_client.get(f"/proposals/{p.id}").text
    assert "다시 시도" in user_client.get(f"/proposals/{p.id}").text
