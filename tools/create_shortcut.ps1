# Creates a "Device Tracker" shortcut on the Desktop.
# Uses dist\DeviceTracker.exe if it was built, otherwise runs device_tracker.py with Python.
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $desktop 'Device Tracker.lnk'))
$exe = Join-Path $root 'dist\DeviceTracker.exe'

if (Test-Path $exe) {
    $lnk.TargetPath = $exe
} else {
    $pyw = (Get-Command pyw -ErrorAction SilentlyContinue).Source
    if (-not $pyw) { $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source }
    if (-not $pyw) {
        Write-Host 'Python was not found. Install it from https://www.python.org/downloads/ first.'
        exit 1
    }
    $lnk.TargetPath = $pyw
    $lnk.Arguments = '"' + (Join-Path $root 'device_tracker.py') + '"'
}
$lnk.WorkingDirectory = $root
$lnk.Save()
Write-Host 'Shortcut "Device Tracker" created on the Desktop.'
