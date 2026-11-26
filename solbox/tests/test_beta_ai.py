"""베타 AI (2026-11-26): 구성안 AI 검토 단계와 핵심 요약·왜 우리인가 장.

구성안을 만든 뒤 두 번째 AI(도구 없음)가 초안을 검토해 고친다. 초안에 없는 숫자를 넣거나 실패하면 초안을 그대로 쓴다.
`tests/conftest.py` 의 `settings` 는 검토를 끈다 (기존 시험이 마지막 호출을 구성안 프롬프트로 본다). 이 파일은 켜고 돈다."""

import copy
import json
from pathlib import Path

import pytest

from app import checks
from app.ai.queue import process_next
from app.config import load_settings
from app.proposals import NO_SOURCE_NOTE, accept_review, review_changes
from tests.conftest import refining_proposal, sample_outline

ROOT = Path(__file__).resolve().parents[1]
REVIEW_MARK = "검토자입니다"


def _draft():
    """clean_outline 을 거친 모양의 초안 (필요한 키만)."""
    return {"title": "제안", "slides": [
        {"kind": "cover", "title": "제안", "sources": [], "needs_check": []},
        {"kind": "bullets", "title": "과제", "bullets": ["전력비 12% 증가"], "sources": ["customers/1.md"], "needs_check": ["증가율 출처 확인"]},
        {"kind": "kpi", "title": "효과", "kpis": [{"value": "30", "unit": "%", "label": "절감", "note": ""}], "sources": ["solutions/1/card.md"], "needs_check": []},
        {"kind": "closing", "title": "마무리", "bullets": [], "sources": [], "needs_check": []},
    ]}


def _reviewed(draft):
    r = copy.deepcopy(draft)
    r["slides"].insert(1, {"kind": "bullets", "title": "핵심 요약", "bullets": ["과제: 전력비 12% 증가", "효과: 30% 절감"], "sources": [], "needs_check": []})
    return r


def test_accept_review_keeps_draft_facts():
    draft = _draft()
    out, why = accept_review(draft, _reviewed(draft))
    assert why == "" and [s["title"] for s in out["slides"]] == ["제안", "핵심 요약", "과제", "효과", "마무리"]
    assert out["slides"][1]["needs_check"] == [NO_SOURCE_NOTE]  # 근거 없는 내용 장에는 늘 확인 항목
    assert all(s["checked"] == [] for s in out["slides"])


@pytest.mark.parametrize("text", ["효과: 35% 절감", "효과: 30% 절감, 투자 회수 6개월", "효과: 1,200만원 절감"])
def test_accept_review_rejects_new_numbers(text):
    draft = _draft()
    reviewed = _reviewed(draft)
    reviewed["slides"][1]["bullets"][1] = text
    out, why = accept_review(draft, reviewed)
    assert out is None and "초안에 없는 숫자" in why


def test_accept_review_allows_counting_words_and_number_formats():
    draft = _draft()
    draft["slides"][2]["kpis"][0]["value"] = "1,200.0"
    reviewed = _reviewed(draft)
    reviewed["slides"][1]["title"] = "3가지로 정리한 4단계 제안"
    reviewed["slides"][1]["bullets"][1] = "효과: 1200 절감"
    assert accept_review(draft, reviewed)[1] == ""


def test_accept_review_restores_dropped_checks_and_filters_sources():
    draft = _draft()
    reviewed = _reviewed(draft)
    reviewed["slides"][2]["needs_check"] = []  # 같은 제목의 장에서 빠뜨림
    reviewed["slides"][2]["sources"] = ["customers/1.md", "solutions/9/card.md"]  # 초안에 없던 경로
    reviewed["slides"][3]["title"] = "기대 효과"
    out, why = accept_review(draft, reviewed)
    assert why == ""
    assert out["slides"][2]["needs_check"] == ["증가율 출처 확인"] and out["slides"][2]["sources"] == ["customers/1.md"]
    reviewed = _reviewed(draft)
    reviewed["slides"][2]["title"] = "이름이 바뀐 과제 장"
    reviewed["slides"][2]["needs_check"] = []
    out, _ = accept_review(draft, reviewed)
    assert "증가율 출처 확인" in [c for s in out["slides"] for c in s["needs_check"]]  # 제목이 바뀌어도 가까운 본문 장에 되돌린다


def test_review_changes_trims():
    assert review_changes(["", " 가 ", "가", "나" * 300] + [f"고침 {i}" for i in range(20)]) == ["가", "나" * 200] + [f"고침 {i}" for i in range(6)]


# ---------------------------------------------------------------- 작업 흐름

def _run(app, settings, db, p, fake_claude, review_answer):
    settings.proposal_review = True
    fake_claude.set(mode="ok", result=json.dumps(sample_outline(8), ensure_ascii=False), by_prompt=[[REVIEW_MARK, review_answer]])
    process_next(app.state.session_factory, app.state.runner, settings)
    db.refresh(p)
    return fake_claude.calls()


def _start(user_client, p):
    assert user_client.post(f"/proposals/{p.id}/outline", data={"instruction": ""}).status_code in (200, 303)


