"""버튼 위계: 수정은 보조 모양의 링크, 삭제는 위험 모양, 저장·만들기는 주 동작."""
import re

from app.models import Solution


def test_detail_head_puts_edit_then_delete_in_title_row(user_client, db):
    s = Solution(name="AI 에너지 절감", created_by_id=1)
    db.add(s)
    db.commit()
    html = user_client.get(f"/solutions/{s.id}").text
    head = re.search(r'<div class="page-head">(.*?)</div>\s*</div>', html, re.S).group(1)
    assert "<h1>AI 에너지 절감</h1>" in head
    assert head.index(f'class="button" href="/solutions/{s.id}/edit"') < head.index('class="danger"')


def test_forms_have_one_primary_button(user_client):
    html = user_client.get("/customers/new").text
    assert html.count('class="primary"') == 1
    assert 'class="primary">저장' in html


def test_run_now_is_primary_on_research_settings(admin_client):
    assert 'class="primary">지금 조사' in admin_client.get("/admin/research").text
