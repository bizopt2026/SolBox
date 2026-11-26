"""베타 T: 올리기에서 글 붙여넣기 (붙여넣은 글은 올린 .txt 파일 하나와 같은 길을 간다)."""
import re
from datetime import datetime

import pytest

from app import intake as intake_lib
from app.models import AIJob, Intake
from tests.conftest import nloc

NONE = "파일을 고르거나 글을 붙여넣어 주세요."


def _post(client, text="", title="", files=(), tok=None, note=""):
    data = {"note": note, "pasted_text": text, "pasted_title": title}
    if tok:
        data["_tok"] = tok
    parts = [("files", f) for f in files] or [("files", ("", b"", "application/octet-stream"))]  # 브라우저처럼 늘 multipart (파일을 안 골라도 빈 파일 칸이 간다)
    return client.post("/intake", data=data, files=parts, follow_redirects=False)


def _read(settings, f):
    return intake_lib.stored_path(settings, f).read_bytes()


def test_paste_only_creates_one_txt_file(user_client, db, settings):
    r = _post(user_client, "안녕하세요\r\n회의 메모입니다\r\n", title="한빛병원 미팅")
    it = db.query(Intake).one()
    assert nloc(r) == f"/intake/{it.id}?notice=intake_uploaded" and it.status == "analyzing"
    assert [(f.original_name, f.kind, f.read_status) for f in it.files] == [("한빛병원 미팅.txt", "document", "ok")]
    assert _read(settings, it.files[0]) == "안녕하세요\n회의 메모입니다\n".encode("utf-8")  # CRLF 는 LF 로
    assert "회의 메모입니다" in (settings.intake_work_dir / str(it.id) / "1-한빛병원 미팅.txt.md").read_text(encoding="utf-8")


def test_lone_cr_is_normalised_and_text_is_not_stripped(user_client, db, settings):
    _post(user_client, "  가\r나\r\n\r\n다  ")
    f = db.query(Intake).one().files[0]
    assert _read(settings, f) == "  가\n나\n\n다  ".encode("utf-8")


def test_default_name_uses_server_local_time(user_client, db):
    before = datetime.now().replace(second=0, microsecond=0)
    _post(user_client, "내용")
    name = db.query(Intake).one().files[0].original_name
    m = re.fullmatch(r"붙여넣은 글 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\.txt", name)
    assert m and datetime.strptime(m.group(1), "%Y-%m-%d %H:%M") >= before


@pytest.mark.parametrize("title,expected", [
    ("../../etc/passwd", "passwd.txt"),
    ("a\\b\\c메모", "c메모.txt"),
    ("메모.TXT", "메모.TXT"),
    ("줄\n바꿈", "줄 바꿈.txt"),
    ("  ", None),
    ("/", None),
    ("가" * 80, "가" * 60 + ".txt"),
])
def test_title_is_sanitised_like_uploaded_names(user_client, db, title, expected):
    _post(user_client, "내용", title=title)
    name = db.query(Intake).one().files[0].original_name
    if expected is None:
        assert name.startswith("붙여넣은 글 ") and name.endswith(".txt")
    else:
        assert name == expected


def test_paste_plus_files_make_one_batch(user_client, db, settings):
    r = _post(user_client, "붙여넣은 내용", title="메일", files=[("a.txt", b"file a", "text/plain"), ("b.txt", b"file b", "text/plain")])
    assert r.status_code == 303
    it = db.query(Intake).one()
    assert sorted(f.original_name for f in it.files) == ["a.txt", "b.txt", "메일.txt"]
    assert {f.original_name: _read(settings, f) for f in it.files}["메일.txt"] == "붙여넣은 내용".encode("utf-8")


def test_nothing_is_rejected(user_client, db):
    r = _post(user_client)
    assert r.status_code == 400 and NONE in r.text and db.query(Intake).count() == 0
    r = user_client.post("/intake", data={"note": "메모만"}, follow_redirects=False)  # 칸 이름이 아예 없는 옛 요청
    assert r.status_code == 400 and NONE in r.text and "메모만" in r.text


@pytest.mark.parametrize("text", [" ", "\n\n\t \r\n", "　"])
def test_whitespace_only_counts_as_empty(user_client, db, text):
    r = _post(user_client, text, title="제목만 있음")
    assert r.status_code == 400 and NONE in r.text and db.query(Intake).count() == 0


def test_whitespace_text_with_a_file_just_uploads_the_file(user_client, db):
    _post(user_client, "  \n ", files=[("a.txt", b"x", "text/plain")])
    assert [f.original_name for f in db.query(Intake).one().files] == ["a.txt"]


