"""Claude CLI 실행기. 프롬프트는 표준 입력으로 넘기고, 모든 실패는 ClaudeError 로 돌려준다."""
from __future__ import annotations

import json
import logging
import re
import os
import shutil
import signal
import subprocess
import sys
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

# 허용 도구 목록은 보안 경계다. FILE_TOOLS 나 WEB_TOOLS 에 도구를 더하면 경계가 넓어진다.
FILE_TOOLS = ("Read", "Glob", "Grep")
WEB_TOOLS = ("WebSearch", "WebFetch")
DEFAULT_TOOLS = FILE_TOOLS

# 로그인 문제는 낱말 경계가 있는 표현만 본다 (일반 답변의 "SSO login", "certificate has expired" 같은 글이 로그인 문제가 되지 않게).
# 그리고 CLI 의 오류 출력(표준 오류, 오류로 끝난 결과 글)에서만 찾는다: `error_text`.
# `api key` 앞에 다른 낱말(`Tavily API key`·`OpenAI api key`·`the weather api key`)이 있으면 Claude 로그인이 아니다. 관사·꾸밈말(`The API key`·`Provided API key`)은 괜찮다.
_VENDOR_OK = (r"(?:(?<!\w )|(?<=\bthe )|(?<=\bthis )|(?<=\bprovided )|(?<=\bgiven )|(?<=\bsupplied )|(?<=\bcurrent )|(?<=\binvalid )"
              r"|(?<=\byour )|(?<=\bclaude )|(?<=\banthropic )|(?<=\ban )|(?<=\ba ))")
_ENV_NAME = r"(?<![\w])(?:anthropic|claude)\w{0,40}(?:api_key|auth_token|oauth_token)"
_ENV_BAD = r"(?:not set|isn't set|is not set|missing|invalid|empty|required|unset|undefined|not defined|not found|expired|not valid|isn't valid)"
_AUTH_PATTERNS = re.compile(
    r"\bplease (?:log ?in|run /login)\b|(?<![\w.])/login\b|\bnot (?:logged|signed) in\b|\bmust be logged in\b|\blog ?in (?:required|failed)\b"
    # `invalid token in JSON`·`invalid token at position 3`·`invalid credentials for MCP server x` 는 로그인이 아니다.
    rf"|\binvalid (?:api[ _-]?key|x-api-key|(?:credentials?|token)(?!\s+(?:in|at|near|on|for)\b))\b|{_VENDOR_OK}\bapi[ _-]?key (?:(?:is|was|has) )?(?:invalid|missing|not set|not found|not valid|isn't valid|expired)\b(?!\s+(?:for|in|on|with)\b)|\bno api[ _-]?key (?:found|provided)\b|\bx-api-key header is (?:required|missing)\b"
    r"|\bunauthori[sz]ed\b(?!\s+tool)|\bunauthenticated\b|\bcould not resolve authentication method\b|\bclaude (?:login|setup-token)\b"
    # Claude 의 환경 변수 이름만 (`ANTHROPIC_API_KEY`·`CLAUDE_CODE_OAUTH_TOKEN`·`ANTHROPIC_BEDROCK_API_KEY`): `OPENAI_API_KEY`·`MY_ANTHROPIC_API_KEY` 는 아니다.
    # 이름 뒤 `:` 나 빈칸 (`\s*[:\s]\s*` 는 빈칸이 많이 이어지면 제곱으로 느려졌다: 두 갈래로 나눠 선형)
    rf"|\b{_ENV_NAME}(?:[ \t]+environment variable)?(?:[ \t]*:[ \t]*|[ \t]+)(?:(?:is|are|was)[ \t]+)?{_ENV_BAD}\b|\b(?:missing|invalid|no|(?:could not|couldn't|cannot|can't|unable to|failed to) find)\s+{_ENV_NAME}\b"
    # 401 은 상태 줄(`API Error: 401`, `HTTP 401`, `status 401`)로만 본다: 초·줄 번호·파일 번호·금액 속 401 은 로그인 문제가 아니다.
    r"|\b(?:error|http(?:/\d(?:\.\d)?)?|status(?: code)?)[: ]+401\b(?![\w/]|\.\w)"
    # `oauth` 는 낱말 하나만으로 보지 않는다 (`oauth-proxy` 같은 이름).
    r"|\bapi error[: ]*\(?401\b|\"status\"\s*:\s*401\b"
    r"|\boauth (?:error|token|authentication|failed|failure|login)\b"
    r"|\b(?:failed to authenticate|not authenticated|authentication[ _](?:error|failed|failure|required))\b"
    r"|\b(?:(?:oauth|access|auth|api|login) )?(?:token|credentials?) (?:has )?expired\b|\b(?:session|login) has expired\b",
    re.I,
)
# 로그인 다음에 본다. 시간 초과·네트워크 글이 한도 낱말에 가려지지 않게, 한도는 429 를 낱말 경계로 상태 줄에서만 본다.
_LIMIT_PATTERN = re.compile(r"usage limit|rate limit|quota|limit reached|hit your limit|\b(?:error|http|status(?: code)?)[: ]+429\b|\b429 too many|\bapi error[: ]*\(?429\b|\"status\"\s*:\s*429\b|\bcredit balance is too low\b", re.I)
# 프록시 인증(407)은 Claude 로그인 문제가 아니라 네트워크 문제다. 407 은 상태 줄(`statusCode=407`, `HTTP 407`, `Error: 407`)로만 본다 ("line 407: proxy_pass" 같은 번호는 아니다).
_PROXY_PATTERN = re.compile(
    # `proxy auth(entication) required` 만 본다 (`mcp-proxy auth ok`·`proxy auth plugin loaded` 는 로그인·한도 신호를 가리지 않는다). `exit code: 407` 은 종료 코드다.
    r"\bproxy[ -]?auth(?:entication)?[ -]+required\b|\bauthentication required for (?:the )?proxy\b|\b407[ :\-]+proxy[ -]?auth"
    r"|(?<!exit )(?<!with )(?<!returned )(?<!return )\b(?:error|http(?:/\d(?:\.\d)?)?|status(?:[ _-]?code)?|(?:response )?code)[=: ]+407\b(?!\s+(?:files?|lines?|entries|items?|errors?|bytes|ms|seconds|times|rows|records|tokens|results?)\b)"
    r"|\bapi error[: ]*\(?407\b|\"status(?:_?code)?\"\s*:\s*407\b|\bproxy returned 407\b|\b407 from (?:the )?proxy\b|\bERR_TUNNEL_CONNECTION_FAILED\b",
    re.I,
)
_NETWORK_WORDS = ("enotfound", "econnrefused", "network", "getaddrinfo", "timed out", "connection error", "fetch failed", "econnreset", "etimedout")


