# 팀 업무 보고 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 솔박스에 일·주·월 팀 업무 보고(코드 집계 실적 + 지난 계획 점검 + AI 대화로 다음 계획 + PPT/워드/MD 파일)를 더한다.

**Architecture:** 실적 숫자는 `app/reports.py` 의 순수 집계 함수가 DB 에서 바로 센다. 화면 흐름·AI 대화는 제안 기능(`routers/proposals.py`, `ai/proposal_tasks.py`)의 틀을 따른다. 파일은 한 내용 모델(`report_content`) 하나에서 `app/report_files.py` 가 세 형식으로 뽑는다. 기간 숫자를 위해 후속 조치 완료일·영업기회 단계 변경 기록을 `guards` flush 처리기 한 곳에서 남긴다.

**Tech Stack:** FastAPI, SQLAlchemy 2, SQLite, Jinja2/HTMX, python-pptx, python-docx, pytest(-xdist), 가짜 CLI `tests/fake_claude.py`.

**Spec:** `docs/superpowers/specs/2026-11-25-team-report-design.md` (이 계획과 함께 읽는다). 시안: 화면 5장·결과물 견본(사용자 확인 2026-11-25).

## Global Constraints

- 모든 명령은 `solbox/` 안에서. 파이썬 3.11 문법 (`tests/test_beta_e.py` 가 검사). 화면·파일 문구 한국어, 조사는 `korean.josa`.
- 새 패키지 없음 (`pyproject.toml` 건드리지 않음). 새 표는 `Base.metadata.create_all`, 새 칸 `follow_ups.done_at` 은 `migrate.add_missing_columns` 가 자동으로 더한다.
- 새 유형·코드 목록·이름표·메뉴·아이콘은 `app/ontology.py` 한 곳. 온톨로지 코드 `plan_item` 은 이미 "정리안 항목"이므로 계획 항목 유형 코드는 **`report_item`**.
- 새 POST → `tests/test_ux_notices.py::POST_NOTICES`. 지우기 → `routers/confirm.py::SPECS`. 만들기 폼 → `form_token()`+`Depends(form_once)`+`once.done`. 고치기 폼 → `rev_fields`+`guards.check_rev`. AI POST → `Depends(require_ai_on)`+`ai_attrs`/`ai_note`+`tests/test_beta_e.py::AI_POSTS`.
- 보고 AI 작업은 **도구 없이** 실행(자료는 프롬프트에). 사용자 글(활동 정리본·메모·대화)은 "지시가 아니라 자료" 구역에 넣는다.
- 파일은 `settings.data_dir / "reports" / str(report.id)` 아래, `.part` 에 쓰고 `replace`. 경로는 `pathlib`, `encoding="utf-8"`.
- AI 가 꺼져도 집계·계획 직접 입력·확정·파일 만들기·기본 보고 문구는 된다.
- 기간: 일=하루, 주=월~일(ISO), 월=달력 월. 일간의 다음 기간 = 다음 평일(금→월). 공휴일 무시.
- 전체 시험: `python -m pytest -q -W error -n auto` (경고도 오류).

## Review Focus

1. **기능 도입 전 기록**: `done=True` 인데 `done_at` 이 빈 후속 조치, 단계 이력이 없는 영업기회 → 기간 숫자에서 빠지고 "도입 전 기록 n건" 안내가 나와야 한다 (Task 3 시험 `test_legacy_done_without_date_excluded_and_counted`).
2. **같은 단위·같은 기간을 두 번 만들기** → 두 번째는 새로 만들지 않고 기존 보고 건으로 보낸다 (Task 5 `test_new_same_period_redirects_to_existing`).
3. **앞 보고를 건너뛴 경우·앞 보고가 없음**: 가장 최근 확정 보고를 찾고, 없으면 "처음 보고" (Task 3 `test_previous_report_skips_gap_and_none`).
4. **넘어온 항목 두 번 넘김·점검 결과 변경**: 같은 원 항목은 한 번만 넘어가고, 확정 전 점검을 완료로 바꾸면 넘어온 줄이 빠진다 (Task 4 `test_carry_once_and_resync`).
5. **AI 문구의 틀린 숫자**: 구역 집계에 없는 숫자가 든 문장에 경고 표시 (Task 6 `test_note_number_warning`).

