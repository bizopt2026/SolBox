# 영업 CRM 보강 C 단계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 영업기회 목록에 파이프라인 보드 보기, 체류·전환·추이·팀 비교 분석 화면, 그 숫자를 읽는 AI 해석을 더한다. DB 변경 없음.

**Architecture:** 숫자 계산은 새 `app/pipeline_stats.py` 한 곳(영업기회·단계 이력·필드 이력을 한 번씩 읽어 파이썬에서 계산). 보드는 목록 라우터가 `view=board` 로 같은 걸러 보기 결과를 다른 템플릿으로 그린다. AI 해석은 작업 `pipeline_insight`(도구 없음, 결과는 `AIJob.result`).

**Tech Stack:** FastAPI, SQLAlchemy 2, Jinja2, 서버 SVG, `app.js` 진행 보강.

**Spec:** `docs/superpowers/specs/2026-11-26-sales-crm-c-design.md`

## Global Constraints
- 명령은 `solbox/`. 파이썬 3.11. 화면 문구 한국어. 외부 라이브러리·CDN 없음.
- 수주·실패는 닫은 일(단계 이력 `to_stage` won/lost 줄) 단위 — `reports.py` 와 같은 기준. 사유는 같은 시각의 필드 이력 `close_reason`, 금액은 그 시각의 금액.
- 기간 코드 `90d`(기본)·`q`·`lq`·`y`·`12m`, 팀 `?team=<번호>`(팀 없음 `none`). 팀 = 지금 담당의 팀.
- 보드 열 = 발굴·제안 중·협상 + 최근 30일 닫은 일. `back=board` 만 받는다.
- 새 AI POST `/opportunities/analysis/ai`: `require_ai_on`·`AI_POSTS`·익명 목록·`POST_NOTICES`·`JOB_KINDS`.

## Review Focus
1. 만든 줄이 없는(이력 전) 영업기회의 첫 구간은 체류에서 빠지고 뺀 수가 맞아야 한다 (Task 1).
2. 같은 시각에 여러 이력 줄이 있어도 구간 순서가 번호순으로 맞아야 한다 (Task 1).
3. 기간 경계: 시작일 0시 포함, 끝 다음날 0시 미포함 (Task 1).
4. `back` 에 다른 값(외부 주소)이 오면 무시하고 상세로 간다 (Task 2).
5. AI 해석이 다른 기간·팀의 결과를 보이지 않아야 한다 (Task 4).

### Task 1: `app/pipeline_stats.py` — 기간·구간·지표
**Produces:** `PERIODS: dict[code, label]`, `period_range(code, today) -> (start: date, end: date)`, `Segment(opp_id, stage, start: datetime|None, end: datetime|None)`, `segments(opp) -> list[Segment]`, `closes(opps) -> list[Close(opp_id, stage, at, reason, amount)]`, `compute(db, period, team, today) -> dict` (keys: `range`, `tiles`{won, won_amount, lost, win_rate, avg_win_days}, `stages`[{stage, avg, median, current_avg, current_n, forward, lost, n}], `excluded`, `months`[{label, won, won_amount, lost}], `reasons`{won:[(label,n)], lost:[…]}, `teams`[{name, open_n, open_amount, won, lost, win_rate, dwell{stage: avg}, stale}], `numbers_hash`).
- [ ] 시험 `tests/test_sales_c_stats.py`: 손으로 짠 이력으로 체류 평균·중앙값·현재 경과·전환·실패 비율·표본·제외 수, 닫은 일 단위 수주율(실패→재수주 둘 다), 사유는 그때 값, 금액은 그때 값, 월별, 팀별(팀 없음), 기간 경계, 같은 시각 순서.
### Task 2: 보드 보기와 `back=board`
**Files:** `routers/opportunities.py`, `templates/opportunities/board.html`(새), `list.html`(전환), `close.html`, `app.css`. **Produces:** `opportunities.board_columns(rows, today) -> list[col]`, 최근 30일 닫은 일 목록, `safe_back(back) -> str|None`.
- [ ] 시험 `tests/test_sales_c_board.py`: 열·합계·정렬, 걸러 보기 유지, 보류 건수 링크, 카드 단계 단추(JS 없음), `/stage`·`/close` 의 `back=board` 돌아가기와 이상한 값 무시, 수주·실패 단추는 사유 화면으로(`back` 유지).
### Task 3: 분석 화면
**Files:** `routers/opportunities.py`(`GET /opportunities/analysis`, `/{id}` 보다 먼저), `templates/opportunities/analysis.html`(새), `ui`/매크로(SVG 막대), `app.css`.
- [ ] 시험 `tests/test_sales_c_analysis.py`: 탭·요약·단계 표·SVG·사유·팀 표(팀 고르면 숨김)·정의 펼침·제외 알림·질의 수 일정·경로.
### Task 4: AI 해석
**Files:** `ai/pipeline_tasks.py`(새), `prompts/pipeline_insight.md`(새), `routers/opportunities.py`(`POST /opportunities/analysis/ai`), `ontology.JOB_KINDS`, 알림, 시험 표들.
- [ ] 시험 `tests/test_sales_c_ai.py`: 프롬프트 숫자 JSON·"숫자를 지어내지"·도구 없음, 결과 표시(같은 기간·팀만), 숫자 바뀜 표시, 진행 상자, AI 꺼짐.
### Task 5: 끌어 놓기·문서
**Files:** `static/app.js`, 도움말·화면구성도·ontology.md·rules.
- [ ] 시험: 보드에 `data-board`·`draggable` 속성, 도움말 글.
### Task 6: 전체 확인 (전체 시험, 화면, 리뷰, push)