# 사용자 화면·`AIJob.error` 에 남는 오류 글에서 비밀처럼 보이는 값을 가린다 (CLI 의 표준 출력·표준 오류가 그대로 들어올 수 있다).
# 가리는 쪽으로 기운다: **정확한 비밀 이름**(`token`·`api_key`·`secret`·`password`·`authorization` …, 앞에 `ANTHROPIC_`·`MY_`·`x-` 같은 이름 조각은 붙어도 된다) 바로 뒤에
# `=`·`:`·`=>`·`->`·`is`·`(`·`%3D` 가 오면 값이 짧아도(`token=abc`, `x-api-key: short1`) 어떤 모양이어도 가린다. 따옴표로 묶인 값은 닫는 따옴표까지(공백·줄바꿈 포함, 닫지 않았으면 줄 끝까지).
# 다음은 보통의 진단 글이라 그대로 둔다: 이름이 더 긴 낱말의 앞부분(`tokens`·`tokenizer`·`token_path`·`token_type`·`secretary`·`passwordless`·`apiKeySource` — 낱말이 이름 뒤에서 끝나지 않는다),
# 값이 진단 낱말(`not set`·`missing`·`required`·`expired`·`invalid`·`incorrect`·`none`·`undefined` …)이거나 소문자 낱말로 이어진 문장(`header is required`)이거나
# 소문자 스네이크 낱말(`invalid_request_error`, `unexpected_end_of_input`)·`***`·`<key>` 자리표시·`:` 뒤 한글 문장이거나, 따옴표 속이 두 글자 이하(`Unexpected token: '}'`)일 때.
# 이름 없이 값 모양만 보는 것(`sk-ant-…`·`sk-…` 긴 값·`Bearer …`·JWT·`ghp_…`·`AKIA…`·`://user:pass@`)과 이름 뒤 빈칸·탭만 오는 경우(`x-api-key X`, `--token X`)는 값이 비밀처럼 보일 때만 가린다
# (따옴표 값, 토큰 꼴 12자 이상, 숫자가 든 8자 이상). 반복 수는 모두 한정해(앞 이름 40자, 값 500자) 같은 낱말이 아주 많이 이어진 글에서도 선형으로 끝난다.
# 짧은 이름(`pass`·`pwd`·`auth`)은 이름 전체일 때만 본다 (앞에 `_`·`-` 말고 글자가 붙은 `compass`·`bypass`·`OAuth`·`OLDPWD` 는 아니다).
# `max_token`·`min_token` 은 한도 값이다. `API_KEY_V2` 처럼 `_v2`·`_2` 꼬리는 붙어도 된다.
_KEYWORD = (
    r"(?:api[ _.\-]?(?:key|키)|(?:auth|oauth|access|refresh|id|session|bearer|bot)[_\-]?token(?:[_\-](?:bedrock|value))?|secret(?:[_\-]?(?:access[_\-]?)?key)?(?:[_\-]?base)?|passw(?:or)?d|passphrase|private[ _\-]?key"
    r"|(?:access|anthropic|encryption|signing|license|master)[_\-]?key|credentials?|(?<![A-Za-z0-9])(?:pass|pwd|pw|auth)|(?<!max_)(?<!max-)(?<!min_)(?<!min-)token|비밀번호|패스워드|토큰|비번)"
)
_NAME = rf"(?P<name>(?<![A-Za-z0-9_\-])(?<!xpected )[A-Za-z0-9_\-]{{0,40}}{_KEYWORD}(?:[_\-]v?\d{{1,2}})?(?![A-Za-z0-9_]))"
_TOKEN_CH = r"[A-Za-z0-9_\-./+=]"
_QUOTED = r"\"(?:\\.|[^\"\\]){3,500}\"|'(?:\\.|[^'\\]){3,500}'|`[^`]{3,500}`|“[^”]{3,500}”|‘[^’]{3,500}’"
_VALUE = (  # 값 모양을 볼 때(이름 뒤 빈칸·탭만 있는 경우)
    rf"(?:{_QUOTED}|{_TOKEN_CH}{{12,}}|(?=[A-Za-z_\-./+=]{{0,10}}\d){_TOKEN_CH}{{8,11}}(?!{_TOKEN_CH}))"
)
_ANY_VALUE = rf"(?P<value>{_QUOTED}|[\"'“‘][^\n]{{3,500}}|\{{[^{{}}\n]{{0,500}}\}}|\[[^\[\]\n]{{0,500}}\]|\S{{1,300}})"
_LOOSE_SEP = (
    r"(?P<sep>[ \t]*=[ \t]*\\?\r?\n[ \t]*"                       # `KEY=` 줄 끝(`\` 이어쓰기 포함) 다음 줄
    r"|(?:\\?[\"'])?[ \t]*(?P<op>=>|->|=|:|%3[Dd])[ \t]*"   # 같은 줄 (`\\"api_key\\": …` 처럼 역슬래시로 이스케이프한 따옴표도)
    r"|[\"']?:[ \t]*\r?\n[ \t]{2,}"                         # YAML 들여쓰기 값
    r"|[ \t]+is[ \t]+"                                        # `api_key is …`
    r"|\()"                                                   # `apiKey(…)`
)
_DIAG_WORDS = frozenset(
    "not none null nil undefined unset empty missing required expired invalid incorrect wrong denied failed error unknown true false redacted hidden masked "
    "set present absent disabled enabled valid ok mismatch rejected revoked forbidden unauthorized unavailable no yes n/a at the a an is was has for to in of on and or "
    "your this that it".split()
)
_PLACEHOLDER_VALUE = re.compile(r"\*+|<[A-Za-z_\-]{1,20}>|\[?<?redact\w*>?\]?|-", re.I)
_SNAKE_WORD = re.compile(r"[a-z]+(?:_[a-z]+)+")
_LOWER_WORD = re.compile(r"[a-z]+")
_NEXT_WORD = re.compile(r"[ \t]+[a-z]+\b")


