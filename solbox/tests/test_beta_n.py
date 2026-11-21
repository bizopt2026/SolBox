"""베타 N: 작성자 메모의 꼬리 이유·`초안 -`·`오타` 고객 기능 문장 (N1~N3)."""
import pytest

from app.proposals import is_author_instruction

MOVE = ['고객에게 보여주지 말 것 (원가 포함)', '고객에게 보여주지 말 것 - 원가 포함', '이 표는 고객에게 보여주지 말 것, 내부 원가임', '이 장표는 최종본에서 고객에게 보여주지 말 것', '고객에게 보여주지 말고 구두로만 설명', '내부 검토용 (배포 금지)', '내부 검토용 - 배포 금지', '내부 검토용, 고객 배포 전 삭제', '내부 검토용 자료', '팀장님 확인 후 금액 확정', '팀장님 피드백 반영', '팀장님 코멘트: 가격 낮출 것', '작성자 메모: 할인율 15%까지 가능', '법무팀 검토 필요 (위약금 조항)', '법무팀 검토 후 반영', '법무팀 검토 중', '폰트 통일 필요 (본문 맑은고딕)', '슬라이드 순서 조정 필요 (3장 뒤로)', '로고 넣기 (우측 상단)', '제목 수정 필요함', '[초안] 미확정', '초안 - 수정 중', '초안 - 검토 전', '초안 – 영업팀 검토 전', '초안: 검토 전']
STAY = ['고객에게 보여주지 말아야 할 정보 자동 마스킹', '내부 검토용 대시보드 제공', '내부 검토용 자료 자동 생성', '팀장님 승인 한 번으로 결재 완료', '법무팀 검토 프로세스 자동화', '계약서 법무팀 검토 기간 5일 → 1일 단축', '법무팀 검토 요청부터 승인까지 한 화면에서', '오타 자동 수정', '오타 실시간 체크', '화면에 보여주지 말고 마스킹 처리']


@pytest.mark.parametrize("line", MOVE)
def test_n1_memo_with_trailing_reason_moves(line):
    assert is_author_instruction(line), line


@pytest.mark.parametrize("line", STAY)
def test_n1_n3_customer_lines_stay(line):
    assert not is_author_instruction(line), line


@pytest.mark.parametrize("line", ["오타 수정", "오타 있음", "제목 오타", "오타 확인 필요"])
def test_n3_typo_memos_still_move(line):
    assert is_author_instruction(line), line


@pytest.mark.parametrize("line", ["초안: 2주 내 제출", "초안 - 2주 내 제출", "(초안) 일정표", "제안 개요 (설계 초안 제출)"])
def test_n2_draft_customer_lines_stay(line):
    assert not is_author_instruction(line) or line.startswith("제안 개요 (초안")


def test_n1_known_limit_earlier_rows_kept():
    # 앞 표의 줄은 그대로: 짧은 `초안: 영업팀` 은 메모로 옮긴다 (고객 글일 수도 있는 한계)
    assert is_author_instruction("초안: 영업팀")


def test_n1_tail_cut_is_not_quadratic_on_long_whitespace_or_separators():
    import time
    for line in ("법무팀 검토" + " " * 200000 + "필요 x", "내부 검토용" + " - " * 50000, "법무팀 검토" + " " * 200000 + "(" * 1, "a" + "," * 100000):
        t0 = time.time()
        is_author_instruction(line)
        assert time.time() - t0 < 5, line[:20]
