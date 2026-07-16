$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        $Line = $_.Trim()
        if ($Line -and -not $Line.StartsWith("#") -and $Line.Contains("=")) {
            $Name, $Value = $Line.Split("=", 2)
            $Value = $Value.Trim().Trim('"').Trim("'")
            Set-Item -Path "Env:$($Name.Trim())" -Value $Value
        }
    }
}

$UvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
if (-not $UvCommand) {
    $LocalUv = Join-Path $ProjectRoot ".venv\Scripts\uv.exe"
    if (Test-Path $LocalUv) {
        $UvCommand = $LocalUv
    } else {
        throw "실행 실패: uv를 설치해주세요. https://docs.astral.sh/uv/"
    }
}

if (-not (Get-Command "ffmpeg" -ErrorAction SilentlyContinue)) {
    throw "실행 실패: FFmpeg를 설치하고 PATH에 추가해주세요."
}

if (-not $env:RTZR_CLIENT_ID -or -not $env:RTZR_CLIENT_SECRET) {
    throw "실행 실패: .env 또는 환경변수에 RTZR credential을 설정해주세요."
}

$HasSource = $false
$HasPreprocess = $false
$HasRecover = $false
$HasYes = $false
foreach ($Arg in $args) {
    if ($Arg -eq "--source-file" -or $Arg.StartsWith("--source-file=")) { $HasSource = $true }
    if ($Arg -eq "--preprocess" -or $Arg.StartsWith("--preprocess=")) { $HasPreprocess = $true }
    if ($Arg -eq "--recover") { $HasRecover = $true }
    if ($Arg -eq "--yes") { $HasYes = $true }
}

$DemoArgs = @()
if (-not $HasSource) { $DemoArgs += @("--source-file", "../subwayaudio.m4a") }
if (-not $HasPreprocess) { $DemoArgs += @("--preprocess", "subway_rumble_cut_v1") }
if (-not $HasRecover) { $DemoArgs += "--recover" }
if (-not $HasYes) { $DemoArgs += "--yes" }
$CommandArgs = @($DemoArgs) + @($args)

& $UvCommand sync --frozen --extra dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $UvCommand run nextstop journey-demo @CommandArgs
exit $LASTEXITCODE
