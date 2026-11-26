# AI 코워크 ①단계: 사업 방향·과제 기록, 자료 올리기, 업무 보고 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사업 방향(`strategy`)과 과제(`initiative`: 이슈/개별 과제) 기록을 솔박스에 더하고, 공간마다 파일을 올리며, 업무 보고에 방향·이슈·과제 구역을 싣는다.

**Architecture:** 온톨로지에 두 유형과 관계를 정의하고(따라가기·상세 "연결된 것"·사본이 따라온다), BM 화면(`routers/bms.py`)과 같은 틀로 목록·폼·상세를 만든다. 상태·종류 등 칸의 변화는 영업기회 필드 이력처럼 `guards` flush 처리기가 `workspace_changes` 에 남기고, 보고 집계(`reports.aggregate`)가 그 이력의 시각으로 기간을 센다. 파일은 솔루션 자료(`records.new_solution_file`)와 같은 방식이다.

**Tech Stack:** FastAPI, SQLAlchemy 2, SQLite, Jinja2/HTMX, pytest(-n auto)

**Spec:** `docs/superpowers/specs/2026-11-26-ai-cowork-design.md` (6.1, 6.2 오른쪽 칸 중 ①에 해당하는 것, 6.3, 6.6 의 방향·이슈·과제 구역, 6.7)

## Global Constraints

- 유형 코드 `strategy`(화면 "사업 방향", 주소 `/strategies`, 사본 `strategies/{id}.md`), `initiative`(화면 "과제", 주소 `/initiatives`, 사본 `initiatives/{id}.md`). `direction` 은 이미 "제안 방향" 코드라 쓰지 않는다. 화면에서 "프로젝트"라는 말로 부르지 않는다.
- 새 메뉴 묶음 `("work", "과제")` — 항목: 사업 방향, 과제.
- 사업 방향 상태 `STRATEGY_STATUSES = {"draft": "초안", "agreed": "합의", "closed": "종료"}`, 기본 `draft`.
- 과제 종류 `INITIATIVE_KINDS = {"task": "과제", "issue": "이슈"}`, 기본 `task`. 상태 `INITIATIVE_STATUSES = {"open": "열림", "doing": "진행 중", "blocked": "막힘", "done": "완료", "dropped": "취소"}`, 기본 `open`. 열린 상태 `INITIATIVE_OPEN = ("open", "doing", "blocked")`, 닫힌 상태 `("done", "dropped")`.
- 이력 칸: 사업 방향 `STRATEGY_TRACKED_FIELDS = ("status", "consensus")`, 과제 `INITIATIVE_TRACKED_FIELDS = ("status", "kind", "owner_user_id", "due", "strategy_id")`.
- 파이썬 3.11 문법, 화면 문구 한국어, 조사는 `korean.josa`. 데이터는 `data_dir` 아래만.
- 새 POST → `tests/test_ux_notices.py` `POST_NOTICES`, 삭제 POST → `routers/confirm.py` `SPECS` + `ontology.DELETE_EFFECTS`, 만들기 폼 → `form_token`/`form_once`/`once.done`, 고치기 폼 → `rev_fields`/`check_rev`.
- DB 마이그레이션이 있다 (새 표, `report_plan_items` 칸 없음 — 칸 추가는 ②③단계). main 병합 전에 사용자에게 알린다. 패키지 추가 없음.
- `KNOWLEDGE_FORMAT` 을 14 로 올린다 (사업 방향·과제 사본, 연결 구역에 과제).
- 테스트: `python -m pytest -q -W error -n auto` 전체 통과가 병합 조건.

## Review Focus

- 사업 방향을 지우면 그 방향의 과제는 남고 "속한 방향"만 비워진다 → 과제 상세가 500 없이 "–" 로 보인다.
- 과제의 연결 대상(솔루션·영업기회 등)을 지우면 연결만 풀린다 → 과제 상세·사본이 깨지지 않는다.
- 같은 파일을 같은 공간에 두 번 올리면 "이미 있는 자료"로 건너뛴다; 크기 제한을 넘으면 아무것도 남지 않는다.
- 이력이 없는(도입 전) 기록이 있는 기간의 보고도 집계가 터지지 않는다; 확정한 옛 보고 스냅숏에 새 키가 없어도 화면·파일이 그려진다.
- 과제 상태만 바꿔 저장해도 이력 한 줄, 같은 값으로 저장하면 이력 없음.

