# 오답노트

틀린 문제를 사진으로 올리면 AI가 문제를 옮겨 적고 정답·여러 풀이·핵심 개념·흔한 실수를 정리해 주는 학생용 앱이에요. 초등 3학년부터 고등 3학년까지 쓰고, 초등이면 쉬운 말·큰 글자·별 모으기·세로셈 판서로 바뀌어요.

- 판서 보기: 풀이가 칠판에 한 줄씩 나타나고 목소리로 설명해요(재생·멈춤·이전/다음·속도).
- 복습: 비슷한 문제를 맞히면 1일 → 3일 → 7일 → 14일 → 정복. AI를 못 쓰면 원래 문제로 복습해요.
- 저장: claude.ai 계정 개인 저장소(로그인 안 되면 이 기기). 사진은 아티팩트 파일 저장소.

## 게시와 사용

- **보호자 claude.ai 계정으로 게시**하고 학생은 그 계정이 로그인된 기기에서 써요(claude.ai 계정은 만 18세 이상). 앱 하나 = 학생 한 명.
- 아티팩트: (게시 후 주소를 여기에 적어요)
- 게시 파일은 빌드 결과인 `index.html` 하나. 기능: `sample`(사진 읽기), `db`, `user`, `assets`, `tts`, `downloads`.

## 개발

명령은 `ohdap/` 안에서 실행해요.

```bash
npm i            # KaTeX(빌드 때 index.html 안에 넣음)
npm run build    # src/ → index.html
npm test         # 단위 테스트(node --test)
npm run e2e      # 화면 테스트(Playwright, 가짜 AI·저장소)
```

- 코드는 `src/`의 작은 모듈. `tools/build.mjs`의 `FILES` 순서로 이어 붙이므로 **모듈 최상위 이름이 서로 겹치면 안 돼요.** 새 모듈은 `FILES`에 넣어요.
- 기능을 바꾸면 `src/main.js`의 `APP_VERSION`을 올리고 다시 빌드해 `index.html`도 함께 커밋해요.
- 설계: `docs/superpowers/specs/2026-11-20-ohdap-design.md`, 계획: `docs/superpowers/plans/2026-11-20-ohdap.md`, UI 시안: https://claude.ai/artifact/7wRW1L9uK4yDvMKdMUTJui
