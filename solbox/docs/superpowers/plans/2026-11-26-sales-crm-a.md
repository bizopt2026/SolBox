# 영업 CRM 보강 A 단계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 영업기회에 금액·예상 수주일·담당·다음 할 일과 필드 이력, 수주·실패 사유, 멈춘 건 알림을 더하고, 팀을 만들어 "내 건/우리 팀" 으로 보며, 고객 목록에 마지막 접촉을 보인다.

**Architecture:** 규칙은 지금처럼 한 곳씩: 코드 목록·이름표는 `app/ontology.py`, 금액 읽기·표시는 새 `app/money.py`, 멈춘 건·사유·담당 규칙은 `app/opportunities.py`, 필드 이력은 `app/guards.py` 의 before_flush(단계 이력과 같은 자리). 화면은 기존 매크로(`list_tools`·`seg`·`props_card`·`stage_stepper`)를 넓혀 쓴다.

**Tech Stack:** FastAPI, SQLAlchemy 2, SQLite, Jinja2, pytest(-n auto, -W error).

**Spec:** `docs/superpowers/specs/2026-11-26-sales-crm-a-design.md`

## Global Constraints
- 모든 명령은 `solbox/` 안에서. 파이썬 3.11 문법 (f-string 안 같은 따옴표·역슬래시 금지).
- 화면 문구 한국어, 조사는 `korean.josa`. 색은 토큰·`tone-*` 만.
- 새 POST: `tests/test_ux_notices.py` `POST_NOTICES` 에 알림 코드. 지우는 POST: `routers/confirm.py` `SPECS`.
- 새 고치기 폼: `rev_fields` + `guards.check_rev` + 시험. 새 만들기 폼: `form_token()` + `Depends(form_once)` + `once.done`.
- DB 칸 추가는 `migrate.add_missing_columns` 가 자동으로(모델에 nullable 칸만 더한다). 새 표는 `create_all`.
- 스크립트 없이도 모든 동작이 된다.
- 기한 정수: `OPPORTUNITY_QUIET_DAYS = 30`, `CUSTOMER_QUIET_DAYS = 90`, 홈 카드 최대 6건.
- 실패 사유: 가격 · 경쟁사 선택 · 예산 없음·취소 · 시기 연기 · 요구 불일치 · 연락 끊김 · 기타. 수주 사유: 가격 경쟁력 · 기술·품질 · 관계·신뢰 · 레퍼런스 · 사업방식(임대·투자) 조건 · 기타. 둘 다 필수, 보류는 사유 없음.

## Review Focus
1. 사용 중지된 담당이 있는 영업기회를 고쳐 저장해도 담당이 바뀌지 않고 이력이 생기지 않아야 한다 (Task 4 시험).
2. 같은 값으로 다시 저장하면 필드 이력이 생기지 않아야 한다 (금액 "12억" → 저장값 같음) (Task 2 시험).
3. 단계 막대에서 수주·실패를 JS 없이 눌러도 사유 화면으로 가고, 사유 없는 POST 로 단계가 바뀌지 않아야 한다 (Task 5 시험).
4. 팀이 없는 사용자는 "우리 팀" 탭이 없고 `?who=team` 은 전체로 본다 (Task 6 시험).
5. 홈 질의 수가 영업기회 수와 무관해야 한다 (Task 7 시험, 기존 질의 수 시험 방식).

---

### Task 1: 금액 읽기·표시 `app/money.py`

**Files:** Create `app/money.py`, `tests/test_money.py`

**Interfaces — Produces:**
- `parse_amount(text: str) -> int | None` — 빈 글이면 None, 읽을 수 없으면 `ValueError(AMOUNT_HELP)`
- `AMOUNT_HELP = "금액은 3.5억, 5천만, 350000000 처럼 적어 주세요."`
- `format_amount(value: int | None) -> str` — None 이면 ""
- `edit_text(value: int | None) -> str` — 폼에 다시 채울 글 (format_amount 와 같은 규칙, 읽으면 같은 값)