def _is_diagnostic(value: str, after: str, op: str | None) -> bool:
    """이름 뒤 값이 비밀이 아니라 진단 글의 한 조각인가 (`API key: invalid_request_error`, `token: expired`, `ANTHROPIC_API_KEY: not set`)."""
    if value[0] in "\"'`“‘" and len(value) >= 3 and value[-1] in "\"'`”’":
        return bool(_PLACEHOLDER_VALUE.fullmatch(value[1:-1]))  # 닫힌 따옴표 값은 가린다 (이미 가린 `"***"` 만 둔다)
    word = value.rstrip(".,;)}]\"'")
    if not word or _PLACEHOLDER_VALUE.fullmatch(word):
        return True
    if _SNAKE_WORD.fullmatch(word):
        return True
    if word.lower() in _DIAG_WORDS:
        return True
    if _LOWER_WORD.fullmatch(word) and _NEXT_WORD.match(after):
        return True  # 소문자 낱말로 이어진 문장: `header is required`
    if op == ":" and "가" <= word[0] <= "힣":
        return True  # `API_KEY: 설정되지 않았습니다`
    return False


_LOOSE = re.compile(rf"(?P<head>{_NAME}{_LOOSE_SEP}){_ANY_VALUE}", re.I)
_BARE = re.compile(rf"(?P<head>{_NAME}[ \t]+)(?P<value>{_VALUE})", re.I)
_AUTHORIZATION = re.compile(
    r"(?P<head>\bAuthorization[\"']?[ \t]*[=:][ \t]*(?P<scheme>(?:Bearer|Basic|Token|Digest|Negotiate|NTLM|AWS4-HMAC-SHA256|Bot|ApiKey)[ \t]+)?)"
    r"(?P<value>\"[^\"\n]{3,500}\"|'[^'\n]{3,500}'|[\"'][^\n]{3,500}|\[[^\[\]\n]{0,500}\]|\{[^{}\n]{0,500}\}|\S{1,300})", re.I)
_AUTH_LINE = re.compile(r"(?P<head>\bAuthorization[\"']?[ \t]*[=:][ \t]*(?:Digest|AWS4-HMAC-SHA256)[ \t]+)(?P<value>[^\n]{1,500})", re.I)
_COOKIE = re.compile(r"(?P<head>(?<![A-Za-z0-9_\-])(?:set-)?cookie[ \t]*[=:][ \t]*)(?P<value>[^\n]{1,500})", re.I)
_CLI_FLAG = re.compile(r"(?P<head>(?<![\w\-])(?:x-api-key|--(?:api[-_]?key|token|password|secret|access[-_]?token|auth[-_]?token))(?:[ \t]+|=))(?P<value>" + _VALUE + ")", re.I)
_QUERY = re.compile(r"(?P<head>(?<=[?&])(?:key|sig|signature|api[_\-]?key|apikey|token|access_token)=)(?P<value>[^&\s#]{4,300})", re.I)
_BEARER = re.compile(r"(?P<head>\bBearer\s+)(?P<value>(?=[A-Za-z]*[\d._~+/=\-])[A-Za-z0-9._~+/=\-]{12,}|sk-[A-Za-z0-9_\-]{3,})", re.I)
_USERINFO = re.compile(r"(?P<head>\b(?!mailto\b)[a-z][a-z0-9+.\-]{1,15}://)(?P<value>[^\s/@:]{0,100}:[^\s/?#]{1,200})(?=@)", re.I)  # 비밀번호 속 `@` 가 있으면 마지막 `@` 앞까지
_USERINFO_TOKEN = re.compile(  # `http://TOKEN@proxy`: 이름 없이 값만 (`ssh://git@host` 의 사용자 이름은 비밀 모양이 아니라 둔다)
    r"(?P<head>\b(?!mailto\b)[a-z][a-z0-9+.\-]{1,15}://)(?P<value>(?=[A-Za-z0-9._~+=\-]{0,10}\d)[A-Za-z0-9._~+=\-]{8,100}|[A-Za-z0-9._~+=\-]{16,100})(?=@)", re.I)
_CURL_USER = re.compile(r"(?P<head>(?<![\w\-])(?:-[uU]|--(?:proxy-)?user)(?:[ \t]+|=))(?P<value>(?![\d]+:\d)[^\s:]{1,100}:(?=\S{5,300})(?=[^\s]*[A-Za-z0-9])\S{5,300})")  # `-u 1000:1000`(숫자 사용자)·`sort -u a:bcd`(짧은 값)는 아니다
_ARGV_FLAG = re.compile(
    r"(?P<head>(?P<fq>[\"'])--?(?:api[-_]?key|token|password|passwd|secret|access[-_]?token|auth[-_]?token|proxy-user|user)(?P=fq)[ \t]*,[ \t]*)(?P<q>[\"'])(?!\*{3}(?P=q))(?P<value>[^\"'\n]{1,300})(?P=q)", re.I)
