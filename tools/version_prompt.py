#!/usr/bin/env python3
"""버전별 복제본 업데이트 프롬프트를 자동으로 만든다.

이전 버전 커밋의 app/index.html 과 지금(작업 트리 또는 지정 커밋)의 app/index.html 을
비교해서, 복제본 Claude가 그대로 따라 할 수 있는 '찾기 → 바꾸기' 목록을 만든다.
버전 값(APP_VERSION·APP_RELEASED·VLOG)은 프롬프트에서 뺀다.
만든 뒤 이전 파일에 그대로 적용해 지금 파일과 같아지는지 스스로 검증한다.

사용:
  python3 tools/version_prompt.py            # 작업 트리의 APP_VERSION 기준, 이전 버전 커밋과 비교
  python3 tools/version_prompt.py 1.5.4      # 특정 버전(그 버전 커밋과 직전 버전 커밋 비교)
  python3 tools/version_prompt.py --all      # 모든 버전 다시 만들기
결과: docs/prompts/versions/vX.Y.Z.md, docs/prompts/versions/README.md 표 갱신
"""
import difflib, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = 'app/index.html'
OUT = os.path.join(ROOT, 'docs', 'prompts', 'versions')
MIN_ANCHOR = 40


def git(*a):
    return subprocess.check_output(['git', '-C', ROOT] + list(a)).decode('utf-8')


def version_commits():
    """[(version, commit)] 오래된 것부터. 커밋 메시지가 'vX.Y.Z '로 시작하는 것."""
    out = []
    for line in git('log', '--reverse', '--format=%H %s').splitlines():
        h, _, msg = line.partition(' ')
        m = re.match(r'v(\d+\.\d+\.\d+)\b', msg)
        if m:
            out.append((m.group(1), h))
    return out


def app_at(rev):
    return git('show', rev + ':' + APP) if rev else open(os.path.join(ROOT, APP), encoding='utf-8').read()


def version_of(src):
    m = re.search(r'var APP_VERSION="([^"]+)",APP_RELEASED="([^"]+)"', src)
    return (m.group(1), m.group(2)) if m else (None, None)


def vlog_entry(src, ver):
    m = re.search(r'\["' + re.escape(ver) + r'","([^"]*)","((?:[^"\\]|\\.)*)","((?:[^"\\]|\\.)*)"\]', src)
    if not m:
        return None
    unq = lambda s: s.replace("\\'", "'").replace('\\"', '"')
    return m.group(1), unq(m.group(2)), unq(m.group(3))


def vlog_span(src):
    """버전 값 영역(APP_VERSION 줄 ~ VLOG 배열 끝) 문자 범위."""
    a = src.index('var APP_VERSION=')
    b = src.index('var VLOG=[', a)
    e = src.index('\n];', b) + 3
    return a, e


def neutral(src):
    a, e = vlog_span(src)
    return src[:a] + '\x00VERSION\x00' + src[e:]