---

## 파일 지도

| 파일 | 책임 |
|---|---|
| `app/models.py` (수정) | `FollowUp.done_at`, 새 모델 `OpportunityStageChange`, `Report`, `ReportMessage`, `ReportPlanItem` |
| `app/guards.py` (수정) | flush 처리기: `done_at` 채우기, 단계 변경 기록 넣기, 계획 항목 변경 → 보고 건 `updated_at` |
| `app/ontology.py` (수정) | 유형 `report`·`report_item`, 코드 목록, 메뉴·아이콘·톤·지우기 영향·관계 |
| `app/report_periods.py` (새) | 기간 계산 (순수 함수) |
| `app/reports.py` (새) | 집계·앞 보고 찾기·넘김·기본 문구·내용 모델 |
| `app/report_files.py` (새) | MD / DOCX / PPTX 쓰기 |
| `app/ai/report_tasks.py` (새) + `app/ai/prompts/report_{summary,plan,chat,notes}.md` | AI 작업 4종 |
| `app/routers/reports.py` (새) + `app/templates/reports/*.html` | 화면·POST |
| `app/main.py` (수정) | 라우터·AI 처리기 모듈 등록 |
| `app/merge.py`, `app/impact.py`, `routers/confirm.py` (수정) | 합치기·지우기 영향·확인 화면 |
| `tests/test_report_*.py` (새) | 시험 |
| 문서 | `docs/rules/업무-보고.md`(새), `docs/rules/README.md`, `docs/화면구성도.md`, `templates/help.html`, `docs/ontology.md` |

---

### Task 1: 날짜 기록 (후속 조치 완료일 · 영업기회 단계 변경)

**Files:**
- Modify: `app/models.py` (FollowUp, 새 `OpportunityStageChange`), `app/guards.py::_stamp_changes`, `tests/test_ontology.py:31` (보조 표 집합에 `"OpportunityStageChange"` 추가), `app/opportunities.py`(영업기회 지우기에서 기록도 지움)
- Test: `tests/test_report_dates.py`

**Interfaces:**
- Produces: `FollowUp.done_at: datetime | None`; `OpportunityStageChange(id, opportunity_id FK ondelete CASCADE index, from_stage str "", to_stage str, changed_at datetime, changed_by_id int|None)`; `Opportunity.stage_changes` 관계(`cascade="all, delete-orphan", passive_deletes=True`, `order_by` changed_at).

- [ ] **Step 1: 시험 쓰기** — `tests/test_report_dates.py`
  - `test_done_at_set_and_cleared(db)`: 후속 조치 `done=True` 로 커밋 → `done_at` 이 채워짐; `done=False` → `None`; 다른 칸만 고치면 `done_at` 그대로.
  - `test_done_toggle_route_sets_done_at(user_client, db)`: 기존 토글 POST(`routers/activities.py` 의 `fu.done = not fu.done` 라우트) 뒤 `done_at` 있음.
  - `test_stage_change_logged_on_create_and_change(db)`: 영업기회 만들기 → 기록 1줄(`from_stage == ""`, `to_stage == "discovery"`); `stage = "won"` 커밋 → 2줄째(`"discovery"→"won"`, `changed_by_id` = `db.info["user_id"]`); 단계 외 칸 수정 → 줄 수 그대로.
  - `test_stage_change_on_won_conversion(user_client, db)`: 영업기회 화면의 단계 POST 로 수주 → 기록 있음.
  - `test_stage_changes_deleted_with_opportunity(db)`: 영업기회 지우면 기록도 없음.
  - `test_merge_keeps_done_at(db)`: `relations.py` 합치기(421행 `it.done = bool(o.done)`)로 옮긴 후속 조치가 원래 `done_at` 을 가짐.
