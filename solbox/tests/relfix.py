"""단위 5 시험용 세계: 파트너·고객·솔루션·BM·영업기회·프로젝트·활동 기록·제안 건·리서치 제안·올리기 묶음이 서로 이어진 작은 현실 자료.

`build(db)` 는 이름 붙은 기록들을 SimpleNamespace 로 돌려준다 (예: `w.c_hanbit`). 지식 사본은 만들지 않는다 (호출자가 필요하면 `rebuild_all`).
화면 확인용 복사본에도 같은 세계를 넣는다 (scratch 의 seed 스크립트)."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from app.models import (
    BM, Activity, Customer, CustomerNeed, FollowUp, Intake, IntakeFile, Opportunity, Partner, Project, Proposal, ResearchReport, ResearchRun,
    Solution, SolutionFile, User,
)


def _fu(task, done=False, due=None, owner="", pos=0, owner_user=None):
    return FollowUp(position=pos, task=task, owner=owner, owner_user_id=owner_user.id if owner_user else None, due=date.fromisoformat(due) if due else None, done=done)


def _need(content, customer=None, name="", pos=0, opportunity=None):
    return CustomerNeed(position=pos, customer_name=name or (customer.name if customer else ""), customer_id=customer.id if customer else None,
                        content=content, opportunity_id=opportunity.id if opportunity else None)


def build(db, owner: User | None = None) -> SimpleNamespace:
    """세계를 만든다. owner 가 있으면 올리기 묶음의 작성자가 된다."""
    w = SimpleNamespace()
    # 고객·파트너 (그린에너지테크 파트너와 그린에너지 고객은 같은 회사)
    w.c_hanbit, w.c_daesung, w.c_green, w.c_mirae = (Customer(name=n) for n in ("한빛병원", "대성물류센터", "그린에너지", "미래제조"))
    w.p_green, w.p_sense = Partner(name="그린에너지테크"), Partner(name="센스웍스")
    db.add_all([w.c_hanbit, w.c_daesung, w.c_green, w.c_mirae, w.p_green, w.p_sense])
    db.flush()
    w.p_green.customer_id = w.c_green.id
    # 솔루션
    w.s_hvac = Solution(name="AI 공조 최적제어", stage="boxed", partner_id=w.p_green.id)
    w.s_sensor = Solution(name="설비 예지보전 센서", stage="survey", partner_id=w.p_sense.id)
    w.s_solar = Solution(name="태양광 자가소비 진단", stage="tapping", partner_id=w.p_green.id)
    w.s_light = Solution(name="스마트 조명 제어", stage="boxed")
    w.s_cold = Solution(name="냉동창고 온도 모니터링", stage="boxed", partner_id=w.p_sense.id)
    db.add_all([w.s_hvac, w.s_sensor, w.s_solar, w.s_light, w.s_cold])
    # BM·프로젝트·영업기회
    w.bm_energy, w.bm_om = BM(name="에너지 절감 구독형"), BM(name="설비 O&M")
    db.add_all([w.bm_energy, w.bm_om])
    db.flush()
    w.bm_energy.solutions = [w.s_hvac, w.s_solar, w.s_light]
    w.bm_om.solutions = [w.s_sensor]
    w.pr_hanbit = Project(name="한빛병원 설비 유지보수", customer_id=w.c_hanbit.id, bm_id=w.bm_om.id, status="진행")
    w.pr_daesung = Project(name="대성 냉동창고 개선", customer_id=w.c_daesung.id, bm_id=w.bm_energy.id)
    w.pr_green = Project(name="그린 사옥 에너지 진단", customer_id=w.c_green.id)
    db.add_all([w.pr_hanbit, w.pr_daesung, w.pr_green])
    db.flush()
    w.o_hanbit = Opportunity(name="한빛 전력 절감", customer_id=w.c_hanbit.id, stage="proposing", bm_id=w.bm_energy.id)
    w.o_daesung = Opportunity(name="대성 온도 관제", customer_id=w.c_daesung.id, stage="won", project_id=w.pr_daesung.id)
    w.o_mirae = Opportunity(name="미래제조 예지보전", customer_id=w.c_mirae.id, stage="discovery")
    db.add_all([w.o_hanbit, w.o_daesung, w.o_mirae])
    db.flush()
    w.o_hanbit.solutions = [w.s_hvac, w.s_light]
    w.o_daesung.solutions = [w.s_cold]
    w.o_mirae.solutions = [w.s_sensor]
    # 올리기 묶음 (A3·A4 와 조명 솔루션이 여기서 만들어졌다)
    w.intake = Intake(note="11월 미팅 자료", status="applied", created_by_id=owner.id if owner else None, applied_at=datetime(2026, 11, 15, 9, 0))
    db.add(w.intake)
    db.flush()
    w.s_light.origin_type, w.s_light.origin_id = "intake", w.intake.id
    w.file = SolutionFile(solution_id=w.s_hvac.id, original_name="소개서.pdf", stored_name="a.pdf", kind="intro")
    db.add(w.file)
    w.c_hanbit.created_by_id = owner.id if owner else None
    # 활동 기록
    w.a_kick = Activity(kind="meeting", date=date(2026, 11, 3), title="한빛 킥오프", raw_text="r", origin_type="manual",
                        follow_ups=[_fu("견적 보내기", due="2026-11-25", owner="kim", owner_user=owner), _fu("자료 공유", done=True, pos=1)])
    w.a_daesung = Activity(kind="tapping", date=date(2026, 11, 10), title="대성 태핑", raw_text="r", follow_ups=[_fu("현장 방문 일정", due="2026-11-24")])
    w.a_daesung2 = Activity(kind="meeting", date=date(2026, 11, 11), title="대성 후속 회의", raw_text="r",
                            follow_ups=[_fu("계약 검토", due="2026-11-30"), _fu("회의록 정리", done=True, pos=1)])
    w.a_mirae = Activity(kind="survey", date=date(2026, 11, 12), title="미래제조 수요조사", raw_text="r", origin_type="intake", origin_id=w.intake.id,
                         follow_ups=[_fu("설비 목록 요청")])
    w.a_hanbit2 = Activity(kind="meeting", date=date(2026, 11, 14), title="한빛 후속", raw_text="r", origin_type="intake", origin_id=w.intake.id,
                           follow_ups=[_fu("제안서 일정 잡기", due="2026-11-27")])
    w.a_green = Activity(kind="partner", date=None, title="그린 파트너 미팅", raw_text="r")
    db.add_all([w.a_kick, w.a_daesung, w.a_daesung2, w.a_mirae, w.a_hanbit2, w.a_green])
    db.flush()
    w.a_kick.solutions, w.a_kick.customers, w.a_kick.partners, w.a_kick.projects = [w.s_hvac, w.s_light], [w.c_hanbit], [w.p_green], [w.pr_hanbit]
    w.a_daesung.solutions, w.a_daesung.customers, w.a_daesung.partners = [w.s_cold, w.s_sensor], [w.c_daesung], [w.p_sense]
    w.a_daesung.projects, w.a_daesung.opportunities = [w.pr_daesung], [w.o_daesung]
    w.a_daesung2.customers = [w.c_daesung]
    w.a_mirae.solutions, w.a_mirae.customers = [w.s_sensor], [w.c_mirae]
    w.a_hanbit2.solutions, w.a_hanbit2.customers, w.a_hanbit2.projects = [w.s_hvac], [w.c_hanbit], [w.pr_hanbit]
    w.a_green.partners, w.a_green.customers = [w.p_green], [w.c_green]
    # 고객 니즈 (영업기회로 묶인 것과 아닌 것)
    w.a_kick.needs = [_need("전기요금 절감 방안이 필요하다", w.c_hanbit, opportunity=w.o_hanbit)]
    w.a_daesung.needs = [_need("냉동창고 온도 이탈 알림", w.c_daesung, opportunity=w.o_daesung), _need("보관 로그 자동화", w.c_daesung, pos=1)]
    w.a_mirae.needs = [_need("설비 고장 예측", w.c_mirae, opportunity=w.o_mirae)]
    w.a_hanbit2.needs = [_need("야간 조명 전력 줄이기", w.c_hanbit)]
    w.ifile = IntakeFile(intake_id=w.intake.id, original_name="미팅.txt", stored_name="m.txt", kind="document",
                         target_type="activity", target_id=w.a_mirae.id)
    db.add(w.ifile)
    # 제안 건
    w.pro_hanbit = Proposal(title="한빛 에너지 절감 제안", customer_id=w.c_hanbit.id, project_id=w.pr_hanbit.id, opportunity_id=w.o_hanbit.id,
                            status="refining", extra_solution_ids=[w.s_light.id], updated_at=datetime(2026, 11, 16))
    w.pro_daesung = Proposal(title="대성 온도 관제 제안", customer_id=w.c_daesung.id, opportunity_id=w.o_daesung.id, status="directions",
                             updated_at=datetime(2026, 11, 17))
    w.pro_mirae = Proposal(title="미래제조 제안", customer_id=w.c_mirae.id, status="new", updated_at=datetime(2026, 11, 18))
    # 다수 고객 제안 (베타 W): 고객이 없고, 붙인 고객 둘과 신규 사업(BM)이 있다
    w.pro_group = Proposal(title="", target_kind="group", group_name="전국 병원", customer_id=None, bm_id=w.bm_energy.id, status="new",
                           updated_at=datetime(2026, 11, 19))
    w.pro_group.group_customers = [w.c_hanbit, w.c_daesung]
    db.add_all([w.pro_hanbit, w.pro_daesung, w.pro_mirae, w.pro_group])
    db.flush()
    w.pro_hanbit.solutions = [w.s_hvac, w.s_solar]
    w.pro_daesung.solutions = [w.s_cold]
    # 리서치 제안 (솔루션 번호는 JSON 안에 있다)
    run = ResearchRun(trigger="manual", status="done")
    db.add(run)
    db.flush()
    w.report = ResearchReport(run_id=run.id, kind="suggest", title="발굴 방향", status="done", suggestions=[
        {"kind": "push", "title": "공조 제어 확산", "detail": "d", "reason": "r", "sources": [], "solution_ids": [w.s_hvac.id, w.s_sensor.id]},
        {"kind": "new", "title": "에너지 저장 솔루션", "detail": "d", "reason": "r", "sources": [], "solution_ids": []},
    ])
    db.add(w.report)
    db.flush()
    w.s_solar.origin_type, w.s_solar.origin_id, w.s_solar.origin_item = "research", w.report.id, "suggestion:1"
    db.commit()
    return w
