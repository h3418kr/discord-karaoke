# 포터블 빌드 스크립트 — PyInstaller 빌드 후 동봉 파일 복원과 검증까지 한 번에 수행
# 사용: powershell -ExecutionPolicy Bypass -File build_portable.ps1
# (수동으로 pyinstaller만 돌리면 ffmpeg/사용법.txt가 빠지는 사고가 재발함 — 반드시 이 스크립트 사용)

$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$dist = Join-Path $src "dist\DiscordKaraoke"

# 1) 동봉 파일 백업 (기존 dist가 있으면)
$bak = Join-Path $env:TEMP "dk_build_bak"
New-Item -ItemType Directory -Force $bak | Out-Null
foreach ($f in @("ffmpeg.exe", "ffprobe.exe", "사용법.txt")) {
    $p = Join-Path $dist $f
    if (Test-Path $p) { Copy-Item $p -Destination $bak -Force }
}

# 2) PyInstaller 빌드
Set-Location $src
python -m PyInstaller --noconfirm --clean --windowed --name DiscordKaraoke app.py --collect-all sv_ttk
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 빌드 실패" }

# 3) 동봉 파일 복원 (백업에 없으면 PATH의 ffmpeg 사용)
foreach ($f in @("ffmpeg.exe", "ffprobe.exe")) {
    $b = Join-Path $bak $f
    if (Test-Path $b) { Copy-Item $b -Destination $dist -Force }
    else {
        $cmd = (Get-Command ($f -replace '\.exe$', '')).Source
        Copy-Item $cmd -Destination (Join-Path $dist $f) -Force
    }
}
$u = Join-Path $bak "사용법.txt"
if (Test-Path $u) { Copy-Item $u -Destination $dist -Force }
elseif (Test-Path (Join-Path $src "사용법.txt")) { Copy-Item (Join-Path $src "사용법.txt") -Destination $dist -Force }

# 4) 필수 파일 존재 검증 — 하나라도 없으면 실패
foreach ($f in @("DiscordKaraoke.exe", "ffmpeg.exe", "ffprobe.exe", "사용법.txt")) {
    if (-not (Test-Path (Join-Path $dist $f))) { throw "필수 파일 누락: $f" }
}

# 5) selftest — 동봉 ffmpeg 경로를 쓰는지 확인
$p = Start-Process (Join-Path $dist "DiscordKaraoke.exe") -ArgumentList "--selftest" -PassThru -Wait
if ($p.ExitCode -ne 0) { throw "selftest 실패 (exit $($p.ExitCode))" }
$log = Get-Content (Join-Path $dist "selftest.log") -Encoding utf8 -Raw
if ($log -notmatch [regex]::Escape((Join-Path $dist "ffmpeg.exe"))) {
    throw "selftest가 동봉 ffmpeg가 아닌 다른 경로를 사용함 — 확인 필요"
}
Remove-Item (Join-Path $dist "selftest.log") -Force

Write-Host ""
Write-Host "빌드 완료. dist 필수 파일 4종 + selftest(동봉 ffmpeg) 검증 통과." -ForegroundColor Green
Write-Host "다음 단계: 배포 zip 재생성 시 이 dist 폴더 전체를 압축할 것."