- [ ] **Step 2: 실패 확인** — `python -m pytest -q -W error -x tests/test_report_dates.py` → 속성 없음 오류.
- [ ] **Step 3: 구현**
  - `FollowUp.done_at = mapped_column(DateTime, nullable=True)`.
  - `_stamp_changes` 에: `session.new`/`dirty` 의 `FollowUp` 중 `done` 기록 변화(`sa_inspect(obj).attrs.done.history`)가 거짓→참이면 `done_at = now` (이미 값이 있으면 둠), 참→거짓이면 `None`. 새 `Opportunity` 와 `stage` 가 바뀐 `Opportunity` 마다 `session.add(OpportunityStageChange(opportunity=obj, from_stage=옛 값 또는 "", to_stage=obj.stage, changed_at=now, changed_by_id=uid))`. 새 객체를 `before_flush` 안에서 더하므로 `idmarks` 처리기보다 **먼저** 돌아야 한다: `idmarks` 의 등록 순서를 확인하고, 필요하면 `event.listen(Session, "before_flush", fn, insert=True)` 로 이 처리기를 앞에 둔다.
  - 합치기에서 `done_at` 도 함께 옮긴다.
- [ ] **Step 4: 통과 확인** — 위 명령 PASS, 그리고 `python -m pytest -q -W error -x tests/test_ontology.py tests/test_opportunities*.py tests/test_follow*.py` PASS.
- [ ] **Step 5: 커밋** — `git commit -m "솔박스: 후속 조치 완료일·영업기회 단계 변경 기록"`

### Task 2: 보고 모델 + 온톨로지 등록

**Files:**
- Modify: `app/models.py`, `app/ontology.py`, `tests/test_ontology.py:31` (`"ReportMessage"` 추가)
- Test: `tests/test_report_models.py`

**Interfaces:**
- Produces (models):
  - `Report(Stamped)`: `id`, `unit: str`(ontology `REPORT_UNITS`), `period_start: date`, `period_end: date`, `status: str` 기본 `"draft"`, `summary_md: Text ""`, `highlights_md: Text ""`, `section_notes: JSON dict`, `snapshot: JSON | None`, `confirmed_at: datetime|None`, `active_job_id: FK ai_jobs|None`, `chat_from_id: int|None`, `file_names: JSON dict`(형식→파일 이름), `files_built_at: datetime|None`, `plan_draft: JSON|None`(AI 가 낸 계획 초안, Task 6), `direction: Text ""`(계획 방향 한 줄); 유일 제약 `(unit, period_start)`. 관계 `messages`, `plan_items`(이 보고가 **세운** 계획, `order_by` position,id, cascade delete-orphan), `active_job`.
  - `ReportMessage`: `ProposalMessage` 와 같은 모양(`report_id` FK ondelete CASCADE).
  - `ReportPlanItem(Base)`: `id`, `report_id` FK CASCADE index, `position`, `task: Text`, `owner: str ""`, `due: date|None`, `note: str ""`, `customer_id`/`opportunity_id`/`project_id`(물리 열 `business_id`) FK `ondelete="SET NULL"`, `result: str ""`(ontology `PLAN_RESULTS`), `result_note: str ""`, `checked_in_report_id: int|None`(FK reports SET NULL), `carried_from_id: int|None`(FK report_plan_items SET NULL, **유일**), `created_at`, `updated_at`.
- Produces (ontology): `REPORT_UNITS = {"day": "일간", "week": "주간", "month": "월간"}`, `REPORT_UNIT_DEFAULT = "week"`, `REPORT_STATUSES = {"draft": "실적 확인", "planning": "계획 작성 중", "confirmed": "확정"}`, `PLAN_RESULTS = {"done": "완료", "doing": "진행 중", "todo": "미착수", "dropped": "취소"}`, `PLAN_CARRY_RESULTS = ("doing", "todo", "")`, `REPORT_STEPS = (("result","실적"),("check","지난 계획 점검"),("plan","다음 계획"),("files","확정·파일"))`; `CodeList` 3개; `CODE_TONES` (draft slate, planning warn, confirmed ok; done ok, doing warn, todo bad, dropped slate); 유형 `ObjectType("report", "업무 보고", "작업", "Report", "reports", "/reports", "/reports/{id}", nav="업무 보고", nav_group="today", description="팀 전체 기록으로 만드는 일·주·월 실적과 다음 기간 계획", props=…)`, `ObjectType("report_item", "보고 계획 항목", "부속", "ReportPlanItem", "report_plan_items", parent=("report","report_id"), props={"task":"할 일","owner":"담당","due":"기한","result":"결과"})`; `NAV_ITEMS` 의 활동 기록 뒤에 `_type_nav_item("report")`; `NAV_ICONS["reports"]` = 시안의 문서 그림 `'<rect x="5" y="3" width="14" height="18" rx="1.5"/><path d="M9 8h6M9 12h6M9 16h3"/>'`; 관계(`RELATIONS` 다음 번호들): report→report_item(FK), report_item→customer/opportunity/project(FK) — 각각 `VIEW_LABELS` 양방향, 사본(`knowledge/`)에는 넣지 않으므로 지식 사본 쪽은 `UNTRAVERSED` 에 이유 "업무 보고는 사본을 만들지 않는다"; `DELETE_EFFECTS["report"]`, 그리고 고객·영업기회·프로젝트 항목에 "업무 보고 계획 항목의 연결이 비워집니다" 한 줄.