- [ ] 시험: `parse_amount` 표 — `"3.5억"→350_000_000`, `"3억 5천만"→350_000_000`, `"5천만"→50_000_000`, `"5,000만 원"→50_000_000`, `"350,000,000"→350_000_000`, `"12억"→1_200_000_000`, `"9000"→9000`, `""→None`, `"  "→None`; 오류: `"0"`, `"-3억"`, `"삼억"`, `"3.5.1억"`. `format_amount` 표 — `1_200_000_000→"12억"`, `350_000_000→"3.5억"`, `123_456_789→"1.23억"`(소수 둘째 자리까지, 끝 0 뗌), `50_000_000→"5,000만"`, `9000→"9,000원"`, `None→""`. 왕복: 위 값마다 `parse_amount(edit_text(v)) == v` 는 1억 이상에서 소수 셋째 자리 이하가 있으면 원 단위 숫자로 보낸다(`edit_text(123_456_789) == "123,456,789"`).
- [ ] 실패 확인 → 구현 → 통과 → 커밋.

### Task 2: 데이터 — 팀·담당·새 칸·필드 이력·사유 코드

**Files:** Modify `app/models.py`, `app/ontology.py`, `app/guards.py`, `app/migrate.py`, `app/opportunities.py`; Test `tests/test_sales_a_data.py`

**Interfaces — Produces:**
- `Team(Stamped, Base)`: `teams` 표, `id, name(유일), position(int, 기본 0)`, `members: list[User]`
- `User.team_id: int|None` (FK teams SET NULL), `User.team`
- `Opportunity` 새 칸: `amount:int|None`, `expected_close:date|None`, `owner_user_id:int|None`(FK users SET NULL), `next_step:str=""`, `next_step_due:date|None`, `close_reason:str=""`, `close_note:str=""`(Text); 관계 `owner: User|None`, `field_changes`
- `OpportunityFieldChange`: `opportunity_field_changes` 표, `opportunity_id(FK CASCADE, index), field, old_value, new_value, changed_at, changed_by_id, source(기본 "")`
- `ontology.OPPORTUNITY_TRACKED_FIELDS = ("amount", "expected_close", "owner_user_id", "next_step", "next_step_due", "close_reason")`
- `ontology.OPPORTUNITY_LOST_REASONS` / `OPPORTUNITY_WON_REASONS`: dict 코드→이름 (코드: `price, competitor, no_budget, delayed, mismatch, no_contact, other` / `price, tech, trust, reference, business_type, other`)
- `ontology.OPPORTUNITY_STAGE_LOST = "lost"`, `OPPORTUNITY_CLOSED_STAGES = ("won", "lost")`, `close_reasons(stage) -> dict`
- `ontology.OPPORTUNITY_QUIET_DAYS = 30`, `CUSTOMER_QUIET_DAYS = 90`
- opportunity props 추가: `amount "예상 금액"`, `expected_close "예상 수주일"`, `owner "담당"`, `next_step "다음 할 일"`, `next_step_due "다음 할 일 날짜"`, `close_reason "사유"`, `close_note "사유 메모"`; user 유형 props 에 `team "팀"`; 새 대상 유형 `team` ("팀", 관리, `/admin/users`, description "영업 담당을 묶는 팀")
- `opportunities.backfill_owners_once(engine) -> int` — `app_settings` 키 `opportunity_owner_backfill` 가 없을 때만 `owner_user_id IS NULL` 줄을 `created_by_id` 로 채우고 표시를 남김. `run_startup_migrations` 끝에서 부른다.

