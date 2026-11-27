"""상세·폼 화면 도우미: 작성자·수정 시각 줄, 오류 문구가 가리키는 칸."""
from datetime import datetime

from app import ontology, ui
from app.models import Solution
from tests.conftest import make_user


def test_stamp_info_names_author_and_dates(db):
    kim = make_user(db, "kim", name="김대리")
    s = Solution(name="솔", created_by_id=kim.id)
    db.add(s)
    db.commit()
    info = ui.stamp_info(db, s)
    assert info["author"] == "김대리"
    assert isinstance(info["created"], datetime) and isinstance(info["updated"], datetime)


def test_stamp_info_without_author(db):
    s = Solution(name="솔")
    db.add(s)
    db.commit()
    assert ui.stamp_info(db, s)["author"] is None


def test_error_field_points_at_the_field_the_message_is_about():
    assert ui.error_field("이름을 입력하세요.") == "name"
    assert ui.error_field("고객을 선택하세요.") == "customer_id"
    assert ui.error_field("존재하지 않는 파트너입니다.") == "partner_id"
    assert ui.error_field("알 수 없는 단계입니다.") == "stage"
    assert ui.error_field(f"존재하지 않는 {ontology.label('bm')}입니다.") == "bm_id"
    assert ui.error_field("메모를 입력하세요.") == "raw_text"
    assert ui.error_field("") is None and ui.error_field(None) is None
    assert ui.error_field("알 수 없는 문제") is None


def test_crumbs_start_with_the_nav_group():
    assert ui.crumbs("solution", "냉동창고") == [("소스", None), ("솔루션", "/solutions"), ("냉동창고", None)]
    assert ui.crumbs("solution", "냉동창고", "/solutions/3", "수정") == [
        ("소스", None), ("솔루션", "/solutions"), ("냉동창고", "/solutions/3"), ("수정", None)]
    assert ui.crumbs("customer", last="등록") == [("영업", None), ("고객관리", "/customers"), ("등록", None)]
    assert ui.crumbs("activity", "회의")[0] == ("오늘", None)
