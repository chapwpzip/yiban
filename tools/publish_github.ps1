<#
.SYNOPSIS
    把本项目发布到 GitHub（无需安装 git，直接走 REST API）。

.DESCRIPTION
    Token 从文件读取（一行 PAT），不会出现在命令行历史里。
    需要权限：classic PAT 勾选 repo；fine-grained PAT 勾选
    Administration: Read and write + Contents: Read and write。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\publish_github.ps1 `
        -TokenFile D:\Deepseek\github_token.txt -Repo yiban-autoposter -Public
#>
param(
    [Parameter(Mandatory = $true)][string]$TokenFile,
    [string]$Repo = "yiban-autoposter",
    [string]$Description = "易班微社区自动发帖工具：自动登录、内容生成、自动填写提交、结果检测；验证码支持离线识图 / 视觉模型 / 外接服务",
    [switch]$Public,
    [switch]$DryRun,
    [string]$ReleaseTag = "v1.1.7",
    [string]$ReleaseZip = "release\YibanAutoPoster-v1.1.7.zip"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

if ($DryRun) {
    $rootPre = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
    Set-Location $rootPre
    $pre = @(".gitignore","LICENSE","README.md","README.txt","main.py","build.bat","requirements.txt","requirements-build.txt","settings.json","YibanAutoPoster.spec","captcha_solver_example.py","tools/publish_github.ps1")
    $list = @()
    foreach ($f in $pre) { if (Test-Path $f) { $list += Get-Item $f } }
    $list += Get-ChildItem "yiban_auto" -Recurse -File -Filter *.py
    $list += Get-ChildItem "assets" -File | Where-Object { $_.Extension -in ".png", ".jpg", ".ico" }
    $list = $list | Sort-Object FullName -Unique
    Write-Host "DryRun：待上传 $($list.Count) 个文件"
    $list | ForEach-Object { Write-Host ("  " + $_.FullName.Substring($rootPre.Length + 1)) }
    $total = ($list | Measure-Object -Property Length -Sum).Sum
    Write-Host ("总大小：{0:N1} KB" -f ($total / 1KB))
    return
}

if (-not (Test-Path $TokenFile)) { throw "找不到 token 文件：$TokenFile" }
$token = (Get-Content $TokenFile -Raw).Trim()
if (-not $token) { throw "token 文件是空的" }

$headers = @{
    Authorization          = "Bearer $token"
    Accept                 = "application/vnd.github+json"
    "User-Agent"           = "YibanAutoPoster-Publisher"
    "X-GitHub-Api-Version" = "2022-11-28"
}

$me = Invoke-RestMethod -Uri "https://api.github.com/user" -Headers $headers -Method Get
$owner = $me.login
Write-Host "已认证：$owner"

# ---------------------------------------------------------------- 文件清单
$include = @(
    ".gitignore", "LICENSE", "README.md", "README.txt", "main.py",
    "build.bat", "requirements.txt", "requirements-build.txt",
    "settings.json", "YibanAutoPoster.spec", "captcha_solver_example.py",
    "tools/publish_github.ps1"
)
$files = @()
foreach ($item in $include) {
    if (Test-Path $item) { $files += Get-Item $item }
}
$files += Get-ChildItem "yiban_auto" -Recurse -File -Filter *.py
$files += Get-ChildItem "assets" -File | Where-Object { $_.Extension -in ".png", ".jpg", ".ico" }

$files = $files | Sort-Object FullName -Unique
Write-Host "待上传文件 $($files.Count) 个："
$files | ForEach-Object { Write-Host ("  " + $_.FullName.Substring($root.Length + 1)) }

if ($DryRun) {
    Write-Host "DryRun：只列清单，不创建仓库。"
    return
}

# ---------------------------------------------------------------- 建仓库
$repoBody = @{
    name        = $Repo
    description = $Description
    private     = (-not $Public)
    has_issues  = $true
    has_wiki    = $false
} | ConvertTo-Json

$repoUrl = "https://api.github.com/repos/$owner/$Repo"
$exists = $true
try {
    Invoke-RestMethod -Uri $repoUrl -Headers $headers -Method Get | Out-Null
    Write-Host "仓库已存在，直接更新文件：$repoUrl"
} catch {
    $exists = $false
}

if (-not $exists) {
    $created = Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Headers $headers -Method Post -Body $repoBody -ContentType "application/json"
    Write-Host "仓库已创建：$($created.html_url)"
}

# ---------------------------------------------------------------- 上传文件
function Get-RemoteSha {
    param([string]$Path)
    $encoded = ($Path -split "/" | ForEach-Object { [uri]::EscapeDataString($_) }) -join "/"
    try {
        $info = Invoke-RestMethod -Uri "https://api.github.com/repos/$owner/$Repo/contents/$encoded" -Headers $headers -Method Get
        return $info.sha
    } catch {
        return $null
    }
}

$count = 0
foreach ($file in $files) {
    $rel = $file.FullName.Substring($root.Length + 1).Replace("\", "/")
    $encoded = ($rel -split "/" | ForEach-Object { [uri]::EscapeDataString($_) }) -join "/"
    $content = [Convert]::ToBase64String([IO.File]::ReadAllBytes($file.FullName))
    $body = @{ message = "Add $rel"; content = $content } | ConvertTo-Json -Compress
    $sha = Get-RemoteSha -Path $rel
    if ($sha) { $body = @{ message = "Update $rel"; content = $content; sha = $sha } | ConvertTo-Json -Compress }
    try {
        Invoke-RestMethod -Uri "https://api.github.com/repos/$owner/$Repo/contents/$encoded" -Headers $headers `
            -Method Put -Body $body -ContentType "application/json" | Out-Null
        $count++
        Write-Host ("  [ok] " + $rel)
    } catch {
        Write-Warning ("  [fail] " + $rel + " -> " + $_.Exception.Message)
    }
}
Write-Host "已上传 $count 个文件"

