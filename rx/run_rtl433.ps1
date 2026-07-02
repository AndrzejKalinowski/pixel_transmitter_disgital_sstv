# Windows twin of run_rtl433.sh — see that file for the field-by-field
# derivation of the -X flex decoder spec. Needs rtl_433.exe on PATH
# (https://github.com/merbanan/rtl_433/releases) and python with
# rx/requirements.txt installed.
#
# Usage:  .\run_rtl433.ps1            (extra args go to reassemble.py,
#         .\run_rtl433.ps1 --show      e.g. --show for a live window)

$freq = if ($env:FREQ) { $env:FREQ } else { "434.000M" }
$flex = 'n=pixeltx,m=FSK_PCM,s=208,l=208,r=3000,preamble=aad391,bits>=80'
$gainArgs = @()
if ($env:GAIN) { $gainArgs = @("-g", $env:GAIN) }

# Find rtl_433: PATH first, then the default install location (a terminal
# opened before the installer updated the user PATH won't see it there yet).
$rtlCmd = Get-Command rtl_433 -ErrorAction SilentlyContinue
if ($rtlCmd) {
    $rtl = $rtlCmd.Source
} elseif (Test-Path "$env:LOCALAPPDATA\Programs\rtl_433\rtl_433.exe") {
    $rtl = "$env:LOCALAPPDATA\Programs\rtl_433\rtl_433.exe"
} else {
    Write-Error "rtl_433 not found on PATH or in $env:LOCALAPPDATA\Programs\rtl_433 - see README"
    exit 1
}

& $rtl -f $freq -s 250k @gainArgs -X $flex -F json |
    python "$PSScriptRoot\reassemble.py" --out "$PSScriptRoot\out.png" @args