- [ ] **Step 1: 시험** — `tests/test_report_models.py`: `test_report_unique_unit_period(db)`(같은 unit·period_start 두 번 → `IntegrityError`), `test_plan_item_link_nulls_on_customer_delete(db)`, `test_carried_from_unique(db)`, `test_report_in_nav_today(app)`(`ontology.nav_groups()` 의 "오늘" 묶음 항목 경로에 `"reports"`).
- [ ] **Step 2: 실패 확인** — `python -m pytest -q -W error -x tests/test_report_models.py`.
- [ ] **Step 3: 구현** — 위 Interfaces 그대로.
- [ ] **Step 4: 통과 확인** — `python -m pytest -q -W error -x tests/test_report_models.py tests/test_ontology.py tests/test_related.py tests/test_shell.py tests/test_beta_d.py` PASS (레지스트리 시험이 빠진 등록을 알려 준다: 지시대로 채운다). `test_type_urls_are_real_routes` 는 Task 5 전까지 실패할 수 있다 → Task 2 에서는 `/reports`·`/reports/{id}` GET 의 최소 라우트(404 아님, 빈 목록)를 Task 5 파일에 먼저 만든다.
- [ ] **Step 5: 커밋**

### Task 3: 기간 계산 + 실적 집계

**Files:**
- Create: `app/report_periods.py`, `app/reports.py`
- Test: `tests/test_report_periods.py`, `tests/test_report_aggregate.py`

**Interfaces:**
- `report_periods.period_of(unit: str, day: date) -> tuple[date, date]`
- `report_periods.next_period(unit: str, start: date, end: date) -> tuple[date, date]` (일간: 다음 평일)
- `report_periods.label(unit: str, start: date, end: date) -> str` — 예 `"주간 2026-11-23 ~ 11-29"`, `"일간 2026-11-24 (화)"`, `"월간 2026-10"`
- `report_periods.file_stem(unit, start, end) -> str` — 예 `"업무보고_주간_2026-11-23~29"`
- `reports.aggregate(db: Session, unit: str, start: date, end: date) -> dict` — 키: `kpis`(dict: `activities`, `activities_by_kind`(dict), `activities_prev`(앞 같은 길이 기간 수), `needs`, `opportunities_new`, `won`, `proposals_active`, `decks_built`, `solutions_new`, `customers_new`, `partners_new`, `followups_done`, `followups_overdue`), `activities`(목록 dict: id,date,kind,title,customers), `opportunities`(id,name,customer,from_stage,to_stage,new: bool), `proposals`(id,title,customer,status), `followups_done`/`followups_overdue`(목록), `legacy_undated`(int: `done=True` 인데 `done_at` 없는 것 중 기간 안에 고쳐진 수 + 이력 없는 영업기회 수, 0 이면 안내 없음). 날짜 기준: 활동은 `Activity.date`(없으면 `created_at.date()`), 니즈·영업기회·제안·솔루션·고객·파트너는 `created_at`, 수주는 `OpportunityStageChange.to_stage == "won"` 의 `changed_at`, 완료는 `done_at`, 기한 지남은 `due <= end` 이고 (`done` 거짓 또는 `done_at.date() > end`). 질의 수는 기록 수와 무관(목록은 `selectinload`).
- `reports.previous_report(db, report: Report) -> Report | None` — 같은 unit, `status == "confirmed"`, `period_start < report.period_start` 중 가장 최근이면서, 그 보고의 항목을 다른 보고(이 보고 아님)가 점검하지 않은 것(`checked_in_report_id` 가 비었거나 이 보고).

