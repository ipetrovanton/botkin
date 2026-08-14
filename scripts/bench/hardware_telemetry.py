from __future__ import annotations

import argparse
import csv
import ctypes
import json
import math
import os
import statistics
import subprocess
import threading
import time
import uuid
from collections.abc import Iterable
from pathlib import Path


_EVENT_MODIFY_STATE = 0x0002
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
_KERNEL32.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p)
_KERNEL32.CreateEventW.restype = ctypes.c_void_p
_KERNEL32.SetEvent.argtypes = (ctypes.c_void_p,)
_KERNEL32.SetEvent.restype = ctypes.c_bool
_KERNEL32.CloseHandle.argtypes = (ctypes.c_void_p,)
_KERNEL32.CloseHandle.restype = ctypes.c_bool

_NVIDIA_FIELDS = (
    "gpu_util_percent",
    "gpu_memory_util_percent",
    "gpu_vram_used_mib",
    "gpu_vram_total_mib",
    "gpu_temp_c",
    "gpu_power_w",
    "gpu_clock_mhz",
    "gpu_memory_clock_mhz",
    "gpu_pstate",
)


def _number(value: object) -> float | None:
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "none", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_nvidia_smi_sample(line: str) -> dict[str, float | str | None]:
    values = next(csv.reader([line], skipinitialspace=True))
    if len(values) != len(_NVIDIA_FIELDS):
        raise ValueError(f"ожидалось {len(_NVIDIA_FIELDS)} полей nvidia-smi, получено {len(values)}")
    sample: dict[str, float | str | None] = {}
    for field, value in zip(_NVIDIA_FIELDS, values, strict=True):
        sample[field] = None if value.strip().lower() in {"n/a", "na", "none", "null"} else value.strip()
    for field in _NVIDIA_FIELDS[:-1]:
        sample[field] = _number(sample[field])
    return sample


def parse_telemetry_line(line: str) -> dict:
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError("telemetry line must be a JSON object")
    return data


def fan_rpm_from_bytes(low: int, high: int) -> int:
    return (high & 0xFF) << 8 | (low & 0xFF)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _stats(values: list[float]) -> dict[str, int | float]:
    return {
        "available": len(values),
        "min": min(values),
        "mean": statistics.fmean(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values),
    }


def _integrate(samples: list[dict], field: str) -> float | None:
    total = 0.0
    intervals = 0
    for left, right in zip(samples, samples[1:], strict=False):
        t0 = _number(left.get("elapsed_s"))
        t1 = _number(right.get("elapsed_s"))
        v0 = _number(left.get(field))
        v1 = _number(right.get(field))
        if None in (t0, t1, v0, v1) or t1 <= t0:
            continue
        total += (v0 + v1) * 0.5 * (t1 - t0)
        intervals += 1
    return total if intervals else None


def summarize_samples(samples: Iterable[dict]) -> dict:
    rows = list(samples)
    if not rows:
        return {"sample_count": 0, "duration_s": 0.0, "metrics": {}, "energy_j": {}, "energy_wh": {}}
    times = [_number(row.get("elapsed_s")) for row in rows]
    valid_times = [value for value in times if value is not None]
    duration = max(valid_times) - min(valid_times) if valid_times else 0.0
    metric_names = sorted({key for row in rows for key in row if key != "elapsed_s"})
    metrics = {}
    for name in metric_names:
        values = [_number(row.get(name)) for row in rows]
        numeric = [value for value in values if value is not None]
        if numeric:
            metrics[name] = _stats(numeric)
    energy_j = {}
    for name in metric_names:
        if not name.endswith("_power_w"):
            continue
        energy = _integrate(rows, name)
        if energy is not None:
            energy_j[name.removesuffix("_power_w")] = energy
    return {
        "sample_count": len(rows),
        "duration_s": duration,
        "metrics": metrics,
        "energy_j": energy_j,
        "energy_wh": {name: value / 3600.0 for name, value in energy_j.items()},
    }


