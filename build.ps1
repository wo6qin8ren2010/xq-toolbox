# 一条命令出包：先同步百宝箱，再打包，自动按版本号建分发目录并生成 version.json
# 用法：在项目根目录执行  .\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 1) 从源码读版本号
$ver = (Select-String -Path "app\native\update.py" -Pattern 'APP_VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "版本：v$ver"

# 2) 同步本机百宝箱到内置 seed（漏了这步别人就看不到你新加的软件）
& "$root\..\.venv\Scripts\python.exe" "$root\sync_seed.py"
if ($LASTEXITCODE -ne 0) { throw "同步 seed 失败" }

# 3) 关掉正在运行的实例再打包
Get-Process XQToolbox -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800

& "$root\..\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --noconsole `
  --name XQToolbox --icon app\app.ico `
  --add-data "app\ui;ui" --add-data "app\seed;seed" --add-data "admin;admin" `
  --collect-data pywebview --exclude-module setuptools --exclude-module pkg_resources `
  --hidden-import clr --hidden-import pythonnet --hidden-import clr_loader `
  --hidden-import webview.platforms.edgechromium --hidden-import pycaw --hidden-import comtypes `
  --hidden-import app.native.audio_format --hidden-import app.native.local_auth `
  --hidden-import app.native.lestore --hidden-import app.native.library `
  --hidden-import app.native.maintenance --hidden-import app.native.update `
  --hidden-import app.native.music --hidden-import app.native.dock --hidden-import native.dock `
  --hidden-import app.native.remote --hidden-import app.native.autostart --hidden-import native.autostart `
  --hidden-import winrt.system --hidden-import winrt.windows.media.control `
  --hidden-import winrt.windows.storage.streams --hidden-import winrt.windows.foundation `
  --hidden-import winrt.windows.foundation.collections --hidden-import native.glass app\main.py
if ($LASTEXITCODE -ne 0) { throw "打包失败" }

# 4) 建分发目录并生成清单
$out = Join-Path $root "..\分发_v$ver"
New-Item -ItemType Directory -Force -Path $out | Out-Null
Copy-Item "dist\XQToolbox.exe" (Join-Path $out "XQToolbox.exe") -Force
$exe = Join-Path $out "XQToolbox.exe"
$sha = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$manifest = @{ version = $ver; file = "XQToolbox.exe"; sha256 = $sha; notes = "见 README 版本记录" } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $out "version.json"), $manifest, [System.Text.UTF8Encoding]::new($false))

Write-Host "完成：$out"
Write-Host "大小：$((Get-Item $exe).Length)  sha256：$sha"
