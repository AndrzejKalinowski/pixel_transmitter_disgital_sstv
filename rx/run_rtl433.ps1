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

rtl_433 -f $freq -s 250k @gainArgs -X $flex -F json |
    python "$PSScriptRoot\reassemble.py" @args
