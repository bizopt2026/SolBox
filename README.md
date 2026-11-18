# 솔루션 박스

솔루션 발굴 과정(아이템 접수 → 사전 태핑 → 수요조사 → 박스 등록)과 파트너·고객·기존 사업·활동 기록을 한곳에 모으는 사내 웹 앱입니다. AI(Claude CLI)는 메모 정리와 소개 초안 작성을 돕는 보조 기능이며, AI가 동작하지 않아도 조회와 직접 입력은 그대로 쓸 수 있습니다.

## 준비물

- Python 3.11 이상
- (AI 기능을 쓸 때) Claude CLI. **앱을 실행하는 PC에 설치하고, 앱을 실행하는 Windows 계정과 같은 계정으로 로그인**해 두어야 합니다. 다른 PC나 다른 계정에서 로그인한 것은 소용없습니다.

## 설치

프로젝트 폴더에서 PowerShell을 열고 아래 명령을 하나씩 실행합니다.

```powershell
python -m pip install -e .
```

## 설정 파일 만들기

예시 파일을 복사해 `config.toml` 을 만듭니다.

```powershell
copy config.example.toml config.toml
```

메모장 등으로 `config.toml` 을 열어 다음을 확인합니다.

- `secret_key`: **반드시 길고 무작위한 값으로 바꾸세요.** 비어 있거나 `change-me` 그대로면 서버가 시작되지 않습니다. 아래 명령으로 만들 수 있습니다.
  ```powershell
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
- `data_dir`: 데이터(`app.db`, `uploads/`, `knowledge/`, `decks/`)를 저장할 폴더. 상대 경로는 `config.toml` 이 있는 폴더 기준입니다. Windows 경로는 `data_dir = "C:/solbox/data"` 처럼 `/` 로 쓰거나 `data_dir = 'C:\solbox\data'` 처럼 작은따옴표로 감싸세요 (큰따옴표 안의 `\` 는 오류가 납니다).
- `host`, `port`: 기본은 `127.0.0.1`, `8443` 입니다. 다른 PC에서도 접속하게 하려면 `host = "0.0.0.0"` 으로 바꿉니다.
- `ssl_certfile`, `ssl_keyfile`: 인증서와 키 파일 경로. 둘 다 적으면 HTTPS로, 적지 않으면 HTTP로 열립니다. 자체 서명 인증서는 `python -m app.cli make-cert --host <접속 주소>` 로 만들고, 출력되는 두 줄을 넣습니다.

## 관리자 계정 만들기

처음 한 번, 관리자 계정을 만듭니다. 아이디·이름·비밀번호는 원하는 값으로 바꾸세요.

```powershell
python -m app.cli create-admin --username admin --name 관리자
```

비밀번호는 실행하면 입력창에서 두 번 물어봅니다 (화면에 보이지 않습니다).

일반 사용자는 관리자로 로그인한 뒤 화면 위쪽 "관리 → 사용자"에서 추가합니다.

## 실행

```powershell
python -m app
```

브라우저에서 아래 주소를 엽니다.

- 인증서를 설정하지 않았을 때: http://127.0.0.1:8443
- `ssl_certfile`, `ssl_keyfile` 을 설정했을 때: https://127.0.0.1:8443 (자체 서명 인증서는 브라우저 경고가 나올 수 있습니다)

포트나 주소를 바꿨다면 `config.toml` 의 값에 맞춰 열면 됩니다. 종료는 실행 창에서 `Ctrl+C` 입니다.

## 2단계: 제안

고객에게 낼 제안서(PPT)를 AI와 함께 만드는 흐름입니다. 화면 위쪽 "제안" 메뉴에서 시작합니다.

1. 제안 건을 만들 때 고객과 요청 메모를 적습니다. 박스에 등록된 솔루션이 조합 대상이고, 다른 단계의 솔루션은 제안 건에서 따로 골라 넣을 수 있습니다.
2. AI가 제안 방향 2~3개를 근거(출처)와 함께 내놓습니다. 하나를 선택하면 구체화 단계로 넘어갑니다.
3. 채팅으로 범위와 강조점을 다듬습니다. 답변은 글자가 늘어나며 보입니다.
4. "제안서 만들기"를 누르면 AI가 6~14장의 슬라이드 구성안을 만듭니다. 구성안 화면에서 장별로 고치고, 옮기고, 지우고, 추가할 수 있습니다. 자료에 근거가 없는 내용은 "확인 필요"로 표시되며, 출처와 확인 필요 항목은 슬라이드 본문이 아니라 검토 화면과 슬라이드 노트에만 나옵니다.
5. "PPT 만들기"로 파일을 만들고 내려받습니다. 구성안을 고치면 "구성안이 바뀌었습니다. 다시 생성하세요." 가 보이니 다시 만드세요. 구성안 수정과 PPT 만들기에는 AI가 필요 없습니다.

만든 PPT는 `data_dir` 아래 `decks/` 에 저장되고, 제안 건을 지우면 함께 지워집니다.

## 3단계: 리서치와 서버 운영

- 관리 → 리서치 설정에서 주제와 실행 주기를 정하면 매주 시장·기술·정책 웹 조사와 발굴 방향 제안이 만들어지고, "리서치" 메뉴에서 봅니다.
- 관리 → 시스템에서 버전, 데이터 폴더, 최근 백업, AI 작업 수를 보고 "AI 연결 확인"과 "지금 백업"을 쓸 수 있습니다.
- 명령: `python -m app.cli check`(설치 점검), `make-cert --host <주소>`(HTTPS 인증서), `backup`, `backup --verify <폴더>`.
- 설치·인증서·방화벽·자동 시작·백업·복원·업데이트·문제 해결은 [docs/운영가이드.md](docs/운영가이드.md), 서버 PC로 옮기는 작업은 [docs/서버이관_Claude용.md](docs/서버이관_Claude용.md) 를 보세요. `scripts/` 의 PowerShell 스크립트는 아직 Windows 에서 실행 확인이 안 됐습니다.
- PowerShell 스크립트는 Windows 10/11 의 기본 실행 정책(Restricted)에서 막히므로 항상 `powershell -ExecutionPolicy Bypass -File .\scripts\<이름>.ps1 <인자>` 로 실행합니다 (저장소 폴더에서).

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\run_server.ps1
  ```

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1
  ```

  `install_autostart.ps1` 은 서버를 실행할 Windows 계정으로 실행합니다 (등록에 권한이 더 필요하다는 "액세스가 거부되었습니다" 가 나오면 같은 계정으로 관리자 PowerShell 을 새로 열어 다시 실행). 방화벽 규칙은 **관리자 PowerShell(새 세션이므로 저장소 폴더로 다시 이동)** 에서 만들고 지웁니다.

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\open_firewall.ps1 -Port 8443
  ```

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\open_firewall.ps1 -Port 8443 -Remove
  ```

## knowledge 폴더 다시 만들기

`data_dir` 아래 `knowledge/` 는 DB 내용을 AI가 읽도록 옮겨 둔 사본입니다. 사본이 지워졌거나 어긋났다면 DB를 기준으로 통째로 다시 만들 수 있습니다. 올려 둔 자료 파일의 텍스트도 원본에서 다시 뽑습니다.

```powershell
python -m app.cli rebuild-knowledge
```

## 테스트 실행

```powershell
python -m pip install -e ".[dev]"
```

```powershell
python -m pytest -q
```

(`[dev]` 항목이 없다면 `python -m pip install pytest httpx` 로 대신합니다.)

## 실제 Claude CLI 수동 확인 목록

자동 테스트는 가짜 CLI로 돌립니다. 실제 PC에 설치한 Claude CLI로는 아래를 한 번 직접 확인하세요.

1. `claude --help` 에 `-p`, `--output-format`, `--allowedTools`, `--tools`, `--strict-mcp-config` 가 모두 있는지 확인합니다. 이름이 다르면 `app/ai/runner.py` 의 명령 구성을 맞춥니다.
2. 활동 기록에 메모를 붙여 넣고 "AI로 정리" → 초안이 나오고 저장되는지 확인합니다.
3. 솔루션에 자료 파일을 올리고 "AI로 소개 초안 만들기" → 초안이 채워지는지 확인합니다.
4. 두 브라우저에서 동시에 요청 → 한쪽에 "AI 대기 2번째" 가 보이는지 확인합니다.
5. Claude CLI에서 로그아웃한 뒤 요청 → 로그인 안내문이 보이고, 목록 조회와 수동 저장은 정상인지 확인합니다.

2단계(제안)에서는 아래도 확인하세요.

6. `claude --help` 에 `--include-partial-messages`, `--verbose` 가 있는지 확인합니다. 없으면 `app/ai/runner.py` 의 스트리밍 명령 구성을 맞춥니다.
7. 박스 등록 솔루션 2개 이상과 자료 파일을 넣고 제안 건을 만듭니다 → 방향 2~3개와 출처가 보이는지 확인합니다.
8. 방향을 고른 뒤 채팅으로 질문합니다 → 답변이 글자가 늘어나며 보이는지 확인합니다.
9. 구성안에서 한 장을 고치고 PPT를 만들어 PowerPoint에서 엽니다 → 글꼴·배치가 정상이고, 슬라이드 노트에 출처가 있는지 확인합니다.
10. AI를 끈 상태(Claude CLI에서 로그아웃)에서 제안 건 목록 조회, 기존 구성안 수정, PPT 만들기가 되는지 확인합니다.