_PEM = re.compile(r"-----BEGIN[A-Z ]{0,30}PRIVATE KEY-----(?:[A-Za-z0-9+/=\s]|\\[nrt]){0,8000}(?:-----END[A-Z ]{0,30}PRIVATE KEY-----)?")  # 줄바꿈이 글자 `\n` 으로 든 것도
_WHOLE = tuple(re.compile(p) for p in (
    # 이 머리는 흔치 않아 왼쪽 경계가 필요 없다 (`KEY_sk-ant-…`·`\\nsk-ant-…`·`한글sk-ant-…`). 꼬리의 `.`·`/`·`+`·`=` 도 함께. `risk-ant-colony` 는 숫자·16자 모양이 아니라 둔다.
    # 앞에 영문자가 붙으면(`task-ant-1`·`disk-ant-2`) 진짜 키 모양(`api03-`·`oat01-`·`ort01-`·`admin01-`·`sid01-`)일 때만, 영문자가 없으면(`KEY_`·`0`·한글·줄 시작) 8자 이상이거나 숫자가 든 꼬리.
    r"(?<![A-Za-z])sk-ant-(?=[A-Za-z0-9_\-]{0,30}\d|[A-Za-z0-9_\-]{8})[A-Za-z0-9_\-./+=]+",
    r"(?<=[A-Za-z])sk-ant-(?:api03|oat01|ort01|admin01|sid01)-[A-Za-z0-9_\-./+=]*",
    r"sk-(?:proj|svcacct|admin|live|test)-[A-Za-z0-9_\-]{12,}",
    r"\bsk-(?=[A-Za-z0-9_\-]{0,60}\d)[A-Za-z0-9_\-]{20,}",
    r"\bsk-[A-Za-z0-9]{20,}",
    r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b|\b(?:xox[abprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16})\b",
))
MASK = "***"


_PATH_VALUE = re.compile(r"(?:~?/|\.{1,2}/|[A-Za-z]:[\\/])\S")
_PATH_ROOT = re.compile(r"^(?:~|\.{1,2}|[A-Za-z]:)?[\\/]+")
_REAL_PATH = re.compile(r"[^\s\\/]*[\\/]\S*|\S*\.[A-Za-z0-9]{1,6}")  # 뿌리(`/`·`./`·`~/`·`C:\`)를 뗀 나머지에 마디가 더 있거나 확장자가 있는 경로 (`/s3cr3t`·`./secret1` 은 아니다)
_SMALL_NUMBER = re.compile(r"\d{1,4}[,.;)]*")
_COUNT_VALUE = re.compile(r"[\d,]+(?:[가-힣]+)?[,.;)]*")  # `210000`·`210000개`·`8자`
_ALGO_VALUE = re.compile(r"(?:[RHEP]S\d{3}|EdDSA|aes[-_]?\d{3}(?:[-_][a-z0-9]+)*|chacha20(?:-poly1305)?|sha-?\d{3}|rsa(?:-\d+)?)[,.;)]*", re.I)  # `signing_key=RS256`·`ENCRYPTION_KEY=aes-256-gcm`
_AUTH_METHODS = frozenset("oauth apikey api bearer basic none login sso cookie session token key".split())
_TAIL = re.compile(r"(pwd|credentials?|auth|pass)(?:[_\-]v?\d{1,2})?\Z", re.I)


def _name_says_not_secret(name: str, value: str, sep: str) -> bool:
    """이름이 비밀 이름이어도 값이 그 이름의 다른 뜻이면 그대로 둔다: `PWD=/home/…`(작업 폴더), `credentials: /home/kim/.claude/.credentials.json`(파일),
    `auth: 401`·`pass: 3`(숫자), `auth: OAuth`(방식), `getAccessToken(/usr/lib/cli.js:1:2)`(스택 줄), `signing_key=RS256`(알고리즘), `토큰: 210000`(개수).
    숫자·방식 예외는 이름이 `pass`·`auth` 하나뿐일 때만 (`DB_PASS=1234`·`PROXY_AUTH=session` 은 비밀이다)."""
    first = value.split(None, 1)[0] if value.strip() else ""
    word = first.strip("\"'`“”‘’")
    if sep == "(" and _PATH_VALUE.match(first):
        return True
    if _ALGO_VALUE.fullmatch(word):
        return True
    if name.endswith(("토큰", "비밀번호", "패스워드")) and name != "" and _COUNT_VALUE.fullmatch(word) and (word[0].isdigit() and (name.endswith("토큰") or not word.isdigit())):
        return True  # `입력 토큰: 210000`·`비밀번호: 8자 이상이어야 합니다` (비밀번호는 숫자만이면 비밀일 수 있어 단위가 붙은 때만)
    tail = _TAIL.search(name)
    if not tail:
        return False
    kind = tail.group(1).lower()
    bare = tail.start() == 0
    if kind == "pwd" and bare and _PATH_VALUE.match(first):
        return True
    if kind in ("pwd", "credential", "credentials") and _PATH_VALUE.match(first) and _REAL_PATH.fullmatch(_PATH_ROOT.sub("", first.rstrip(",;"), count=1)):
        return True
    if bare and kind in ("auth", "pass") and _SMALL_NUMBER.fullmatch(first):
        return True
    return bare and kind == "auth" and first.rstrip(".,;)").lower() in _AUTH_METHODS


def _mask_loose(m: re.Match) -> str:
    sep = m.group("sep").strip()[-1:] if m.group("sep") else ""
    value = m.group("value")
    if value.lstrip("\"'`“‘").startswith(MASK):
        return m.group(0)  # 이미 가린 값 (두 번 가려도 같다)
    if _is_diagnostic(value, m.string[m.end():m.end() + 40], m.groupdict().get("op")):
        return m.group(0)
    if _name_says_not_secret(m.group("name"), value, "(" if sep == "(" else ""):
        return m.group(0)
    close = _CLOSERS.get(value[0])
    if close is not None and len(value) >= 5 and value[-1] == close:
        return m.group("head") + value[0] + MASK + close  # 따옴표를 지킨다: `{"api_key":"***","model":"x"}` 가 계속 JSON 이다
    return m.group("head") + MASK


