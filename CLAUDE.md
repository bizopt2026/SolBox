# 저장소 안내

이 저장소는 앱마다 폴더를 나눠 관리해요.

| 폴더 | 앱 | 작업 규칙 |
|---|---|---|
| `sub3/` | 써브쓰리 (마라톤 훈련 앱, 예전 이름 Sub-3 훈련 체크, claude.ai 아티팩트 https://claude.ai/artifact/S65CCJg77Gy9NKmqZ1bzzQ) | `sub3/CLAUDE.md` — 버전 올리기, 버전별·날짜별 복제본 프롬프트 규칙 |
| `mitjul/` | 밑줄 앱 | `mitjul/README.md` |
| `ohdap/` | 오답노트 (초3~고3 오답 정리·판서 설명·복습 앱, claude.ai 아티팩트 https://claude.ai/artifact/HtbsY9Etd4woGQiqQud2TC) | `ohdap/README.md` — `npm run build`로 `index.html` 생성, `npm test`·`npm run e2e`, 모듈 최상위 이름 중복 금지, `APP_VERSION` 올리기 |
| `solbox/` | 솔박스 (사내 웹 앱: 솔루션 발굴, 활동 기록, AI 제안서, 주간 리서치) | `solbox/CLAUDE.md` — 도구 분리·지식 사본·데이터 위치 규칙, 실행·테스트 명령 |

- 사용자와는 한국어로 대화해요.
- 써브쓰리 앱을 고칠 때는 반드시 `sub3/CLAUDE.md` 규칙을 따라요.
- 솔박스를 고칠 때는 반드시 `solbox/CLAUDE.md` 규칙을 따르고, 명령은 `solbox/` 폴더 안에서 실행해요.
