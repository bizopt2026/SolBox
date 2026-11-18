import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

FAKE_CLAUDE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        secret_key="test",
        ai_worker_enabled=False,
        scheduler_enabled=False,
        claude_cmd=[sys.executable, str(FAKE_CLAUDE)],
    )


@pytest.fixture
def app(settings):
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def db(app):
    session = app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(app):
    return TestClient(app)


def make_user(db, username, role="user", password="pw1234", name=None):
    from app.models import User
    from app.security import hash_password

    user = User(
        username=username,
        name=name or username,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    return user


def _logged_in_client(app, db, username, role):
    make_user(db, username, role=role)
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": "pw1234"}, follow_redirects=False)
    assert r.status_code == 303
    return c


@pytest.fixture
def user_client(app, db):
    return _logged_in_client(app, db, "kim", "user")


@pytest.fixture
def admin_client(app, db):
    return _logged_in_client(app, db, "admin", "admin")


class FakeClaude:
    """tests/fake_claude.py 를 조종하는 제어 파일 도우미."""

    def __init__(self, control: Path):
        self.control = control
        self.set()

    def set(self, **keys):
        self.control.write_text(json.dumps(keys), encoding="utf-8")

    def calls(self) -> list[dict]:
        log = Path(str(self.control) + ".calls.jsonl")
        if not log.exists():
            return []
        lines = log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    control = tmp_path / "fake_claude_control.json"
    monkeypatch.setenv("FAKE_CLAUDE_CONTROL", str(control))
    return FakeClaude(control)


def make_proposal_world(db, settings):
    """제안 건 시험용 세계: 고객·사업, 박스 등록 솔루션 둘과 수요조사 솔루션 하나, 지식 사본, 제안 건."""
    from app.knowledge import KnowledgeMirror
    from app.models import Business, Customer, Proposal, Solution

    c = Customer(name="한빛병원")
    db.add(c)
    db.flush()
    b = Business(name="설비 유지보수", customer_id=c.id)
    s1 = Solution(name="AI 에너지 절감", stage="boxed", summary="전력 절감")
    s2 = Solution(name="스마트 조명", stage="boxed", summary="조명")
    s3 = Solution(name="안전 관제", stage="survey", summary="안전")
    db.add_all([b, s1, s2, s3])
    db.flush()
    p = Proposal(customer_id=c.id, business_id=b.id, request_memo="에너지 절감 쪽으로")
    db.add(p)
    db.commit()
    mirror = KnowledgeMirror(settings.knowledge_dir)
    for obj in (c, b, s1, s2, s3):
        mirror.sync(obj)
    return p, c, b, s1, s2, s3


def refining_proposal(db, settings):
    """방향 둘을 저장하고 첫 번째(전력비 절감 패키지)를 선택해 구체화 단계가 된 제안 건."""
    p, c, b, s1, s2, s3 = make_proposal_world(db, settings)
    p.directions = [
        {"title": "전력비 절감 패키지", "summary": "", "effects": [], "needs_check": [], "rationale": [],
         "solutions": [{"solution_id": s1.id, "name": s1.name, "role": ""}, {"solution_id": s2.id, "name": s2.name, "role": ""}]},
        {"title": "조명 단독 교체", "summary": "", "effects": [], "needs_check": [], "rationale": [],
         "solutions": [{"solution_id": s2.id, "name": s2.name, "role": ""}]},
    ]
    p.selected_index = 0
    p.status = "refining"
    p.solutions = [s1, s2]
    db.commit()
    return p


def outlined_proposal(db, settings):
    """구성안(8장)이 저장된 제안 건. 2번 장에 확인 필요 항목과 출처가 있다."""
    from datetime import datetime

    from app.ai.proposal_tasks import Outline
    from app.proposals import clean_outline

    p = refining_proposal(db, settings)
    outline = clean_outline(Outline.model_validate(sample_outline(8)), settings.knowledge_dir)
    for slide in outline["slides"][1:-1]:  # 내용 장은 모두 출처가 있고, 확인 필요는 2번 장에만 있다
        slide["needs_check"] = []
        slide["sources"] = ["solutions/1/card.md"]
    outline["slides"][2]["needs_check"] = ["절감률 수치"]
    p.outline = outline
    p.status = "outlined"
    p.outline_updated_at = datetime.now()
    db.commit()
    return p


def sample_outline(n=8):
    """AI 가 돌려주는 모양의 구성안 dict (Outline 으로 검증된다). 첫 장 cover, 마지막 장 closing, n >= 6 이면 여섯 종류를 모두 쓴다."""
    middle = [
        {"kind": "bullets", "title": "고객 현황과 과제", "key_message": "전력비가 부담입니다.", "bullets": ["전력비 증가", "노후 설비"]},
        {"kind": "two_column", "title": "제안 개요", "left_title": "현재", "left": ["수동 운영"], "right_title": "제안", "right": ["자동 제어"]},
        {"kind": "table", "title": "솔루션 구성", "columns": ["솔루션", "역할"], "rows": [["에너지 관리", "절감"], ["조명 제어", "교체"]]},
        {"kind": "steps", "title": "추진 일정", "steps": [{"name": "착수", "detail": "1개월"}, {"name": "구축", "detail": "3개월"}]},
    ]
    k = 0
    while len(middle) < n - 2:
        k += 1
        middle.append({"kind": "bullets", "title": f"상세 {k}", "key_message": "핵심입니다.", "bullets": [f"내용 {k}"]})
    slides = [{"kind": "cover", "title": "전력비 절감 제안", "subtitle": "고객사 귀중"}] + middle[: max(n - 2, 0)] + [{"kind": "closing", "title": "감사합니다"}]
    return {"title": "전력비 절감 제안", "slides": slides[:n]}


def make_topics(db):
    """리서치 시험용 주제: 기술 둘, 정책 하나 (시장 없음)."""
    from app.models import ResearchTopic

    db.add_all([
        ResearchTopic(name="건물 에너지 관리", keywords="BEMS, AI 공조", area="tech"),
        ResearchTopic(name="설비 예지보전", keywords="", area="tech"),
        ResearchTopic(name="전기요금 정책", keywords="", area="policy"),
    ])
    db.commit()


def make_research_world(db, settings):
    """제안 작업 시험용 세계: 주제, boxed 솔루션 하나, 고객과 최근 활동(수요 단서), 그 지식 사본."""
    from datetime import date, timedelta
    from types import SimpleNamespace

    from app.knowledge import KnowledgeMirror
    from app.models import Activity, Customer, Solution

    make_topics(db)
    s1 = Solution(name="AI 공조 최적제어", stage="boxed", summary="공조 전력 절감")
    customer = Customer(name="한빛병원")
    db.add_all([s1, customer])
    db.flush()
    activity = Activity(kind="meeting", date=date.today() - timedelta(days=10), title="한빛병원 미팅", raw_text="전기요금 부담 논의",
                        demand_signals=[{"customer": "한빛병원", "content": "전기요금 절감 요청"}])
    activity.solutions = [s1]
    activity.customers = [customer]
    db.add(activity)
    db.commit()
    mirror = KnowledgeMirror(settings.knowledge_dir)
    for obj in (s1, customer, activity):
        mirror.sync(obj)
    return SimpleNamespace(s1=s1, customer=customer, activity=activity)


def finished_run(db, settings):
    """끝난(partial) 조사 실행: 기술 분야 보고서(done), 정책 분야 보고서(failed), 제안 보고서(done)와 지식 사본."""
    from datetime import datetime

    from app.knowledge import KnowledgeMirror
    from app.models import ResearchReport, ResearchRun

    world = make_research_world(db, settings)
    run = ResearchRun(trigger="manual", status="partial", finished_at=datetime.now())
    db.add(run)
    db.flush()
    tech = ResearchReport(
        run_id=run.id, kind="area", area="tech", status="done", title="기술 동향 보고",
        trends=[
            {"headline": "AI 공조 제어 확산", "summary": "병원 적용 증가",
             "sources": [{"title": "기사", "url": "https://example.com/a"}], "needs_check": []},
            {"headline": "출처 없는 동향", "summary": "", "sources": [], "needs_check": ["출처 링크 없음, 확인 필요"]},
        ],
        insights=["설비 운영 고객에 제안 기회"],
    )
    policy = ResearchReport(run_id=run.id, kind="area", area="policy", status="failed", error="조사 결과가 비어 있습니다.")
    suggest = ResearchReport(
        run_id=run.id, kind="suggest", status="done", title="발굴 방향 제안",
        suggestions=[
            {"kind": "new", "title": "수요반응(DR) 솔루션 발굴", "detail": "DR 사업자 탐색", "reason": "전기요금 정책 변화",
             "solution_ids": [], "sources": ["activities/2026/1.md"]},
            {"kind": "push", "title": "AI 공조를 병원 고객에 우선 제안", "detail": "병원 중심", "reason": "병원 수요",
             "solution_ids": [world.s1.id], "sources": []},
        ],
    )
    db.add_all([tech, policy, suggest])
    db.commit()
    mirror = KnowledgeMirror(settings.knowledge_dir)
    for r in (tech, suggest):
        mirror.sync(r)
    return run