_CLOSERS = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’"}


_FLAG_VALUE = re.compile(r"[\"'`]--?[A-Za-z][\w\-]*[\"'`]")


def _mask_bare(m: re.Match) -> str:
    if _FLAG_VALUE.fullmatch(m.group("value")):
        return m.group(0)  # `The token '--verbose' is not recognized`
    return m.group("head") + MASK


def _mask_cookie(m: re.Match) -> str:
    word = m.group("value").split(None, 1)[0].lower().rstrip(".,;)")
    if word in _DIAG_WORDS or _PLACEHOLDER_VALUE.fullmatch(word):
        return m.group(0)  # `Cookie: disabled`
    return m.group("head") + MASK


def _mask_authorization(m: re.Match) -> str:
    value, scheme = m.group("value").strip(), (m.group("scheme") or "").strip().lower()
    if scheme in ("digest", "aws4-hmac-sha256") or (scheme and not _LOWER_WORD.fullmatch(value.rstrip(".,;)"))) or (
        not scheme and not _is_diagnostic(value, m.string[m.end():m.end() + 40], ":")
    ):
        if len(value) >= 5 and value[0] in "\"'" and value[-1] == value[0]:
            return m.group("head") + value[0] + MASK + value[0]  # 따옴표를 지킨다
        return m.group("head") + MASK
    return m.group(0)  # `Authorization: required`, `Bearer authentication failed`, `Authorization: Bearer token expired`


def _mask_value(m: re.Match) -> str:
    return m.group("head") + MASK


_HOST_PORT_THEN_LIST = re.compile(r"[^:]*\.[A-Za-z]{2,}:\d{1,5}[,;]")
_KEBAB_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9.]+){2,}")


def _mask_userinfo(m: re.Match) -> str:
    if _HOST_PORT_THEN_LIST.match(m.group("value")):
        return m.group(0)  # `http://a.com:80,kim@kt.com`: 주소 다음에 쉼표로 이은 이메일
    return m.group("head") + MASK


def _mask_userinfo_token(m: re.Match) -> str:
    if _KEBAB_ID.fullmatch(m.group("value")):
        return m.group(0)  # `vertex://claude-sonnet-4-5@20250929`: 모델 이름@버전
    return m.group("head") + MASK


def _mask_quoted_pair(m: re.Match) -> str:
    """`['--api-key', 'X']`·`["claude","--token","X"]`: 따옴표를 지키고 값만 가린다."""
    q = m.group("q")
    return m.group("head") + q + MASK + q


def redact(text: str) -> str:
    """오류 글의 비밀 값(`sk-ant-…` 토큰, `*_API_KEY=…`·`"api_key": "…"`, `Authorization: Bearer …`, `x-api-key: …`, `password=…`, 주소 속 `user:pass@`, JWT)을 `***` 로 바꾼다."""
    text = text or ""
    if len(text) <= MAX_TEXT:
        return _redact_all(text)
    head, tail = _cap_parts(text)
    return _redact_all(head) + "\n…\n" + _redact_all(tail)  # 반쪽을 따로 가린다: 자르는 자리에 걸친 키가 반쪽만 보이지 않게


def _redact_all(out: str) -> str:
    out = _PEM.sub(MASK, out)
    for pat in _WHOLE:
        out = pat.sub(MASK, out)
    out = _USERINFO.sub(_mask_userinfo, out)
    out = _USERINFO_TOKEN.sub(_mask_userinfo_token, out)
    out = _AUTH_LINE.sub(_mask_value, out)
    out = _AUTHORIZATION.sub(_mask_authorization, out)
    out = _COOKIE.sub(_mask_cookie, out)
    out = _CURL_USER.sub(_mask_value, out)
    out = _ARGV_FLAG.sub(_mask_quoted_pair, out)
    out = _CLI_FLAG.sub(_mask_value, out)
    out = _QUERY.sub(_mask_value, out)
    out = _BEARER.sub(_mask_value, out)
    out = _LOOSE.sub(_mask_loose, out)
    return _BARE.sub(_mask_bare, out)


def _join(*parts: str | None) -> str:
    """빈 조각을 빼고 줄바꿈 하나로 잇는다 (빈 줄이 겹치지 않게)."""
    return "\n".join(p.strip() for p in parts if p and p.strip())


class ClaudeError(Exception):
    def __init__(self, kind: str, message: str = ""):
        message = redact(message)
        super().__init__(message)
        self.kind = kind
        self.message = message

    def __str__(self) -> str:
        return self.message


def assert_tool_separation(allowed_tools: tuple[str, ...] | list[str]) -> None:
    """도구 이름은 FILE_TOOLS 또는 WEB_TOOLS 와 정확히 같아야 하고, 두 종류를 섞을 수 없다."""
    if not isinstance(allowed_tools, (tuple, list)) or not all(isinstance(t, str) for t in allowed_tools):
        raise TypeError("allowed_tools 는 문자열의 tuple 또는 list 여야 합니다.")
    for name in allowed_tools:
        if name not in FILE_TOOLS and name not in WEB_TOOLS:
            raise ValueError(f"허용되지 않은 도구입니다: {name}")
    if any(t in WEB_TOOLS for t in allowed_tools) and any(t in FILE_TOOLS for t in allowed_tools):
        raise ValueError("웹 도구와 파일 도구를 함께 쓸 수 없습니다.")


MAX_TEXT = 200_000  # 분류·가리기에 쓰는 글의 최대 길이 (앞 절반 + 뒤 절반: 상태 줄은 보통 처음이나 끝에 있다)


_CUT_WINDOW = 2000


