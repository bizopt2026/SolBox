"""기록의 유래(어디서 만들어졌는지): 쓰기, 화면 표시, 짝 기록을 지울 때 정리.

유래 열(`origin_type`, `origin_id`)은 솔루션·활동 기록·고객·파트너에 있다. `origin_id` 는 올리기 묶음 번호 또는 리서치 보고서 번호이고
외래 키 제약이 없다 (응용 수준에서만 지킨다). 올리기 묶음·리서치 실행을 지우는 곳은 같은 트랜잭션에서 `clear_dangling` 을 불러야 한다.
커밋하지 않는다."""
from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app import ontology
from app.models import Activity, Customer, Intake, Partner, ResearchReport, Solution, User

ORIGIN_MODELS = (Solution, Activity, Customer, Partner)
MANUAL = (ontology.ORIGIN_MANUAL, None)
is_ai_generated = ontology.is_ai_generated  # "AI 생성" 표기의 기준 (올리기·리서치 유래). 정의와 이름표는 ontology 한 곳


def intake_origin(intake_id: int) -> tuple[str, int]:
    return ontology.ORIGIN_INTAKE, intake_id


def research_origin(report_id: int) -> tuple[str, int]:
    return ontology.ORIGIN_RESEARCH, report_id


def can_open_intake(user: User, intake: Intake) -> bool:
    """올리기 묶음을 열 수 있는 사람: 올린 사람과 관리자."""
    return user.role == ontology.USER_ROLE_ADMIN or intake.created_by_id == user.id


def clear_dangling(db: Session, origin_type: str, ids: list[int]) -> int:
    """짝 기록(묶음·보고서)이 지워질 때 그 번호를 가리키는 유래의 번호만 비운다 (유형은 남겨 "삭제됨" 으로 보인다). 바꾼 행 수."""
    if not ids:
        return 0
    total = 0
    for model in ORIGIN_MODELS:
        result = db.execute(
            update(model).where(model.origin_type == origin_type, model.origin_id.in_(ids)).values(origin_id=None)
            .execution_options(synchronize_session=False)
        )
        total += result.rowcount
    return total


def view(db: Session, user: User, obj) -> dict | None:
    """상세 화면의 "유래:" 줄: `{"label", "href", "ai"}`. 직접 입력·모름은 None. `href` 는 열 수 있을 때만, `ai` 는 "AI 생성" 배지를 붙일지."""
    result = _view(db, user, obj)
    if result is not None:
        result["ai"] = ontology.is_ai_generated(obj)
    return result


def _view(db: Session, user: User, obj) -> dict | None:
    kind, number = obj.origin_type, obj.origin_id
    if kind == ontology.ORIGIN_INTAKE:
        intake = db.get(Intake, number) if number is not None else None
        if intake is None:
            return {"label": "올리기 (묶음 삭제됨)", "href": None}
        if can_open_intake(user, intake):
            return {"label": f"올리기 묶음 {intake.id}번", "href": f"/intake/{intake.id}"}
        # 열 수 없는 사람에게는 링크 없이 누가 올렸는지만 (링크는 빈 목록으로 가므로)
        uploader = db.get(User, intake.created_by_id) if intake.created_by_id else None
        who = (uploader.name or uploader.username) if uploader else ""
        return {"label": f"{who}님이 올린 묶음 {intake.id}번" if who else f"올리기 묶음 {intake.id}번", "href": None}
    if kind == ontology.ORIGIN_RESEARCH:
        report = db.get(ResearchReport, number) if number is not None else None
        if report is None:
            return {"label": "리서치 (조사 삭제됨)", "href": None}
        day = report.run.started_at.date().isoformat()
        what = ontology.REPORT_KINDS.get(report.kind, report.kind)
        if report.kind == "area" and report.area:
            what = f"{ontology.RESEARCH_AREAS.get(report.area, report.area)} {what}"
        return {"label": f"리서치 {day} {what}", "href": f"/research/runs/{report.run_id}"}
    return None
