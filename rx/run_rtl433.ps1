# Windows twin of run_rtl433.sh — see that file for the field-by-field
# derivation of the -X flex decoder spec. Needs rtl_433.exe on PATH
# (https://github.com/merbanan/rtl_433/releases) and python with
# rx/requirements.txt installed.
#
# Usage:  .\run_rtl433.ps1            (extra args go to reassemble.py,
#         .\run_rtl433.ps1 --show      e.g. --show for a live window)

# DELIBERATELY tuned 25 kHz below the real carrier. Measured on this bench
# (analyze_capture.py): carrier ~433.985M (nominal 434.000M minus crystal +
# dongle ppm), deviation +/-4.6 kHz. Tuning at 433.960M puts the FSK tones
# at +20.6/+29.8 kHz — clear of the RTL-SDR's DC spike, which otherwise
# swallows the lower tone (tones must never sit near 0 Hz offset).
$freq = if ($env:FREQ) { $env:FREQ } else { "433.960M" }
$flex = 'n=pixeltx,m=FSK_PCM,s=104,l=104,r=2000,preamble=aad391,bits>=80'
# Fixed gain by default: rtl_433's auto gain drives this bench's front end
# into clipping on idle noise alone (measured +1.5 dBFS idle at auto vs a
# clean -45 dBFS floor at 20 dB). Override with $env:GAIN.
$gain = if ($env:GAIN) { $env:GAIN } else { "20" }
$gainArgs = @("-g", $gain)

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