# ---------------------------------------------------------------- 发布 Release
if (Test-Path $ReleaseZip) {
    $repoInfo = Invoke-RestMethod -Uri "https://api.github.com/repos/$owner/$Repo" -Headers $headers -Method Get
    $defaultBranch = if ($repoInfo.default_branch) { $repoInfo.default_branch } else { "main" }
    Write-Host "默认分支：$defaultBranch"

    $tagBody = @{
        tag_name         = $ReleaseTag
        target_commitish = $defaultBranch
        name             = $ReleaseTag
        body             = "Windows 单文件 exe（解压即用）。含 settings.json 配置模板与 README。"
        draft            = $false
        prerelease       = $false
    } | ConvertTo-Json

    $release = $null
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$owner/$Repo/releases/tags/$ReleaseTag" -Headers $headers -Method Get
        Write-Host "Release $ReleaseTag 已存在，跳过创建"
    } catch {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$owner/$Repo/releases" -Headers $headers -Method Post -Body $tagBody -ContentType "application/json"
        Write-Host "Release 已创建：$($release.html_url)"
    }

    $assetName = Split-Path $ReleaseZip -Leaf
    $uploadUrl = ($release.upload_url -replace "\{.*\}", "") + "?name=" + [uri]::EscapeDataString($assetName)
    try {
        $uploadHeaders = @{ Authorization = "Bearer $token"; "User-Agent" = "YibanAutoPoster-Publisher" }
        Invoke-RestMethod -Uri $uploadUrl -Headers $uploadHeaders -Method Post -InFile (Resolve-Path $ReleaseZip) `
            -ContentType "application/zip" | Out-Null
        Write-Host "附件已上传：$assetName"
    } catch {
        Write-Warning ("附件上传失败（可能已存在）：" + $_.Exception.Message)
    }
}

Write-Host ""
Write-Host "完成："
Write-Host ("  仓库：https://github.com/" + $owner + "/" + $Repo)
Write-Host ("  发布：https://github.com/" + $owner + "/" + $Repo + "/releases/tag/" + $ReleaseTag)
