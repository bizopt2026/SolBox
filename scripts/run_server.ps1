# 솔루션 박스 서버를 실행한다. 서버가 끝나면 10초 뒤 다시 시작한다 (무한 반복).
# 주의: Linux에서 작성되어 실행 검증이 되지 않았습니다. 처음 실행할 때 내용을 확인하세요.
param(
    # 파이썬 실행 파일. 가상환경을 쓰면 전체 경로를 넘긴다. 예: -PythonExe C:\solbox\.venv\Scripts\python.exe
    [string]$PythonExe = "python"
)
$ErrorActionPreference = "Stop"

# 저장소 폴더 = 이 스크립트가 있는 scripts 폴더의 상위 폴더
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# 파이썬 출력을 UTF-8 로 맞춘다
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# 콘솔 로그: data\logs\server-console.log (앱 로그는 data\logs\app.log).
# config.toml 의 data_dir 를 바꿨다면 아래 경로도 맞춘다.
$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "server-console.log"

while ($true) {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 서버를 시작합니다." | Out-File -FilePath $logFile -Append -Encoding utf8
    # 표준 출력과 오류를 한 파일에 덧붙인다. 파이썬이 UTF-8 로 쓰므로 그대로 이어 붙인다.
    & cmd.exe /c "`"$PythonExe`" -m app >> `"$logFile`" 2>&1"
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 서버가 끝났습니다 (종료 코드 $LASTEXITCODE). 10초 뒤 다시 시작합니다." | Out-File -FilePath $logFile -Append -Encoding utf8
    Start-Sleep -Seconds 10
}
