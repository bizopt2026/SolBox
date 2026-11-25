# 밑줄

책에서 마음에 닿은 문장에 밑줄을 긋고, 선명이와 사유를 나누는 독서 앱입니다. 하나의 HTML 파일(`index.html`)로 되어 있고, claude.ai 아티팩트로 게시해 사용합니다.

- 아티팩트: https://claude.ai/artifact/7yvtd9p2PWqxwCyHZkFBWz

## 작업 규칙

- 밑줄 앱(`index.html`)을 고치면 **항상** 아티팩트에 올리고 main에 합쳐요. 사용자에게 따로 묻지 않아요.
  1. 올리기 전에 `git fetch`로 main의 `mitjul/index.html`이 바뀌었는지 확인하고, 바뀌었으면 먼저 합쳐요. 아티팩트가 그사이 다른 곳에서 바뀌었으면 게시가 거절되니, 그 내용을 합친 뒤 다시 올려요.
  2. 아티팩트(위 주소)에 `index.html`을 게시해요.
  3. main에는 `mitjul/` 파일만 커밋해 올려요. 작업 브랜치에 다른 앱의 커밋이 섞여 있어도 함께 올리지 않아요.
- 커밋 메시지 끝에 아티팩트 버전을 적어요. 예: `밑줄: 독서 캘린더 정리 (Version 167)`
- `index.html`을 고칠 때마다 `const MJ_BUILD = …;` 판 번호를 1 올려요. 판 번호는 아티팩트 버전과 따로 세요.

## 복제본 업데이트 (npm)

다른 사람이 복제한 밑줄도 **더보기 › 업데이트 확인**으로 새 판을 받아요. 기록(아티팩트 저장소)은 그대로 남고 화면 코드만 바뀌어요.

- 아티팩트는 외부에서 파일을 받을 수 없고, npm CDN(`cdn.jsdelivr.net/npm/`, `unpkg.com`)의 스크립트만 불러올 수 있어요. GitHub 주소는 막혀 있어요.
- 그래서 main 에 `mitjul/index.html`이 들어오면 GitHub Actions(`.github/workflows/mitjul-npm.yml`)가 `mitjul/npm/build.mjs`로 `index.html`을 `app.js` 문자열로 묶어 npm 패키지 `mitjul@<MJ_BUILD>.0.0`으로 올려요. 같은 판이 이미 있으면 건너뛰어요.
- 앱은 `MJ_BUILD + 1`부터 판을 하나씩 불러 보고(빠진 번호는 3개까지 건너뜀), 가장 높은 판의 `html`로 자기 자신을 다시 게시해요(`artifact` 기능). 주인만 업데이트할 수 있고, 하루 한 번 앱을 열 때 조용히 확인해요.
- 아티팩트 선언에 `artifact` 기능이 있어야 해요. 이 기능이 들어간 원본(Version 178 이후)을 복제한 사람부터 업데이트를 받을 수 있어요.
- npm 올리기는 토큰 없이 npm 신뢰 게시(Trusted Publishing: 저장소 `bizopt2026/bizopt2026`, 워크플로 `mitjul-npm.yml`)로 해요. 워크플로 파일 이름을 바꾸면 npm 패키지 설정의 Trusted Publisher 도 같이 바꿔야 해요.
