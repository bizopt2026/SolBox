# 고객니즈 관리 (2단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 소스 › 고객니즈(`/needs`)에서 니즈를 상태·분야·메모와 함께 고객별·분야별로 관리하고, 활동 기록 없이도 추가할 수 있게 한다.

**Architecture:** `customer_needs` 표를 넓힌다(활동 칸 선택·SET NULL, 상태·분야·메모·작성자). 행위 규칙은 새 모듈 `app/needs.py` 한 곳, 화면은 `app/routers/needs.py` + `templates/needs/`. 대상 유형·코드 목록·관계는 `app/ontology.py`.

**Tech Stack:** FastAPI, SQLAlchemy 2, SQLite, Jinja2, pytest

**Spec:** `docs/superpowers/specs/2026-11-27-customer-needs-design.md`

## Global Constraints
- 상태 코드·이름 (이 순서): `new` 새 니즈, `reviewing` 검토 중, `opportunity` 영업기회로, `hold` 보류, `resolved` 해결됨. 저장하는 값은 `new`·`reviewing`·`hold`·`resolved`.
- 보이는 상태 = `opportunity_id` 있으면 `opportunity`, 아니면 저장값.
- 손대지 않은 니즈 = 저장 상태 `new` + `opportunity_id` 없음 + 메모 빈 글.
- 분야 = `solution_fields` (사용 중인 분야만 새로 고름).
- 지식 사본 형식 그대로 (`KNOWLEDGE_FORMAT` 안 올림).
- 파이썬 3.11 문법, 화면 문구 한국어, 모든 POST 는 알림 코드·폼 표/고침 표시 규칙(solbox/CLAUDE.md).

## Review Focus
- 옛 DB(활동 칸 NOT NULL·CASCADE)를 다시 만든 뒤 줄 수·번호·값이 그대로이고, 활동을 지우면 손댄 니즈가 남는지.
- 활동 기록 폼에서 줄을 지울 때 손댄 줄이 사라지지 않는지.
- 직접 추가한 니즈가 영업기회 "고객 니즈 고르기"·고객 상세·검색에 나오는지 (안쪽 조인이 빠뜨리지 않는지).
- 사용을 끈 분야를 새로 고를 수 없고, 니즈가 쓰는 분야는 지울 수 없는지.
- 니즈를 고친 뒤 열어 둔 옛 활동 기록 폼 저장이 409 인지.

---

### Task 1: 표 다시 만들기와 모델
**Files:** `app/models.py`(CustomerNeed → `Stamped`, `activity_id` nullable + `ondelete="SET NULL"`, `status` String server_default 'new', `field_id` FK solution_fields SET NULL, `memo` Text server_default '', `field` 관계; `Activity.needs` cascade `"save-update, merge"` + `passive_deletes=True`), `app/migrate.py`(`customer_needs_need_rebuild(engine) -> bool`, `rebuild_customer_needs(engine, db_path, backup=None) -> dict | None`, `run_startup_migrations` 맨 앞 근처에서 호출), Test `tests/test_needs_migrate.py`
- [ ] 시험: 옛 모양(CREATE 글을 손으로: activity_id NOT NULL, CASCADE, 새 칸 없음)으로 만든 DB 에 줄 3개를 넣고 `run_startup_migrations` → 줄 수·id·content·opportunity_id 그대로, status='new', memo='', `activity_id` notnull 0, 활동 지우면 니즈 activity_id NULL(삭제 아님), 다시 돌리면 None(한 번만). 새 DB 는 `customer_needs_need_rebuild` 가 False.
- [ ] 구현: 새 표는 `CreateTable(CustomerNeed.__table__)` 를 sqlite 로 컴파일한 글의 표 이름만 `customer_needs_new` 로 바꿔 만들고, 옛 표와 새 표에 다 있는 칸만 `INSERT … SELECT`, 모델의 색인을 다시 만든다. 순서·복사본·foreign_key_check 는 proposals 다시 만들기와 같다 (공통 부분은 작은 도우미로 나눈다).
- [ ] `python -m pytest -q -W error tests/test_needs_migrate.py tests/test_migrate*.py` 통과, 커밋.

### Task 2: 행위 규칙 `app/needs.py`
**Produces:** `effective_status(n) -> str`, `status_condition(code) -> ColumnElement`, `is_untouched(n) -> bool`, `drop_from_activity(db, need) -> None`(손대지 않았으면 지우고 아니면 활동만 비움), `before_activity_delete(db, activity) -> None`, `mark_bound(need) -> None`(new → reviewing), `can_delete_need(user, need) -> bool`, `field_problem(db, raw, need) -> tuple[int|None, FieldError|None]`.
**Files:** `app/needs.py`, `app/activity_rows.py`(reconcile_needs 가 빠진 줄에 `drop_from_activity`), `app/routers/activities.py`(delete 앞에 `before_activity_delete`), `app/opportunities.py`(`bundlable_needs`·`needs_of` 바깥 조인, 차례 coalesce(활동 날짜, 만든 날); `apply_needs` 가 묶을 때 `mark_bound`), `app/solution_fields.py`(`delete_problem`·`usage_counts` 가 니즈도 셈), `app/knowledge.py`(영업기회 사본 니즈 줄: 활동 없으면 "직접 입력"), Test `tests/test_needs_rules.py`
- [ ] 시험: 활동 폼에서 손댄 줄을 빼고 저장 → 줄이 남고 activity_id None; 손대지 않은 줄 → 지워짐. 활동 삭제도 같음. 묶으면 status reviewing, 풀면 보이는 상태 reviewing. 직접 추가 니즈가 `bundlable_needs` 에 나옴. 니즈가 쓰는 분야 지우기 막힘. 영업기회 사본에 "직접 입력".
- [ ] 구현·통과·커밋.

