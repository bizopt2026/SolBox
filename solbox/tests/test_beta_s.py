"""베타 S: 괄호 입력 표시(`(수치 입력)` 꼴)를 다시 자리표시로 거른다 (사용자 결정 2026-11-23)."""
import time

import pytest

from app.proposals import is_author_instruction

MOVE = ['(수치 입력)', '(금액 입력)', '(입력)', '[일정 입력]', '(내용을 입력)', '절감액 (금액 입력)', '도입 일정 [일정 입력]', 'XX% 절감 (수치 입력)']
STAY = [
    '견적 요청 화면 (수량 입력)', '주문 화면 [수량 입력]', '(선택) 날짜 입력', '간편 로그인 (비밀번호 입력 불필요)', '신청서 (이름 입력) 단계 생략',
    '로그인 화면 (ID 입력)', '검색창 (키워드 입력)', '조회 조건 (기간 입력)', '고객 정보 입력 (이름·연락처) 자동화', '(금액 입력) 화면', '(사번 입력) 후 로그인',
    '금액 입력 화면 간소화', '현장 점검 결과 모바일 입력', '[필수] 고객명 입력', '고객 데이터를 입력',
    # 낱말 규칙은 되살리지 않았다
    '작성자 확인 필요', '팀장님 검토 요망', '오타 수정', '내부 검토용', '폰트 통일 필요',
]


@pytest.mark.parametrize("line", MOVE)
def test_s_bracket_input_placeholder_moves(line):
    assert is_author_instruction(line)


@pytest.mark.parametrize("line", STAY)
def test_s_customer_input_lines_and_word_lines_stay(line):
    assert not is_author_instruction(line)


@pytest.mark.parametrize("shape", ["(" * 100_000, "(금액 " * 30_000 + "입력)", "[" + "가" * 100_000 + " 입력]", "화면 (수량 입력) " * 20_000])
def test_s_bracket_input_is_fast_on_long_lines(shape):
    start = time.perf_counter()
    is_author_instruction(shape)
    assert time.perf_counter() - start < 5
