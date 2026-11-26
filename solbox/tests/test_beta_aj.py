"""베타 AJ (2026-11-26): 구성안의 장 하나만 AI 로 다시 쓰기와 되돌리기."""

import json
from pathlib import Path

from app.ai import proposal_tasks
from app.ai.queue import process_next
from app.ai.tasks import load_prompt
from app.models import AIJob
from app.proposals import save_outline
from tests.conftest import outlined_proposal

ROOT = Path(__file__).resolve().parents[1]
NEW = {"kind": "table", "title": "두 솔루션의 역할이 나뉩니다", "key_message": "역할을 나눠 효과를 키웁니다.",
       "columns": ["솔루션", "역할"], "rows": [["에너지 관리", "절감"], ["조명 제어", "교체"]], "sources": ["solutions/1/card.md"], "needs_check": []}


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def _start(user_client, p, index=3, instruction="표로 바꿔 줘"):
    return user_client.post(f"/proposals/{p.id}/outline/slides/{index}/ai", data={"instruction": instruction}, follow_redirects=False)


def test_rewrite_one_slide_and_undo(app, user_client, db, settings, fake_claude):
    p = outlined_proposal(db, settings)
    before = [dict(s) for s in p.outline["slides"]]
    r = _start(user_client, p)
    assert r.status_code == 303 and r.headers["location"] == f"/proposals/{p.id}/outline?open=3#slide-3"
    page = user_client.get(f"/proposals/{p.id}/outline").text
    assert 'id="job-box-outline"' in page and "AI 작업이 진행 중입니다" not in page  # 구성안 화면에도 진행 상자 (시험은 처리기가 꺼져 있어 꺼짐 글)
    db.refresh(p)
    job = db.get(AIJob, p.active_job_id)
    job.status = "running"
    db.commit()
    page = user_client.get(f"/proposals/{p.id}/outline").text
    assert "AI가 4번 장을 다시 쓰는 중…" in page
    job.status = "pending"
    db.commit()
    fake_claude.set(mode="ok", result=json.dumps(NEW, ensure_ascii=False))
    _run(app, settings)
    db.refresh(p)
    stdin = fake_claude.calls()[0]["stdin"]
    assert "8장 가운데 4번째 장만" in stdin and "표로 바꿔 줘" in stdin and ">> 4. [table] 솔루션 구성" in stdin
    assert "## 대상과 자료" in stdin and "아홉 가지" in stdin and "9. 출력은" not in stdin and "## 출력 구조 (Outline)" not in stdin
    slides = p.outline["slides"]
    assert slides[3]["title"] == NEW["title"] and slides[3]["checked"] == []
    assert [s for i, s in enumerate(slides) if i != 3] == [s for i, s in enumerate(before) if i != 3]
    assert p.outline["slide_undo"] == {"index": 3, "slide": before[3]} and p.active_job_id is None
    page = user_client.get(f"/proposals/{p.id}/outline?open=3").text
    assert "다시 쓰기 전으로 되돌리기" in page and page.count("AI로 다시 쓰기") == 7  # 표지를 뺀 일곱 장
    r = user_client.post(f"/proposals/{p.id}/outline/slides/3/undo", follow_redirects=False)
    assert r.status_code == 303 and "notice=slide_restored" in r.headers["location"]
    db.refresh(p)
    assert p.outline["slides"] == before and "slide_undo" not in p.outline
    assert user_client.post(f"/proposals/{p.id}/outline/slides/3/undo").status_code == 400  # 한 번만


def test_other_saves_drop_undo(app, user_client, db, settings, fake_claude):
    p = outlined_proposal(db, settings)
    _start(user_client, p)
    fake_claude.set(mode="ok", result=json.dumps(NEW, ensure_ascii=False))
    _run(app, settings)
    db.refresh(p)
    save_outline(db, p, list(p.outline["slides"]))
    db.refresh(p)
    assert "slide_undo" not in p.outline
    assert "다시 쓰기 전으로 되돌리기" not in user_client.get(f"/proposals/{p.id}/outline").text


