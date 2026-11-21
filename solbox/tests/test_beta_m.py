"""베타 M 묶음: 고정 낱말은 메모 모양의 줄만 옮긴다(M1), 주소는 실제 원격 모양만(M2), 선형 시험 자식의 부모 감시(M3)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.deck import has_internal_mark
from app.proposals import is_author_instruction
from tests import _linear

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(_linear.__file__)))

# ================= M1: review10 a1 의 고객 글머리표 85줄 =================
# 아래 여섯 줄은 진짜 자리표시(`XX`·`OO`·`○○`)가 든 줄이라 옮긴다 (알려진 한계: 자리표시 규칙은 줄 어디서든 본다. 검토자도 옮겨도 된다고 한 줄이다).
CUSTOMER_MOVABLE = [
    'XX% 이상',
    '기존 대비 OO배 빠른 처리',
    'XX 모델 대비 30% 향상',
    '5G XX 대역 지원',
    '○○ 은행 콜센터 적용 사례',
    'OO 공사 설비 관제 구축',
]
CUSTOMER_STAY = [
    '작성자 이력 관리 기능',
    '문서 작성자·승인자 추적',
    '게시글 작성자 실명 표시 및 익명 옵션',
    '작성자별 권한 분리 (조회·수정·삭제)',
    '보고서 작성자 자동 기록으로 감사 추적성 확보',
    '결재 문서의 작성자와 결재자 분리',
    'AI 가 보고서 초안 작성자 역할을 대신 수행',
    '초안: 2주 내 제출',
    '(초안) 일정표',
    '제안서 초안 자동 생성',
    '계약서 초안 검토 지원',
    '회의록 초안 5분 내 생성',
    '보고서 초안 → 검토 → 확정 3단계 워크플로',
    'AI 가 생성한 초안',
    '1차 산출물: 설계서 (초안)',
    '오타 자동 교정',
    '맞춤법·오타 검사',
    '입력 오타 방지를 위한 자동 완성',
    'OCR 오타 검출률 98%',
    '검색어 오타 교정',
    '음성 인식 후 오타 검토 단계 제공',
    '상담 기록의 맞춤법·오타 수정 자동화',
    '문서 내 오타 발견 시 알림',
    '폰트 변경 기능 제공',
    '사용자별 테마·폰트 변경 지원',
    '보고서 템플릿 폰트 통일 기능',
    '큰 글씨 모드: 폰트 확대 지원',
    '기업 전용 폰트 적용 가능',
    '내부용 포털',
    '사내 내부용: 별도 라이선스',
    '내부용 챗봇과 고객용 챗봇 분리 운영',
    '배포 대상: 내부용',
    '1단계 내부용, 2단계 고객용',
    '사내 직원 내부용',
    '슬라이드 순서 자동 정렬',
    '발표 자료 슬라이드 순서 추천 기능',
    '정확도 95% 이상',
    'OO시 스마트시티 구축 사례',
    '○○구청 민원 챗봇 도입',
    'A사·B사·OO사 공동 구축',
    '(금액 입력) 화면',
    '(사번 입력) 후 로그인',
    '금액 입력 화면 간소화',
    '로고 넣기 기능',
    '보고서에 회사 로고 넣기 지원',
    '제목 수정 필요 시 관리자 문의',
    '게시글 제목 수정 필요 시 이력 저장',
    '법무팀 검토 프로세스 자동화',
    '계약서 법무팀 검토 기간 5일 → 1일 단축',
    '법무팀 검토 요청부터 승인까지 한 화면에서',
    '내부 검토용 대시보드 제공',
    '팀장님 승인 한 번으로 결재 완료',
    '고객에게 보여주지 말아야 할 정보 자동 마스킹',
    '상담사 화면에만 표시 (고객에게 보여주지 않음)',
    '고객 정보 입력 (이름·연락처) 자동화',
    '견적 요청 화면 (수량 입력)',
    '주문 화면 [수량 입력]',
    '검색창 (키워드 입력)',
    '간편 로그인 (비밀번호 입력 불필요)',
    '현장 점검 결과 모바일 입력',
    '(선택) 날짜 입력',
    '조회 조건 (기간 입력)',
    '[필수] 고객명 입력',
    '신청서 (이름 입력) 단계 생략',
    'PoC 2주, 본사업 3개월',
    '월 구독료 300만원',
    '24x7 관제 지원',
    '전담 PM 1명·엔지니어 2명 투입',
    '계약 후 4주 내 구축 완료',
    'ISMS-P 인증 보유',
    '무상 유지보수 1년 제공',
    '내부망·폐쇄망 설치 지원',
    '초안 작성 시간 70% 단축',
    '폰트·색상 등 브랜드 가이드 반영',
    '오타율 0.5% 이하',
    '작성자 본인만 수정 가능',
    '내부용·외부용 문서 자동 분류',
    '대상: 임직원 (내부용)',
    '용도: 내부용',
]


@pytest.mark.parametrize("line", CUSTOMER_STAY)
def test_m1_customer_bullets_stay(line):
    assert not is_author_instruction(line), line


@pytest.mark.parametrize("line", CUSTOMER_MOVABLE)
def test_m1_real_placeholders_move(line):
    assert is_author_instruction(line), line


def test_m1_sample_is_the_whole_85():
    assert len(CUSTOMER_STAY) + len(CUSTOMER_MOVABLE) == 85


@pytest.mark.parametrize("line", [
    "작성자 확인 필요", "팀장님 검토 요망", "법무팀 검토 필요", "내부 검토용", "초안", "오타 수정", "폰트 통일 필요", "슬라이드 순서 조정 필요", "제목 수정 필요", "로고 넣기",
    "고객에게 보여주지 말 것", "XX% 절감 (수치 입력)", "(수치 입력)", "[일정 입력]", "(초안)", "[초안] 금액 수정 필요", "초안 - 2차 수정 예정", "오타", "제목 오타", "오타 있음",
])
def test_m1_memo_shaped_lines_still_move(line):
    assert is_author_instruction(line), line


def test_m1_known_limits():
    # 오래된 표의 줄이 먼저다: 이 줄들은 짧아서 메모로 본다 (고객 문장일 수도 있다)
    assert is_author_instruction("제안 개요 (초안)")
    assert is_author_instruction("초안: 영업팀")
    assert is_author_instruction("작성자: 김대리")
    # 표의 열 이름 `작성자` 는 줄 전체여도 옮기지 않는다
    assert not is_author_instruction("작성자")
    assert not is_author_instruction("팀장님")


# ================= M2: 주소는 허용 목록의 scheme 만 =================
@pytest.mark.parametrize("text", [
    "C://solbox/data/app.db", "경로:C://solbox/data/uploads/solutions/3/a.pdf", "C://Users/kim/solbox/data/decks/7/deck-20261121-101500-ab12cd.pptx", "c://solbox/data/logs/app.log",
    "sqlite:///C:/solbox/data/app.db", "sqlite:///data/app.db", "x://data/uploads/solutions/3/a.pdf", "path://data/app.db", "note://decks/4/deck-20261121-101500-ab12cd.pptx",
    "(x://intake_work/5/1-회의록.md)", "data/decks/7", "data\\decks\\7", "data/uploads/solutions", "data/uploads/intake",
])
def test_m2_fake_schemes_and_bare_app_folders_are_removed(text):
    assert has_internal_mark(text), text


@pytest.mark.parametrize("text", [
    "s3://kt-lake/data/logs/2026/", "gs://bucket/data/models/v3", "hdfs://nn:8020/data/models/v1", "ftp://files.kt.com/data/app.db", "s3a://b/data/decks/7", "sftp://h/data/uploads/intake",
    "abfss://c@acct.dfs.core.windows.net/data/uploads/solutions/3/x.pdf", "jdbc:postgresql://db/data/app.db", "mqtt://broker/data/logs/x", "opc.tcp://plc:4840/data/models/v1",
    "data/uploads/", "S3 버킷 data/uploads/ 에 저장", "data/decks/", "data/logs/metrics 통합 수집",
])
def test_m2_real_remote_schemes_stay_addresses(text):
    assert not has_internal_mark(text), text


# ================= M3: 선형 시험 자식은 부모가 바뀌었을 때만 끝낸다 =================
def test_m3_child_baseline_is_taken_at_start_and_exits_with_a_reason_only_on_change():
    # 시작할 때 부모 번호가 달라도(부모가 알린 번호가 낡아도) 바로 끝내지 않고, 시작 뒤 부모가 바뀌면 사유를 stderr 로 남기고 끝낸다
    code = (
        "import os, sys, time\n"
        "from tests import _linear\n"
        "_linear._exit_when_parent_dies()\n"
        "print('alive', flush=True)\n"
        "time.sleep(3)\n"
        "print('still', flush=True)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30, cwd=_REPO)
    assert "alive" in r.stdout and "still" in r.stdout, (r.stdout, r.stderr)


@pytest.mark.skipif(sys.platform == "win32", reason="/proc 로 자식의 상태를 본다")
def test_m3_child_prints_a_reason_to_stderr_when_the_parent_is_gone(tmp_path):
    root = _REPO
    err = tmp_path / "err.txt"
    child = f"import sys, time\nsys.path.insert(0, {root!r})\nfrom tests import _linear\n_linear._exit_when_parent_dies()\nprint('ready', file=sys.stderr, flush=True)\ntime.sleep(60)\n"
    # 부모는 자식이 기준(부모 번호)을 잡은 뒤에야 끝난다 (자식이 뜨기 전에 죽으면 기준이 이미 고아의 번호다)
    parent = ("import subprocess, sys, time\n"
              f"p = subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.DEVNULL, stderr=open({str(err)!r}, 'w'))\n"
              f"for _ in range(200):\n    if 'ready' in open({str(err)!r}).read():\n        break\n    time.sleep(0.1)\n"
              "print(p.pid, flush=True)\n")
    pid = int(subprocess.run([sys.executable, "-c", parent], capture_output=True, text=True, timeout=60).stdout.split()[0])
    import time
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().split(")")[-1].split()[0] == "Z":
                    break
        except (ProcessLookupError, FileNotFoundError):
            break
        time.sleep(0.3)
    else:
        os.kill(pid, 9)
        pytest.fail("the child did not exit after its parent was gone")
    assert "parent changed" in err.read_text(), err.read_text()
