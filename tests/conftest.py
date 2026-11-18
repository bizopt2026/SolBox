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
    outline["slides"][2]["needs_check"] = ["절감률 수치"]
    outline["slides"][2]["sources"] = ["solutions/1/card.md"]
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