def test_over_limit_is_rejected_and_nothing_stored(user_client, db, settings):
    text = "가" * (intake_lib.MAX_PASTE + 1)
    r = _post(user_client, text, title="긴 글", files=[("a.txt", b"x", "text/plain")])
    assert r.status_code == 400 and "붙여넣은 글은 200,000자까지 올릴 수 있습니다." in r.text
    assert "긴 글" in r.text and text in r.text  # 쓴 것은 그대로 남는다
    assert db.query(Intake).count() == 0
    assert not (settings.uploads_dir / "intake").exists() or not any((settings.uploads_dir / "intake").iterdir())


def test_exactly_the_limit_is_accepted_and_crlf_counts_as_one(user_client, db, settings):
    text = "가\r\n" * (intake_lib.MAX_PASTE // 2)  # 정규화하면 MAX_PASTE 자
    assert _post(user_client, text).status_code == 303
    assert len(_read(settings, db.query(Intake).one().files[0]).decode("utf-8")) == intake_lib.MAX_PASTE


def test_paste_counts_toward_max_files(user_client, db):
    files = [(f"f{i}.txt", f"내용{i}".encode(), "text/plain") for i in range(intake_lib.MAX_FILES)]
    r = _post(user_client, "붙여넣은 글", title="먼저", files=files)
    assert r.status_code == 200 and f"한 번에 {intake_lib.MAX_FILES}개까지" in r.text and "f19.txt" in r.text
    it = db.query(Intake).one()
    assert len(it.files) == intake_lib.MAX_FILES and "먼저.txt" in [f.original_name for f in it.files]  # 글은 잘려 나가지 않는다


def test_script_text_is_escaped_on_error(user_client, db):
    evil = "<script>alert(1)</script>" + "가" * intake_lib.MAX_PASTE
    r = _post(user_client, evil, title='"><img src=x onerror=alert(2)>')
    assert r.status_code == 400
    assert "<script>alert(1)" not in r.text and "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert "<img src=x" not in r.text and "&lt;img src=x" in r.text


def test_double_submit_with_same_token_creates_one_batch(user_client, db):
    first = _post(user_client, "한 번만", title="연타", tok="t-paste")
    second = _post(user_client, "한 번만", title="연타", tok="t-paste")
    assert first.status_code == 303 and second.status_code == 303
    assert db.query(Intake).count() == 1
    assert second.headers["location"].split("?")[0] == first.headers["location"].split("?")[0]


def test_error_releases_token_so_the_fixed_form_can_be_resent(user_client, db):
    assert _post(user_client, "", tok="t-fix").status_code == 400
    assert _post(user_client, "고쳐서 다시", tok="t-fix").status_code == 303
    assert db.query(Intake).count() == 1


def test_home_card_posts_text(user_client, db, settings):
    home = user_client.get("/intake/new").text  # UX 개선(2026-11-26): 홈의 올리기 칸을 없앴다. 같은 칸이 올리기 화면에 있다
    form = re.search(r'<form[^>]*action="/intake".*?</form>', home, re.S).group(0)
    assert 'name="pasted_text"' in form and "글 붙여넣기" in form and 'name="files" multiple required' not in form
    r = user_client.post("/intake", data={"note": "", "pasted_text": "홈에서 붙여넣음"}, follow_redirects=False)
    assert r.status_code == 303
    f = db.query(Intake).one().files[0]
    assert _read(settings, f) == "홈에서 붙여넣음".encode("utf-8") and f.original_name.endswith(".txt")


def test_new_page_has_paste_area_and_no_required_file_input(user_client):
    page = user_client.get("/intake/new").text
    assert 'name="pasted_text"' in page and 'name="pasted_title"' in page and "회의 메모, 메일, 웹페이지 글을 붙여넣으세요" in page
    assert "0</span> / 200,000자" in page and 'for="intake-paste"' in page
    tag = re.search(r'<input[^>]*name="files"[^>]*>', page).group(0)
    assert "multiple" in tag and "required" not in tag
    assert "붙여넣" in page and "메일·회의 메모 붙여넣기" in page


def test_error_page_keeps_typed_text_and_title(user_client):
    r = _post(user_client, "x" * (intake_lib.MAX_PASTE + 1), title="내 제목")
    assert 'value="내 제목"' in r.text and "200,001" in r.text


@pytest.mark.ai_gate
def test_ai_off_keeps_the_batch_without_a_job(app, user_client, db, settings):
    r = _post(user_client, "AI 꺼진 때 붙여넣음", title="보관")
    assert r.status_code == 303 and "notice=intake_kept_ai_off" in r.headers["location"]
    it = db.query(Intake).one()
    assert it.status == "uploaded" and it.job_id is None and db.query(AIJob).count() == 0
    assert it.files[0].original_name == "보관.txt" and _read(settings, it.files[0]) == "AI 꺼진 때 붙여넣음".encode("utf-8")


def test_detail_shows_pasted_file_as_text_document(user_client, db):
    _post(user_client, "상세 확인", title="메모")
    it = db.query(Intake).one()
    page = user_client.get(f"/intake/{it.id}").text
    assert "메모.txt" in page and "문서" in page
