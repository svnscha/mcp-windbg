# GCE startup script (windows-startup-script-ps1) for the debugger VM: OpenSSH with key login,
# and the KDNET port open. gcp-rig.sh fills in @SSH_PUBKEY@.
# The guest agent creates the SSH user only when the instance has BOTH enable-windows-ssh=TRUE and
# ssh-keys metadata; the capability, the firewall rule and the admin key file still come from here.
$c = Get-WindowsCapability -Online -Name OpenSSH.Server*
if ($c.State -ne 'Installed') { Add-WindowsCapability -Online -Name $c.Name }
Set-Service sshd -StartupType Automatic
Start-Service sshd
if (-not (Get-NetFirewallRule -Name sshd-in -ErrorAction SilentlyContinue)) { New-NetFirewallRule -Name sshd-in -DisplayName sshd -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow }
New-ItemProperty -Path HKLM:\SOFTWARE\OpenSSH -Name DefaultShell -Value C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -PropertyType String -Force
Set-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value '@SSH_PUBKEY@' -Encoding ascii
icacls.exe C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
Restart-Service sshd
if (-not (Get-NetFirewallRule -Name kdnet-in -ErrorAction SilentlyContinue)) { New-NetFirewallRule -Name kdnet-in -DisplayName kdnet -Direction Inbound -Protocol UDP -LocalPort 50000 -Action Allow }
