import time

from app.markdown_lite import render_markdown as md
from app.models import Activity, ProposalMessage
from tests.conftest import refining_proposal


def test_headings():
    assert str(md("# a\n## b\n### c")) == "<h4>a</h4><h5>b</h5><h6>c</h6>"
    assert "<h" not in md("#### d") and "#### d" in md("#### d")
    assert "<h" not in md("#없음")


def test_bullets_numbers_and_continuation():
    assert str(md("- a\n* b")) == "<ul><li>a</li><li>b</li></ul>"
    assert str(md("1. a\n2. b")) == "<ol><li>a</li><li>b</li></ol>"
    assert str(md("- a\n  이어서\n- b")) == "<ul><li>a<br>이어서</li><li>b</li></ul>"
    assert str(md("- a\n1. b")) == "<ul><li>a</li></ul><ol><li>b</li></ol>"


def test_bold_code_paragraphs():
    assert str(md("**굵게** 와 `code`")) == "<p><strong>굵게</strong> 와 <code>code</code></p>"
    assert str(md("`**x**`")) == "<p><code>**x**</code></p>"
    assert str(md("한 줄\n두 줄\n\n새 문단")) == "<p>한 줄<br>두 줄</p><p>새 문단</p>"
    assert str(md("문단\n- 목록")) == "<p>문단</p><ul><li>목록</li></ul>"
    assert str(md("")) == "" and str(md("\r\na\r\nb")) == "<p>a<br>b</p>"


def test_html_is_escaped():
    out = str(md("<script>alert(1)</script>"))
    assert "<script" not in out and "&lt;script&gt;" in out
    out = str(md("**<b>x</b>**"))
    assert out == "<p><strong>&lt;b&gt;x&lt;/b&gt;</strong></p>"
    out = str(md("- <img src=x onerror=1>\n## <i>t</i>"))
    assert "<img" not in out and "<i>" not in out


def test_links_stay_literal():
    out = str(md("[a](javascript:alert(1)) ![i](x.png)"))
    assert "<a" not in out and "<img" not in out and "[a](javascript:alert(1))" in out


def test_unbalanced_markers_literal():
    assert str(md("**열림만")) == "<p>**열림만</p>"
    assert str(md("`열림만")) == "<p>`열림만</p>"
    assert str(md("**a\nb**")) == "<p>**a<br>b**</p>"


def test_large_input_is_fast():
    for text in ("**a " * 70_000, "`a" * 100_000, "- 가나다 **굵게** `c`\n" * 10_000, "*" * 200_000, "#" * 200_000):
        t = time.perf_counter()
        md(text)
        assert time.perf_counter() - t < 1.0


def test_proposal_page_renders_assistant_markdown_not_user(user_client, db, settings):
    p = refining_proposal(db, settings)
    db.add(ProposalMessage(proposal_id=p.id, role="assistant", content="## 제목\n- 하나\n<script>alert(1)</script>"))
    db.add(ProposalMessage(proposal_id=p.id, role="user", content="## 내 말\n**그대로**"))
    db.commit()
    page = user_client.get(f"/proposals/{p.id}").text
    assert "<h5>제목</h5>" in page and "<li>하나</li>" in page
    assert "&lt;script&gt;" in page and "<script>alert(1)" not in page
    assert "## 내 말\n**그대로**" in page


def test_chat_live_fragment_renders_markdown(user_client, db, settings):
    from app.models import AIJob
    p = refining_proposal(db, settings)
    job = AIJob(kind="proposal_chat", status="running", result="## 진행\n**중**", input={"proposal_id": p.id})
    db.add(job)
    db.flush()
    p.active_job_id = job.id
    db.commit()
    page = user_client.get(f"/proposals/{p.id}/chat/live").text  # 부분 답변은 대화 구역 조각에 나온다
    assert "<h5>진행</h5>" in page and "<strong>중</strong>" in page


def test_activity_summary_rendered(user_client, db):
    a = Activity(title="t", kind="meeting", raw_text="r", summary_md="## 논의\n- 전력비 <b>x</b>")
    db.add(a)
    db.commit()
    page = user_client.get(f"/activities/{a.id}").text
    assert "<h5>논의</h5>" in page and "<li>전력비 &lt;b&gt;x&lt;/b&gt;</li>" in page


def test_loose_lists_stay_one_list():
    assert str(md("1. a\n\n2. b\n\n3. c")) == "<ol><li>a</li><li>b</li><li>c</li></ol>"
    assert str(md("- a\n\n- b")) == "<ul><li>a</li><li>b</li></ul>"
    assert str(md("- a\n\n1. b")) == "<ul><li>a</li></ul><ol><li>b</li></ol>"
    assert str(md("- a\n\n문단")) == "<ul><li>a</li></ul><p>문단</p>"


def test_many_blank_lines_before_item_is_fast():
    for text, expect in (("- a" + "\n" * 40000 + "- b", "<ul><li>a</li><li>b</li></ul>"),
                         ("1. a" + "\n" * 40000 + "2. b", "<ol><li>a</li><li>b</li></ol>")):
        t = time.perf_counter()
        out = str(md(text))
        assert time.perf_counter() - t < 1.0
        assert out == expect