def hunks(old, new):
    """문자 단위 변경 구간 [(o1,o2,n1,n2)] (줄 diff 후 각 블록 안에서 앞뒤 공통 부분을 잘라냄)."""
    ol, nl = old.splitlines(True), new.splitlines(True)
    ooff, noff = [0], [0]
    for x in ol: ooff.append(ooff[-1] + len(x))
    for x in nl: noff.append(noff[-1] + len(x))
    res = []
    sm = difflib.SequenceMatcher(None, ol, nl, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        o1, o2, n1, n2 = ooff[i1], ooff[i2], noff[j1], noff[j2]
        while o1 < o2 and n1 < n2 and old[o1] == new[n1]:
            o1 += 1; n1 += 1
        while o2 > o1 and n2 > n1 and old[o2 - 1] == new[n2 - 1]:
            o2 -= 1; n2 -= 1
        res.append([o1, o2, n1, n2])
    return res


def anchor(old, new, h):
    """변경 구간을 앞뒤로 넓혀 이전 파일에서 딱 한 번만 나오는 '찾기' 문자열을 만든다."""
    o1, o2, n1, n2 = h
    a, b = o1, o2
    step = 0
    while True:
        s = old[a:b]
        if len(s) >= MIN_ANCHOR and s.strip() and old.count(s) == 1:
            break
        step += 1
        # 줄 경계까지 우선 넓히고, 그래도 겹치면 한 줄씩 더
        na = old.rfind('\n', 0, a - 1) + 1 if a > 0 else 0
        nb = old.find('\n', b + 1)
        nb = len(old) if nb < 0 else nb
        if step <= 2 and (b - a) < 4000:
            # 먼저 문자 단위로 조금씩 넓혀 짧은 앵커를 시도
            a2, b2 = max(0, a - 24), min(len(old), b + 24)
            if old.count(old[a2:b2]) == 1 and len(old[a2:b2]) >= MIN_ANCHOR:
                a, b = a2, b2
                break
            a, b = a2, b2
            continue
        if na == a and nb == b:
            break
        a, b = na, nb
    # 단어 중간에서 잘리지 않게 앞뒤를 단어 경계까지 넓힘
    wc = lambda ch: ch.isalnum() or ch in '_-.$#'
    while a > 0 and wc(old[a - 1]) and (a == len(old) or wc(old[a])):
        a -= 1
    while b < len(old) and wc(old[b]) and b > 0 and wc(old[b - 1]):
        b += 1
    pre, post = o1 - a, b - o2
    return a, b, n1 - pre, n2 + post


def build(old, new):
    oldn, newn = neutral(old), neutral(new)
    hs = hunks(oldn, newn)
    wins = [anchor(oldn, newn, h) for h in hs]
    # 겹치거나 맞닿은 구간 합치기
    wins.sort()
    merged = []
    for w in wins:
        if merged and w[0] <= merged[-1][1]:
            m = merged[-1]
            merged[-1] = (m[0], max(m[1], w[1]), m[2], max(m[3], w[3]))
        else:
            merged.append(w)
    edits = []
    for a, b, c, d in merged:
        f, r = oldn[a:b], newn[c:d]
        if '\x00VERSION\x00' in f or '\x00VERSION\x00' in r:
            raise SystemExit('버전 영역과 겹치는 변경이 있어요. 손으로 정리해 주세요.')
        edits.append((f, r))
    # 검증: 이전 파일에 차례로 적용하면 새 파일과 같아야 함
    t = oldn
    for f, r in edits:
        if t.count(f) != 1:
            raise SystemExit('찾기 문자열이 한 번만 나오지 않아요: ' + f[:80])
        t = t.replace(f, r, 1)
    if t != newn:
        raise SystemExit('검증 실패: 적용 결과가 새 파일과 달라요')
    return edits


def fence(s):
    f = '```'
    while f in s:
        f += '`'
    return f


def where(new, r):
    i = new.find(r)
    if i < 0:
        return ''
    s = new.rfind('<style', 0, i); se = new.rfind('</style>', 0, i)
    return ' (CSS)' if s > se else ''


def render(ver, date, title, desc, prev, edits, new):
    L = []
    L.append('# 복제본 업데이트: v%s (%s) %s\n' % (ver, date, title))
    L.append('> 아래 전체를 복사해서, **복제한 \'Sub-3 훈련 체크\' 앱을 만든 Claude 대화**에 붙여 넣으세요.')
    L.append('> `<여기에 내 앱 링크>` 자리에 내 앱(복제본) 링크를 넣어 주세요.')
    L.append('> **v%s까지 적용한 앱**을 기준으로 자동 생성했어요(원본 v%s → v%s 차이).\n' % (prev, prev, ver))
    L.append('---\n')
    L.append('내 아티팩트 "Sub-3 훈련 체크"에 아래 업데이트를 적용해서 **같은 URL에 다시 게시**해 줘.\n')
    L.append('- 아티팩트: <여기에 내 앱 링크>\n')
    L.append('## 0. 먼저 지킬 것')
    L.append('1. `artifact-capabilities` 스킬과 `artifact-design` 스킬을 먼저 로드해.')
    L.append('2. 아티팩트를 `read`로 읽고, **그 원본 파일을 고쳐서** 같은 `url`로 재게시해. 새 아티팩트를 만들지 마.')
    L.append('3. 게시할 때 `capabilities`와 `contract`를 넘기지 마. 기존 권한 선언을 그대로 이어가.')
    L.append('4. 아래에 적은 곳만 고치고, 다른 기능은 건드리지 마.')
    L.append('5. 버전 값(APP_VERSION·VLOG)은 바꾸지 마. 복제한 앱의 버전(`APP_VERSION`, `APP_RELEASED`, `VLOG`)은 복제 시점 값으로 고정해서 관리해.')
    L.append('6. \'찾기\' 문자열은 파일에서 한 번만 나와. 그대로 없으면 같은 역할을 하는 곳을 찾아 같은 결과가 나오게 고치고, 무엇을 바꿨는지 알려줘.\n')
    L.append('## 1. 바뀌는 내용')
    L.append('- **%s**' % title)
    if desc:
        L.append('- ' + desc)
    L.append('')
    L.append('## 2. 고칠 곳 (위에서부터 차례로, %d곳)\n' % len(edits))
    for k, (f, r) in enumerate(edits, 1):
        L.append('**%d.%s**\n' % (k, where(new, r)))
        ff, rf = fence(f), fence(r)
        L.append('찾기:\n%s\n%s\n%s' % (ff, f, ff))
        L.append('바꾸기:\n%s\n%s\n%s\n' % (rf, r, rf))
    L.append('## 3. 확인하고 알려줘')
    L.append('- 문법 오류 없이 페이지가 열리는지')
    L.append('- 위 \'바뀌는 내용\'대로 보이고 동작하는지')
    L.append('- 게시 후 저장된 권한 선언이 이전과 같은지')
    return '\n'.join(L) + '\n'


def update_index(rows):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, 'README.md')
    head = ('# 버전별 복제본 업데이트 프롬프트\n\n'
            '원본 앱 버전이 오를 때마다 `tools/version_prompt.py`가 자동으로 만들어 저장해요.\n'
            '각 파일은 **바로 앞 버전까지 적용한 복제본**을 기준으로, 그 버전에서 바뀐 곳만 담아요.\n'
            '복제본 버전이 여러 단계 뒤처졌으면 낮은 버전부터 차례로 적용하세요. 날짜별 묶음은 [../README.md](../README.md)에 있어요.\n\n'
            '| 버전 | 날짜 | 내용 | 파일 | 고칠 곳 |\n|---|---|---|---|---|\n')
    old = {}
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            m = re.match(r'\| (\d+\.\d+\.\d+) \|', line)
            if m:
                old[m.group(1)] = line.rstrip('\n')
    for r in rows:
        old[r[0]] = '| %s | %s | %s | [v%s.md](v%s.md) | %d |' % (r[0], r[1], r[2].replace('|', '/'), r[0], r[0], r[3])
    keys = sorted(old, key=lambda v: [int(x) for x in v.split('.')], reverse=True)
    open(p, 'w', encoding='utf-8').write(head + '\n'.join(old[k] for k in keys) + '\n')


