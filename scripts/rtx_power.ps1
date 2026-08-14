[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Status", "Disable", "Enable")]
    [string]$Action,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$rtxInstanceId = "PCI\VEN_10DE&DEV_249C&SUBSYS_22E417AA&REV_A1\4&33F38E31&0&0008"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-RtxDevice {
    return Get-PnpDevice -InstanceId $rtxInstanceId
}

function Get-LoadedOllamaModels {
    $output = & ollama ps 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @()
    }
    return @($output | Select-Object -Skip 1 | Where-Object { $_.Trim() })
}

function Test-RtxDrivesDisplay {
    $nvidiaAdapter = Get-CimInstance Win32_VideoController | Where-Object {
        $_.Name -eq "NVIDIA GeForce RTX 3080 Laptop GPU"
    }
    return $null -ne $nvidiaAdapter.CurrentHorizontalResolution
}

function Get-RtxTelemetry {
    $sample = & nvidia-smi --query-gpu=pstate,power.draw,temperature.gpu,utilization.gpu,memory.used --format=csv,noheader 2>$null
    if ($LASTEXITCODE -ne 0) {
        return "nvidia-smi unavailable: RTX disabled or driver not ready"
    }
    return $sample
}

function Show-RtxStatus {
    $device = Get-RtxDevice
    $driver = Get-CimInstance Win32_PnPSignedDriver | Where-Object {
        $_.DeviceName -eq "NVIDIA GeForce RTX 3080 Laptop GPU"
    }
    [PSCustomObject]@{
        DeviceStatus = $device.Status
        DeviceProblem = $device.Problem
        DriverVersion = $driver.DriverVersion
        DriverInf = $driver.InfName
        LoadedOllamaModels = (Get-LoadedOllamaModels).Count
        NvidiaTelemetry = Get-RtxTelemetry
    } | Format-List
}

function Assert-CanDisable {
    if ($Force) {
        return
    }

    $reasons = [Collections.Generic.List[string]]::new()
    if (Test-RtxDrivesDisplay) {
        $reasons.Add("NVIDIA currently drives an active display")
    }
    if ((Get-LoadedOllamaModels).Count) {
        $reasons.Add("Ollama has a loaded model")
    }
    if ($reasons.Count) {
        throw "RTX disable canceled: $($reasons -join '; '). Use -Force only when the impact is understood."
    }
}

function Wait-RtxStatus([string]$expectedStatus) {
    for ($attempt = 1; $attempt -le 15; $attempt++) {
        $device = Get-RtxDevice
        if ($device.Status -eq $expectedStatus) {
            return $device
        }
        Start-Sleep -Seconds 2
    }
    throw "RTX did not reach expected status $expectedStatus within 30 seconds."
}

switch ($Action) {
    "Status" {
        Show-RtxStatus
    }
    "Disable" {
        if (-not (Test-IsAdministrator)) {
            throw "Administrator rights are required to disable RTX."
        }
        Assert-CanDisable
        if ($PSCmdlet.ShouldProcess("RTX 3080 Laptop GPU", "disable")) {
            Disable-PnpDevice -InstanceId $rtxInstanceId -Confirm:$false | Out-Null
            Wait-RtxStatus "Error" | Out-Null
            Show-RtxStatus
        }
    }
    "Enable" {
        if (-not (Test-IsAdministrator)) {
            throw "Administrator rights are required to enable RTX."
        }
        if ($PSCmdlet.ShouldProcess("RTX 3080 Laptop GPU", "enable")) {
            Enable-PnpDevice -InstanceId $rtxInstanceId -Confirm:$false | Out-Null
            Wait-RtxStatus "OK" | Out-Null
            Show-RtxStatus
        }
    }
}
