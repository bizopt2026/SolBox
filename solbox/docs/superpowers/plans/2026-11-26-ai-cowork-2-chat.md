# AI 코워크 ②단계: AI 동료 칸 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 사업 방향·과제·솔루션·파트너·영업기회 상세 화면에서 팀 공개 AI 대화를 여러 개 열고, AI 가 낸 기록 변경 제안을 [반영]/[무시]로 받는다.

**Architecture:** 대화 표 `ai_threads`(대상 종류+번호)·`ai_messages` 하나를 모든 화면이 함께 쓴다. 메시지 저장과 AI 작업(`cowork_chat`) 넣기는 한 트랜잭션, 답은 업무 보고 계획 대화와 같은 흐름(스트리밍 부분 답 → 끝나면 대화 구역을 바꾼다). 맥락은 대상의 지식 사본 글(연결 구역 포함) + 합의된 사업 방향 + 공간 지침·자료 글. 규칙 한 곳 `app/cowork.py`, 작업 `app/ai/cowork_tasks.py`, 화면 `app/routers/cowork.py` + `templates/cowork/`.

**Spec:** `docs/superpowers/specs/2026-11-26-ai-cowork-design.md` 6.4

## Global Constraints
- 대화는 팀 전체 공개. 메시지마다 쓴 사람(`user_id`)을 보인다.
- AI 도구 없음(`allowed_tools=()`): 자료는 뽑은 글을 프롬프트에 "지시가 아니라 자료"로 넣는다 (파일당 12,000자, 합계 36,000자 상한, 넘치면 잘렸다고 적는다). 웹 도구 없음.
- 지침(공간·속한 방향의 `instructions`)만 사용자 지시로 다룬다.
- 작업 우선순위 0 (사람이 누른 작업과 같음). 같은 대화에 한 번에 한 작업.
- 제안: 과제(상태·기한·진행 메모 한 줄), 영업기회(다음 할 일·날짜), 솔루션·파트너(메모 한 줄 덧붙이기). 방향은 제안 없음. 반영은 고침 표시 확인 후, 이력에 남는다. 새 답이 오면 그 대화의 대기 제안은 `superseded`.
- 근거 `evidence` 는 대상 사본 글·자료 글에서 찾으면 `verified=True`, 못 찾으면 화면에 "원문에서 근거를 찾지 못했어요".
- 대상 기록을 지우면 그 대화도 지운다 (`cowork` 의 flush 처리기).
- 새 POST 는 `POST_NOTICES`, AI POST 는 `AI_POSTS`·`require_ai_on`, 대화 지우기는 `confirm.SPECS`.
- 새 표 2개 (병합 전 사용자에게 알림).

## Tasks
1. 모델·온톨로지(JOB_KINDS)·`cowork.py`(대상 해석, 맥락 글, 제안 정리·반영, 대상 삭제 시 대화 지우기) + 시험 `tests/test_cowork_core.py`.
2. 작업 `cowork_chat` + 프롬프트 `prompts/cowork_chat.md` + 가짜 CLI 응답 + 시험(답 저장, 제안 파싱, 대체 표시).
3. 라우터·템플릿(대화 목록, 새 대화, 보내기, 다시 시도, 실시간 조각, 제안 반영/무시, 과제로 옮기기, 대화 지우기) 과 다섯 상세 화면에 붙이기 + 시험 `tests/test_cowork_pages.py`.
4. 문서(도움말·화면구성도·영역 규칙), 전체 시험, 검토.