def _cap_parts(text: str) -> tuple[str, str]:
    """긴 글의 앞 절반과 뒤 절반. 자르는 자리는 공백에서 맞춘다(창 안에 공백이 없으면 뒤 조각의 앞 창을 버린다): 낱말 한가운데를 잘라 키의 뒷부분이 따로 남지 않게."""
    half = MAX_TEXT // 2
    head, tail = text[:half], text[-half:]
    cut = max(head.rfind(" ", half - _CUT_WINDOW), head.rfind("\n", half - _CUT_WINDOW))
    if cut > 0:
        head = head[:cut]
    k = min((i for i in (tail.find(" ", 0, _CUT_WINDOW), tail.find("\n", 0, _CUT_WINDOW)) if i >= 0), default=-1)
    tail = tail[k + 1:] if k >= 0 else tail[_CUT_WINDOW:]
    return head, tail


def _cap(text: str) -> str:
    """아주 긴 글은 앞뒤만 남긴다 (정규식이 선형이어도 수 MB 를 여러 번 훑지 않게)."""
    if len(text) <= MAX_TEXT:
        return text
    head, tail = _cap_parts(text)
    return head + "\n…\n" + tail


# 잘려 나간 가운데의 상태 줄만 싸게 찾는다 (정규식 한 개, 선형): 401·429·407 이 가운데에 있어도 놓치지 않게.
_MIDDLE_STATUS = re.compile(r"\b(?:api error|error|http(?:/\d(?:\.\d)?)?|status(?: code)?)[: ]+\(?(?P<a>401|429|407)\b(?![\w/]|\.\w)|\"status\"\s*:\s*(?P<b>401|429|407)\b", re.I)
_MIDDLE_TRIES = 1000  # 가운데에서 확인하는 후보 줄 수의 한도 (선형)
_MIDDLE_KIND = {"407": "network", "401": "auth", "429": "limit"}


def classify_error(text: str) -> str:
    text = text or ""
    if len(text) <= MAX_TEXT:
        return _classify(text)
    half = MAX_TEXT // 2
    kind = _classify(_cap(text))
    if kind == "unknown":  # 앞뒤에서 못 찾았으면 가운데의 상태 줄(401·429·407)만 본다
        for n, m in enumerate(_MIDDLE_STATUS.finditer(text, half, len(text) - half)):
            if n >= _MIDDLE_TRIES:
                break
            code = m.group("a") or m.group("b")
            lo = max(0, m.start() - _LINE_WINDOW)
            window = text[max(text.rfind("\n", lo, m.start()) + 1, lo):m.end() + 40]  # 그 줄만: 짧은 글과 같은 규칙(`error: 407 files could not be read` 는 네트워크가 아니다)으로 다시 확인한다
            pattern = {"407": _PROXY_PATTERN, "401": _AUTH_PATTERNS, "429": _LIMIT_PATTERN}[code]
            if pattern.search(window):
                return _MIDDLE_KIND[code]
    return kind


# 이름 없는 `API key` 글은 Claude 로그인이다. 같은 줄에 **알려진 다른 업체·도구 표지**(MCP, 검색·도구 업체 이름)가 있거나 `for` 바로 뒤에 Claude·Anthropic·Node 가 아닌 고유 이름(`for Tavily`)이 오면
# 그 업체의 키 문제라 로그인이 아니다 (`Tavily: invalid API key`·`Brave Search error: the API key is missing`·`Invalid API key for Tavily`·`No API key provided for OpenAI`).
# 알려진 표지가 없는 글(`node: Invalid API key`·`Execution error: Invalid API key`·`Invalid API key for this organization`)은 Claude 로그인이다 (베타 L: 거꾸로 바꿨다).
# Claude·Anthropic 환경 변수 이름이 든 일치에는 적용하지 않는다.
_API_KEY_MATCH = re.compile(r"api[ _-]?key", re.I)
_VENDOR_MARK = re.compile(
    r"\bmcp|(?<![\w/\\.@\-])(?:tavily|brave|openai|google|gemini|perplexity|serper|exa|github|gitlab|slack|notion|aws|azure|bedrock|vertex|cohere|mistral|groq|huggingface|firecrawl|jina|bing|duckduckgo|"
    r"stripe|twilio|sendgrid|jira|confluence|linear|figma|supabase|pinecone|elevenlabs|deepl|naver|kakao)(?![\w/\\@\-])", re.I)
_FOR_PROPER = re.compile(r"\bfor\s+(?:the\s+|your\s+|this\s+)?(?P<w>[A-Z][A-Za-z0-9]*)")
_NOT_VENDOR_WORDS = frozenset("claude anthropic node nodejs error api this the your request response organization account workspace user session cli sdk".split())
_LINE_WINDOW = 300


def _vendor_line(text: str, m: re.Match) -> bool:
    lo = max(0, m.start() - _LINE_WINDOW)  # 줄 시작을 찾는 범위도 한정한다 (줄바꿈 없는 긴 글에서 일치마다 처음까지 훑으면 제곱)
    start = max(text.rfind("\n", lo, m.start()) + 1, lo)
    nl = text.find("\n", m.end(), m.end() + _LINE_WINDOW)
    end = nl if nl >= 0 else min(len(text), m.end() + _LINE_WINDOW)
    line = text[start:end]
    if _VENDOR_MARK.search(line):
        return True
    f = _FOR_PROPER.search(text[m.end():end])
    return f is not None and f.group("w").lower() not in _NOT_VENDOR_WORDS


def _has_auth(text: str) -> bool:
    for m in _AUTH_PATTERNS.finditer(text):
        found = m.group(0)
        if _API_KEY_MATCH.search(found) and not re.search(r"anthropic|claude", found, re.I) and _vendor_line(text, m):
            continue
        return True
    return False


