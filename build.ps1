$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

$bundledPython = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$python = if (Test-Path $bundledPython) { $bundledPython } else { (Get-Command python -ErrorAction Stop).Source }
$pythonHome = Split-Path -Parent $python
$tclRoot = Join-Path $pythonHome "tcl"
$dllRoot = Join-Path $pythonHome "DLLs"

$requiredTkFiles = @(
    (Join-Path $dllRoot "_tkinter.pyd"),
    (Join-Path $dllRoot "tcl86t.dll"),
    (Join-Path $dllRoot "tk86t.dll"),
    (Join-Path $tclRoot "tcl8.6"),
    (Join-Path $tclRoot "tk8.6")
)
foreach ($required in $requiredTkFiles) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing Tk runtime component: $required"
    }
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $python -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
& ".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "activity_price_helper" `
    --hidden-import "tkinter" `
    --hidden-import "tkinter.ttk" `
    --add-binary "$dllRoot\_tkinter.pyd;." `
    --add-binary "$dllRoot\tcl86t.dll;." `
    --add-binary "$dllRoot\tk86t.dll;." `
    --add-data "$tclRoot\tcl8.6;_tcl_data" `
    --add-data "$tclRoot\tk8.6;_tk_data" `
    --runtime-hook ".\pyi_rth_tkdata.py" `
    --collect-all rapidocr_onnxruntime `
    --collect-all onnxruntime `
    app.py
Copy-Item -LiteralPath ".\dist\activity_price_helper.exe" -Destination ".\activity-discount-helper.exe" -Force
Write-Host "Build complete: $project\activity-discount-helper.exe"
