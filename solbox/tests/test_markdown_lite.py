import re
import time

from app.markdown_lite import render_markdown as md
from app.models import Activity, ProposalMessage
from tests.conftest import refining_proposal


def test_headings():
    assert str(md("# a\n## b\n### c")) == "<h3>a</h3><h4>b</h4><h5>c</h5>"
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


def test_unsafe_links_and_images_stay_literal():
    out = str(md("[a](javascript:alert(1)) ![i](x.png) [b](data:text/html,x) [c](//evil.com) [d](ftp://x)"))
    assert "<a" not in out and "<img" not in out and "[a](javascript:alert(1))" in out and "[c](//evil.com)" in out


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
    assert "<h4>제목</h4>" in page and "<li>하나</li>" in page
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
    assert "<h4>진행</h4>" in page and "<strong>중</strong>" in page


def test_activity_summary_rendered(user_client, db):
    a = Activity(title="t", kind="meeting", raw_text="r", summary_md="## 논의\n- 전력비 <b>x</b>")
    db.add(a)
    db.commit()
    page = user_client.get(f"/activities/{a.id}").text
    assert "<h4>논의</h4>" in page and "<li>전력비 &lt;b&gt;x&lt;/b&gt;</li>" in page


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


# ---- 링크 ----

def test_http_links_become_safe_anchors():
    out = str(md("보세요 [공식 문서](https://example.com/a?b=1&c=2) 와 [내부](http://intranet/x)"))
    assert '<a href="https://example.com/a?b=1&amp;c=2" rel="noopener noreferrer" target="_blank">공식 문서</a>' in out
    assert '<a href="http://intranet/x" rel="noopener noreferrer" target="_blank">내부</a>' in out


def test_link_xss_attempts_are_escaped():
    for text in ('[x](https://a.com/"onmouseover="alert(1))', "[x](https://a.com/'><script>alert(1)</script>)",
                 '[<img src=x onerror=alert(1)>](https://a.com)', "[x](https://a.com/**b**)", "[**b**](https://a.com)"):
        out = str(md(text))
        assert "<script" not in out and "<img" not in out and 'onmouseover="' not in out, text
        for href in re.findall(r'href="([^"]*)"', out):
            assert "<" not in href and '"' not in href and "strong" not in href, (text, href)
    assert "&lt;script&gt;" in str(md("[x](https://a.com/'><script>alert(1)</script>)"))


def test_bold_does_not_reach_into_an_href():
    out = str(md("[a](https://a.com/**b**) **c**"))
    assert 'href="https://a.com/**b**"' in out and "<strong>c</strong>" in out


# ---- 표 ----

def test_pipe_tables():
    out = str(md("| 이름 | 값 |\n|---|:-:|\n| **가** | 1 |\n| 나 |\n\n끝"))
    assert out == ('<div class="md-table"><table><thead><tr><th>이름</th><th>값</th></tr></thead><tbody>'
                   '<tr><td><strong>가</strong></td><td>1</td></tr><tr><td>나</td><td></td></tr></tbody></table></div><p>끝</p>')


def test_table_cells_are_escaped_and_plain_pipes_stay_text():
    out = str(md("| <b>x</b> | y |\n|--|--|\n| <script>1</script> | [a](javascript:1) |"))
    assert "<script" not in out and "<b>" not in out and "&lt;script&gt;" in out
    assert "<table" not in str(md("a | b 만 있는 한 줄"))
    assert "<table" not in str(md("| 구분선 없음 |\n| 값 |"))


def test_table_input_is_linear():
    for text in ("|a" * 100_000 + "\n" + "|-" * 50_000, ("|a|b|\n|-|-|\n" + "|1|2|\n" * 20_000), "[" * 200_000, "[a](http://" * 20_000, "`solutions/1/" * 20_000):
        t = time.perf_counter()
        md(text)
        assert time.perf_counter() - t < 1.0


# ---- 사본 경로는 출처 칩 ----

def _resolve(path):
    known = {"solutions/2/files/5-소개.pdf.md": {"url": "/solutions/2", "label": "소개.pdf (공조 솔루션)", "kind": "자료"}}
    return known.get(path, {"url": None, "label": path, "kind": ""})


def test_known_mirror_paths_become_source_chips_and_unknown_stay_text():
    text = "근거는 solutions/2/files/5-소개.pdf.md 입니다. `solutions/2/files/5-소개.pdf.md` 도. 없는 것 customers/99.md 는 그대로."
    out = str(md(text, _resolve))
    assert out.count('<a class="chip" href="/solutions/2"><span class="chip-kind">자료</span>소개.pdf (공조 솔루션)</a>') == 2
    assert "customers/99.md" in out and "<code>solutions" not in out
    assert "<a" not in str(md(text))  # 조회가 없으면 글자 그대로


def test_chip_labels_are_escaped():
    out = str(md("solutions/2/files/5-소개.pdf.md", lambda p: {"url": '/x"><script>', "label": "<img src=x>", "kind": "<b>"}))
    assert "<script" not in out and "<img" not in out and "<b>" not in out


def test_chat_page_renders_chips_tables_and_links(user_client, db, settings):
    from app.models import Solution, SolutionFile

    p = refining_proposal(db, settings)
    s = Solution(name="공조 솔루션", stage="boxed")
    db.add(s)
    db.flush()
    f = SolutionFile(solution_id=s.id, original_name="소개.pdf", stored_name="x", kind="intro")
    db.add(f)
    db.flush()
    path = f"solutions/{s.id}/files/{f.id}-소개.pdf.md"
    db.add(ProposalMessage(proposal_id=p.id, role="assistant", content=f"근거 {path}\n\n| a | b |\n|--|--|\n| 1 | 2 |\n\n[링크](https://example.com)"))
    db.commit()
    page = user_client.get(f"/proposals/{p.id}").text
    assert f'<a class="chip" href="/solutions/{s.id}">' in page and "<table>" in page and 'rel="noopener noreferrer"' in page