- [ ] **Step 1: 시험** — 기간: `test_week_period`(2026-11-25 → 11-23~11-29), `test_month_period`(11-25 → 11-01~11-30), `test_day_next_is_weekday`(2026-11-27 금 → 11-30 월), `test_labels_and_file_stem`. 집계(각 시험은 기록을 만들고 **같은 조건으로 따로 센 질의**와 비교): `test_counts_match_independent_queries`, `test_activity_date_fallback_created_at`, `test_won_counted_by_stage_change_date`, `test_overdue_uses_period_end`(오늘 날짜와 무관하게 같은 값), `test_legacy_done_without_date_excluded_and_counted`, `test_previous_report_skips_gap_and_none`, `test_query_count_constant`(기록 5개와 50개에서 질의 수 같음: 기존 시험의 질의 세기 도구를 쓴다 — `grep -rn "query_count\|before_cursor_execute" tests/` 로 찾아 재사용).
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q -W error -x tests/test_report_periods.py tests/test_report_aggregate.py`
- [ ] **Step 5: 커밋**

### Task 4: 계획 넘김 · 점검 · 확정 로직

**Files:**
- Modify: `app/reports.py`
- Test: `tests/test_report_flow.py`

**Interfaces:**
- `reports.items_to_check(db, report) -> list[ReportPlanItem]` — `previous_report` 의 `plan_items`.
- `reports.save_check(db, report, results: dict[int, tuple[str, str]]) -> None` — 항목 번호 → (결과 코드, 메모). 코드가 `PLAN_RESULTS` 밖이면 `""`. `checked_in_report_id = report.id`. 확정 전이면 이어서 `sync_carry`.
- `reports.sync_carry(db, report) -> None` — 넘길 대상(`result in PLAN_CARRY_RESULTS`)마다 이 보고에 `carried_from_id` 줄이 없으면 맨 앞에 더하고(task·owner 복사, due 비움), 대상에서 빠진 원 항목의 넘어온 줄은 지운다. 확정된 보고에서는 아무것도 안 함.
- `reports.confirm(db, report, user) -> None` — `snapshot = aggregate(...)`(JSON 직렬화 가능한 값으로: 날짜는 ISO 글), `status="confirmed"`, `confirmed_at=now`, 비어 있는 `section_notes` 는 `default_notes(...)` 로 채움.
- `reports.unconfirm(db, report) -> str | None` — 다음 보고(같은 unit, 더 늦은 기간)가 이 보고의 항목을 점검했으면 거절 이유 글을 돌려주고 바꾸지 않음; 아니면 `status="planning"`, `snapshot=None`.
- `reports.data_of(db, report) -> dict` — 확정이면 `snapshot`, 아니면 새 `aggregate`.

- [ ] **Step 1: 시험** — `test_carry_doing_todo_unchecked_not_done_dropped`, `test_carry_once_and_resync`(같은 원 항목 두 번 sync → 한 줄; 완료로 바꾸면 넘어온 줄 사라짐; 다시 미착수 → 돌아옴), `test_no_resync_after_confirm`, `test_confirm_freezes_numbers`(확정 뒤 활동 추가해도 `data_of` 숫자 그대로), `test_unconfirm_blocked_when_next_checked`.
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과** (`tests/test_report_flow.py`)
- [ ] **Step 5: 커밋**

### Task 5: 화면 (목록·만들기·상세 4단계·저장 POST)

**Files:**
- Create: `app/routers/reports.py`, `app/templates/reports/{list,new,detail,_result,_check,_plan,_files}.html`
- Modify: `app/main.py`(라우터 등록 — 기존 `include_router` 목록 옆), `app/notices.py`(코드: `report_created`, `report_exists`, `saved`, `report_checked`, `report_planned`, `report_confirmed`, `report_unconfirmed`, `report_unconfirm_blocked`, `deleted`), `app/routers/confirm.py::SPECS`(`/reports/{obj_id}/delete`, 권한은 로그인한 누구나 — 설계 §5.4b), `tests/test_ux_notices.py::POST_NOTICES`
- Test: `tests/test_report_routes.py`

**Interfaces (라우트):**
- `GET /reports` (구분 탭 전체/일간/주간/월간 `?unit=`, 검색 없음 — 이번 범위), `GET /reports/new?unit=&day=` (기간 미리보기 글 포함), `POST /reports` (폼 표; 같은 unit·기간이 있으면 그 보고로 `report_exists`),
- `GET /reports/{id}?step=result|check|plan|files` (단계 띠 `_stepbar` 틀을 따라 `REPORT_STEPS`; 기본 단계: draft→result, planning→plan, confirmed→files),
- `POST /reports/{id}/summary` (요약 글·이슈 글, 고침 표시), `POST /reports/{id}/notes` (구역별 보고 문구, 고침 표시),
- `POST /reports/{id}/check` (점검 표: 숨은 칸 `item_id` 여러 개 + `result_<id>`·`note_<id>`, 고침 표시 = 보고 건 rev), status draft→planning,
- `POST /reports/{id}/plan` (계획 표: `pi_id`·`pi_task`·`pi_owner`·`pi_due`·`pi_note`·`pi_customer` 줄 단위, 활동 기록의 후속 조치 저장처럼 줄 번호로 맞춤 — 같은 번호 고침/빠진 번호 지움/새 줄 만듦; 고객 이름은 `activity_rows.LinkIndex` 정확히 하나 일치일 때만 연결; 고침 표시),
- `POST /reports/{id}/confirm`, `POST /reports/{id}/unconfirm`, `POST /reports/{id}/delete`.
- 화면 모양은 시안(목록·실적·점검·계획+대화·확정·파일)을 따른다: 실적 단계는 숫자 카드 8칸 + 요약 글 + 영업기회 변화 + 주요 활동(5건 + "n건 모두 보기" 는 `/activities` 목록), 오른쪽 `.daside` 보고 정보 카드와 안내(도입 전 기록 `legacy_undated` 가 0 보다 크면 한 줄). 각 구역 카드 아래 "보고 문구" 칸(접힘)에서 `section_notes` 를 고친다.

- [ ] **Step 1: 시험** — `test_list_and_tabs(user_client)`, `test_create_week_report(user_client, db)`, `test_new_same_period_redirects_to_existing`, `test_detail_steps_render`(각 `?step=` 200, 숫자 카드에 집계 값), `test_summary_conflict_409`(옛 rev 로 저장 → 409, 값 안 바뀜), `test_check_saves_and_carries`, `test_plan_rows_add_edit_delete`, `test_plan_customer_link_exact_only`, `test_confirm_then_unconfirm`, `test_delete_has_confirm_page`, `test_works_with_ai_off`(AI 꺼진 설정에서 만들기→점검→계획 직접 입력→확정 모두 200/303).
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과** — `python -m pytest -q -W error -x tests/test_report_routes.py tests/test_ux_notices.py tests/test_ontology.py tests/test_shell.py` (레지스트리 시험이 요구하는 등록을 채운다).
- [ ] **Step 5: 커밋**

### Task 6: AI 작업 4종 + 대화 + 구역별 보고 문구

**Files:**
- Create: `app/ai/report_tasks.py`, `app/ai/prompts/report_summary.md`, `report_plan.md`, `report_chat.md`, `report_notes.md`
- Modify: `app/routers/reports.py`, `app/templates/reports/_plan.html`(대화창 — `proposals/_chat*.html` 틀 재사용), `app/main.py`(처리기 모듈 import 위치는 `proposal_tasks` 를 불러오는 곳 옆), `tests/test_beta_e.py::AI_POSTS`, `tests/test_ux_notices.py::POST_NOTICES`, `tests/fake_claude.py`(보고 프롬프트를 알아보고 정해진 JSON/글을 돌려주는 분기 — 기존 제안 분기 방식 따름)
- Test: `tests/test_report_ai.py`

**Interfaces:**
- 처리기 `HANDLERS["report_summary"|"report_plan"|"report_chat"|"report_notes"]`, 입력 `{"report_id": int}` (+ chat 은 `{"message_id": int}`).
- `report_summary` → `summary_md` 에 마크다운 5~10줄 저장(초안 표시).
- `report_plan` → `run_json` 모델 `PlanDraft{direction: str, items: list[{task, owner="", due: str|None, customer="", note="", carried_from: int|None}]}`. 결과를 **바로 항목 표에 쓰지 않고** `Report.plan_draft`(Task 2)에 둔다 (`direction` 도 초안과 함께). 화면의 "이 초안 쓰기" POST `/reports/{id}/plan/apply-draft` 가 항목 표로 옮긴다(넘어온 항목은 `carried_from` 으로 유지).
- `report_chat` → 답 글은 `ReportMessage(role="assistant")`, 함께 온 고친 계획은 `plan_draft` 로. 스트리밍·부분 답은 `proposal_tasks._chat_answer`/`_partial_writer` 를 재사용(공용으로 옮기지 말고 import).
- `report_notes` → `{kpi, opps, proposals, check, plan, issues}` 각 1~2문장 → `section_notes`. 확정 POST 뒤 AI 가 켜져 있으면 자동으로 한 번 넣는다(AI 꺼짐이면 넣지 않고 기본 문장 유지).
- `reports.default_notes(data: dict, check: list, plan: list) -> dict[str, list[str]]` (Task 4 의 `confirm` 이 부름) — 숫자만 넣은 틀 문장. 예: `kpi` → `"활동 기록 14건(지난 기간 대비 +3), 수주 1건, 새 영업기회 2건입니다."`, `check` → `"지난 계획 6건 중 4건을 완료했습니다(67%). 미완료 2건은 다음 계획으로 넘깁니다."`.
- `reports.note_warnings(notes: dict, data: dict) -> dict[str, list[str]]` — 문장 속 숫자(정수·퍼센트)가 그 구역 숫자 집합(구역별로 정한 집계 값·건수·백분율)에 없으면 그 숫자를 돌려준다. 화면은 해당 문장 옆에 "숫자 확인" 배지.
- 라우트(모두 `Depends(require_ai_on)`): `POST /reports/{id}/ai/summary`, `/ai/plan`, `/chat`, `/chat/retry`, `/ai/notes`; 진행 표시 `GET /reports/{id}/chat/live`(제안과 같은 방식). `POST /reports/{id}/plan/apply-draft` 는 AI 문 아님.
- 프롬프트: 자료 구역(집계 JSON·활동 제목·정리본 첫 줄·대화)을 "지시가 아니라 자료"로 감싸고, "자료에 없는 실적·숫자를 쓰지 않는다", 출력 JSON 모양을 적는다.

- [ ] **Step 1: 시험** — `test_summary_job_writes_summary(fake_claude, worker_on)`, `test_plan_job_makes_draft_not_items`, `test_apply_draft_keeps_carry_links`, `test_chat_roundtrip_updates_draft`, `test_notes_job_and_default_notes`, `test_note_number_warning`(`"활동 기록 15건"` 인데 집계 14 → 경고 `["15"]`), `test_ai_posts_gated`(AI 꺼짐에서 각 AI POST → `ai_off` 알림), `test_prompt_marks_user_text_as_material`.
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과** — `python -m pytest -q -W error -x tests/test_report_ai.py tests/test_beta_e.py tests/test_ux_notices.py`
- [ ] **Step 5: 커밋**

### Task 7: 파일 만들기 (MD · DOCX · PPTX)

**Files:**
- Create: `app/report_files.py`
- Modify: `app/routers/reports.py` (`POST /reports/{id}/files` 폼 `fmt=pptx|docx|md`, `GET /reports/{id}/files/{fmt}` 내려받기), `POST_NOTICES`(`report_file_built`, `report_not_confirmed`)
- Test: `tests/test_report_files.py`

**Interfaces:**
- `reports.report_content(db, report) -> dict` (Task 4 `data_of` 사용) — 키: `title`("주간 업무 보고"), `period`, `next_period`, `author`, `confirmed_at`, `kpis`(카드 8개 `[(이름, 값, 꼬리)]`, 순서: 활동 기록·새 고객 니즈·새 영업기회·수주·제안 진행·새 솔루션·후속 조치 완료·기한 지남), `summary`(줄 목록), `opps`, `proposals`, `check`(`[(할 일, 담당, 결과 이름, 메모)]`), `done_count`, `direction`, `plan`(`[(할 일, 담당, 기한, "지난 계획"|"")]`), `issues`, `notes`(구역별 문장), `legacy_undated`.
- `report_files.write(content: dict, fmt: str, out_dir: Path, stem: str) -> Path` — `fmt` 밖이면 `ValueError`. 확정된 보고에서만 부른다(라우터).
- 형식 규칙 (시안 그대로 — `scratchpad` 견본 생성기를 참고 구현으로 삼아도 된다):
  - MD: `# 제목` / 기간·다음 기간·작성 줄 / 구역 `## n. 이름` → **보고 문구 1~2줄** → 표. 구역: 요약 숫자, 주요 성과(목록), 영업기회 단계 변화, 지난 계획 점검(완료 n/m), 다음 계획(방향 줄 + 표), 이슈·건의(목록 + 문구).
  - DOCX: A4, 여백 2cm/1.8cm, 글꼴 "맑은 고딕"(동아시아 글꼴도), 제목 20pt, 핵심 요약 회색 상자(`F1F5F9`), 소제목 12.5pt 남색(`1F3A8A`), 보고 문구는 `○ ` 글머리 10pt, 표 머리글 음영 `E8EDF8` + 쪽 넘김 시 머리글 되풀이(`w:tblHeader`) + `autofit=False` 열 폭, 결과 글자색 완료 `15803D`·진행 중/미착수 `B45309`.
  - PPTX: `pptx_template.blank_presentation()`, 16:9(13.333×7.5in), 6장 — 표지(남색 배경) → "이번 주 한눈에"(카드 4×2) → 주요 성과 + 영업기회 표 → 지난 계획 점검 표 → 다음 계획(방향 줄 + 표) → 이슈·건의. 표가 10줄을 넘으면 다음 장으로 나눠 "(계속)" 제목. 단위에 따라 "이번 주/오늘/이번 달" 글을 바꾼다.
  - 파일 이름 `file_stem + "." + fmt`, `Report.file_names[fmt]` 갱신(새 dict 대입), `files_built_at`.
