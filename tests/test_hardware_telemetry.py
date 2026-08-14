import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "hardware_telemetry.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("hardware_telemetry", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["hardware_telemetry"] = module
    spec.loader.exec_module(module)
    return module


def test_parse_nvidia_smi_sample():
    module = _load_module()

    sample = module.parse_nvidia_smi_sample(
        "42, 13, 2048, 16384, 73, 118.5, 1545, 6001, P2"
    )

    assert sample == {
        "gpu_util_percent": 42.0,
        "gpu_memory_util_percent": 13.0,
        "gpu_vram_used_mib": 2048.0,
        "gpu_vram_total_mib": 16384.0,
        "gpu_temp_c": 73.0,
        "gpu_power_w": 118.5,
        "gpu_clock_mhz": 1545.0,
        "gpu_memory_clock_mhz": 6001.0,
        "gpu_pstate": "P2",
    }


def test_parse_nvidia_smi_sample_keeps_unavailable_values():
    module = _load_module()

    sample = module.parse_nvidia_smi_sample(
        "0, 0, 0, 16384, 70, 33.88, 1245, 6001, N/A"
    )

    assert sample["gpu_pstate"] is None


def test_parse_telemetry_line():
    module = _load_module()

    assert module.parse_telemetry_line('{"elapsed_s":1.25,"fan1_rpm":4282}') == {
        "elapsed_s": 1.25,
        "fan1_rpm": 4282,
    }


def test_parse_telemetry_line_rejects_non_object():
    module = _load_module()

    with pytest.raises(ValueError, match="JSON object"):
        module.parse_telemetry_line("[]")


def test_fan_rpm_from_little_endian_bytes():
    module = _load_module()

    assert module.fan_rpm_from_bytes(0x3F, 0x10) == 4159


def test_summarize_samples_calculates_percentiles_and_energy():
    module = _load_module()
    samples = [
        {"elapsed_s": 0.0, "gpu_power_w": 10.0, "gpu_temp_c": 60.0},
        {"elapsed_s": 1.0, "gpu_power_w": 20.0, "gpu_temp_c": 70.0},
        {"elapsed_s": 2.0, "gpu_power_w": 30.0, "gpu_temp_c": 80.0},
    ]

    summary = module.summarize_samples(samples)

    assert summary["sample_count"] == 3
    assert summary["duration_s"] == 2.0
    assert summary["metrics"]["gpu_temp_c"] == {
        "available": 3,
        "min": 60.0,
        "mean": 70.0,
        "p50": 70.0,
        "p95": 80.0,
        "max": 80.0,
    }
    assert summary["energy_j"]["gpu"] == pytest.approx(40.0)
    assert summary["energy_wh"]["gpu"] == pytest.approx(40.0 / 3600.0)


def test_summarize_samples_ignores_missing_sensor_values():
    module = _load_module()
    samples = [
        {"elapsed_s": 0.0, "cpu_package_power_w": None},
        {"elapsed_s": 1.0, "cpu_package_power_w": 20.0},
        {"elapsed_s": 2.0, "cpu_package_power_w": None},
    ]

    summary = module.summarize_samples(samples)

    assert summary["metrics"]["cpu_package_power_w"]["available"] == 1
    assert "cpu_package" not in summary["energy_j"]
