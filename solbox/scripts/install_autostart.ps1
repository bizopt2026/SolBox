# 작업 스케줄러에 "SolutionBox" 작업을 등록한다: 현재 Windows 계정으로 로그온할 때 서버를 시작한다.
# (Claude CLI 로그인이 Windows 계정별이므로 서비스 계정이 아니라 로그인한 계정으로 돌린다.)
# 주의: Linux에서 작성되어 실행 검증이 되지 않았습니다. 처음 실행할 때 내용을 확인하세요.
param(
    [switch]$Remove,                  # 등록한 작업을 삭제한다
    [string]$PythonExe = "python",    # run_server.ps1 에 넘길 파이썬 실행 파일
    # 작업 이름. 한 PC 에 운영·베타 솔박스를 함께 둘 때 베타 폴더에서는 다른 이름을 준다 (예: -TaskName SolutionBoxBeta).
    # 같은 이름으로 등록하면 운영 서버의 자동 시작 작업을 덮어쓴다 (운영가이드 14장).
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$TaskName = "SolutionBox"
)
$ErrorActionPreference = "Stop"

$taskName = $TaskName

if ($Remove) {
    # 작업이 없어도 오류로 끝내지 않는다
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "작업 '$taskName' 을 삭제했습니다."
    } else {
        Write-Host "작업 '$taskName' 이 등록되어 있지 않습니다. 할 일이 없습니다."
    }
    return
}

$script = Join-Path $PSScriptRoot "run_server.ps1"
if (-not (Test-Path $script)) { throw "run_server.ps1 을 찾을 수 없습니다: $script" }
$user = "$env:USERDOMAIN\$env:USERNAME"

# 창을 숨기고 실행한다
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -PythonExe `"$PythonExe`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "작업 '$taskName' 을 등록했습니다. $user 계정으로 로그온하면 서버가 시작됩니다."
Write-Host "지금 바로 시작하려면: Start-ScheduledTask -TaskName $taskName"