- [ ] **Step 1: 시험** — `test_md_has_notes_before_tables`(각 `## ` 뒤 첫 비지 않은 줄이 표(`|`)가 아니라 문장), `test_docx_opens_and_has_sections`(python-docx 로 다시 열어 소제목 6개·표 머리글 tblHeader), `test_pptx_six_slides_and_split`(계획 15줄이면 7장), `test_download_requires_confirmed`, `test_download_filename_korean`(Content-Disposition 에 RFC 5987 `filename*`), `test_day_and_month_wording`.
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과** — `python -m pytest -q -W error -x tests/test_report_files.py`
- [ ] **Step 5: 커밋**

### Task 8: 합치기·지우기 영향 · 백업 · 문서 · 전체 시험

**Files:**
- Modify: `app/merge.py`(고객 합치기 시 `ReportPlanItem.customer_id` 옮김), `app/impact.py`(고객·영업기회·프로젝트 지우기 영향에 계획 항목 연결 수), `app/backup.py`(백업 대상에 `reports/` 폴더가 `decks/` 처럼 들어가야 하는지 확인 후 같은 방식으로 추가), `docs/rules/업무-보고.md`(새: 이 기능 규칙 — 날짜 기록 처리기, 넘김 규칙, 확정 스냅숏, 문구 경고), `docs/rules/README.md`, `solbox/CLAUDE.md` 영역 표에 한 줄, `docs/화면구성도.md`, `app/templates/help.html`("이럴 땐 이렇게"에 업무 보고 한 항목), `docs/ontology.md`(유형·관계 결정)
- Test: `tests/test_report_merge.py`

- [ ] **Step 1: 시험** — `test_customer_merge_moves_plan_links`, `test_delete_impact_lists_plan_links`, `test_backup_includes_reports_folder`(백업에 넣기로 한 경우).
- [ ] **Step 2~4: 실패 확인 → 구현 → 통과**
- [ ] **Step 5: 전체 시험** — `python -m pytest -q -W error -n auto` → 모두 PASS (실패 0, 경고 0).
- [ ] **Step 6: 실제 화면 확인** — 임시 `data_dir` 로 `python -m app` 실행, 주간 보고 만들기 → 점검 → 계획 → 확정 → 세 형식 내려받기, 스크린숏으로 시안과 비교.
- [ ] **Step 7: 커밋·push** (`claude/eager-brown-euqdz2`). main 병합 전 사용자에게 알림: 새 표 4개(`opportunity_stage_changes`, `reports`, `report_messages`, `report_plan_items`)·칸 1개(`follow_ups.done_at`).
