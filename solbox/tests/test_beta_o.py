"""베타 O: 작성자 메모 규칙이 고객 기능 문장을 옮기던 것 (베타 N 이 악화) 과 제곱 시간 두 길 (O1·O2)."""
import time

import pytest

from app.proposals import is_author_instruction
from tests import _linear

STAY = [
    "팀장님 확인 한 번으로 휴가 결재 완료", "작성자 메모 기능으로 결재 의견 전달", "팀장님 피드백 반영 시간 절반으로 단축",
    "오타, 누락 조항, 금액 불일치 자동 탐지", "로고 넣기, 색상 변경 등 화이트라벨 지원", "슬라이드 순서 변경, 템플릿 교체를 클릭 한 번으로",
    "내부 검토용, 외부 제출용 양식 자동 전환", "작성자 본인 확인 (2단계 인증)", "팀장님 보고 자료 자동 생성", "팀장님 확인 없이 자동 결재",
    "작성자 의견 등록 기능 제공", "작성자 메모 기능", "고객에게 보여주지 말고 마스킹 처리", "오타, 띄어쓰기 자동 교정",
    "법무팀 검토 - 승인 - 날인 자동 연결", "폰트 변경, 색상 변경 지원", "내부 검토용 - 외부 공유용 권한 분리", "작성자 확인 (본인 인증)",
    "법무팀 검토 (자동)", "법무팀 검토, 승인 자동화",
]
MOVE = [  # 좁힌 뒤에도 옮기는 메모 (표의 기존 줄과 같은 모양)
    "내부 검토용, 고객 배포 전 삭제", "내부 검토용 - 배포 금지", "내부 검토용 (배포 금지)", "법무팀 검토 필요 (위약금 조항)", "로고 넣기 (우측 상단)",
    "팀장님 확인 후 금액 확정", "팀장님 코멘트: 가격 낮출 것", "작성자 메모: 할인율 15%까지 가능", "고객에게 보여주지 말고 구두로만 설명",
    "고객에게 보여주지 말 것 (원가 포함)", "오타, 확인 필요",
]


@pytest.mark.parametrize("line", STAY)
def test_o1_customer_feature_lines_stay(line):
    assert not is_author_instruction(line), line


@pytest.mark.parametrize("line", MOVE)
def test_o1_memos_still_move(line):
    assert is_author_instruction(line), line


def test_o1_tail_with_product_word_is_never_cut():
    for tail in ("자동", "실시간 알림", "인증", "분리", "생성", "단축"):
        assert not is_author_instruction(f"내부 검토용 - {tail}"), tail
        assert not is_author_instruction(f"법무팀 검토 필요 ({tail})"), tail


@pytest.mark.parametrize("line", ["이 x" + " " * 20000 + "y", "오타," * 20000 + "x", "오타/" * 20000 + "x (a"])
def test_o2_old_quadratic_shapes_are_fast(line):
    t0 = time.time()
    is_author_instruction(line)
    assert time.time() - t0 < 2.0, line[:12]


def test_o2_shapes_are_in_the_linear_harness_at_100k():
    names = set(_linear.AUTHOR)
    assert {"fixed 이 x + spaces", "fixed 오타 commas", "fixed 오타 slashes + paren"} <= names
    assert 100_000 in _linear.SIZES
