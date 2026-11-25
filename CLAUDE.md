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

## 운영 규칙 (main = 회사 PC 솔박스 배포)

회사 PC 는 push 하지 않고 main 을 pull 만 해서 솔박스(`solbox/`)를 운영해요. 개발·테스트는 모두 클라우드 세션에서 해요.

- **main 은 회사 PC 솔박스의 운영 배포 브랜치예요.** main 에 커밋이 들어가는 순간 회사 PC 의 관리 › 시스템 "시스템 업데이트" 버튼에 새 버전으로 떠요(`solbox/app/update.py` 가 `origin/main` 을 확인). 솔박스와 상관없는 커밋(개인 앱)도 새 커밋 수에 세어져요.
- **작업은 `claude/*` 브랜치에서 해요.** main 에 바로 커밋하지 않아요.
- **solbox 를 건드렸으면 `solbox/` 안에서 테스트를 통과시킨 뒤에만 main 에 병합해요:** `cd solbox && python -m pytest -q -W error` (경고도 오류). GitHub Actions(`.github/workflows/solbox-tests.yml`)도 같은 테스트를 돌려요.
- **병합한 브랜치는 바로 지워요** (원격 `git push origin --delete <브랜치>`).
- **병합 전에 사용자에게 먼저 알려요:** solbox 의 DB 마이그레이션(`solbox/app/migrate.py`, 모델 컬럼 추가·변경)이나 패키지(`solbox/pyproject.toml`) 변경이 들어 있을 때. 회사 PC 업데이트 때 백업과 `pip install -e` 가 일어나요 (pyproject.toml 이 바뀌면 pip 설치, 업데이트마다 백업).
- **개인 앱(`sub3/`·`ohdap/`·`mitjul/`)은 회사 PC 에 받지 않아요.** 회사 PC 는 sparse-checkout 으로 `solbox/` 만 받아요(cone 모드라 루트 파일 `CLAUDE.md`·`README.md`·`.gitignore` 는 같이 받아져요). 그래서 개인 앱에 필요한 파일을 `solbox/` 에 두거나, 솔박스가 `solbox/` 밖 파일에 기대게 만들지 않아요.