- [ ] 시험: (a) 영업기회 만들 때 값 있는 추적 칸만 이력(old ""), (b) 금액·담당 고치면 줄 2개(old·new 글, changed_by_id 는 `db.info["user_id"]`), (c) 같은 값 대입은 이력 없음, (d) 메모·이름 고침은 이력 없음, (e) 영업기회 지우면 이력도 지워짐, (f) 옛 구조 DB(새 칸 없는 `opportunities`·`users`)로 앱 시작 → 칸이 생기고 담당이 만든 사람으로 채워지며, 담당을 비운 뒤 다시 시작해도 다시 채우지 않음, (g) 사유 코드 목록이 Global Constraints 의 글과 순서 그대로, (h) `tests/test_ontology.py`·`test_related.py` 가 새 유형·props 로도 통과 (관계는 더하지 않으면 `UNTRAVERSED`/레지스트리 규칙을 맞춘다).
- [ ] 이력 글: 날짜 `isoformat()`, 정수 `str()`, None `""`.
- [ ] 실패 확인 → 구현 → 해당 시험 + `tests/test_ontology.py tests/test_related.py tests/test_bm_opportunity.py` 통과 → 커밋.

### Task 3: 관리 › 사용자 — 팀 만들기·배정

**Files:** Modify `app/routers/admin.py`, `app/templates/admin/users.html`, `app/notices.py`, `app/routers/confirm.py`, `app/ontology.py`(DELETE_EFFECTS team); Test `tests/test_teams.py`, `tests/test_ux_notices.py`

**Interfaces — Produces:** `POST /admin/teams` (만들기, 폼 표, 알림 `team_created`), `POST /admin/teams/{id}` (이름 바꾸기, `_rev`, `team_saved`), `POST /admin/teams/{id}/delete` (`team_deleted`, 사람의 team_id 비움), 사용자 저장 폼에 `team_id` 칸. `teams.ordered(db) -> list[Team]` (position, name 순) — 새 `app/teams.py` 에 두고 이후 Task 가 쓴다. `teams.grouped_users(db, keep: User|None) -> list[tuple[str, list[User]]]` — 사용 중 계정을 팀 이름별로(팀 없음은 끝 "팀 없음"), `keep` 이 사용 중지여도 그 팀 묶음에 넣는다.

- [ ] 시험: 관리자만(사용자는 403), 빈 이름·중복 이름 오류 400, 이름 바꾸기 충돌 409, 지우면 사람 팀 비고 영업기회 그대로, 사용자 저장에 팀 반영·없는 팀 번호 오류, 확인 화면 GET 존재, `POST_NOTICES` 등록.
- [ ] 구현 → 통과 → 커밋.

### Task 4: 영업기회 폼 "진행" 구역

**Files:** Modify `app/routers/opportunities.py`, `app/templates/opportunities/form.html`, `app/opportunities.py`; Test `tests/test_sales_a_form.py`

**Interfaces:** Consumes `money.parse_amount/edit_text`, `teams.grouped_users`. Produces `opportunities.parse_progress(db, amount, expected_close, owner_user_id, next_step, next_step_due) -> tuple[dict, str|None]` (값 dict 의 키는 모델 칸 이름).

- [ ] 시험: 만들 때 담당 기본 = 로그인 사용자(폼 처음 값), 금액 "3.5억" 저장 → 350_000_000, 잘못된 금액은 400 + 쓴 글 그대로 다시 그림 + 오류 칸 표시, 날짜 형식 오류 400, 없는/사용 중지 계정 번호를 새로 고르면 오류, 지금 담당이 사용 중지여도 그대로 저장되고 이력 없음(Review 1), 수정 폼의 다른 곳 변경 충돌에 새 칸 포함(금액만 다른 곳에서 바뀌면 409), 담당 칸이 팀별 `optgroup`.
- [ ] 구현 → 통과 → 커밋.

### Task 5: 수주·실패 사유

**Files:** Modify `app/routers/opportunities.py`, `app/opportunities.py`, `app/templates/opportunities/close.html`(새), `app/templates/opportunities/detail.html`, `app/notices.py`; Test `tests/test_sales_a_close.py`, `tests/test_ux_notices.py`