def _classify(text: str) -> str:
    lowered = text.lower()
    if _PROXY_PATTERN.search(text):
        return "network"
    if _has_auth(text):
        return "auth"
    if _LIMIT_PATTERN.search(text):
        return "limit"
    if any(w in lowered for w in _NETWORK_WORDS):
        return "network"
    return "unknown"


def error_text(result: str | None, stderr: str, *, result_is_error: bool, stdout: str = "", returncode: int | None = None) -> str:
    """분류에 쓸 글: 표준 오류 + 오류로 끝난 결과 글. 정상 답변이 들어 있을 수 있는 표준 출력·결과 글은 쓰지 않는다
    (JSON 이 아닌 표준 출력은 종료 코드가 0 이 아니고 읽은 결과가 없을 때만, 표준 오류가 있어도 함께)."""
    parts = []
    if result_is_error and isinstance(result, str):
        parts.append(result)
    parts.append(stderr)
    # 표준 오류에 Node 경고 같은 잡음이 있어도 JSON 이 아닌 표준 출력(`Invalid API key · Please run /login`)을 같이 본다.
    if not result_is_error and stdout.strip() and returncode not in (0, None) and result is None:
        parts.append(stdout)
    return _join(*parts)


def _kill_tree(proc: subprocess.Popen) -> None:
    """프로세스와 가능한 한 그 자식까지 죽인다 (best effort)."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, check=False, timeout=10,
            )
        elif getattr(proc, "returncode", None) is None:  # 이미 회수한 자식의 번호는 다른 프로세스 그룹에 재사용됐을 수 있어 killpg 를 부르지 않는다
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _reap(proc: subprocess.Popen) -> None:
    """죽인 프로세스를 회수한다. 손자 프로세스가 파이프를 쥐고 있어도 멈추지 않는다.

    Windows 에서는 `communicate` 가 띄운 읽기 스레드가 파이프 읽기에 걸려 있을 수 있고, 그 파일 객체의 `close()` 는 읽는 쪽이 쥔 잠금을 기다려 끝없이 멈춘다.
    그래서 거기서는 파이프를 닫지 않고 버린다 (프로세스가 이미 죽었으니 파이프는 곧 끝나고, 끝나지 않아도 스레드는 데몬이다). 그 밖에는 닫는다."""
    try:
        proc.communicate(timeout=5)
    except Exception:  # noqa: BLE001 - TimeoutExpired, 닫힌 파이프(ValueError, Windows 에서는 IndexError 도 가능)
        if sys.platform != "win32":
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if pipe is not None:
                        pipe.close()
                except OSError:
                    pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # 죽이지 못한 프로세스는 버린다 (한정 없이 기다리지 않는다)


def _wait_after_readers(proc: subprocess.Popen) -> None:
    """읽기·쓰기 스레드가 파이프를 모두 닫은 뒤 프로세스만 회수한다.

    이미 닫힌 파이프에 `communicate` 를 부르면 플랫폼마다 다르게 깨질 수 있다 (Windows 는 파이프마다 읽기 스레드를 새로 띄워
    닫힌 파일을 읽다 실패하고 Python 버전에 따라 IndexError 가 난다). 그래서 정상 종료 뒤에는 부르지 않는다."""
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.wait()


class ClaudeRunner:
    def __init__(self, cmd: list[str], cwd: Path):
        self.cmd = list(cmd)
        self.cwd = Path(cwd)

    def _spawn(
        self,
        output_args: list[str],
        allowed_tools: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        file_scope: bool = False,
    ) -> subprocess.Popen:
        """공통 명령 구성과 프로세스 시작. 실패는 ClaudeError 로 돌려준다.

        `file_scope` 이면 파일 도구를 작업 폴더 안으로만 허용한다 (`Read(./**)` 같은 경로 한정 규칙). 이름만 쓴 `Read` 는
        어느 경로든 미리 허용하는 것이라 `../` 나 절대 경로로 다른 파일을 읽을 수 있다. 파일 도구만 쓸 수 있다."""
        assert_tool_separation(allowed_tools)
        if file_scope and any(t not in FILE_TOOLS for t in allowed_tools):
            raise ValueError("파일 범위 제한은 파일 도구에만 쓸 수 있습니다.")
        approved = [f"{t}(./**)" for t in allowed_tools] if file_scope else list(allowed_tools)
        work_dir = Path(cwd) if cwd is not None else self.cwd
        exe = shutil.which(self.cmd[0])
        if exe is None:
            raise ClaudeError("not_installed", f"Claude CLI를 찾을 수 없습니다: {self.cmd[0]}")
        argv = [exe, *self.cmd[1:], "-p", *output_args,
                "--allowedTools", ",".join(approved),
                "--tools", ",".join(allowed_tools), "--strict-mcp-config"]
        work_dir.mkdir(parents=True, exist_ok=True)
        popen_kwargs: dict = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            return subprocess.Popen(
                argv, cwd=work_dir, text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **popen_kwargs,
            )
        except FileNotFoundError as e:
            raise ClaudeError("not_installed", str(e)) from e
        except OSError as e:
            raise ClaudeError("unknown", str(e)[-500:]) from e

    def run(
        self,
        prompt: str,
        *,
        allowed_tools: tuple[str, ...] | list[str] = DEFAULT_TOOLS,
        timeout_s: int = 600,
        cwd: Path | None = None,
        file_scope: bool = False,
    ) -> str:
        proc = self._spawn(["--output-format", "json"], allowed_tools, cwd, file_scope)

        try:
            stdout, stderr = proc.communicate(prompt, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            _reap(proc)
            raise ClaudeError("timeout", f"{timeout_s}초 안에 끝나지 않았습니다.") from None
        except BaseException:
            _kill_tree(proc)
            _reap(proc)
            raise

        stdout, stderr = stdout or "", stderr or ""
        data = None
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            pass

        if proc.returncode == 0 and data is not None and not data.get("is_error"):
            result = data.get("result")
            if isinstance(result, str):
                return result

        raw = _join(stdout, stderr)
        if data is not None and isinstance(data.get("result"), str):
            raw = _join(data["result"], stderr)
        shown = error_text(data.get("result") if data is not None else None, stderr, result_is_error=bool(data is not None and data.get("is_error")),
                           stdout=stdout, returncode=proc.returncode)
        raise ClaudeError(classify_error(shown), redact(raw)[-500:])

    def run_stream(
        self,
        prompt: str,
        *,
        on_text: Callable[[str], None],
        allowed_tools: tuple[str, ...] | list[str] = DEFAULT_TOOLS,
        timeout_s: int = 600,
        cwd: Path | None = None,
    ) -> str:
        """stream-json 으로 실행한다. 받은 텍스트를 누적해 on_text(누적)을 부르고 최종 결과 텍스트를 돌려준다."""
        proc = self._spawn(
            ["--output-format", "stream-json", "--verbose", "--include-partial-messages"], allowed_tools, cwd
        )
        stderr_parts: list[str] = []
        timed_out = False
        lines: queue.Queue = queue.Queue()
        deadline = time.monotonic() + timeout_s

        def write_prompt() -> None:
            try:
                proc.stdin.write(prompt)
                proc.stdin.flush()
            except (OSError, ValueError):
                pass  # 프로세스가 입력을 다 읽기 전에 끝났다. 결과 분류는 종료 상태로 한다.
            finally:
                try:
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass

        def drain_stderr() -> None:
            try:
                for chunk in iter(lambda: proc.stderr.read(4096), ""):
                    stderr_parts.append(chunk)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    proc.stderr.close()  # 읽기 스레드가 끝나면서 스스로 닫는다
                except (OSError, ValueError):
                    pass

        def read_stdout() -> None:
            # 손자 프로세스가 파이프를 쥐고 있어도 본 루프가 마감 시각에 멈출 수 있게 줄을 큐로 넘긴다.
            try:
                for line in proc.stdout:
                    lines.put(line)
            except (OSError, ValueError):
                pass
            finally:
                lines.put(None)
                try:
                    proc.stdout.close()
                except (OSError, ValueError):
                    pass

        writer = threading.Thread(target=write_prompt, name="claude-stdin", daemon=True)
        err_reader = threading.Thread(target=drain_stderr, name="claude-stderr", daemon=True)
        out_reader = threading.Thread(target=read_stdout, name="claude-stdout", daemon=True)
        threads = [writer, err_reader, out_reader]
        accumulated = ""
        parsed_events = 0
        plain_lines: list[str] = []
        result_line: dict | None = None
        try:
            for t in threads:
                t.start()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    line = lines.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    data = json.loads(line)
                except ValueError:
                    if sum(map(len, plain_lines)) < 4000:
                        plain_lines.append(line)  # JSON 이 아닌 줄(CLI 가 표준 출력으로 낸 오류 글)
                    continue
                if not isinstance(data, dict):
                    continue
                parsed_events += 1
                kind = data.get("type")
                if kind == "result":
                    result_line = data
                elif kind == "stream_event":
                    event = data.get("event")
                    delta = event.get("delta") if isinstance(event, dict) else None
                    if (isinstance(event, dict) and event.get("type") == "content_block_delta"
                            and isinstance(delta, dict) and delta.get("type") == "text_delta"
                            and isinstance(delta.get("text"), str)):
                        accumulated += delta["text"]
                        try:
                            on_text(accumulated)
                        except Exception:  # noqa: BLE001 - 콜백 오류로 실행을 멈추지 않는다
                            log.exception("on_text 콜백 오류")
            if not timed_out:
                try:
                    proc.wait(timeout=max(deadline - time.monotonic(), 1.0))
                except subprocess.TimeoutExpired:
                    timed_out = True
        except BaseException:
            _kill_tree(proc)
            raise
        finally:
            if timed_out or proc.poll() is None:
                _kill_tree(proc)
            for t in threads:
                t.join(timeout=1 if timed_out else 5)
            if out_reader.is_alive():
                # 읽는 중인 파이프는 여기서 닫지 않는다. 손자가 끝나 읽기 스레드가 끝나면 스레드가 stdout·stderr 를 스스로 닫는다.
                proc.wait()
                if not writer.is_alive():
                    try:
                        proc.stdin.close()
                    except (OSError, ValueError):
                        pass
            else:
                _wait_after_readers(proc)

        if timed_out:
            raise ClaudeError("timeout", f"{timeout_s}초 안에 끝나지 않았습니다.")

        stderr = "".join(stderr_parts)
        result = result_line.get("result") if result_line is not None else None
        if (proc.returncode == 0 and result_line is not None
                and not result_line.get("is_error") and isinstance(result, str)):
            return result
        raw = _join(result if isinstance(result, str) else "", stderr)
        if result is None and proc.returncode not in (0, None) and plain_lines:
            raw = _join(raw, "\n".join(l.rstrip() for l in plain_lines))
        if raw.strip():
            shown = error_text(result, stderr, result_is_error=bool(result_line is not None and result_line.get("is_error")),
                               stdout="\n".join(plain_lines), returncode=proc.returncode)
            raise ClaudeError(classify_error(shown), redact(raw)[-500:])
        # 아무 글도 없이 끝났다. 조용히 빈 답으로 넘기지 않고 이유를 분명히 적는다.
        if result_line is not None:
            raise ClaudeError("bad_output", "Claude 가 결과 글 없이 끝났습니다.")
        if proc.returncode == 0:
            if parsed_events == 0:
                raise ClaudeError("bad_output", "Claude 의 스트리밍 결과를 읽지 못했습니다 (알아볼 수 있는 이벤트가 없습니다).")
            raise ClaudeError("bad_output", "Claude 가 최종 결과를 보내지 않고 끝났습니다.")
        raise ClaudeError("unknown", f"Claude 가 결과 없이 끝났습니다 (종료 코드 {proc.returncode}).")
