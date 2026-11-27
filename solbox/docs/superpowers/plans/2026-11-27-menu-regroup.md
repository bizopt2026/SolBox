# 메뉴 재편 (1단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사이드 메뉴를 오늘·과제·소스·사업기획·영업·관리로 다시 묶고, 고객 메뉴 이름을 "고객관리"로 바꾼다.

**Architecture:** 메뉴는 `app/ontology.py` 의 `NAV_GROUPS`·`NAV_ITEMS`·`ObjectType.nav/nav_group` 에서만 그려진다. 그 정의를 바꾸고 기대값을 가진 시험과 문서를 맞춘다.

**Tech Stack:** FastAPI + Jinja2, pytest

**Spec:** `docs/superpowers/specs/2026-11-27-menu-regroup-design.md`

## Global Constraints
- 묶음: `today` 오늘, `work` 과제, `source` 소스, `planning` 사업기획, `sales` 영업, `admin` 관리 (이 순서)
- 소스 = 솔루션·파트너·리서치, 사업기획 = BM, 영업 = 고객관리·영업기회·제안·프로젝트
- 고객의 `label` 은 "고객" 그대로, `nav` 만 "고객관리". 고객 목록 화면 제목은 메뉴 이름(`nav`)
- DB·패키지 변경 없음

## Review Focus
- 경로 줄(crumbs)이 새 묶음 이름을 쓰는지: 솔루션 "소스 › 솔루션 › …", 고객 "영업 › 고객관리 › …", BM "사업기획 › BM › …"
- 고객 상세·연결 카드·검색 결과는 "고객" 이름을 그대로 쓰는지 (label 은 안 바뀜)
- 메뉴 아이콘 표(`NAV_ICONS`)는 path 기준이라 그대로 맞는지
- 사이드 메뉴 aria-label 이 "소스"·"사업기획" 인지

---

### Task 1: 메뉴 정의와 시험

**Files:**
- Modify: `app/ontology.py` (`_TYPE_LIST` 의 solution·partner·research_run `nav_group="source"`, customer `nav="고객관리"`, bm `nav_group="planning"`; `NAV_GROUPS`; `NAV_ITEMS` 차례; 위 주석)
- Modify: `app/templates/customers/list.html` (제목 `ont.TYPES["customer"].nav`)
- Test: `tests/test_shell.py`, `tests/test_ux_navigation.py`, `tests/test_detail_form_helpers.py`

- [ ] **Step 1: 시험 기대값을 새 구조로 고친다**
  - `test_shell.py`: 묶음 코드 `["today","work","source","planning","sales","admin"]`, `("소스", ["솔루션","파트너","리서치"])`, `("사업기획", ["BM"])`, `("영업", ["고객관리","영업기회","제안","프로젝트"])`
  - `test_ux_navigation.py`: "발굴 ›" → "소스 ›", "영업 › 고객 › 등록" → "영업 › 고객관리 › 등록", `aria-label="소스"`; BM 등록 경로 "사업기획 › BM › 등록" 확인 추가
  - `test_detail_form_helpers.py`: `("소스", None)`, `("영업", None), ("고객관리", "/customers")`
- [ ] **Step 2: 실패 확인** `python -m pytest -q -W error tests/test_shell.py tests/test_ux_navigation.py tests/test_detail_form_helpers.py` → FAIL
- [ ] **Step 3: `ontology.py`·고객 목록 제목을 고친다**
- [ ] **Step 4: 통과 확인** 같은 명령 → PASS
- [ ] **Step 5: 문서** `docs/화면구성도.md`(메뉴 그림·"### 발굴" 제목·영업 구역의 BM 줄을 새 "### 사업기획" 로), `solbox/CLAUDE.md`(묶음: 오늘·과제·소스·사업기획·영업·관리), `ontology.py` 주석
- [ ] **Step 6: 전체 시험** `python -m pytest -q -W error -n auto` → 모두 통과
- [ ] **Step 7: 커밋·푸시** (`claude/cool-davinci-jcbjji`)