def test_review_applied(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    _start(user_client, p)
    reviewed = sample_outline(8)
    reviewed["slides"].insert(1, {"kind": "bullets", "title": "핵심 요약", "key_message": "전력비를 줄입니다.", "bullets": ["과제: 전력비 증가", "제안: 자동 제어"]})
    reviewed["slides"].pop(-2)
    reviewed["changes"] = ["핵심 요약 장을 표지 뒤에 더했습니다.", ""]
    calls = _run(app, settings, db, p, fake_claude, json.dumps(reviewed, ensure_ascii=False))
    assert len(calls) == 2 and REVIEW_MARK not in calls[0]["stdin"] and REVIEW_MARK in calls[1]["stdin"]
    argv = calls[1]["argv"]
    assert argv[argv.index("--tools") + 1] == ""  # 검토는 도구 없이 초안 글만 본다
    assert '"title": "고객 현황과 과제"' in calls[1]["stdin"] and "<<<MATERIAL" in calls[1]["stdin"]
    assert p.status == "outlined" and p.active_job_id is None
    assert p.outline["slides"][1]["title"] == "핵심 요약" and len(p.outline["slides"]) == 8
    assert p.outline["review"] == {"status": "applied", "changes": ["핵심 요약 장을 표지 뒤에 더했습니다."]}
    page = user_client.get(f"/proposals/{p.id}").text
    assert "AI 검토에서 고친 점 1가지" in page and "핵심 요약 장을 표지 뒤에 더했습니다." in page


def test_review_with_new_number_falls_back_to_draft(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    _start(user_client, p)
    reviewed = sample_outline(8)
    reviewed["slides"][1]["bullets"] = ["전력비 25% 증가", "노후 설비"]
    reviewed["changes"] = ["수치를 더했습니다."]
    _run(app, settings, db, p, fake_claude, json.dumps(reviewed, ensure_ascii=False))
    assert p.status == "outlined" and p.outline["slides"][1]["bullets"] == ["전력비 증가", "노후 설비"]
    assert p.outline["review"] == {"status": "rejected", "changes": []}
    assert "초안을 그대로 두었습니다 (검토가 규칙을 어겼습니다)" in user_client.get(f"/proposals/{p.id}").text


def test_broken_review_does_not_fail_the_job(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    _start(user_client, p)
    calls = _run(app, settings, db, p, fake_claude, "JSON 이 아닌 답")
    assert len(calls) == 3  # 구성안 1 + 검토 2 (형식 오류는 한 번 다시)
    assert p.status == "outlined" and len(p.outline["slides"]) == 8 and p.outline["review"] == {"status": "failed", "changes": []}
    assert "초안을 그대로 두었습니다." in user_client.get(f"/proposals/{p.id}").text


def test_review_off_makes_one_call(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    _start(user_client, p)
    fake_claude.set(mode="ok", result=json.dumps(sample_outline(8), ensure_ascii=False))
    process_next(app.state.session_factory, app.state.runner, settings)
    db.refresh(p)
    assert len(fake_claude.calls()) == 1 and "review" not in p.outline
    assert "AI 검토" not in user_client.get(f"/proposals/{p.id}").text


def test_review_notes_survive_slide_edit_save(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    _start(user_client, p)
    reviewed = sample_outline(8)
    reviewed["changes"] = ["제목을 결론으로 바꿨습니다."]
    _run(app, settings, db, p, fake_claude, json.dumps(reviewed, ensure_ascii=False))
    from app.proposals import save_outline  # noqa: PLC0415
    save_outline(db, p, p.outline["slides"][:-1])
    db.refresh(p)
    assert p.outline["review"]["changes"] == ["제목을 결론으로 바꿨습니다."]


# ---------------------------------------------------------------- 프롬프트·설정

def test_outline_prompt_has_summary_and_why_us():
    prompt = (ROOT / "app" / "ai" / "prompts" / "proposal_outline.md").read_text(encoding="utf-8")
    assert "표지 → **핵심 요약** →" in prompt and "**왜 우리인가**" in prompt
    assert "`과제: …`, `제안: …`, `효과: …`" in prompt and "\"글머리 장 2장까지\" 제한에 세지 않습니다" in prompt
    assert "차별점·실적 자료 확인" in prompt


def test_review_prompt_rules():
    prompt = (ROOT / "app" / "ai" / "prompts" / "proposal_outline_review.md").read_text(encoding="utf-8")
    for needle in ("새 사실을 더하지 마세요", "초안에 없는 숫자가 하나라도 들어가면 이 검토는 버려집니다", "`sources` 와 `needs_check` 는 지우지 마세요",
                   "핵심 요약 장", "제목은 결론", "<<<MATERIAL", "{{draft}}", "{{direction}}", "\"changes\"", "담당자·연락처를 쓰지 마세요"):
        assert needle in prompt, needle


def test_config_proposal_review(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('data_dir = "data"\nsecret_key = "' + "k" * 20 + '"\n', encoding="utf-8")
    assert load_settings(cfg).proposal_review is True
    cfg.write_text(cfg.read_text(encoding="utf-8") + "proposal_review = false\n", encoding="utf-8")
    assert load_settings(cfg).proposal_review is False
    assert "proposal_review = true" in (ROOT / "config.example.toml").read_text(encoding="utf-8")


def test_no_source_note_is_shared():
    assert NO_SOURCE_NOTE == checks.NO_EVIDENCE_NOTE
