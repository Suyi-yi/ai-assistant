# 生成可以发给别人的发布包（自动剥掉你的密钥、聊天记录和备份）
#
# 用法：powershell -ExecutionPolicy Bypass -File package_release.ps1

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = $PSScriptRoot
$version = (Select-String -LiteralPath (Join-Path $root 'main.py') -Pattern '^VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$dist = Join-Path $root 'dist\AI小助理'
$releaseRoot = Join-Path $root 'release'
$staging = Join-Path $releaseRoot "AI小助理-v$version"
$zip = Join-Path $releaseRoot "AI小助理-v$version.zip"

if (-not (Test-Path -LiteralPath (Join-Path $dist 'AI小助理.exe'))) {
  throw "还没打包。先运行 build.bat，或执行 pyinstaller 命令生成 dist\AI小助理。"
}

# 安全校验：所有删除动作都必须发生在项目目录内
$rootFull = (Resolve-Path -LiteralPath $root).Path
function Assert-Inside([string]$path) {
  $full = [System.IO.Path]::GetFullPath($path)
  if (-not $full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝操作项目目录之外的路径：$full"
  }
  return $full
}

Write-Host "[1/4] 准备发布目录"
if (Test-Path -LiteralPath $staging) {
  Remove-Item -LiteralPath (Assert-Inside $staging) -Recurse -Force
}
New-Item -ItemType Directory -Path $staging -Force | Out-Null

Write-Host "[2/4] 复制程序文件"
Copy-Item -Path (Join-Path $dist '*') -Destination $staging -Recurse -Force

# 剥掉个人数据：密钥、会话、备份一律不进发布包
$private = @('data', 'data\backups', 'data\sessions', 'data\config.json')
foreach ($item in $private) {
  $target = Join-Path $staging $item
  if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath (Assert-Inside $target) -Recurse -Force
    Write-Host "      已剥离 $item"
  }
}
Get-ChildItem -LiteralPath $staging -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
  ForEach-Object { Remove-Item -LiteralPath (Assert-Inside $_.FullName) -Recurse -Force }

Write-Host "[3/4] 写入使用说明"
$readme = Join-Path $root '使用说明.txt'
if (Test-Path -LiteralPath $readme) {
  Copy-Item -LiteralPath $readme -Destination $staging -Force
} else {
  Write-Warning "没找到 使用说明.txt，发布包里不会有说明文件"
}

Write-Host "[4/4] 压缩成 zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath (Assert-Inside $zip) -Force }
Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zip -CompressionLevel Optimal

# 顺手生成更新清单：把 url 换成你能公开下载的地址，再把这个 json 传到同一个地方即可
$manifestPath = Join-Path $releaseRoot 'manifest.json'
$manifest = [ordered]@{
  version = $version
  url     = "https://把这里换成你的下载地址/AI小助理-v$version.zip"
  notes   = "把这一版改了什么写在这里，朋友更新时会看到。"
}
$manifest | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$sizeMb = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
# 额外存一份纯英文名的副本：上传到 GitHub 时中文文件名会被搞坏
Copy-Item -LiteralPath $zip -Destination (Join-Path $releaseRoot "ai-assistant-v$version.zip") -Force
Write-Host ""
Write-Host "发布包已生成：$zip（$sizeMb MB）" -ForegroundColor Green
Write-Host "校验：$staging\data 是否存在 -> $(Test-Path -LiteralPath (Join-Path $staging 'data'))"
Write-Host "更新清单已生成：$manifestPath（把 url 改成真实下载地址后再发给朋友）" -ForegroundColor Yellow
