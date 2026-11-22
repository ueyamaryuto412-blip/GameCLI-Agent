$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'GameCLI-Agent/voicevox-0.17.1'
$null = New-Item -ItemType Directory -Force $root
$assets = @(
    @('https://github.com/VOICEVOX/onnxruntime-builder/releases/download/voicevox_onnxruntime-1.23.2/voicevox_onnxruntime-win-x64-1.23.2.tgz', '1bf9635d1c0ffd4e7345e0f628cecbabde65782c73c5051d52a52e7edf7ce11c'),
    @('https://github.com/r9y9/open_jtalk/releases/download/v1.11.1/open_jtalk_dic_utf_8-1.11.tar.gz', 'fe6ba0e43542cef98339abdffd903e062008ea170b04e7e2a35da805902f382a'),
    @('https://github.com/VOICEVOX/voicevox_vvm/releases/download/0.17.1/0.vvm', 'ecd35374d4182cd883cba5040376f7f888cc6ba248b1c2f4cea07cdb34bb1318')
)
foreach ($asset in $assets) {
    $path = Join-Path $root ($asset[0].Split('/')[-1])
    if (!(Test-Path -LiteralPath $path) -or (Get-FileHash -LiteralPath $path).Hash -ne $asset[1]) {
        Invoke-WebRequest -Uri $asset[0] -OutFile $path
    }
    if ((Get-FileHash -LiteralPath $path).Hash -ne $asset[1]) { throw "Checksum mismatch: $path" }
    if ($path -notlike '*.vvm') {
        tar.exe -xf $path -C $root
        if ($LASTEXITCODE -ne 0) { throw "Extraction failed: $path" }
    }
}
$python = Join-Path $PSScriptRoot '../.venv/Scripts/python.exe'
& $python -m pip install 'https://github.com/VOICEVOX/voicevox_core/releases/download/0.17.1/voicevox_core-0.17.1-cp310-abi3-win_amd64.whl'
if ($LASTEXITCODE -ne 0) { throw 'Could not install VOICEVOX Core' }