---

### Task 1: 모델·온톨로지·마이그레이션·사본

**Files:**
- Modify: `app/models.py` (새 클래스 `Strategy`, `Initiative`, `WorkspaceFile`, `WorkspaceChange`, 연결 표 `initiative_solutions`·`initiative_partners`·`initiative_customers`·`initiative_opportunities`·`initiative_projects`(열 `business_id`))
- Modify: `app/ontology.py` (위 코드 목록, `_TYPE_LIST` 에 `strategy`·`initiative`·`workspace_file`, `NAV_GROUPS`, `NAV_ITEMS`, `NAV_ICONS`, `CODE_TONES`/`COLUMN_CODES`, 관계 R41~R49, `VIEW_LABELS`, `RELATED_EXCLUDE`, `MAIN_GROUPS`, `GROUP_ORDER`, `LIST_FILTERS`, `LIST_SORTS`, `LIST_LINKS`, `DELETE_EFFECTS`, `DELETE_IMPACT`, `HELP_GLOSSARY`)
- Modify: `app/guards.py` (이력 남기기), `app/knowledge.py` (`_strategy`, `_initiative`, `CONNECTED`, `KNOWLEDGE_FORMAT=14`)
- Test: `tests/test_workspace_models.py`

**Interfaces — Produces:**
- `Strategy(Stamped)`: `id, title, body(Text), consensus(Text), instructions(Text), period_start(date|None), period_end(date|None), status(str, active_history)`, `consensus` active_history; 관계 `initiatives`, `files`, `changes`.
- `Initiative(Stamped)`: `id, title, kind, status, owner_user_id(FK users SET NULL), due(date|None), strategy_id(FK strategies SET NULL), description(Text), instructions(Text), progress(Text)`; 추적 칸은 active_history; 관계 `strategy, owner, solutions, partners, customers, opportunities, projects, files, changes`.
- `WorkspaceFile(Stamped)`: `strategy_id|None (FK CASCADE)`, `initiative_id|None (FK CASCADE)`, `original_name, stored_name, extract_status, size, sha256` (둘 중 정확히 하나가 채워진다, 응용 수준).
- `WorkspaceChange`: `strategy_id|None, initiative_id|None (FK CASCADE, index), field, old_value, new_value, changed_at, changed_by_id`.
- `ontology.strategy_is_active(s, today) -> bool`: `status == "agreed"` 이고 기간이 비었거나 오늘이 기간 안.
- 관계: R41 initiative→strategy N:1 fk `initiatives.strategy_id`; R42~R46 initiative→solution/partner/customer/opportunity/project N:M link (각 연결 표); R47 strategy→workspace_file 1:N fk `workspace_files.strategy_id`; R48 initiative→workspace_file 1:N fk `workspace_files.initiative_id`; R49 user→initiative 맡는다 N:1 fk `initiatives.owner_user_id` (QUIET 아님, 단 `RELATED_EXCLUDE` 로 화면 연결 목록에서는 뺀다).

- [ ] **Step 1: 실패하는 시험** — `test_workspace_models.py`:
  - `test_initiative_links_roundtrip`: 과제 하나에 솔루션·영업기회를 잇고 `ontology.related(db, solution)` 그룹에 과제가 보인다, 반대로 과제에서 솔루션이 보인다.
  - `test_status_change_history`: 과제 상태 `open`→`doing` 저장 시 `WorkspaceChange(field="status", old_value="open", new_value="doing")` 한 줄; 같은 값 저장 시 줄 없음; 만들 때는 값 있는 추적 칸만 한 줄씩(old 빈 글).
  - `test_strategy_active_rule`: `draft`는 아님, `agreed`+기간 없음은 참, `agreed`+기간 지남은 거짓.
  - `test_delete_strategy_keeps_initiatives`: 방향 삭제 후 과제 `strategy_id is None`.
  - `test_mirror_writes_strategy_and_initiative`: `mirror.sync` 가 `strategies/{id}.md`·`initiatives/{id}.md` 를 쓰고 제목·상태·합의 내용 줄을 담는다.