**Interfaces — Produces:** `GET /opportunities/{id}/close?to=won|lost`, `POST /opportunities/{id}/close` (form `stage, reason, note`, 알림 `opportunity_closed.won|lost`); `POST /stage` 가 won/lost 를 받으면 단계를 바꾸지 않고 303 으로 close 화면. `opportunities.close_problem(stage, reason) -> str|None`, `opportunities.apply_stage(opp, stage)` — 진행 중·보류로 가면 사유·메모 비움 (이미 won/lost 끼리 바꿀 때도 close 화면을 거친다).

- [ ] 시험: stepper 의 수주·실패 버튼 POST → 303 `/close?to=…`, 단계 그대로(Review 3), close POST 사유 없음 400, 다른 단계의 사유 코드 400, 성공 → 단계·사유·메모 저장 + 단계 이력 + 사유 필드 이력, 진행 중으로 되돌리면 사유 빔(이력엔 남음), 상세에 "실패 사유: 경쟁사 선택 · 메모" 줄, 수주 후 상세 주 동작은 그대로 프로젝트로 전환.
- [ ] 구현 → 통과 → 커밋.

### Task 6: 멈춘 건 규칙 + 영업기회 목록·상세

**Files:** Modify `app/opportunities.py`, `app/routers/opportunities.py`, `app/templates/opportunities/list.html`, `detail.html`, `app/ontology.py`(LIST_SORTS·CODE_TONES·STALE 이름표), `app/static/app.css`; Test `tests/test_sales_a_stale.py`, `tests/test_sales_a_list.py`

**Interfaces — Produces:**
- `ontology.OPPORTUNITY_STALE_REASONS = {"overdue": "다음 할 일 날짜 지남", "close_past": "예상 수주일 지남", "no_next": "다음 할 일 없음", "quiet": "소식 없음"}` (보이는 순서 그대로)
- `opportunities.stale_reasons(opp, today: date, last_touch: date) -> list[str]` (진행 중 단계가 아니면 [])
- `opportunities.last_touches(db, opps) -> dict[int, date]` = max(연결 활동 일자, created_at.date()), `relations.latest_activity_dates` 한 번
- `opportunities.stale_text(code, opp, today, last_touch) -> str` — "다음 할 일 3일 지남", "42일째 소식 없음", "예상 수주일 지남 (11/15)", "다음 할 일 없음"
- `opportunities.who_filter(rows, user, who) -> tuple[list, str]` — `who in ("", "team", "mine")`, 팀 없으면 "team" → ""
- 목록 `?who=`, `?stale=1`(걸러 보기 칩, `list_tools` 에 넣는 방식은 기존 칩 걸러 보기 모양), 정렬 `LIST_SORTS["opportunity"]` 에 `("amount", "금액순"), ("close", "예상 수주일순")` (빈 값은 뒤)
- 목록 칸: 영업기회(고객, 멈춘 사유 배지) · 단계 · 금액 · 예상 수주일 · 담당(팀) · 다음 할 일 · 최근 활동. 사업방식·BM·프로젝트·연결 칸 삭제(좁은 화면 `row-meta` 에는 단계·금액·담당).
- 상세: 멈춘 건 알림(`.notice`, 링크 `/opportunities/{id}/edit#next_step`), 단계 막대 밑 요약 띠(금액·예상 수주일 "N일 남음/지남"·담당 · 팀·다음 할 일), 오른쪽 "변경 이력" 카드(단계·필드 이력 합쳐 최신순 8줄 + `<details>` 나머지; 담당은 이름으로, 금액은 format_amount), 왼쪽 "연결된 활동의 열린 후속 조치" 카드(없으면 안 그림).
- 날짜 표시 헬퍼 `ui.short_date(d: date, today: date) -> str` (올해면 "12/20", 아니면 ISO).

