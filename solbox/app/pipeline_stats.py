"""영업기회 파이프라인 숫자 (영업 C): 기간·단계 구간·체류·전환·닫은 일·월별·사유·팀별. 정의는 이 한 곳이고 화면과 AI 해석이 같이 쓴다.

- 수주·실패는 **닫은 일** 단위로 센다: 단계 이력에서 `to_stage` 가 won·lost 인 줄 (팀 업무 보고 `reports.py` 와 같은 기준).
  그 일의 사유는 같은 시각의 필드 이력 `close_reason`, 금액은 그 시각의 금액(`amount_at`).
- 단계에 머문 구간은 단계 이력을 시간순(같은 시각은 번호순)으로 이어 만든다. 만든 줄(`from_stage` 빈 글)이 없으면 첫 구간의 시작을 모른다
  (이력 기능 전) → 체류일에서 빼고 몇 건을 뺐는지 알린다.
- 팀은 지금 담당의 팀이다.
- 영업기회·이력을 한 번씩 읽어 파이썬에서 계산한다 (질의 수는 건수와 무관)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import ontology
from app.models import Opportunity, User

PERIODS = {"90d": "최근 90일", "q": "이번 분기", "lq": "지난 분기", "y": "올해", "12m": "최근 12개월"}
PERIOD_DEFAULT = "90d"
TEAM_NONE = "none"
NO_TEAM = "팀 없음"


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def period_range(code: str, today: date) -> tuple[date, date]:
    """(시작일, 끝일) — 둘 다 포함. 모르는 코드는 기본(최근 90일)."""
    if code == "q":
        return _quarter_start(today), today
    if code == "lq":
        this = _quarter_start(today)
        end = this - timedelta(days=1)
        return _quarter_start(end), end
    if code == "y":
        return date(today.year, 1, 1), today
    if code == "12m":
        try:
            start = today.replace(year=today.year - 1) + timedelta(days=1)
        except ValueError:  # 2월 29일
            start = date(today.year - 1, 3, 1)
        return start, today
    return today - timedelta(days=89), today


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())


@dataclass(frozen=True)
class Segment:
    opp_id: int
    stage: str
    start: datetime | None  # None = 이력 전이라 모름
    end: datetime | None  # None = 지금 그 단계
    next_stage: str | None = None


def _changes(opp: Opportunity) -> list:
    return sorted(opp.stage_changes, key=lambda c: (c.changed_at, c.id))


def segments(opp: Opportunity) -> list[Segment]:
    rows = _changes(opp)
    if not rows:
        return [Segment(opp.id, opp.stage, None, None)]
    out: list[Segment] = []
    if rows[0].from_stage:  # 만든 줄이 없다: 첫 단계의 시작을 모른다
        cur_stage, cur_start = rows[0].from_stage, None
    else:
        cur_stage, cur_start = rows[0].to_stage, rows[0].changed_at
        rows = rows[1:]
    for c in rows:
        out.append(Segment(opp.id, cur_stage, cur_start, c.changed_at, c.to_stage))
        cur_stage, cur_start = c.to_stage, c.changed_at
    out.append(Segment(opp.id, cur_stage, cur_start, None))
    return out


@dataclass(frozen=True)
class Close:
    opp_id: int
    stage: str  # won / lost
    at: datetime
    reason: str
    amount: int | None
    days: int | None  # 만든 날부터 닫은 날까지


def amount_at(opp: Opportunity, at: datetime) -> int | None:
    """그 시각의 예상 금액: 그때까지의 마지막 금액 이력, 없으면 그 뒤 첫 이력의 옛 값, 이력이 없으면 지금 값."""
    rows = sorted((f for f in opp.field_changes if f.field == "amount"), key=lambda f: (f.changed_at, f.id))
    before = [f for f in rows if f.changed_at <= at]
    raw = before[-1].new_value if before else (rows[0].old_value if rows else None)
    if raw is None:
        return opp.amount
    return int(raw) if raw.isdigit() else None


def _reason_at(opp: Opportunity, at: datetime, stage: str) -> str:
    rows = [f for f in opp.field_changes if f.field == "close_reason" and f.changed_at == at]
    code = rows[-1].new_value if rows else (opp.close_reason if opp.stage == stage else "")
    return ontology.close_reasons(stage).get(code, "")


def _created(opp: Opportunity) -> datetime | None:
    rows = _changes(opp)
    if rows and not rows[0].from_stage:
        return rows[0].changed_at
    return opp.created_at


def closes(opp: Opportunity) -> list[Close]:
    made = _created(opp)
    return [Close(opp.id, c.to_stage, c.changed_at, _reason_at(opp, c.changed_at, c.to_stage), amount_at(opp, c.changed_at),
                  (c.changed_at - made).days if made else None)
            for c in _changes(opp) if c.to_stage in ontology.OPPORTUNITY_CLOSED_STAGES]


def _avg(values) -> float | None:
    values = list(values)
    return round(sum(values) / len(values), 1) if values else None


def _days(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 86400


def _stage_rows(opps, lo, hi, today: date) -> tuple[list[dict], int]:
    flow = ontology.OPPORTUNITY_OPEN_STAGES
    segs = [s for o in opps for s in segments(o)]
    excluded = sum(1 for s in segs if s.stage in flow and s.start is None and s.end is not None and lo <= s.end < hi)
    rows = []
    for i, stage in enumerate(flow):
        ended = [s for s in segs if s.stage == stage and s.end is not None and lo <= s.end < hi]
        known = [_days(s.start, s.end) for s in ended if s.start is not None]
        later = (*flow[i + 1:], ontology.OPPORTUNITY_STAGE_WON)
        current = [(today - s.start.date()).days for s in segs if s.stage == stage and s.end is None and s.start is not None]
        n = len(ended)
        rows.append({"stage": stage, "name": ontology.OPPORTUNITY_STAGES[stage], "n": n,
                     "avg": _avg(known), "median": round(median(known), 1) if known else None,
                     "current_n": len(current), "current_avg": _avg(current),
                     "forward": (sum(1 for s in ended if s.next_stage in later) / n) if n else None,
                     "lost": (sum(1 for s in ended if s.next_stage == ontology.OPPORTUNITY_STAGE_LOST) / n) if n else None})
    return rows, excluded


def _months(start: date, end: date) -> list[tuple[date, date]]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        out.append((cur, nxt))
        cur = nxt
    return out


def _tiles(cl: list[Close]) -> dict:
    won = [c for c in cl if c.stage == ontology.OPPORTUNITY_STAGE_WON]
    lost = [c for c in cl if c.stage == ontology.OPPORTUNITY_STAGE_LOST]
    days = [c.days for c in won if c.days is not None]
    return {"won": len(won), "lost": len(lost), "won_amount": sum(c.amount or 0 for c in won),
            "win_rate": (len(won) / (len(won) + len(lost))) if (won or lost) else None,
            "avg_win_days": round(sum(days) / len(days)) if days else None}


def _team_of(opp: Opportunity):
    return opp.owner.team if opp.owner is not None else None


def _matches(opp: Opportunity, team) -> bool:
    if team is None:
        return True
    t = _team_of(opp)
    return t is None if team == TEAM_NONE else (t is not None and t.id == team)


def compute(db: Session, period: str, team, today: date) -> dict:
    """화면·AI 해석이 쓰는 숫자 전부. team = None(전체) / 팀 번호 / TEAM_NONE."""
    from app import opportunities as opps_lib, teams as teams_lib

    period = period if period in PERIODS else PERIOD_DEFAULT
    start, end = period_range(period, today)
    lo, hi = _bounds(start, end)
    every = list(db.scalars(select(Opportunity).options(
        selectinload(Opportunity.stage_changes), selectinload(Opportunity.field_changes), selectinload(Opportunity.owner).selectinload(User.team))))
    chosen = [o for o in every if _matches(o, team)]
    cl = [c for o in chosen for c in closes(o) if lo <= c.at < hi]
    stages, excluded = _stage_rows(chosen, lo, hi, today)
    months = []
    for m_start, m_end in _months(start, end):
        a, b = _bounds(m_start, m_end - timedelta(days=1))
        mc = [c for c in cl if a <= c.at < b]
        label = f"{m_start.month}월" if start.year == end.year else f"{m_start.year % 100}년 {m_start.month}월"
        won = [c for c in mc if c.stage == ontology.OPPORTUNITY_STAGE_WON]
        months.append({"label": label, "won": len(won), "won_amount": sum(c.amount or 0 for c in won),
                       "lost": sum(1 for c in mc if c.stage == ontology.OPPORTUNITY_STAGE_LOST)})
    reasons = {}
    for stage, key in ((ontology.OPPORTUNITY_STAGE_WON, "won"), (ontology.OPPORTUNITY_STAGE_LOST, "lost")):
        counts: dict[str, int] = {}
        for c in cl:
            if c.stage == stage:
                counts[c.reason or "사유 없음"] = counts.get(c.reason or "사유 없음", 0) + 1
        reasons[key] = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    # 팀별 비교 (전체를 볼 때)
    team_rows = []
    if team is None:
        open_all = [o for o in every if o.stage in ontology.OPPORTUNITY_OPEN_STAGES]
        touches = opps_lib.last_touches(db, open_all)
        groups = [(t.name, t.id) for t in teams_lib.ordered(db)]
        if any(_team_of(o) is None for o in every):
            groups.append((NO_TEAM, TEAM_NONE))
        for name, key in groups:
            mine = [o for o in every if _matches(o, key)]
            if not mine:
                continue
            opened = [o for o in mine if o.stage in ontology.OPPORTUNITY_OPEN_STAGES]
            tc = [c for o in mine for c in closes(o) if lo <= c.at < hi]
            st, _ = _stage_rows(mine, lo, hi, today)
            tl = _tiles(tc)
            team_rows.append({"name": name, "key": key, "open_n": len(opened), "open_amount": sum(o.amount or 0 for o in opened),
                              "won": tl["won"], "lost": tl["lost"], "win_rate": tl["win_rate"],
                              "dwell": {r["stage"]: r["avg"] for r in st},
                              "stale": sum(1 for o in opened if opps_lib.stale_reasons(o, today, touches.get(o.id)))})
    result = {"period": period, "period_label": PERIODS[period], "range": (start, end), "tiles": _tiles(cl), "stages": stages,
              "excluded": excluded, "months": months, "reasons": reasons, "teams": team_rows}
    result["numbers_hash"] = hashlib.sha256(json.dumps(
        {k: result[k] for k in ("tiles", "stages", "excluded", "months", "reasons", "teams")}, default=str, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()[:16]
    return result