- [ ] **Step 2:** `python -m pytest -q -W error -x tests/test_workspace_models.py` → FAIL
- [ ] **Step 3:** 모델·온톨로지·guards·knowledge 구현. guards 의 이력은 `_stamp_field_changes` 와 같은 모양으로 `_stamp_workspace_changes(session, obj, uid, now)`.
- [ ] **Step 4:** 위 시험 + `tests/test_ontology.py tests/test_related.py tests/test_knowledge.py tests/test_migrate.py tests/test_shell.py` 통과.
- [ ] **Step 5:** 커밋.

### Task 2: 사업 방향 화면

**Files:**
- Create: `app/routers/strategies.py`, `app/templates/strategies/{list,form,detail}.html`
- Modify: `app/main.py` (라우터 등록), `app/routers/confirm.py` (`SPECS`), `app/notices.py` (필요 시), `tests/test_ux_notices.py`
- Test: `tests/test_strategies.py`

**Interfaces — Consumes:** Task 1 의 `Strategy`, `ontology.STRATEGY_STATUSES`.
**Produces:** 경로 `GET /strategies`(구분 탭: 상태별), `GET /strategies/new`, `POST /strategies`, `GET /strategies/{id}`, `GET /strategies/{id}/edit`, `POST /strategies/{id}`, `POST /strategies/{id}/delete`.

- [ ] **Step 1: 실패하는 시험** — `test_strategies.py`: 만들기(폼 표, 제목 필수 오류 400), 상세에 합의 내용·상태 배지·"이 방향의 과제" 목록, 고치기 충돌 409(`check_rev`), 상태 목록에 없는 값 400, 삭제 확인 화면·삭제 후 목록으로, 목록 상태 탭 건수.
- [ ] **Step 2:** 실행 → FAIL
- [ ] **Step 3:** BM 라우터와 같은 틀로 구현. 상세 왼쪽: 합의 내용 카드, 내용 카드, 이 방향의 과제 표(MAIN_GROUPS `R41:initiative`, 동작 "+ 과제 만들기" `/initiatives/new?strategy_id={id}`); 오른쪽: 지침, 자료 카드(Task 4), 기간, 최근 이력 5줄, `related_card`, `meta_card`.
- [ ] **Step 4:** 시험 + `tests/test_ux_notices.py tests/test_detail_pages.py tests/test_list_pages.py` 통과.
- [ ] **Step 5:** 커밋.

### Task 3: 과제 화면

**Files:**
- Create: `app/routers/initiatives.py`, `app/templates/initiatives/{list,form,detail}.html`
- Modify: `app/main.py`, `app/routers/confirm.py`, `tests/test_ux_notices.py`
- Test: `tests/test_initiatives.py`

**Produces:** `GET /initiatives`(구분 탭: 종류 전체/과제/이슈, 칩: 열린 것만 `?open=1` 기본 켬, 담당 `?who=mine`, `?strategy_id=`), 만들기(`?strategy_id=` 미리 채우기), 상세, 고치기, `POST /initiatives/{id}/status`(상세에서 상태만 바꾸기, `_rev` 포함), 삭제.

- [ ] **Step 1: 실패하는 시험** — 만들기(담당 기본 = 만든 사람, 연결 고르기 `check_picker` 로 솔루션·파트너·고객·영업기회·프로젝트), 미리 채우기 `?strategy_id=`(없는 번호 무시), 상태 바꾸기 POST 로 이력 한 줄·알림 `saved`, 목록 기본은 열린 것만, `?open=0` 이면 모두, 종류 탭, 상세의 연결 카드, 연결된 솔루션 상세에 "과제" 그룹, 삭제 후 솔루션은 남음.
- [ ] **Step 2:** 실행 → FAIL
- [ ] **Step 3:** 구현. 상세 왼쪽: 설명·진행 메모 카드, 연결 카드(MAIN_GROUPS R42~R46); 오른쪽: 상태 바꾸기 작은 폼, 속한 방향(합의 내용 첫 두 줄), 지침, 자료 카드(Task 4), 최근 이력, `related_card`, `meta_card`.
- [ ] **Step 4:** 시험 + `tests/test_ux_notices.py tests/test_detail_pages.py tests/test_list_pages.py tests/test_related.py` 통과.
- [ ] **Step 5:** 커밋.

### Task 4: 공간 자료 올리기