def test_changed_outline_is_not_overwritten(app, user_client, db, settings, fake_claude):
    p = outlined_proposal(db, settings)
    _start(user_client, p)
    slides = [dict(s) for s in p.outline["slides"]]
    slides[3]["title"] = "다른 곳에서 고친 제목"
    p.outline = {**p.outline, "slides": slides}
    db.commit()
    fake_claude.set(mode="ok", result=json.dumps(NEW, ensure_ascii=False))
    _run(app, settings)
    db.refresh(p)
    job = db.get(AIJob, p.active_job_id)
    assert job.status == "failed" and job.error == proposal_tasks.SLIDE_CHANGED and fake_claude.calls() == []
    assert p.outline["slides"][3]["title"] == "다른 곳에서 고친 제목"
    page = user_client.get(f"/proposals/{p.id}/outline").text
    assert "AI가 4번 장을 다시 쓰지 못했어요." in page and "장은 그대로입니다" in page


def test_closing_must_stay_closing(app, user_client, db, settings, fake_claude):
    p = outlined_proposal(db, settings)
    _start(user_client, p, index=3)
    fake_claude.set(mode="ok", result=json.dumps({"kind": "closing", "title": "감사합니다", "bullets": ["다음 단계"]}, ensure_ascii=False))
    _run(app, settings)
    db.refresh(p)
    assert db.get(AIJob, p.active_job_id).status == "failed" and p.outline["slides"][3]["kind"] == "table"
    last = len(p.outline["slides"]) - 1
    _start(user_client, p, index=last, instruction="")
    fake_claude.set(mode="ok", result=json.dumps({"kind": "closing", "title": "함께 시작하겠습니다", "bullets": ["현장 진단 일정 협의"]}, ensure_ascii=False))
    _run(app, settings)
    db.refresh(p)
    assert p.outline["slides"][last]["title"] == "함께 시작하겠습니다"
    stdin = fake_claude.calls()[-1]["stdin"]
    assert "`kind` 는 `closing` 그대로" in stdin and "작성자의 요청\n\n<<<MATERIAL\n(없음)" in stdin


def test_rewrite_refusals(app, user_client, db, settings, fake_claude):
    p = outlined_proposal(db, settings)
    assert _start(user_client, p, index=0).status_code == 404  # 표지는 다시 쓰지 않는다
    assert _start(user_client, p, index=8).status_code == 404
    assert _start(user_client, p, instruction="가" * 301).status_code == 400
    assert _start(user_client, p).status_code == 303
    r = _start(user_client, p, index=2)
    assert r.status_code == 303 and "notice=busy" in r.headers["location"]
    page = user_client.get(f"/proposals/{p.id}/outline").text
    assert "AI로 다시 쓰기" not in page  # 진행 중에는 다시 쓰기·편집이 숨는다


def test_slide_prompt_reuses_outline_rules():
    """한 장 다시 쓰기는 구성안 프롬프트의 자료·규칙 구역을 잘라 쓴다: 표지가 바뀌면 이 시험이 먼저 깨진다."""
    outline = load_prompt("proposal_outline", invest_rule="(해당 없음)")
    materials, rules = proposal_tasks.slide_prompt_parts(outline)
    assert materials.startswith("## 대상과 자료") and "Glob, Grep, Read" in materials and "대화 내역" not in materials
    assert rules.startswith("슬라이드 종류(`kind`)는") and "`diagram` (도식)" in rules and "8. 슬라이드 문구 규칙" in rules and "9. 출력은" not in rules
    slide = (ROOT / "app" / "ai" / "prompts" / "proposal_slide.md").read_text(encoding="utf-8")
    for needle in ("{{materials}}", "{{rules}}", "{{titles}}", "{{slide}}", "{{instruction}}", "{{kind_rule}}", "<<<MATERIAL", "다른 장은 바꾸지 않습니다"):
        assert needle in slide, needle