- [ ] 시험(규칙): 네 사유의 경계 — next_step_due == 오늘은 지남 아님, 어제는 지남; last_touch 가 오늘−30 은 quiet 아님, −31 은 quiet; 단계 won/lost/hold 는 []; 여러 사유 순서.
- [ ] 시험(화면): who 탭 건수, 팀 없는 사용자에게 "우리 팀" 탭 없음·`?who=team` 은 전체(Review 4), stale 칩, 금액순 정렬 빈 값 뒤, 목록에 BM 칸 없음, 상세 이력 카드 내용("금액 8억 → 12억"), 멈춘 알림 문구.
- [ ] 구현 → 통과 → 커밋.

### Task 7: 홈 — 챙길 영업기회 카드와 금액 합계

**Files:** Modify `app/routers/home.py`, `app/templates/home.html`, `app/static/app.css`; Test `tests/test_sales_a_home.py`

**Interfaces — Produces:** `home.stale_opportunities(db, user, today, who) -> dict` (`items: list[dict(opp, reasons_text, amount_text)]`(최대 6), `counts: {"mine","team","all"}`, `who`) — 진행 중 영업기회를 한 번 읽고 마지막 접촉 한 번, 담당·팀은 미리 읽기. 주소 `?opp=team|all` (기본 내 건). `opportunity_bars` 에 단계별 `amount` 합과 `open_amount`, `open_missing`(금액 없는 진행 중 수) 추가.

- [ ] 시험: 내 건/우리 팀/전체 건수, 0건 문구 "멈춘 내 영업기회가 없습니다", 6건 넘으면 "모두 보기"가 `/opportunities?stale=1&who=…`, 합계 "43.7억 (금액 미입력 4건)" 형식, 질의 수가 영업기회 5건과 25건에서 같음(Review 5, `test_home_dashboard.py` 의 질의 세기 방식).
- [ ] 구현 → 통과 → 커밋.

### Task 8: 고객 — 마지막 접촉

**Files:** Modify `app/routers/customers.py`, `app/templates/customers/list.html`, `customers/detail.html`, `app/ontology.py`(LIST_SORTS customer 에 `("quiet", "마지막 접촉 오래된 순")`); Test `tests/test_sales_a_customers.py`

**Interfaces:** Consumes `ontology.CUSTOMER_QUIET_DAYS`. Produces 목록 `?quiet=1` 칩(활동 없는 고객 포함), 칸 "최근 활동" → "마지막 접촉"(글 "2026-07-12 · 137일 전", 90일 넘거나 기록 없으면 주의 배지), 칸 "진행 중 영업기회"("1건 · 2억", 없으면 –), 상세 제목 밑 한 줄 "마지막 접촉 24일 전 · 진행 중 영업기회 2건 (합계 4억)".

- [ ] 시험: 경계 90/91일, 기록 없음 포함, 오래된 순 정렬(기록 없음 맨 앞), 상세 한 줄.
- [ ] 구현 → 통과 → 커밋.

### Task 9: 사본·검색·문서

**Files:** Modify `app/knowledge.py`(영업기회 사본에 금액·예상 수주일·담당·다음 할 일·사유 줄, `KNOWLEDGE_FORMAT` 12), `app/search.py`(`FIELDS` 에 영업기회 다음 할 일), `app/templates/help.html`, `docs/화면구성도.md`, `docs/ontology.md`, `docs/운영가이드.md`, `docs/rules/대상과-데이터.md`(끝에 "영업 A 단계" 묶음 규칙), `docs/rules/README.md`; Test `tests/test_knowledge.py` 등 기존 시험 보정

- [ ] 시험: 사본에 "- 예상 금액: 12억", "- 담당: 김영업 (영업1팀)", 실패면 "- 사유: 경쟁사 선택"; 검색어가 다음 할 일에 맞으면 영업기회가 나옴.
- [ ] 구현 → 통과 → 커밋.

### Task 10: 전체 확인
- [ ] `python -m pytest -q -W error -n auto` 전부 통과.
- [ ] 앱을 띄워 화면 확인(스크린샷: 홈·목록·상세·사유·고객·관리), 시안과 다른 점 정리.
- [ ] 브랜치 전체 리뷰 → 고친 뒤 push. 병합은 사용자에게 DB 변경을 알린 뒤.