def find_lhm_dll() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    packages = local_app_data / "Microsoft" / "WinGet" / "Packages"
    matches = sorted(packages.glob("LibreHardwareMonitor.LibreHardwareMonitor_*/*HardwareMonitorLib.dll"))
    if not matches:
        matches = sorted(packages.glob("LibreHardwareMonitor.LibreHardwareMonitor_*/LibreHardwareMonitorLib.dll"))
    if not matches:
        raise FileNotFoundError("LibreHardwareMonitorLib.dll не найдена в WinGet Packages")
    return matches[-1]


class TelemetrySession:
    def __init__(self, output_prefix: Path | str | None = None, interval_s: float = 1.0):
        self.output_prefix = Path(output_prefix) if output_prefix is not None else None
        self.interval_s = interval_s
        self.samples: list[dict] = []
        self.errors: list[str] = []
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop_handle: int | None = None
        self._stop_event_name: str | None = None

    def start(self) -> "TelemetrySession":
        if self._process is not None:
            raise RuntimeError("telemetry session уже запущена")
        script = Path(__file__).with_suffix(".ps1")
        self._stop_event_name = f"Local\\BotkinTelemetry_{os.getpid()}_{uuid.uuid4().hex}"
        self._stop_handle = _KERNEL32.CreateEventW(None, True, False, self._stop_event_name)
        if not self._stop_handle:
            raise OSError(ctypes.get_last_error(), "не удалось создать stop event")
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-LibreHardwareMonitorDll",
            str(find_lhm_dll()),
            "-StopEventName",
            self._stop_event_name,
            "-IntervalSeconds",
            str(self.interval_s),
        ]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_errors, daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        return self

    def _read_output(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        raw_file = None
        if self.output_prefix is not None:
            self.output_prefix.parent.mkdir(parents=True, exist_ok=True)
            raw_file = self.output_prefix.with_suffix(".jsonl").open("w", encoding="utf-8", newline="\n")
        try:
            for line in self._process.stdout:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    sample = parse_telemetry_line(stripped)
                except (json.JSONDecodeError, ValueError) as exc:
                    self.errors.append(f"{exc}: {stripped[:300]}")
                    continue
                self.samples.append(sample)
                self._ready.set()
                if raw_file is not None:
                    raw_file.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")
                    raw_file.flush()
        finally:
            if raw_file is not None:
                raw_file.close()

    def _read_errors(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        for line in self._process.stderr:
            stripped = line.strip()
            if stripped:
                self.errors.append(stripped)

    def wait_ready(self, timeout_s: float = 60.0) -> None:
        if self._ready.wait(timeout_s):
            return
        if self._process is not None and self._process.poll() is not None:
            stderr = self._process.stderr.read().strip() if self._process.stderr is not None else ""
            raise RuntimeError(f"telemetry helper завершился до первого sample: {stderr}")
        details = "; ".join(self.errors[-10:])
        raise TimeoutError(f"telemetry helper не выдал sample за {timeout_s:g} секунд: {details}")

    def stop(self, timeout_s: float = 15.0) -> dict:
        if self._process is None:
            return self.summary()
        if self._stop_handle:
            _KERNEL32.SetEvent(self._stop_handle)
        try:
            self._process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=5)
            self.errors.append("PowerShell telemetry helper остановлен принудительно")
        if self._reader is not None:
            self._reader.join(timeout=5)
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=5)
        if self._stop_handle:
            _KERNEL32.CloseHandle(self._stop_handle)
            self._stop_handle = None
        self._process = None
        result = self.summary()
        if self.output_prefix is not None:
            summary_path = self.output_prefix.with_suffix(".summary.json")
            temp_path = summary_path.with_suffix(summary_path.suffix + ".tmp")
            temp_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(summary_path)
        return result

    def summary(self) -> dict:
        result = summarize_samples(self.samples)
        result["errors"] = list(self.errors)
        return result

    def __enter__(self) -> "TelemetrySession":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    session = TelemetrySession(args.output, args.interval).start()
    try:
        session.wait_ready()
        time.sleep(args.duration)
    finally:
        summary = session.stop()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["sample_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
