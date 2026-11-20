"""후속 조치 목록: 남은 일·기한 지남·완료. 완료 표시는 기존 `POST /follow-ups/{id}/toggle` 를 그대로 쓴다."""
from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import follow_ups as fu_lib
from app import relations, ui
from app.db import get_db
from app.main import render
from app.models import Activity, Customer, FollowUp, User
from app.routers._common import PER_PAGE, Page
from app.security import require_user

router = APIRouter(prefix="/follow-ups")
OWNER_ME = "me"


def today() -> date:
    return date.today()


@router.get("")
def list_follow_ups(
    request: Request, status: str = fu_lib.FU_DEFAULT, customer_id: str = "", owner: str = "", q: str = "", page: str = "1", fu_toggled: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    now = today()
    status = status if status in fu_lib.FU_STATUSES else fu_lib.FU_DEFAULT
    scope, chips = [], []
    rest = [(k, v) for k, v in request.query_params.multi_items() if k not in ("page", fu_lib.NOTICE_PARAM)]

    def clear_of(param: str) -> str:
        left = [(k, v) for k, v in rest if k != param]
        return "/follow-ups" + (f"?{urlencode(left)}" if left else "")

    cid = customer_id.strip()
    if cid:
        found = db.get(Customer, int(cid)) if cid.isascii() and cid.isdecimal() and len(cid) <= 18 else None
        # 고객에 연결된 활동 기록에서 나온 모든 후속 조치 (완료한 것 포함): 관계 R5 -> R8 을 따라간다
        ids = [f.id for f in relations.path_query(db, found, ["R5", "R8"])] if found is not None else []
        scope.append(FollowUp.id.in_(ids))
        chips.append({"label": f"고객 '{found.name}'" if found else f"고객 {cid[:20]} (없는 항목)", "clear": clear_of("customer_id")})
    q = q.strip()
    if q:  # 할 일 글에서 찾기 (전체 검색의 "모두 보기"가 여기로 온다)
        from app.search import like_pattern

        scope.append(FollowUp.task.like(like_pattern(q), escape="\\"))
    if owner == OWNER_ME:
        scope.append(FollowUp.owner_user_id == user.id)
        chips.append({"label": "내 담당", "clear": clear_of("owner")})
    # 구분별 건수: 지금의 걸러 보기를 적용한 목록 기준, 질의 한 번
    counts_row = db.execute(select(
        func.coalesce(func.sum(case((fu_lib.status_condition("open", now), 1), else_=0)), 0),
        func.coalesce(func.sum(case((fu_lib.status_condition("overdue", now), 1), else_=0)), 0),
        func.coalesce(func.sum(case((fu_lib.status_condition("done", now), 1), else_=0)), 0),
    ).where(*scope)).one()
    counts = dict(zip(fu_lib.FU_STATUSES, (int(n) for n in counts_row)))
    total = counts[status]
    pages = max(1, -(-total // PER_PAGE))
    try:
        number = int(page)
    except ValueError:
        number = 1
    number = number if 1 <= number <= pages else 1
    stmt = (select(FollowUp, Activity).join(Activity, FollowUp.activity_id == Activity.id)
            .where(fu_lib.status_condition(status, now), *scope))
    if status == "done":
        stmt = stmt.order_by(FollowUp.updated_at.desc(), FollowUp.id.desc())
    else:
        stmt = stmt.order_by(FollowUp.due.is_(None), FollowUp.due, Activity.id, FollowUp.position, FollowUp.id)
    pairs = [(f, a) for f, a in db.execute(stmt.offset((number - 1) * PER_PAGE).limit(PER_PAGE))]
    pg = Page(items=fu_lib.rows(db, pairs, now, with_customers=True), page=number, pages=pages, total=total, per_page=PER_PAGE)
    tabs = ui.seg_tabs(request, "status", list(fu_lib.FU_STATUSES.items()), counts, status, total=total, with_all=False)
    return render(request, "follow_ups/list.html", pg=pg, tabs=tabs, q=q, status=status, chips=chips, filtered=bool(chips) or bool(q), today=now,
                  toggled=fu_lib.toggled_notice(db, fu_toggled) if fu_toggled else None)
