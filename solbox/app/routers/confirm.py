"""삭제 확인 단계 (스크립트 없이도): 지우기 버튼이 가리키는 주소(`…/delete`, `…/discard`)를 GET 하면 "정말 지울까요?" 화면이 나오고,
화면의 버튼이 같은 주소로 POST 한다 (기존 POST 라우터는 그대로). 대상마다 따로 만들지 않고 아래 표(`SPECS`) 한 줄씩이다.
- 이름·권한·막는 조건은 각 삭제 라우터의 규칙을 그대로 쓴다 (본인이 만든 것 또는 관리자 / 관리자 전용).
- "지우면 이렇게 됩니다" 글은 `ontology.DELETE_EFFECTS`. 스크립트가 있으면 버튼이 이 화면 대신 그 자리에서 되묻는다 (app.js)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import impact as impact_lib, ontology, opportunities as opps, solution_fields as sfields, ui
from app.db import get_db
from app.main import render
from app.models import BM, Activity, Contact, Customer, Intake, Opportunity, Partner, Project, Proposal, Report, ResearchRun, ResearchTopic, Solution, SolutionField, SolutionFile, Team, User, Strategy, Initiative, WorkspaceFile, AIThread
from app.routers._common import get_deletable_or_404, get_or_404
from app.security import ensure_can_delete, require_admin, require_user

router = APIRouter()


@dataclass(frozen=True)
class Spec:
    path: str  # 삭제 POST 라우터와 같은 주소 (GET 으로 열면 확인 화면)
    effects: str  # ontology.DELETE_EFFECTS 의 키
    load: Callable[[Session, User, dict], object]  # 대상 (없으면 404, 권한이 없으면 403)
    name: Callable[[object], str]
    back: Callable[[object], str]  # 취소하면 갈 곳
    crumbs: Callable[[object, str], list]
    kind_label: str  # "고객" 처럼 "고객 'OO'" 에 쓰는 이름
    verb: str = "삭제"
    blocker: Callable[[Session, object], str | None] | None = None  # 지금은 지울 수 없는 이유 (있으면 버튼을 주지 않는다)


def _num(raw) -> int:
    """주소의 번호 글자를 정수로. 숫자가 아니면(/customers/abc/delete) 없는 항목으로 404."""
    text = str(raw)
    if not (text.isascii() and text.isdecimal() and len(text) <= 18):
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    return int(text)


def _owned(model):
    return lambda db, user, p: get_deletable_or_404(db, model, _num(p["obj_id"]), user)


def _load_need(db, user, p):
    from app.routers.needs import load_deletable

    return load_deletable(db, user, _num(p["obj_id"]))


def _bm_crumb(obj, name):
    from app import bm_plan  # 기존 BM · 신규 BM 경로 (사업기획 2026-11-27)

    return bm_plan.crumbs(obj, obj.kind, (name, f"/bms/{obj.id}"), ("삭제", None))


def _crumb(code):
    return lambda obj, name: ui.crumbs(code, name, ontology.TYPES[code].url(obj.id), "삭제")


def _customer_blocker(db, c):
    from app.routers.customers import HAS_OPPORTUNITIES, HAS_PROJECTS, HAS_PROPOSALS

    return HAS_PROJECTS if c.projects else HAS_PROPOSALS if c.proposals else HAS_OPPORTUNITIES if c.opportunities else None


def _load_file(db, user, p):
    f = get_or_404(db, SolutionFile, _num(p["obj_id"]))
    ensure_can_delete(user, f)
    return f


def _load_workspace_file(db, user, p):
    f = get_or_404(db, WorkspaceFile, _num(p["obj_id"]))
    ensure_can_delete(user, f)
    return f


def _workspace_back(f) -> str:
    return f"/strategies/{f.strategy_id}" if f.strategy_id is not None else f"/initiatives/{f.initiative_id}"


def _workspace_crumbs(f, name):
    owner = f.owner
    code = "strategy" if f.strategy_id is not None else "initiative"
    return ui.crumbs(code, owner.title if owner else "", _workspace_back(f), "자료 삭제")


def _load_intake(db, user, p):
    from app.routers.intake import get_intake

    return get_intake(db, user, _num(p["obj_id"]))


def _load_admin(model, message):
    def load(db, user, p):
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="관리자만 할 수 있습니다.")
        obj = db.get(model, _num(p["obj_id"]))
        if obj is None:
            raise HTTPException(status_code=404, detail=message)
        return obj
    return load


def _load_slide(db, user, p):
    proposal = get_or_404(db, Proposal, _num(p["obj_id"]))
    index = _num(p["index"])
    slides = (proposal.outline or {}).get("slides") or []
    if not 0 <= index < len(slides):
        raise HTTPException(status_code=404, detail="없는 슬라이드입니다.")
    return proposal, index, slides[index]


def _slide_blocker(db, obj):
    return "표지는 지울 수 없습니다." if obj[1] == 0 else None


def _run_blocker(db, run):
    from app.routers.research import RUNNING_DELETE

    return RUNNING_DELETE if run.status == "running" else None


def _intake_blocker(db, intake):
    return "저장 중이거나 저장이 끝난 묶음은 버릴 수 없습니다." if intake.status in ("applying", "applied") else None


SPECS: tuple[Spec, ...] = (
    Spec("/solutions/{obj_id}/delete", "solution", _owned(Solution), lambda o: o.name, lambda o: f"/solutions/{o.id}", _crumb("solution"), "솔루션"),
    Spec("/partners/{obj_id}/delete", "partner", _owned(Partner), lambda o: o.name, lambda o: f"/partners/{o.id}", _crumb("partner"), "파트너"),
    Spec("/customers/{obj_id}/delete", "customer", _owned(Customer), lambda o: o.name, lambda o: f"/customers/{o.id}", _crumb("customer"), "고객",
         blocker=_customer_blocker),
    Spec("/projects/{obj_id}/delete", "project", _owned(Project), lambda o: o.name, lambda o: f"/projects/{o.id}", _crumb("project"), "프로젝트"),
    Spec("/needs/{obj_id}/delete", "customer_need", _load_need, lambda o: ui.first_line(o.content, 60), lambda o: f"/needs/{o.id}", _crumb("customer_need"), "고객 니즈"),
    Spec("/bms/{obj_id}/delete", "bm", _owned(BM), lambda o: o.name, lambda o: f"/bms/{o.id}", _bm_crumb, "BM",
         blocker=lambda db, o: opps.bm_delete_problem(db, o)),
    Spec("/strategies/{obj_id}/delete", "strategy", _owned(Strategy), lambda o: o.title, lambda o: f"/strategies/{o.id}", _crumb("strategy"), "사업 방향"),
    Spec("/initiatives/{obj_id}/delete", "initiative", _owned(Initiative), lambda o: o.title, lambda o: f"/initiatives/{o.id}", _crumb("initiative"), "과제"),
    Spec("/contacts/{obj_id}/delete", "contact", _owned(Contact), lambda o: o.name, lambda o: f"/contacts/{o.id}",
         lambda o, name: ui.crumbs(o.company_type, o.company.name, ontology.detail_url(o.company_type, o.company.id), "담당자 삭제"), "담당자"),
    Spec("/opportunities/{obj_id}/delete", "opportunity", _owned(Opportunity), lambda o: o.name, lambda o: f"/opportunities/{o.id}", _crumb("opportunity"), "영업기회"),
    Spec("/activities/{obj_id}/delete", "activity", _owned(Activity), lambda o: o.title or "(제목 없음)", lambda o: f"/activities/{o.id}", _crumb("activity"), "활동 기록"),
    Spec("/proposals/{obj_id}/delete", "proposal", _owned(Proposal), lambda o: _proposal_name(o), lambda o: f"/proposals/{o.id}", _crumb("proposal"), "제안 건"),
    Spec("/reports/{obj_id}/delete", "report", lambda db, user, p: get_or_404(db, Report, _num(p["obj_id"])), lambda o: _report_name(o), lambda o: f"/reports/{o.id}",
         _crumb("report"), "업무 보고"),  # 팀원 누구나 지울 수 있다 (설계 §5.4b)
    Spec("/files/{obj_id}/delete", "solution_file", _load_file, lambda o: o.original_name, lambda o: f"/solutions/{o.solution_id}",
         lambda o, name: ui.crumbs("solution", o.solution.name, f"/solutions/{o.solution_id}", "자료 삭제"), "자료"),
    Spec("/cowork/threads/{obj_id}/delete", "cowork_thread", _owned(AIThread), lambda o: o.title or "대화",
         lambda o: ontology.TYPES[o.target_type].url(o.target_id) + "#cowork",
         lambda o, name: [(ontology.nav_group_label(ontology.TYPES[o.target_type].list_url.strip("/")), None), (ontology.label(o.target_type), ontology.TYPES[o.target_type].list_url), (name, None), ("삭제", None)], "AI 동료 대화"),
    Spec("/workspace-files/{obj_id}/delete", "workspace_file", _load_workspace_file, lambda o: o.original_name, _workspace_back, _workspace_crumbs, "자료"),
    Spec("/intake/{obj_id}/discard", "intake_discard", _load_intake, lambda o: f"올리기 묶음 {o.id}번", lambda o: f"/intake/{o.id}",
         lambda o, name: [(ontology.nav_group_label("intake"), None), (ontology.label("intake"), "/intake"), (name, f"/intake/{o.id}"), ("버리기", None)], "올리기 묶음", verb="버리기", blocker=_intake_blocker),
    Spec("/research/runs/{obj_id}/delete", "research_run", _load_admin(ResearchRun, "조사 실행을 찾을 수 없습니다."),
         lambda o: f"{ui.short_date(o.started_at, date.today())} 조사 결과", lambda o: f"/research/runs/{o.id}",
         lambda o, name: ui.crumbs("research_run", name, f"/research/runs/{o.id}", "삭제"), "조사 결과", blocker=_run_blocker),
    Spec("/admin/research/topics/{obj_id}/delete", "research_topic", _load_admin(ResearchTopic, "주제를 찾을 수 없습니다."),
         lambda o: o.name, lambda o: "/admin/research", lambda o, name: [("관리", None), ("리서치 설정", "/admin/research"), (name, None), ("삭제", None)], "리서치 주제"),
    Spec("/admin/teams/{obj_id}/delete", "team", _load_admin(Team, "팀을 찾을 수 없습니다."),
         lambda o: o.name, lambda o: "/admin/users", lambda o, name: [("관리", None), ("사용자", "/admin/users"), (name, None), ("삭제", None)], "팀"),
    Spec("/admin/solution-fields/{obj_id}/delete", "solution_field", _load_admin(SolutionField, "분야를 찾을 수 없습니다."),
         lambda o: o.name, lambda o: "/admin/solution-fields", lambda o, name: [("관리", None), ("솔루션 분야", "/admin/solution-fields"), (name, None), ("삭제", None)],
         "솔루션 분야", blocker=lambda db, o: sfields.delete_problem(db, o)),
    Spec("/proposals/{obj_id}/outline/slides/{index}/delete", "outline_slide", _load_slide,
         lambda o: (o[2].get("title") or f"{o[1] + 1}번째 슬라이드"), lambda o: f"/proposals/{o[0].id}/outline#slide-{o[1]}",
         lambda o, name: ui.crumbs("proposal", _proposal_name(o[0]), f"/proposals/{o[0].id}", "슬라이드 삭제"), "슬라이드", blocker=_slide_blocker),
)


def _report_name(r) -> str:
    from app.relations import report_label

    return report_label(r)


def _proposal_name(p) -> str:
    from app.proposals import display_title

    return display_title(p)


def _make(spec: Spec):
    def page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
        obj = spec.load(db, user, request.path_params)
        name = spec.name(obj)
        impact = impact_lib.sentences(db, spec.effects, obj) if spec.effects in ontology.DELETE_IMPACT and not (spec.blocker and spec.blocker(db, obj)) else []
        return render(
            request, "confirm.html", impact=impact, obj_name=name, kind=spec.kind_label, verb=spec.verb, action=request.url.path, back=spec.back(obj),
            effects=ontology.DELETE_EFFECTS[spec.effects], problem=spec.blocker(db, obj) if spec.blocker else None,
            crumbs=spec.crumbs(obj, name),
        )
    page.__name__ = f"confirm_{spec.effects}"
    return page


for _spec in SPECS:
    router.add_api_route(_spec.path, _make(_spec), methods=["GET"], include_in_schema=False)
