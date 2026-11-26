# 영업 CRM 보강 D 단계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 고객·파트너에 담당자(사람) 기록과 역할을 두고, 연락처 메모를 담당자로 나누며, 영업기회에 담당자를 잇고, 올리기·명함이 담당자를 만든다.

**Architecture:** 모델 `Contact`·연결 표 `opportunity_contacts`, 규칙 한 곳 `app/contacts.py`(같은 사람 판단·만들기·나누기 파서), 화면 `routers/contacts.py`, 관계 R50~R52 를 온톨로지 레지스트리에 등록해 연결·합치기·검색·사본이 따라오게 한다.

**Spec:** `docs/superpowers/specs/2026-11-26-sales-crm-d-design.md`

## Global Constraints
- 역할 코드 `decision` 의사결정자 · `user` 실무자 · `influencer` 영향자 · `other` 기타 (빈 값 가능).
- 같은 사람: 같은 회사 안 휴대폰 숫자 같음 또는 이메일(소문자) 같음, 둘 다 없으면 이름(공백 무시) 같음.
- 연락처 개인 정보는 AI 로 보내지 않는다 (나누기는 코드). B 단계 분석 프롬프트에는 담당자 이름·직함·역할만.
- 올리기의 "추가만" 규칙 유지: 담당자 새 기록만 만들고 기존 담당자는 고치지 않는다.
- DB: 새 표 2개. `KNOWLEDGE_FORMAT` 14.

## Review Focus
1. 이름만 있고 번호·이메일이 없는 명함을 두 번 올려도 담당자가 하나 (Task 4).
2. 고객 합치기에서 담당자가 남는 쪽으로 옮겨진다 (Task 1).
3. 다른 고객의 담당자 번호를 영업기회 폼에 넣어도 잇지 않는다 (Task 3).
4. 나누기 화면에서 이름 없는 줄은 넣을 수 없다 (Task 2).
5. 고객 지우기가 담당자와 연결을 함께 지운다 (Task 1).

### Task 1: 데이터·온톨로지·합치기·지우기
### Task 2: `app/contacts.py`(같은 사람·나누기 파서) + 담당자 폼·상세·회사 카드·나누기 화면
### Task 3: 영업기회 담당자 잇기 (폼·상세·AI 분석 프롬프트)
### Task 4: 올리기·명함 저장이 담당자를 만든다
### Task 5: 검색·사본·도움말·문서
### Task 6: 전체 확인 (전체 시험·화면·리뷰·push)
