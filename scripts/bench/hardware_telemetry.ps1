param(
    [Parameter(Mandatory = $true)]
    [string]$LibreHardwareMonitorDll,
    [Parameter(Mandatory = $true)]
    [string]$StopEventName,
    [double]$IntervalSeconds = 1.0
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -Path $LibreHardwareMonitorDll

function Get-SensorValue($hardware, [string]$type, [string]$name) {
    foreach ($sensor in $hardware.Sensors) {
        if ($sensor.SensorType.ToString() -eq $type -and $sensor.Name -eq $name) {
            return $sensor.Value
        }
    }
    return $null
}

function Read-Rpm($controller) {
    [UInt16[]]$registers = @(0x84, 0x85)
    [Byte[]]$data = @(0, 0)
    $controller.Read($registers, $data)
    return [int]$data[0] -bor ([int]$data[1] -shl 8)
}

$ecType = [LibreHardwareMonitor.Hardware.Motherboard.Lpc.EC.WindowsEmbeddedControllerIO]
$writeByte = $ecType.GetMethod("WriteByte", [Reflection.BindingFlags]"NonPublic,Instance")
$computer = [LibreHardwareMonitor.Hardware.Computer]::new()
$computer.IsCpuEnabled = $true
$computer.IsGpuEnabled = $true
$computer.IsMemoryEnabled = $true
$computer.Open()
$watch = [Diagnostics.Stopwatch]::StartNew()
$stopEvent = [Threading.EventWaitHandle]::new($false, [Threading.EventResetMode]::ManualReset, $StopEventName)
$intervalMs = [Math]::Max(100, [int]($IntervalSeconds * 1000))
$nextDueMs = 0
$lastFan1Rpm = $null
$lastFan2Rpm = $null

try {
    while (-not $stopEvent.WaitOne(0)) {
        $sample = [ordered]@{
            timestamp_utc = [DateTime]::UtcNow.ToString("o")
            elapsed_s = [Math]::Round($watch.Elapsed.TotalSeconds, 6)
        }
        foreach ($hardware in $computer.Hardware) {
            $hardware.Update()
            if ($hardware.HardwareType.ToString() -eq "Cpu") {
                $sample.cpu_util_percent = Get-SensorValue $hardware "Load" "CPU Total"
                $sample.cpu_temp_max_c = Get-SensorValue $hardware "Temperature" "Core Max"
                $sample.cpu_temp_avg_c = Get-SensorValue $hardware "Temperature" "Core Average"
                $sample.cpu_package_temp_c = Get-SensorValue $hardware "Temperature" "CPU Package"
                $sample.cpu_package_power_w = Get-SensorValue $hardware "Power" "CPU Package"
                $sample.cpu_platform_power_w = Get-SensorValue $hardware "Power" "CPU Platform"
                $clocks = @($hardware.Sensors | Where-Object {
                    $_.SensorType.ToString() -eq "Clock" -and $_.Name -like "CPU Core #*" -and $null -ne $_.Value
                } | ForEach-Object { [double]$_.Value })
                $sample.cpu_clock_avg_mhz = if ($clocks.Count) { ($clocks | Measure-Object -Average).Average } else { $null }
                $sample.cpu_clock_max_mhz = if ($clocks.Count) { ($clocks | Measure-Object -Maximum).Maximum } else { $null }
            }
            elseif ($hardware.HardwareType.ToString() -eq "Memory") {
                $sample.ram_load_percent = Get-SensorValue $hardware "Load" "Memory"
                $sample.ram_used_gib = Get-SensorValue $hardware "Data" "Memory Used"
                $sample.ram_available_gib = Get-SensorValue $hardware "Data" "Memory Available"
            }
        }

        $gpuLine = & nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,clocks.current.graphics,clocks.current.memory,pstate,clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.sw_power_cap,clocks_event_reasons.hw_slowdown --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        if ($gpuLine) {
            $parts = @($gpuLine.Split(",") | ForEach-Object { $_.Trim() })
            $sample.gpu_util_percent = $parts[0]
            $sample.gpu_memory_util_percent = $parts[1]
            $sample.gpu_vram_used_mib = $parts[2]
            $sample.gpu_vram_total_mib = $parts[3]
            $sample.gpu_temp_c = $parts[4]
            $sample.gpu_power_w = $parts[5]
            $sample.gpu_clock_mhz = $parts[6]
            $sample.gpu_memory_clock_mhz = $parts[7]
            $sample.gpu_pstate = $parts[8]
            $sample.gpu_thermal_slowdown = $parts[9]
            $sample.gpu_power_cap = $parts[10]
            $sample.gpu_hw_slowdown = $parts[11]
        }

        $ollamaProcesses = @(Get-Process -Name "ollama*" -ErrorAction SilentlyContinue)
        if ($ollamaProcesses.Count) {
            $sample.ollama_working_set_mib = ($ollamaProcesses | Measure-Object WorkingSet64 -Sum).Sum / 1MB
            $sample.ollama_private_memory_mib = ($ollamaProcesses | Measure-Object PrivateMemorySize64 -Sum).Sum / 1MB
        }

        $ec = $null
        $haveSelector = $false
        [Byte]$selectorOriginal = 0
        try {
            $ec = [LibreHardwareMonitor.Hardware.Motherboard.Lpc.EC.WindowsEmbeddedControllerIO]::new()
            [UInt16[]]$selectorRegister = @(0x31)
            [Byte[]]$selectorData = @(0)
            $ec.Read($selectorRegister, $selectorData)
            $selectorOriginal = $selectorData[0]
            $haveSelector = $true
            [Byte]$fan1Selector = $selectorOriginal -band 0xFE
            [Byte]$fan2Selector = $selectorOriginal -bor 0x01
            $writeByte.Invoke($ec, @([Byte]0x31, $fan1Selector)) | Out-Null
            $fan1Rpm = Read-Rpm $ec
            $writeByte.Invoke($ec, @([Byte]0x31, $fan2Selector)) | Out-Null
            $fan2Rpm = Read-Rpm $ec
            if ($fan1Rpm -eq 0 -or ($fan1Rpm -ge 1000 -and $fan1Rpm -le 0x1FFF)) { $lastFan1Rpm = $fan1Rpm }
            if ($fan2Rpm -eq 0 -or ($fan2Rpm -ge 1000 -and $fan2Rpm -le 0x1FFF)) { $lastFan2Rpm = $fan2Rpm }
            $sample.fan1_rpm = $lastFan1Rpm
            $sample.fan2_rpm = $lastFan2Rpm
        }
        catch {
            $sample.fan_error = $_.Exception.Message
        }
        finally {
            if ($null -ne $ec) {
                if ($haveSelector) {
                    $writeByte.Invoke($ec, @([Byte]0x31, $selectorOriginal)) | Out-Null
                }
                $ec.Dispose()
            }
        }

        $json = $sample | ConvertTo-Json -Compress -Depth 3
        [Console]::Out.WriteLine($json)
        [Console]::Out.Flush()
        $nextDueMs += $intervalMs
        $waitMs = [Math]::Max(0, $nextDueMs - $watch.Elapsed.TotalMilliseconds)
        if ($stopEvent.WaitOne([int]$waitMs)) {
            break
        }
    }
}
finally {
    $stopEvent.Dispose()
    $computer.Close()
}
