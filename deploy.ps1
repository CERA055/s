#Requires -Version 5.1
<#
    一条命令把签到部署到 GitHub Actions。

    前置：
      1. 已经跑过  python setup.py   并在本机通过验证（cookie.txt 存在且有效）
      2. 已登录 gh（本脚本会检查，没登录会提示你跑 gh auth login）

    用法：
      powershell -ExecutionPolicy Bypass -File deploy.ps1
      powershell -ExecutionPolicy Bypass -File deploy.ps1 -Repo my-claim -Public
#>
param(
    [string]$Repo = "soliumbra-claim",
    [switch]$Public,
    [switch]$SkipRun
)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Fail($msg) { Write-Host "× $msg" -ForegroundColor Red; exit 1 }
function Ok($msg)   { Write-Host "√ $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "· $msg" -ForegroundColor Cyan }

# ---------- 1. 找 gh ----------
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) {
    # winget 装完不会刷新当前进程的 PATH，得手动找
    $cands = @(
        (Join-Path $env:ProgramFiles "GitHub CLI\gh.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "GitHub CLI\gh.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\GitHub CLI\gh.exe"),
        (Join-Path $here "tools\gh\bin\gh.exe")
    )
    $gh = $cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $gh) {
    Fail ("没找到 gh (GitHub CLI)。`n" +
          "装法: winget install --id GitHub.cli -e`n" +
          "或下载 https://github.com/cli/cli/releases/latest 的 windows_amd64.zip，" +
          "解压到 $here\tools\gh\ 后重跑本脚本。")
}
Ok "gh: $gh"

# ---------- 2. 检查 cookie ----------
$cookieFile = Join-Path $here "cookie.txt"
if (-not (Test-Path $cookieFile)) {
    Fail "没有 cookie.txt，先跑：python setup.py"
}
$raw = (Get-Content $cookieFile -Raw -Encoding utf8) -replace "^\uFEFF", ""
$raw = $raw.Trim()
if ($raw -match "^(?i)cookie:\s*") { $raw = $raw -replace "^(?i)cookie:\s*", "" }
if ($raw -notmatch "=") { Fail "cookie.txt 内容不像 Cookie 头" }
Info ("cookie 长度 {0} 字节" -f $raw.Length)

Write-Host "`n验证 cookie 是否还有效…"
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Fail "没找到 python" }
& $py soliumbra_claim.py --check
if ($LASTEXITCODE -ne 0) {
    Fail "cookie 验证没过（退出码 $LASTEXITCODE）。回浏览器重新登录后重跑 setup.py。"
}
Ok "cookie 有效"

# ---------- 3. 检查 gh 登录 ----------
& $gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "还没登录 GitHub。现在跑这条，浏览器授权完再回来重跑本脚本：" -ForegroundColor Yellow
    Write-Host "  $gh auth login -h github.com -p https -w" -ForegroundColor White
    Write-Host ""
    Write-Host "（也可以直接把 gh auth login 敲进去，跟着提示走）"
    exit 1
}
$who = (& $gh api user --jq .login 2>$null)
Ok "已登录：$who"

# ---------- 4. 建仓并推 ----------
$existing = (& $gh repo view "$who/$Repo" --json name 2>&1 | Out-String)
if ($LASTEXITCODE -eq 0 -and $existing) {
    Info "仓库 $who/$Repo 已存在，改为推送"
    git remote get-url origin *> $null
    if ($LASTEXITCODE -ne 0) { git remote add origin "https://github.com/$who/$Repo.git" }
    git branch -M main; git push -u origin main
} else {
    git branch -M main
    $vis = if ($Public) { "--public" } else { "--private" }
    & $gh repo create $Repo $vis --source . --remote origin --push
    if ($LASTEXITCODE -ne 0) { Fail "建仓失败" }
}
Ok "代码已推送"

# ---------- 5. 注入 Secrets ----------
Info "写入 SOLIUMBRA_COOKIE"
# 注意：gh secret set 不带 --body 时从 stdin 读。
# 千万别写 --body -，那会把字面量 "-" 当成值存进去。
$raw | & $gh secret set SOLIUMBRA_COOKIE --repo "$who/$Repo"
if ($LASTEXITCODE -ne 0) { Fail "写 secret 失败" }

# 校验：secret 列表里必须有这个名字
$secs = & $gh secret list --repo "$who/$Repo" 2>$null
if ($secs -notmatch "SOLIUMBRA_COOKIE") { Fail "secret 没写上" }
Ok "SOLIUMBRA_COOKIE 已注入"

foreach ($pair in @(
    @{ n = "TELEGRAM_BOT_TOKEN"; f = "telegram_token.txt" },
    @{ n = "TELEGRAM_CHAT_ID";   f = "telegram_chat.txt" },
    @{ n = "CAPSOLVER_KEY";      f = "capsolver_key.txt" }
)) {
    $p = Join-Path $here $pair.f
    if (Test-Path $p) {
        (Get-Content $p -Raw).Trim() | & $gh secret set $pair.n --repo "$who/$Repo"
        Ok "$($pair.n) 已注入（来自 $($pair.f)）"
    }
}

# ---------- 6. 触发一次 ----------
if (-not $SkipRun) {
    Info "触发一次 workflow 验证"
    & $gh workflow run claim.yml --repo "$who/$Repo"
    Start-Sleep -Seconds 12
    & $gh run list --repo "$who/$Repo" --workflow claim.yml --limit 1
    Write-Host ""
    Info "看详细日志：gh run view --repo $who/$Repo --log"
}

Write-Host ""
Ok "部署完成：https://github.com/$who/$Repo/actions"
Write-Host "之后每小时自动跑一次；cookie 过期会由最后的 --check 步骤报错，GitHub 会发邮件提醒你。" -ForegroundColor Gray
