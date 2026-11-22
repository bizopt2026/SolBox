"""베타 O [베타 Q: 이 낱말 규칙은 없앴다. STAY 줄은 그대로 남고 MOVE 줄은 이제 모두 슬라이드에 남는다]: 작성자 메모 규칙이 고객 기능 문장을 옮기던 것 (베타 N 이 악화) 과 제곱 시간 두 길 (O1·O2)."""
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
# 사용자 결정 2026-11-22: 내부 단어 규칙을 뺐다. 베타 O 가 좁힌 뒤에도 옮기던 메모. 이제 슬라이드에 남는다 (글은 그대로, 이름만 MOVE 에서 RETIRED_MOVE 로).
RETIRED_MOVE = [
    "내부 검토용, 고객 배포 전 삭제", "내부 검토용 - 배포 금지", "내부 검토용 (배포 금지)", "법무팀 검토 필요 (위약금 조항)", "로고 넣기 (우측 상단)",
    "팀장님 확인 후 금액 확정", "팀장님 코멘트: 가격 낮출 것", "작성자 메모: 할인율 15%까지 가능", "고객에게 보여주지 말고 구두로만 설명",
    "고객에게 보여주지 말 것 (원가 포함)", "오타, 확인 필요",
]


@pytest.mark.parametrize("line", STAY)
def test_o1_customer_feature_lines_stay(line):
    assert not is_author_instruction(line), line


@pytest.mark.parametrize("line", RETIRED_MOVE)
def test_o1_retired_memos_stay(line):
    assert not is_author_instruction(line), line


def test_o2_shapes_are_in_the_linear_harness_at_100k():
    # 베타 Q: 낱말 규칙의 선형 모양은 뺐다. 자리표시 모양의 선형 모양이 남았다
    names = set(_linear.AUTHOR)
    assert {"placeholder ○○ words", "placeholder O run", "placeholder fullwidth X run", "placeholder X run + percent", "placeholder long spaces"} <= names
    assert 100_000 in _linear.SIZES
