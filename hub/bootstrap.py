"""Agent bootstrap: the short command typed on a target and the script it downloads.

Both pin the hub's certificate by SHA-256 fingerprint, so the download can't be swapped on the network.
Windows PowerShell 5.1 can't pin with a script-block callback reliably, hence the small C# helper.
"""

AGENT_DIR = r"$env:LOCALAPPDATA\pi-switcher-hub"

# Shared by the typed command and the script: pin the hub certificate for Invoke-WebRequest.
# The C# helper installs the callback itself: PowerShell 5.1 can't cast a method to the delegate type.
# Loading it is skipped if a previous run in the same window already did.
PIN_PS = (
    "if(-not('HubPin'-as[type])){Add-Type -TypeDefinition 'using System.Net;using System.Net.Security;"
    "using System.Security.Cryptography;using System.Security.Cryptography.X509Certificates;"
    "public static class HubPin{static string F;public static void Install(string f){F=f;"
    "ServicePointManager.SecurityProtocol=SecurityProtocolType.Tls12;"
    "ServicePointManager.ServerCertificateValidationCallback=Check;}"
    "static bool Check(object s,X509Certificate c,X509Chain h,SslPolicyErrors e){return "
    "System.BitConverter.ToString(SHA256.Create().ComputeHash(c.GetRawCertData())).Replace(\"-\",\"\").ToLower()==F;}}'};"
    "[HubPin]::Install('{fp}')"
)


def windows_command(token, fingerprint, host, port):
    """One line for a PowerShell window: pin the hub, fetch the bootstrap script, run it."""
    pin = PIN_PS.replace("{fp}", fingerprint)
    return f"{pin};iex(iwr -UseBasicParsing https://{host}:{port}/agent/{token}/bootstrap.ps1).Content"


def windows_bootstrap(token, fingerprint, hosts, port):
    """The script served at /agent/<token>/bootstrap.ps1: install, pair, autostart, launch."""
    host = hosts[0] if hosts else "127.0.0.1"
    return f"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$hub = '{host}:{port}'
$root = "{AGENT_DIR}"
$app = Join-Path $root 'app'
Write-Host 'pi-switcher-hub agent: installing to' $app

# Stop a running agent so its files can be replaced.
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object {{ $_.ExecutablePath -like "$app\\*" }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}

New-Item -ItemType Directory -Force $root | Out-Null
$zip = Join-Path $env:TEMP 'pi-switcher-agent.zip'
Invoke-WebRequest -UseBasicParsing "https://$hub/agent/{token}/agent-windows.zip" -OutFile $zip
if (Test-Path $app) {{ Remove-Item -Recurse -Force $app }}
Expand-Archive $zip -DestinationPath $app -Force
Remove-Item $zip

$python = Join-Path $app 'python\\python.exe'
& $python -m agent pair --hub '{host}' --port {port} --token '{token}' --fingerprint '{fingerprint}'
if ($LASTEXITCODE -ne 0) {{ throw 'pairing failed' }}

$pythonw = Join-Path $app 'python\\pythonw.exe'
Set-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' 'pi-switcher-agent' "`"$pythonw`" -m agent run"
Start-Process $pythonw -ArgumentList '-m', 'agent', 'run' -WorkingDirectory $app
Write-Host 'pi-switcher-hub agent: installed and running.'
"""
