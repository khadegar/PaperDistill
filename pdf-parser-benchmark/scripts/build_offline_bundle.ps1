[CmdletBinding()]
param(
    [ValidateSet('all','mineru','marker','docling','eval')]
    [string]$Tool = 'all',
    [ValidateSet('all','wheels','models')]
    [string]$Phase = 'all',
    [string]$Python = 'C:\Users\zzx\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe',
    [string]$Uv = 'C:\Users\zzx\AppData\Local\hermes\bin\uv.exe',
    [string]$SmokePdf,
    [switch]$Rebuild
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Offline = Join-Path $ProjectRoot 'offline'
$BuildEnvs = Join-Path $Offline '.build-envs'
$Wheels = Join-Path $Offline 'wheels'
$Models = Join-Path $Offline 'models'
$Locks = Join-Path $Offline 'locks'
New-Item -ItemType Directory -Force -Path $BuildEnvs,$Wheels,$Models,$Locks | Out-Null
$env:UV_CACHE_DIR = (Join-Path $Offline '.uv-cache')
New-Item -ItemType Directory -Force -Path $env:UV_CACHE_DIR | Out-Null

$Specs = @{
    mineru = 'mineru[all]==3.4.4'
    marker = 'marker-pdf==2.0.0'
    docling = 'docling[vlm]==2.114.0'
    eval = 'psutil>=7,<8'
}
$CompatibilityPins = @{
    # ONNX Runtime 1.28 imports dxcore.dll during initialization. Windows
    # Server 2019 does not ship that DLL, so Magika (and therefore MinerU)
    # fails before inference. 1.23.2 retains the required CPython 3.11 wheel
    # while loading cleanly on the benchmark host.
    mineru = @('onnxruntime==1.23.2')
}
$CompatibilityRemovals = @{
    # LMDeploy selects Turbomind on Windows, whose allocator is unsupported on
    # this Server 2019/A100 host; its PyTorch backend requires Triton, which has
    # no supported Windows wheel. With LMDeploy absent, MinerU's official
    # Windows selector uses the Transformers CUDA backend.
    mineru = @('lmdeploy')
}
$TorchIndex = 'https://download.pytorch.org/whl/cu118'
$GpuTorchSpecs = @('torch==2.7.1+cu118','torchvision==0.22.1+cu118')
$GpuWheelDir = Join-Path $Wheels '_gpu'
$Targets = if ($Tool -eq 'all') { @('mineru','marker','docling','eval') } else { @($Tool) }

function Ensure-BuildEnv([string]$Name) {
    $EnvRoot = Join-Path $BuildEnvs $Name
    $EnvPython = Join-Path $EnvRoot 'Scripts\python.exe'
    $ResolvedBuildRoot = [IO.Path]::GetFullPath($BuildEnvs).TrimEnd('\') + '\'
    $ResolvedEnvRoot = [IO.Path]::GetFullPath($EnvRoot)
    if (-not $ResolvedEnvRoot.StartsWith($ResolvedBuildRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe build-env path: $ResolvedEnvRoot"
    }
    $PipUsable = (Test-Path $EnvPython) -and (Test-Path (Join-Path $EnvRoot 'Scripts\pip.exe'))
    if (($Rebuild -or -not $PipUsable) -and (Test-Path $EnvRoot)) {
        Remove-Item -LiteralPath $EnvRoot -Recurse -Force
    }
    if (-not (Test-Path $EnvPython)) {
        & $Uv venv --python $Python --seed $EnvRoot | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "uv venv failed for $Name ($LASTEXITCODE)" }
    }
    & $EnvPython -m pip install --upgrade pip setuptools wheel | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed for $Name ($LASTEXITCODE)" }
    return $EnvRoot
}

if ($Phase -in @('all','wheels')) {
    if ($Rebuild -and ($Targets | Where-Object { $_ -in @('mineru','marker','docling') })) {
        if (Test-Path $GpuWheelDir) {
            $ResolvedWheelRoot = [IO.Path]::GetFullPath($Wheels).TrimEnd('\') + '\'
            $ResolvedGpuWheelDir = [IO.Path]::GetFullPath($GpuWheelDir)
            if (-not $ResolvedGpuWheelDir.StartsWith($ResolvedWheelRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Unsafe GPU wheelhouse path: $ResolvedGpuWheelDir"
            }
            Remove-Item -LiteralPath $GpuWheelDir -Recurse -Force
        }
    }
    foreach ($Name in $Targets) {
        $EnvRoot = Ensure-BuildEnv $Name
        $EnvPython = Join-Path $EnvRoot 'Scripts\python.exe'
        if ($Name -in @('mineru','marker','docling')) {
            & $EnvPython -m pip install $Specs[$Name] @GpuTorchSpecs --extra-index-url $TorchIndex | Out-Host
        } else {
            & $EnvPython -m pip install $Specs[$Name] | Out-Host
        }
        if ($LASTEXITCODE -ne 0) { throw "pip install failed for $Name ($LASTEXITCODE)" }
        if ($CompatibilityPins.ContainsKey($Name)) {
            & $EnvPython -m pip install $CompatibilityPins[$Name] | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "compatibility pin install failed for $Name ($LASTEXITCODE)" }
        }
        if ($CompatibilityRemovals.ContainsKey($Name)) {
            & $EnvPython -m pip uninstall -y $CompatibilityRemovals[$Name] | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "compatibility removal failed for $Name ($LASTEXITCODE)" }
        }
        & $EnvPython -m pip check | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "pip dependency check failed for $Name ($LASTEXITCODE)" }
        $LockPath = Join-Path $Locks "$Name.txt"
        $Frozen = & $EnvPython -m pip freeze --all
        if ($LASTEXITCODE -ne 0) { throw "pip freeze failed for $Name ($LASTEXITCODE)" }
        $Frozen | Set-Content -LiteralPath $LockPath -Encoding UTF8
        $WheelDir = Join-Path $Wheels $Name
        if ($Rebuild -and (Test-Path $WheelDir)) {
            $ResolvedWheelRoot = [IO.Path]::GetFullPath($Wheels).TrimEnd('\') + '\'
            $ResolvedWheelDir = [IO.Path]::GetFullPath($WheelDir)
            if (-not $ResolvedWheelDir.StartsWith($ResolvedWheelRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Unsafe wheelhouse path: $ResolvedWheelDir"
            }
            Remove-Item -LiteralPath $WheelDir -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $WheelDir | Out-Null
        if ($Name -in @('mineru','marker','docling')) {
            $DownloadLockPath = Join-Path $BuildEnvs "$Name-download.txt"
            $Frozen | Where-Object { $_ -notmatch '^(torch|torchvision)==' } | Set-Content -LiteralPath $DownloadLockPath -Encoding UTF8
            & $EnvPython -m pip download --dest $WheelDir --no-deps -r $DownloadLockPath --extra-index-url $TorchIndex | Out-Host
        } else {
            & $EnvPython -m pip download --dest $WheelDir -r $LockPath | Out-Host
        }
        if ($LASTEXITCODE -ne 0) { throw "wheel download failed for $Name ($LASTEXITCODE)" }
        & $EnvPython -m pip download --dest $WheelDir pip setuptools wheel | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "bootstrap wheel download failed for $Name ($LASTEXITCODE)" }
    }
    if ($Targets | Where-Object { $_ -in @('mineru','marker','docling') }) {
        New-Item -ItemType Directory -Force -Path $GpuWheelDir | Out-Null
        & $Python -m pip download --dest $GpuWheelDir --no-deps @GpuTorchSpecs --index-url $TorchIndex | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "GPU wheel download failed ($LASTEXITCODE)" }
    }
}

if ($Phase -in @('all','models')) {
    if ($Tool -in @('all','mineru')) {
        $EnvRoot = Ensure-BuildEnv 'mineru'
        $MineruModels = Join-Path $Models 'mineru'
        New-Item -ItemType Directory -Force -Path $MineruModels | Out-Null
        $env:HF_HOME = (Join-Path $MineruModels 'huggingface')
        $env:MODELSCOPE_CACHE = (Join-Path $MineruModels 'modelscope')
        $env:MINERU_TOOLS_CONFIG_JSON = (Join-Path $MineruModels 'mineru.json')
        Remove-Item Env:MINERU_MODEL_SOURCE -ErrorAction SilentlyContinue
        & (Join-Path $EnvRoot 'Scripts\mineru-models-download.exe') -s huggingface -m all | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "MinerU model download failed ($LASTEXITCODE)" }
        Set-Content -LiteralPath (Join-Path $MineruModels 'source-root.txt') -Value $MineruModels -Encoding UTF8
    }
    if ($Tool -in @('all','marker')) {
        $EnvRoot = Ensure-BuildEnv 'marker'
        $MarkerModels = Join-Path $Models 'marker'
        New-Item -ItemType Directory -Force -Path $MarkerModels | Out-Null
        $env:HF_HOME = (Join-Path $MarkerModels 'huggingface')
        $env:MODEL_CACHE_DIR = (Join-Path $MarkerModels 'datalab-models')
        $env:HF_HUB_DISABLE_TELEMETRY = '1'
        $env:TORCH_DEVICE = 'cpu'
        $env:CUDA_VISIBLE_DEVICES = '-1'
        & (Join-Path $EnvRoot 'Scripts\python.exe') (Join-Path $PSScriptRoot 'prefetch_marker_models.py') --output $MarkerModels | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Marker model prefetch failed ($LASTEXITCODE)" }
        Remove-Item Env:TORCH_DEVICE -ErrorAction SilentlyContinue
        Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue
        $LlamaTag = 'b9632'
        $LlamaSourceZip = Join-Path $MarkerModels "llama.cpp-$LlamaTag.zip"
        if (-not (Test-Path $LlamaSourceZip)) {
            $LlamaTemp = "$LlamaSourceZip.partial"
            Invoke-WebRequest -Uri "https://github.com/ggml-org/llama.cpp/archive/refs/tags/$LlamaTag.zip" -OutFile $LlamaTemp
            Move-Item -LiteralPath $LlamaTemp -Destination $LlamaSourceZip -Force
        }
        Get-FileHash -Algorithm SHA256 -LiteralPath $LlamaSourceZip |
            Select-Object Algorithm,Hash,Path |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $MarkerModels 'llama.cpp-source.json') -Encoding UTF8
    }
    if ($Tool -in @('all','docling')) {
        $EnvRoot = Ensure-BuildEnv 'docling'
        $DoclingModels = Join-Path $Models 'docling'
        New-Item -ItemType Directory -Force -Path $DoclingModels | Out-Null
        $env:HF_HOME = (Join-Path $DoclingModels 'huggingface')
        $env:HF_HUB_DISABLE_TELEMETRY = '1'
        & (Join-Path $EnvRoot 'Scripts\docling-tools.exe') models download granitedocling layout tableformer rapidocr -o $DoclingModels | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Docling model download failed ($LASTEXITCODE)" }
    }
}

& $Python (Join-Path $ProjectRoot 'scripts\pdfbench.py') bundle-manifest --config (Join-Path $ProjectRoot 'config\benchmark.json')