def make(ver=None):
    vc = version_commits()
    if ver is None:  # 작업 트리
        new = app_at(None)
        ver, date = version_of(new)
        prevs = [x for x in vc if x[0] != ver]
        if not prevs:
            raise SystemExit('이전 버전 커밋이 없어요')
        prev, prev_rev = prevs[-1]
    else:
        idx = [i for i, x in enumerate(vc) if x[0] == ver]
        if not idx:
            raise SystemExit('v%s 커밋을 찾지 못했어요' % ver)
        i = idx[-1]
        if i == 0:
            print('v%s: 이전 버전이 없어 건너뜀' % ver); return None
        new = app_at(vc[i][1]); prev, prev_rev = vc[i - 1]
        date = version_of(new)[1]
    old = app_at(prev_rev)
    ent = vlog_entry(new, ver) or (date, '', '')
    edits = build(old, new)
    md = render(ver, ent[0] or date, ent[1], ent[2], prev, edits, new)
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, 'v%s.md' % ver), 'w', encoding='utf-8').write(md)
    print('v%s ← v%s: %d곳, 검증 통과 → docs/prompts/versions/v%s.md' % (ver, prev, len(edits), ver))
    return (ver, ent[0] or date, ent[1], len(edits))


if __name__ == '__main__':
    a = sys.argv[1:]
    rows = []
    if a and a[0] == '--all':
        for v, _ in version_commits():
            r = make(v)
            if r: rows.append(r)
    else:
        r = make(a[0].lstrip('v') if a else None)
        if r: rows.append(r)
    update_index(rows)
