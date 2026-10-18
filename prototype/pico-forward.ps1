# Runs on the Windows host. Receives mouse packets over UDP and writes them to the Pico's serial port.
# Usage: powershell -ExecutionPolicy Bypass -File pico-forward.ps1 -Allow <Linux PC address> [-Port 47100] [-Com COM5]
param(
    [Parameter(Mandatory = $true)][string]$Allow,  # only accept packets from this PC
    [int]$Port = 47100,
    [string]$Com
)

if (-not $Com) {
    # CircuitPython boards use USB vendor ID 239A.
    $dev = Get-CimInstance Win32_PnPEntity |
        Where-Object { $_.PNPDeviceID -like 'USB\VID_239A*' -and $_.Name -match '\(COM\d+\)' } |
        Select-Object -First 1
    if (-not $dev) { Write-Error "Pico serial port not found. Is it plugged in and running boot.py/code.py?"; exit 1 }
    $null = $dev.Name -match '\((COM\d+)\)'
    $Com = $Matches[1]
}

$sp = New-Object System.IO.Ports.SerialPort $Com, 115200
$sp.DtrEnable = $true
$sp.Open()
$udp = New-Object System.Net.Sockets.UdpClient $Port
$from = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Any, 0)
Write-Host "Forwarding UDP :$Port from $Allow -> $Com (Ctrl+C to stop)"

try {
    while ($true) {
        $b = $udp.Receive([ref]$from)
        if ($from.Address.ToString() -eq $Allow -and $b.Length -eq 7 -and $b[0] -eq 0xA5) {
            $sp.Write($b, 0, 7)
        }
    }
} finally {
    $udp.Close()
    $sp.Close()
}