### Task 3: 온톨로지
**Files:** `app/ontology.py`: `customer_need` 유형에 `/needs`·`/needs/{id}`·nav "고객니즈"·`nav_group="source"`·props(status·field·memo·activity), `NEED_STATUSES`(+ CodeList, CODE_TONES), NAV_ITEMS(리서치 다음), NAV_ICONS["needs"], LIST_SORTS["customer_need"](최신순, 고객 이름순), LIST_FILTERS(customer_id·field_id), DELETE_EFFECTS["customer_need"] 와 활동 기록 글, R9 설명(선택, 남는 규칙), 새 관계 "분야가 니즈를 묶는다"(`customer_needs.field_id`, VIEW_LABELS 양방향), MAIN_GROUPS(고객 상세 R10 그룹에 "+ 니즈 추가" `/needs/new?customer_id={id}`). Test: `tests/test_shell.py`(소스 = 솔루션·파트너·리서치·고객니즈), `tests/test_ontology.py`·`tests/test_related.py` 통과.
- [ ] 기대값 고치고 실패 확인 → 구현 → 통과 → 커밋.

### Task 4: 화면 `/needs`
**Files:** `app/routers/needs.py`(목록·상세·new·create·edit·update·delete), `app/main.py` 라우터 등록, `templates/needs/{list,detail,form}.html`, `app/notices.py`, `routers/confirm.py` SPECS, `tests/test_ux_notices.py` POST_NOTICES, `app/search.py`(니즈 결과 링크 `/needs/{id}`, 모두 보기 `/needs?q=`), Test `tests/test_needs_pages.py`
- 목록: 구분 탭(전체·5상태, `ui.seg_tabs`), 묶어 보기 `?group=customer|field`(쿠키 `need_group`), `?customer_id=`·`?field_id=`·`q`(내용·쓴 고객 이름·메모), 칸 니즈·고객·분야·상태·나온 곳·영업기회·만든 날, 정렬, 쪽.
- 상세: 내용·메모·기본 정보, 연결된 것, 제목 줄 삭제(권한)·수정·"영업기회 만들기"(고객 있고 영업기회 없을 때).
- 폼: 고객(직접 추가 필수, 활동 니즈는 선택 + "쓴 이름" 안내, 고르면 customer_name 도 바꿈, 바뀌면 `retarget_need_customer`), 내용 필수, 분야, 상태(묶인 동안 잠김), 메모. 추가 `form_once`, 수정 `rev_form`·`check_rev`. 저장 뒤 사본: 나온 활동·묶인 영업기회·고객.
- [ ] 시험: 목록 탭 건수·묶어 보기·걸러 보기, 직접 추가(폼 표 두 번 → 한 건), 수정 충돌 409, 니즈 수정 뒤 옛 활동 폼 저장 409, 고객 바꾸면 영업기회에서 풀림, 묶인 니즈 상태 칸 무시, 지우기 권한, 검색 링크.
- [ ] 구현·통과·커밋.

### Task 5: 분야 제안 (AI 정리·활동 폼·올리기)
**Files:** `prompts/activity_structure.md`·`prompts/intake_analyze.md`(`demand_signals[].field`: 목록 이름 하나 또는 빈 글, 목록은 `solution_fields.prompt_names`), `app/ai/tasks.py` 와 올리기 작업의 프롬프트 채우기, `records.need_rows`/`reconcile_needs`(세 번째 값 field 이름 → `suggest_id`, 칸이 안 오면 그대로), `routers/activities.py`(`ds_field: list[str] = Form([])`), `templates/activities/_form_fields.html`·`templates/intake/_item_activity.html`(분야 선택 칸), `intake.py`(ds_field), Test 기존 활동·올리기 시험 + 분야 저장 시험.
- [ ] 구현·통과·커밋.

### Task 6: 문서·전체 시험
`docs/화면구성도.md`(소스 › 고객니즈), `help.html`(이럴 땐), `docs/ontology.md`(속성·R9·행위), `docs/rules/대상과-데이터.md` 끝 "고객니즈 관리 (2026-11-27)", `solbox/CLAUDE.md` 의 니즈 관련 문장(검색 "고객 니즈는 링크 없음" 등). `python -m pytest -q -W error -n auto` 전체 통과 후 커밋·푸시.