**Files:**
- Create: `app/routers/workspace_files.py`, `app/templates/_workspace_files.html`(자료 카드 조각)
- Modify: `app/records.py` (`workspace_file_path(settings, f) -> Path`: `uploads_dir/"workspaces"/("strategy"|"initiative")/<번호>/<stored_name>`, `new_workspace_file(db, settings, owner, name, user, write) -> tuple[WorkspaceFile, ExtractResult] | None`, 같은 공간 중복이면 `DuplicateFile`), `app/knowledge.py` (`write_workspace_text`/`remove_workspace_text`, 사본 `workspaces/{owner_type}/{owner_id}/files/{id}-{name}.md`), 방향·과제 삭제 라우터(원본·사본 지우기)
- Test: `tests/test_workspace_files.py`

**Produces:** `POST /strategies/{id}/files`, `POST /initiatives/{id}/files` (여러 파일, `max_upload_mb`), `GET /workspace-files/{fid}` 내려받기, `POST /workspace-files/{fid}/delete`.

- [ ] **Step 1: 실패하는 시험** — 올리기 후 원본 파일·사본 글 존재, 같은 내용 두 번째는 건너뜀 알림, 크기 초과는 행 없음, 내려받기 이름(한글) 헤더, 삭제 확인 화면·삭제 후 원본/사본 없음, 과제 삭제 시 그 자료 원본·사본도 없음.
- [ ] **Step 2:** 실행 → FAIL
- [ ] **Step 3:** `routers/files.py` 의 `_save_upload` 흐름을 공간용으로 구현 (공용으로 끌어내지 말고 같은 규칙으로).
- [ ] **Step 4:** 시험 + `tests/test_files.py tests/test_ux_notices.py` 통과.
- [ ] **Step 5:** 커밋.

### Task 5: 업무 보고 연동

**Files:**
- Modify: `app/reports.py` (`aggregate` 반환에 `"workspace"` 키, `report_content` 에 `"workspace"`), `app/templates/reports/_result.html`, `app/report_files.py` (MD·docx·PPT 에 "사업 방향·과제" 구역)
- Test: `tests/test_report_workspace.py`

**Interfaces — Produces:** `aggregate(...)["workspace"] = {"strategies": [{"id","title","status","changed": bool,"open_tasks": int,"done_tasks": int}], "issues_open": [{"id","title","status","owner"}], "issues_new": int, "issues_closed": int, "initiatives_changed": [{"id","title","kind","from_status","to_status","progress"}]}`. 기간 판단: `WorkspaceChange.changed_at` 과 `created_at`. "기간 끝에 열린 이슈" = 기간 끝 시각 기준으로 만든 것 중 마지막 상태 이력이 열린 상태인 것(이력 없으면 지금 상태).
`report_content(...)["workspace"]` = 같은 내용을 표 행(글)로: `strategies` `(제목, 상태, 과제 진행/완료)`, `issues` `(제목, 상태, 담당)`, `initiatives` `(제목, 종류, 상태 변화, 진행 메모 첫 줄)`.

- [ ] **Step 1: 실패하는 시험** — 기간 안에 과제 상태 변경 2건·기간 밖 1건 → `initiatives_changed` 2줄; 기간 끝에 열린 이슈 수가 과제 목록 `?kind=issue&open=1` 건수와 같다(그 시점이 지금일 때); 방향 합의 내용 변경이 기간 안이면 `changed=True`; 옛 스냅숏(키 없음)으로 `report_content` 가 빈 목록을 돌려준다; MD 파일에 "사업 방향·과제" 제목.
- [ ] **Step 2:** 실행 → FAIL
- [ ] **Step 3:** 구현. 기존 "이슈·건의"(`highlights_md`, 문구 키 `issues`)는 건드리지 않는다. 확정 스냅숏에는 `aggregate` 결과가 통째로 들어가므로 따로 할 일 없음.
- [ ] **Step 4:** 시험 + `tests/test_report*.py` 통과.
- [ ] **Step 5:** 커밋.

### Task 6: 문서·도움말·전체 시험

**Files:** `app/templates/help.html`(이럴 땐 이렇게: 사업 방향·과제 한 단락), `docs/화면구성도.md`, `docs/ontology.md`(R41~R49), `docs/rules/대상과-데이터.md`(끝에 "AI 코워크 ①" 묶음 규칙), `README.md` 필요 없음.

- [ ] **Step 1:** 문서 고치기.
- [ ] **Step 2:** `python -m pytest -q -W error -n auto` 전체 통과 (출력 마지막 줄 `N passed`).
- [ ] **Step 3:** 커밋, 브랜치 push.
