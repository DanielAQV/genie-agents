<#
.SYNOPSIS
  깨어남을 작업 스케줄러에 건다. 내 PC 용 — EC2 로 미는 것은 `push.sh` 다.

.DESCRIPTION
  `deploy/task.xml` 의 {{...}} 를 채워 등록한다. 값은 전부 여기서 정해지고,
  **무엇이 언제 도는지는 XML 에 있다** — 그 파일을 읽으면 알 수 있게 갈라 뒀다.

  ━━ 왜 schtasks 가 아니라 Register-ScheduledTask 인가 ━━

  `schtasks /create /xml` 은 XML 이 UTF-16 이길 요구한다. 한글 주석이 들어간
  파일을 UTF-8 로 두면 조용히 깨져서 "잘못된 XML" 만 뜬다.
  `Register-ScheduledTask -Xml` 은 문자열을 받으므로 인코딩이 문제가 안 된다.

.EXAMPLE
  .\deploy\install-task.ps1 -Folder C:\Projects\followup
  .\deploy\install-task.ps1 -Folder C:\Projects\followup -WhatIf   # 등록 안 하고 보기만
  .\deploy\install-task.ps1 -Remove                                # 걷어낸다
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    # 에이전트 폴더. `agent.toml` 이 있는 자리.
    [string]$Folder,

    # 작업 이름. 여럿 걸 수 있게 폴더마다 다르게 준다.
    [string]$TaskName = 'followup-wake',

    # 안 주면 이 스크립트를 돌리는 파이썬 옆의 pythonw.exe 를 쓴다.
    [string]$Pythonw,

    # 걸어 둔 것을 걷어낸다.
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "걷어냈다: $TaskName"
    } else {
        Write-Host "걸린 게 없다: $TaskName"
    }
    return
}

if (-not $Folder) { throw "-Folder 를 줘라 (agent.toml 이 있는 폴더)" }
$Folder = (Resolve-Path $Folder).Path
if (-not (Test-Path (Join-Path $Folder 'agent.toml'))) {
    throw "agent.toml 이 없다: $Folder"
}

# ★ 창이 안 뜨는 파이썬을 쓴다. python.exe 로 걸면 매시 검은 창이 깜빡이고,
#   하루 열 번 깜빡이는 물건은 사람이 먼저 꺼 버린다.
if (-not $Pythonw) {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { throw "python 을 못 찾았다. -Pythonw 로 직접 줘라" }
    $Pythonw = Join-Path (Split-Path -Parent $py) 'pythonw.exe'
}
if (-not (Test-Path $Pythonw)) { throw "pythonw.exe 가 없다: $Pythonw" }

# 걸기 전에 정의가 성한지 본다. 반쯤 걸린 작업이 제일 나쁘다 —
# 매시 조용히 실패하고, 실패한다는 것을 아무도 모른다.
& $Pythonw -c "import genie_agents" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "그 파이썬에 genie_agents 가 없다. 먼저: $Pythonw -m pip install -e `"$repo`""
}

$id = Split-Path -Leaf $Folder
$xml = (Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'task.xml')).
    Replace('{{ID}}',       $id).
    Replace('{{TASKNAME}}', $TaskName).
    Replace('{{USER}}',     "$env:USERDOMAIN\$env:USERNAME").
    Replace('{{PYTHONW}}',  $Pythonw).
    Replace('{{FOLDER}}',   $Folder).
    Replace('{{WORKDIR}}',  $repo)

Write-Host "작업     $TaskName"
Write-Host "폴더     $Folder"
Write-Host "파이썬   $Pythonw"
Write-Host "트리거   로그온+3분 · 09~19시 매시 · 매일 18:00 (놓치면 켤 때)"

if ($PSCmdlet.ShouldProcess($TaskName, '작업 스케줄러에 등록')) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Register-ScheduledTask -TaskName $TaskName -Xml $xml | Out-Null
    Write-Host "`n걸었다. 한 번 돌려 보려면:"
    Write-Host "  Start-ScheduledTask -TaskName $TaskName"
    Write-Host "  python -m genie_agents wake `"$Folder`" --dry-run   # 무엇이 밀렸는지만"
}
