# 담당자 관리 부담 줄이기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의 메모의 참석자를 담당자·영업기회에 저절로 잇고, 옛 연락처 메모를 한 번에 정리하고, 명함의 바뀐 정보를 제안으로 올리고, 의사결정자 접촉 공백을 알린다.

**Architecture:** 규칙은 `app/contacts.py`(참석자 맞추기·글에서 찾기·일괄 정리 대상)와 새 `app/contact_suggestions.py`(제안 만들기·반영), 화면은 기존 활동 기록 폼·AI 제안함·관리 화면에 얹는다. 관계 R53 은 온톨로지 레지스트리에 등록한다.

**Tech Stack:** FastAPI, SQLAlchemy 2, SQLite, Jinja2/HTMX, pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-contacts-ai-assist-design.md`

## Global Constraints
- AI 로는 회의 메모와 담당자 이름·직함만. 전화·이메일은 보내지 않는다.
- 새 담당자·담당자 칸 변경은 사람이 누를 때만 (일괄 정리는 관리자 한 번 확인).
- 미리 체크/자동 연결은 "그 활동의 고객·파트너 담당자 중 이름(공백 무시)이 정확히 하나 맞음" 일 때만. 사용자 계정 이름·아이디와 같은 이름은 버린다.
- 연락처 메모 글은 바꾸지 않는다.
- 새 표 `activity_contacts`, `contact_suggestions`. `KNOWLEDGE_FORMAT` 16. 30일 기준은 `ontology.OPPORTUNITY_QUIET_DAYS`.

## Review Focus
1. 두 회사가 연결된 회의에서 같은 이름 담당자가 양쪽에 있으면 어느 쪽도 미리 체크하지 않는다 (Task 2).
2. 확인 화면에서 체크를 푼 담당자는 저장 뒤 다시 붙지 않는다 (Task 3).
3. 같은 명함을 세 번 올려도 열린 제안은 하나 (Task 5).
4. 제안 반영 전에 사람이 칸을 고쳤으면 반영하지 않는다 (Task 5).
5. 일괄 정리를 두 번 눌러도 담당자는 한 벌 (Task 4).

### Task 1: 데이터 (R53, 제안 표)
- Modify `app/models.py`: `activity_contacts` 표, `Activity.contacts`·`Contact.activities`, `ContactSuggestion`(spec 4.2 칸, `new_value` 는 Text, `payload` JSON(이름만 같은 사람 제안의 들어온 값 전체)), `Contact.suggestions`(cascade).
- Modify `app/ontology.py`: R53 "참석했다"(contact ↔ activity, link `activity_contacts`), VIEW_LABELS, RELATED_EXCLUDE(activity 쪽 R53: 화면이 속성 줄로 보임), `CONTACT_SUGGESTION_FIELDS = {"title": "직함", "phone": "휴대폰", "email": "이메일", "person": "같은 이름의 새 명함"}`.
- Tests: `tests/relfix.py`(ct_choi 를 a_hanbit 류 활동에 참석), `test_related` EXPECT R53, `test_ontology` range(1, 54), `tests/test_contacts_ai_data.py`(활동 지우면 연결만, 담당자 지우면 제안도).

### Task 2: 참석자 맞추기 (`app/contacts.py`)
- `attendee_list(raw) -> list[dict]`: AI 의 `attendees` (글 또는 `{name,title,company,internal}`) → `[{"name","title","company","internal"}]`.
- `match_attendees(contacts, attendees, staff_names) -> list[dict]`: 줄마다 `{"name","title","status": "matched"|"ambiguous"|"new", "contact_id"|None, "candidates": [id]}`. 내부·직원·2자 미만 이름은 뺀다.
- `found_in_text(contacts, text) -> list[Contact]`: 글에 이름(공백 무시, 2자 이상)이 나오고 같은 이름이 범위에 하나뿐인 담당자.
- `staff_names(db) -> set[str]`: 사용자 이름·아이디의 공백 무시 키.
- Modify `app/ai/tasks.py` `ActivityDraft.attendees: list[Any]`; `draft_summary_md` 는 이름(직함)으로. 프롬프트 `activity_structure.md`·`intake_analyze.md`: attendees 를 객체로, 우리 회사 사람은 `internal: true`.
- Tests `tests/test_contacts_ai_match.py`: 정확히 하나·여럿(Review 1)·새 사람·직원 제외·옛 글 모양·글에서 찾기 표.

### Task 3: 활동 기록 저장에 잇기 (`app/routers/activities.py`, `app/records.py`)
- `records.apply_contacts(db, a, contact_ids, new_people, user, origin)`: 범위(그 활동의 고객·파트너) 안 담당자만 잇고, `new_people`(`"이름|직함"`)은 연결된 회사가 정확히 하나일 때 그 회사에 `same_person` 으로 견줘 만든다.
- `records.auto_link_contacts(db, a)`: 피커가 없는 경로(메모 "저장" `/activities/raw`, 직접 입력 새 폼, 올리기 활동 항목)용: `found_in_text` + (있으면) AI 참석자 "matched".
- `records.spread_contacts(a)`: `linked(a)` 의 영업기회마다 그 고객 담당자인 참석자를 더한다 (빼지 않음).
- 폼: `_form_fields.html` 에 "참석한 담당자" (숨은 칸 `contact_field=1`, `contact_ids` 체크, 새 사람 `new_people` 체크). 초안 화면은 `draft_values` 가 `match_attendees`+`found_in_text` 로 미리 체크, 수정 화면은 저장된 연결 + 범위 담당자. 새 폼(직접 입력)에는 칸을 두지 않는다 (자동 연결).
- `contact_field` 가 오면 체크가 정본 (Review 2), 안 오면 `auto_link_contacts`.
- 상세: 기본 정보에 "참석한 담당자" 줄. 사본: 활동 기록 사본 "- 참석한 담당자: 이름 직함" 줄, `KNOWLEDGE_FORMAT` 16.
- 올리기 활동 항목: 저장 때 `auto_link_contacts` + `spread_contacts` (확인 화면 칩은 두지 않는다: 내 판단, 올리기 확인 화면이 이미 길다; 결과 줄에 "참석 담당자 n명 연결").
- Tests `tests/test_contacts_ai_activity.py`: 초안 칩 미리 체크·새 사람 칩은 회사 하나일 때만, 저장 → 활동·영업기회 연결, 체크 푼 사람은 안 붙음, 새 사람 생성·유래, 메모 저장 자동 연결, 범위 밖 번호 무시, 수정 폼 빼기, 올리기 활동.

### Task 4: ③ 연락처 메모 일괄 정리 + 접기
- `contacts.bulk_rows(db) -> list[tuple[company, list[row]]]` (split_rows 의 checked 줄), `contacts.bulk_apply(db, user) -> int`.
- 라우터 `GET/POST /admin/contacts/cleanup` (관리자, 폼 표, 알림 `contacts_cleanup`·`contacts_cleanup_none`), 관리 메뉴 링크 (중복 정리 옆).
- `contacts.memo_view(company) -> (shown_lines, hidden_count)`: 같은 사람 줄 숨김. 고객·파트너 상세의 "연락처 메모" 값과 "담당자로 옮긴 n줄 보기"(`<details>`).
- Tests `tests/test_contacts_ai_cleanup.py`: 관리자만(403), 건수 미리보기, 옮김·같은 사람 건너뜀·메모 글 그대로, 두 번 제출(Review 5), 상세 접힘.

### Task 5: ④ 명함 변경 제안 (`app/contact_suggestions.py`)
- `propose_changes(db, contact, incoming: dict, source) -> int`: 직함·휴대폰·이메일 비교(spec 5.3), 열린 같은 제안이 있으면 만들지 않음 (Review 3).
- `propose_person(db, company, company_type, incoming, source) -> ContactSuggestion | None`: 이름만 같고 번호·이메일이 모두 다를 때.
- `apply(db, s, user) -> str|None`(지금 값 ≠ old_value 면 `"changed"` 로 닫음, Review 4; 이메일은 덧붙임, 휴대폰은 바꾸고 옛 번호 메모 끝 "이전 휴대폰 …"), `apply_as_new(db, s, user)`, `dismiss`, `pending(db)`.
- `intake._add_person`: 같은 사람이면 `propose_changes`, 이름만 같으면 `propose_person`(담당자를 만들지 않음). 명함의 `skip_default` 같은 사람 항목도 저장 때 `propose_changes` (cardplan 제외 글 문구 변경). 결과 줄 "(이미 있는 담당자, 바뀐 정보 n건 제안)".
- AI 제안함: `ai_inbox` 출처 `contact` 줄·건수, 라우트 `POST /contacts/suggestions/{id}/apply|new|dismiss`, `POST /contacts/suggestions/apply-all`. 담당자 상세에도 열린 제안.
- Tests `tests/test_contacts_ai_suggest.py`.

### Task 6: ⑤ 의사결정자 접촉 공백
- `opportunities.decision_gaps(db, opps, today) -> dict[int, list[tuple[Contact, int]]]` (참석 활동의 최근 날짜, 없으면 담당자 만든 날; `OPPORTUNITY_QUIET_DAYS` 넘은 것만, 진행 중만).
- 브리핑 카드 맨 위 줄 "최현수(의사결정자) 마지막 접촉 41일 전". `opportunity_ai.build_prompt` 담당자 줄에 "마지막 접촉 n일 전".
- Tests `tests/test_contacts_ai_gap.py`: 경계 30/31일, 참석 없음, 진행 중 아님, 프롬프트.

### Task 7: 문서·전체 확인
- `help.html`(담당자 항목), `docs/화면구성도.md`, `docs/ontology.md`(R53, 29장), `POST_NOTICES`·`SPECS` 점검.
- 전체 시험, 화면 확인(초안 칩·관리 정리·제안함·브리핑), 최종 리뷰, 사용자에게 DB 변경 알림.
