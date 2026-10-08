"""Independent 3x25 A phase load monitor for Home Assistant/AppDaemon.

This app only observes and analyses.  It never controls a switch.  The fuse
stress value is a heuristic thermal indicator, not an IEC fuse-curve model.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Tuple

import appdaemon.plugins.hass.hassapi as hass


PHASES = ("L1", "L2", "L3")
LEVELS = ("NORMAL", "ELEVATED", "HIGH", "CRITICAL", "OVERLOAD")
INVALID_STATES = {"", "unknown", "unavailable", "none", "null", "nan"}


def finite_float(value: Any) -> Optional[float]:
    """Return a finite float, or None for HA's unavailable values."""
    if value is None or str(value).strip().lower() in INVALID_STATES:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def percentile(values: Iterable[float], percent: float) -> Optional[float]:
    """Inclusive, linearly interpolated percentile."""
    ordered = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * max(0.0, min(100.0, percent)) / 100.0
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def histogram_percentile(histogram: Mapping[str, int], percent: float, bin_width: float = 0.1) -> Optional[float]:
    """Percentile from a compact binned distribution (maximum error: one bin)."""
    total = sum(max(0, int(count)) for count in histogram.values())
    if total <= 0:
        return None
    target = max(1, math.ceil(total * max(0.0, min(100.0, percent)) / 100.0))
    cumulative = 0
    for raw_bin, count in sorted(histogram.items(), key=lambda item: int(item[0])):
        cumulative += max(0, int(count))
        if cumulative >= target:
            return int(raw_bin) * bin_width
    return None


def linear_trend(points: Iterable[Tuple[float, float]]) -> Tuple[float, float]:
    """Return least-squares slope (A/s) and R-squared."""
    data = list(points)
    if len(data) < 2:
        return 0.0, 0.0
    xs = [item[0] for item in data]
    ys = [item[1] for item in data]
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator <= 0:
        return 0.0, 0.0
    slope = sum((x - x_mean) * (y - y_mean) for x, y in data) / denominator
    fitted = [y_mean + slope * (x - x_mean) for x in xs]
    total = sum((y - y_mean) ** 2 for y in ys)
    residual = sum((y - fit) ** 2 for y, fit in zip(ys, fitted))
    r_squared = 1.0 if total <= 1e-9 else max(0.0, min(1.0, 1.0 - residual / total))
    return slope, r_squared


def duration_text(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@dataclass
class PhaseRuntime:
    samples: Deque[Tuple[float, float, float]] = field(default_factory=deque)
    ema: Optional[float] = None
    maximum: float = 0.0
    stress: float = 0.0
    source: str = "unavailable"
    direction: str = "unknown"
    pending_outlier: Optional[float] = None
    pending_count: int = 0
    last_accepted: Optional[float] = None
    consecutive: Dict[str, float] = field(default_factory=dict)
    today_above: Dict[str, float] = field(default_factory=dict)
    day_values: List[float] = field(default_factory=list)
    hour_values: List[float] = field(default_factory=list)


class PhasePeakGuard(hass.Hass):
    """Monitor current, headroom, trends and imbalance on a 3x25 A supply."""

    VERSION = "1.1.2"

    def initialize(self) -> None:
        self.service_fuse_amp = self._number("service_fuse_amp", 25.0, 1.0, 200.0)
        self.nominal_voltage = self._number("nominal_voltage", 230.0, 100.0, 300.0)
        self.sample_interval = self._integer("sample_interval_sec", 5, 2, 60)
        self.ema_window = self._number("ema_window_sec", 30.0, 2.0, 900.0)
        self.publish_interval = self._integer("mqtt_publish_interval_sec", 20, 5, 300)
        self.save_interval = self._integer("persistent_save_interval_sec", 300, 60, 3600)
        self.prediction_window = self._integer("prediction_window_sec", 120, 30, 900)
        self.min_prediction_samples = self._integer("prediction_min_samples", 8, 3, 100)
        self.prediction_max_change = self._number("prediction_max_change_amp", 6.0, 0.5, 50.0)
        self.assumed_power_factor = self._number("assumed_power_factor", 0.90, 0.1, 1.0)
        # Explicit multipliers remain supported, but omission enables automatic
        # conversion from the unit_of_measurement attribute supplied by HA.
        self.current_multiplier = self._optional_number("current_multiplier")
        self.voltage_multiplier = self._optional_number("voltage_multiplier")
        self.active_power_multiplier = self._optional_number("active_power_multiplier")
        self.apparent_power_multiplier = self._optional_number("apparent_power_multiplier")
        self.max_source_age = self._integer("max_source_age_sec", 30, 5, 600)
        self.shared_source_freshness = self._boolean(self.args.get("shared_source_freshness", True))
        self.max_plausible_current = self._number("max_plausible_current_amp", 80.0, 1.0, 500.0)
        self.outlier_jump = self._number("outlier_jump_amp", 15.0, 1.0, 100.0)
        self.outlier_confirmations = self._integer("outlier_confirmation_samples", 2, 1, 10)
        self.outlier_tolerance = self._number("outlier_confirmation_tolerance_amp", 3.0, 0.1, 20.0)
        self.imbalance_min_average = self._number("imbalance_min_average_amp", 2.0, 0.0, 50.0)
        self.imbalance_mild = self._number("structural_imbalance_mild_amp", 3.0, 0.1, 50.0)
        self.imbalance_significant = self._number("structural_imbalance_significant_amp", 6.0, 0.1, 50.0)
        self.imbalance_severe = self._number("structural_imbalance_severe_amp", 10.0, 0.1, 50.0)
        self.structural_min_samples = self._integer("structural_min_samples", 720, 10, 1000000)
        self.stress_tau = self._number("fuse_stress_time_constant_sec", 1200.0, 60.0, 86400.0)
        self.direction_deadband_w = self._number("direction_deadband_w", 25.0, 0.0, 1000.0)

        self.thresholds = {
            "elevated": self._number("threshold_elevated_amp", 17.5, 0.0, 200.0),
            "high": self._number("threshold_high_amp", 20.0, 0.0, 200.0),
            "critical": self._number("threshold_critical_amp", 22.5, 0.0, 200.0),
            "limit": self._number("threshold_limit_amp", 25.0, 0.0, 200.0),
        }
        if list(self.thresholds.values()) != sorted(self.thresholds.values()):
            raise ValueError("PhaseGuard thresholds must be in ascending order")

        self.entities: Dict[str, Dict[str, Optional[str]]] = {}
        for phase in PHASES:
            suffix = phase.lower()
            self.entities[phase] = {
                "current": self._entity_arg(f"current_{suffix}"),
                "voltage": self._entity_arg(f"voltage_{suffix}"),
                "apparent": self._entity_arg(f"apparent_power_{suffix}"),
                "net": self._entity_arg(f"net_power_{suffix}"),
                "import": self._entity_arg(f"import_power_{suffix}"),
                "export": self._entity_arg(f"export_power_{suffix}"),
            }

        self.mqtt_enabled = self._boolean(self.args.get("mqtt_enabled", True))
        self.discovery_prefix = str(self.args.get("mqtt_discovery_prefix", "homeassistant")).strip("/")
        self.mqtt_base = str(self.args.get("mqtt_base_topic", "home/phase_guard")).strip("/")
        self.device_name = str(self.args.get("mqtt_device_name", "3x25A Phase Guard"))
        self.device_id = str(self.args.get("mqtt_device_id", "phase_peak_guard"))
        self.storage_file = Path(str(self.args.get("storage_file", "/homeassistant/python_storage/phase_peak_guard.json")))
        self.max_storage_bytes = int(self._number("max_storage_mb", 5.0, 0.1, 100.0) * 1024 * 1024)
        self.daily_retention = self._integer("daily_retention_days", 35, 7, 366)
        self.hourly_retention = self._integer("hourly_retention_hours", 168, 24, 744)

        # Accepted for forward-compatible configuration, but deliberately unused.
        self.known_loads = self.args.get("known_loads", {})
        self.sheddable_loads = self.args.get("sheddable_loads", [])
        self.load_shedding_enabled = False

        max_samples = max(40, int(1800 / self.sample_interval) + 2)
        self.phase = {name: PhaseRuntime(samples=deque(maxlen=max_samples)) for name in PHASES}
        self.daily_history: List[Dict[str, Any]] = []
        self.hourly_history: List[Dict[str, Any]] = []
        self.current_day = datetime.now().astimezone().date()
        self.current_hour = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
        self.sample_count = 0
        self.last_sample_mono: Optional[float] = None
        self.last_publish_mono = 0.0
        self.last_save_mono = 0.0
        self.last_status = "startup"
        self.last_phase_levels = {phase: "UNKNOWN" for phase in PHASES}
        self.last_source_valid = {phase: True for phase in PHASES}
        self.last_log: Dict[str, float] = {}
        self._unknown_units_logged: set[str] = set()
        self.latest_state: Dict[str, Any] = {}
        self.mqtt_announced_online = False

        self._load_data()
        self._publish_discovery()
        # Keep retained state valid JSON during a restart. Publishing an empty
        # retained state makes Home Assistant evaluate every value_json template
        # against an undefined value. Availability stays offline until the first
        # fresh state has been published.
        self._mqtt_publish(f"{self.mqtt_base}/availability", "offline", True)
        self.log("PhaseGuard v%s gestart (alleen monitoren; load shedding uit)", self.VERSION)
        self._log_configuration()
        start = datetime.now().astimezone() + timedelta(seconds=2)
        self.run_every(self._sample, start, self.sample_interval)

    def terminate(self) -> None:
        self._finalize_periods(datetime.now().astimezone())
        self._save_data()
        self._mqtt_publish(f"{self.mqtt_base}/availability", "offline", True)

    # ------------------------------ sampling ------------------------------

    def _sample(self, kwargs: Mapping[str, Any]) -> None:
        try:
            now = datetime.now().astimezone()
            mono = now.timestamp()
            elapsed = self.sample_interval if self.last_sample_mono is None else max(
                0.0, min(mono - self.last_sample_mono, self.sample_interval * 2.5)
            )
            self.last_sample_mono = mono
            self._finalize_periods(now)
            self._state_cache: Dict[str, Any] = {}
            self._shared_source_is_fresh = self._detect_shared_source_freshness(now)

            readings: Dict[str, Optional[float]] = {}
            metadata: Dict[str, Dict[str, Any]] = {}
            for phase in PHASES:
                value, source, direction, voltage, error = self._read_phase(phase, now)
                accepted = self._filter_current(phase, value) if value is not None else None
                readings[phase] = accepted
                metadata[phase] = {
                    "source": source,
                    "direction": direction,
                    "voltage": voltage,
                    "error": error,
                }
                if accepted is None:
                    self.phase[phase].consecutive = {key: 0.0 for key in self.thresholds}
                    self._source_event(phase, False, error or "ongeldige data")
                    continue
                self._source_event(phase, True, "")
                self._update_phase(phase, accepted, mono, elapsed, source, direction)

            self.sample_count += 1
            self.latest_state = self._build_state(now, readings, metadata)
            status = str(self.latest_state.get("status", "data_error"))
            self._log_transitions(status)

            if mono - self.last_publish_mono >= self.publish_interval:
                self._publish_state(self.latest_state)
                self.last_publish_mono = mono
            if mono - self.last_save_mono >= self.save_interval:
                self._save_data()
                self.last_save_mono = mono
        except Exception as err:
            self._rate_log("sample_error", f"PhaseGuard samplefout: {err}", 300, "ERROR")

    def _read_phase(
        self, phase: str, now: datetime
    ) -> Tuple[Optional[float], str, str, Optional[float], str]:
        cfg = self.entities[phase]
        current, current_error = self._read_measurement(cfg["current"], now, "current")
        voltage, voltage_error = self._read_measurement(cfg["voltage"], now, "voltage")
        apparent, apparent_error = self._read_measurement(cfg["apparent"], now, "apparent_power")
        net, net_error = self._read_measurement(cfg["net"], now, "active_power")
        imported, import_error = self._read_measurement(cfg["import"], now, "active_power")
        exported, export_error = self._read_measurement(cfg["export"], now, "active_power")

        direction = self._direction(net, imported, exported)
        if current is not None:
            return abs(current), "measured", direction, voltage, ""
        effective_voltage = voltage if voltage is not None and voltage > 1 else self.nominal_voltage
        if apparent is not None:
            return abs(apparent) / effective_voltage, "estimated_from_apparent_power", direction, voltage, ""
        active_power: Optional[float] = net
        if active_power is None and (imported is not None or exported is not None):
            active_power = (imported or 0.0) - (exported or 0.0)
        if active_power is not None:
            estimate = abs(active_power) / (effective_voltage * self.assumed_power_factor)
            return estimate, "estimated_from_active_power", direction, voltage, ""
        errors = [current_error, apparent_error, net_error, import_error, export_error, voltage_error]
        message = next((error for error in errors if error), "geen bruikbare bron")
        return None, "unavailable", direction, voltage, message

    def _read_entity(self, entity: Optional[str], now: datetime) -> Tuple[Optional[float], str]:
        if not entity:
            return None, "niet geconfigureerd"
        try:
            record = self._state_cache.get(entity)
            if record is None:
                record = self.get_state(entity, attribute="all")
                self._state_cache[entity] = record
        except Exception as err:
            return None, f"{entity}: {err}"
        if not isinstance(record, Mapping):
            return None, f"{entity} ontbreekt"
        value = finite_float(record.get("state"))
        if value is None:
            return None, f"{entity} is {record.get('state')}"
        # HA's last_updated may remain unchanged while a sensor repeatedly reports
        # the same value. Newer HA versions expose last_reported for that case.
        stamp = record.get("last_reported") or record.get("last_updated") or record.get("last_changed")
        parsed = self._timestamp(stamp)
        if parsed is None:
            return None, f"{entity} heeft geen geldige timestamp"
        age = max(0.0, (now.astimezone(timezone.utc) - parsed).total_seconds())
        if age > self.max_source_age and not (self.shared_source_freshness and self._shared_source_is_fresh):
            return None, f"{entity} is {int(age)}s oud"
        return value, ""

    def _read_measurement(
        self, entity: Optional[str], now: datetime, quantity: str
    ) -> Tuple[Optional[float], str]:
        """Read and normalize a HA measurement to A, V, W or VA."""
        value, error = self._read_entity(entity, now)
        if value is None or not entity:
            return value, error
        record = self._state_cache.get(entity)
        attributes = record.get("attributes", {}) if isinstance(record, Mapping) else {}
        unit = str(attributes.get("unit_of_measurement", "")).strip()
        multiplier = self._measurement_multiplier(quantity, unit, entity)
        return value * multiplier, error

    def _measurement_multiplier(self, quantity: str, unit: str, entity: str = "") -> float:
        overrides = {
            "current": self.current_multiplier,
            "voltage": self.voltage_multiplier,
            "active_power": self.active_power_multiplier,
            "apparent_power": self.apparent_power_multiplier,
        }
        override = overrides.get(quantity)
        if override is not None:
            return override
        normalized = unit.replace(" ", "").replace("µ", "u").lower()
        unit_maps = {
            "current": {"": 1.0, "a": 1.0, "ma": 0.001},
            "voltage": {"": 1.0, "v": 1.0, "mv": 0.001},
            "active_power": {"": 1.0, "w": 1.0, "kw": 1000.0, "mw": 1000000.0},
            "apparent_power": {"": 1.0, "va": 1.0, "kva": 1000.0, "mva": 1000000.0},
        }
        mapping = unit_maps.get(quantity, {"": 1.0})
        if normalized in mapping:
            return mapping[normalized]
        key = f"{quantity}:{unit}"
        if key not in self._unknown_units_logged:
            self.log(
                "PhaseGuard: onbekende eenheid '%s' voor %s (%s); multiplier 1 gebruikt",
                unit or "leeg", entity or quantity, quantity, level="WARNING",
            )
            self._unknown_units_logged.add(key)
        return 1.0

    def _detect_shared_source_freshness(self, now: datetime) -> bool:
        """Use the newest sibling phase report as heartbeat for one shared meter.

        DSMR entities commonly do not change last_updated while their numeric
        value stays exactly equal (notably a zero-current phase). An unavailable
        or non-numeric entity is still rejected by _read_entity; this heartbeat
        only prevents unchanged valid values from being called stale.
        """
        if not self.shared_source_freshness:
            return False
        for phase in PHASES:
            for entity in self.entities[phase].values():
                if not entity:
                    continue
                try:
                    record = self._state_cache.get(entity)
                    if record is None:
                        record = self.get_state(entity, attribute="all")
                        self._state_cache[entity] = record
                except Exception:
                    continue
                if not isinstance(record, Mapping) or finite_float(record.get("state")) is None:
                    continue
                stamp = record.get("last_reported") or record.get("last_updated") or record.get("last_changed")
                parsed = self._timestamp(stamp)
                if parsed is None:
                    continue
                age = max(0.0, (now.astimezone(timezone.utc) - parsed).total_seconds())
                if age <= self.max_source_age:
                    return True
        return False

    def _filter_current(self, phase: str, value: Optional[float]) -> Optional[float]:
        if value is None or value < 0 or value > self.max_plausible_current:
            return None
        runtime = self.phase[phase]
        previous = runtime.last_accepted
        if previous is None or self.outlier_confirmations <= 1 or abs(value - previous) <= self.outlier_jump:
            runtime.pending_outlier = None
            runtime.pending_count = 0
            runtime.last_accepted = value
            return value
        if runtime.pending_outlier is not None and abs(value - runtime.pending_outlier) <= self.outlier_tolerance:
            runtime.pending_count += 1
        else:
            runtime.pending_outlier = value
            runtime.pending_count = 1
        if runtime.pending_count >= self.outlier_confirmations:
            runtime.last_accepted = value
            runtime.pending_outlier = None
            runtime.pending_count = 0
            return value
        return previous

    def _update_phase(
        self, phase: str, current: float, timestamp: float, elapsed: float, source: str, direction: str
    ) -> None:
        runtime = self.phase[phase]
        alpha = 1.0 - math.exp(-max(0.0, elapsed) / self.ema_window)
        runtime.ema = current if runtime.ema is None else runtime.ema + alpha * (current - runtime.ema)
        runtime.samples.append((timestamp, current, runtime.ema))
        runtime.maximum = max(runtime.maximum, current)
        runtime.source = source
        runtime.direction = direction
        runtime.day_values.append(current)
        runtime.hour_values.append(current)

        for key, threshold in self.thresholds.items():
            if current >= threshold:
                runtime.consecutive[key] = runtime.consecutive.get(key, 0.0) + elapsed
                runtime.today_above[key] = runtime.today_above.get(key, 0.0) + elapsed
            else:
                runtime.consecutive[key] = 0.0

        # First-order thermal accumulator. Target rises quadratically with load.
        target = min(200.0, 100.0 * (current / self.service_fuse_amp) ** 2)
        stress_alpha = 1.0 - math.exp(-max(0.0, elapsed) / self.stress_tau)
        runtime.stress = max(0.0, min(100.0, runtime.stress + stress_alpha * (target - runtime.stress)))

    # ------------------------------ analysis ------------------------------

    def _build_state(
        self,
        now: datetime,
        readings: Mapping[str, Optional[float]],
        metadata: Mapping[str, Mapping[str, Any]],
    ) -> Dict[str, Any]:
        valid = all(readings.get(phase) is not None for phase in PHASES)
        state: Dict[str, Any] = {
            "status": "data_error",
            "reason": "Onvoldoende betrouwbare meetdata voor alle drie fasen.",
            "data_valid": "ON" if valid else "OFF",
            "warning": "OFF",
            "critical": "OFF",
            "overload": "OFF",
            "samples": self.sample_count,
            "last_update": now.isoformat(timespec="seconds"),
            "load_shedding_enabled": "OFF",
            "recommended_action": "check_phase_data" if not valid else "none",
        }
        sources: List[str] = []
        currents: Dict[str, float] = {}

        for phase in PHASES:
            key = phase.lower()
            current = readings.get(phase)
            runtime = self.phase[phase]
            source = str(metadata[phase].get("source", "unavailable"))
            sources.append(source)
            state[f"{key}_current_source"] = source
            state[f"{key}_direction"] = str(metadata[phase].get("direction", "unknown"))
            voltage = metadata[phase].get("voltage")
            state[f"{key}_voltage"] = self._round(voltage, 1)
            state[f"{key}_data_error"] = str(metadata[phase].get("error", ""))[:200]
            if current is None:
                for suffix in ("current", "current_smoothed", "average_1m", "average_5m", "average_15m",
                               "headroom", "trend", "predicted_30s", "predicted_60s", "predicted_120s",
                               "prediction_confidence", "fuse_stress"):
                    state[f"{key}_{suffix}"] = None
                state[f"{key}_level"] = "DATA_ERROR"
                continue

            currents[phase] = current
            state[f"{key}_current"] = round(current, 2)
            state[f"{key}_current_smoothed"] = self._round(runtime.ema, 2)
            state[f"{key}_average_1m"] = self._round(self._rolling_average(runtime, now.timestamp(), 60), 2)
            state[f"{key}_average_5m"] = self._round(self._rolling_average(runtime, now.timestamp(), 300), 2)
            state[f"{key}_average_15m"] = self._round(self._rolling_average(runtime, now.timestamp(), 900), 2)
            state[f"{key}_maximum"] = round(runtime.maximum, 2)
            state[f"{key}_headroom"] = round(self.service_fuse_amp - current, 2)
            slope, predictions, confidence = self._prediction(runtime, now.timestamp())
            state[f"{key}_trend"] = round(slope * 60.0, 3)
            for horizon in (30, 60, 120):
                state[f"{key}_predicted_{horizon}s"] = round(predictions[horizon], 2)
            state[f"{key}_prediction_confidence"] = round(confidence, 1)
            state[f"{key}_fuse_stress"] = round(runtime.stress, 1)
            state[f"{key}_level"] = self._level(current)
            for threshold_key in self.thresholds:
                state[f"{key}_{threshold_key}_duration"] = round(runtime.consecutive.get(threshold_key, 0.0))
                state[f"{key}_{threshold_key}_today"] = round(runtime.today_above.get(threshold_key, 0.0))

        unique_sources = sorted(set(sources))
        state["current_source"] = unique_sources[0] if len(unique_sources) == 1 else "mixed"
        self._add_historical_state(state, now.date())
        if not valid:
            missing = ", ".join(phase for phase in PHASES if readings.get(phase) is None)
            state["warning"] = "ON"
            state["reason"] = f"Meetdata ontbreekt of is ongeldig voor {missing}; veiligheid kan niet worden beoordeeld."
            return state

        ordered = sorted(currents, key=currents.get)  # type: ignore[arg-type]
        least, most = ordered[0], ordered[-1]
        values = list(currents.values())
        average = statistics.fmean(values)
        imbalance = max(values) - min(values)
        imbalance_pct = (imbalance / average * 100.0) if average >= self.imbalance_min_average else 0.0
        peak = currents[most]
        level = self._level(peak)
        status = level.lower()
        state.update({
            "status": status,
            "total_current": round(sum(values), 2),
            "peak_phase_current": round(peak, 2),
            "most_loaded_phase": most,
            "least_loaded_phase": least,
            "lowest_headroom": round(self.service_fuse_amp - peak, 2),
            "phase_imbalance_amp": round(imbalance, 2),
            "phase_imbalance_percent": round(imbalance_pct, 1),
            "warning": "ON" if level != "NORMAL" else "OFF",
            "critical": "ON" if level in {"CRITICAL", "OVERLOAD"} else "OFF",
            "overload": "ON" if level == "OVERLOAD" else "OFF",
            "recommended_action": self._recommended_action(level, most),
        })
        state["reason"] = self._reason(state, most, peak, level)
        return state

    def _prediction(self, runtime: PhaseRuntime, now_ts: float) -> Tuple[float, Dict[int, float], float]:
        points = [(stamp, ema) for stamp, _raw, ema in runtime.samples if now_ts - stamp <= self.prediction_window]
        current = runtime.ema or runtime.last_accepted or 0.0
        if len(points) < self.min_prediction_samples:
            return 0.0, {30: current, 60: current, 120: current}, 0.0
        origin = points[0][0]
        normalized = [(stamp - origin, value) for stamp, value in points]
        slope, r_squared = linear_trend(normalized)
        coverage = min(1.0, (points[-1][0] - points[0][0]) / self.prediction_window)
        sample_factor = min(1.0, len(points) / max(self.min_prediction_samples * 2, 1))
        confidence = 100.0 * coverage * sample_factor * r_squared
        predictions: Dict[int, float] = {}
        for horizon in (30, 60, 120):
            change_limit = self.prediction_max_change * horizon / 60.0
            change = max(-change_limit, min(change_limit, slope * horizon))
            predictions[horizon] = max(0.0, min(self.max_plausible_current, current + change))
        return slope, predictions, confidence

    def _rolling_average(self, runtime: PhaseRuntime, now_ts: float, seconds: int) -> Optional[float]:
        values = [raw for stamp, raw, _ema in runtime.samples if now_ts - stamp <= seconds]
        return statistics.fmean(values) if values else None

    def _level(self, current: float) -> str:
        if current >= self.thresholds["limit"]:
            return "OVERLOAD"
        if current >= self.thresholds["critical"]:
            return "CRITICAL"
        if current >= self.thresholds["high"]:
            return "HIGH"
        if current >= self.thresholds["elevated"]:
            return "ELEVATED"
        return "NORMAL"

    def _reason(self, state: Mapping[str, Any], phase: str, current: float, level: str) -> str:
        key = phase.lower()
        predicted = finite_float(state.get(f"{key}_predicted_60s")) or current
        duration = float(self.phase[phase].consecutive.get(
            "limit" if level == "OVERLOAD" else "critical" if level == "CRITICAL" else
            "high" if level == "HIGH" else "elevated", 0.0
        ))
        if level == "NORMAL":
            if state.get("structural_imbalance") in {"significant", "severe"}:
                historical = state.get("historically_most_loaded_phase", "onbekend")
                spread = finite_float(state.get("structural_imbalance_amp")) or 0.0
                return f"Actueel normaal; structurele fase-onbalans: {historical} ligt circa {spread:.1f} A hoger."
            return f"Alle fasen normaal; grootste belasting {phase} {current:.1f} A."
        if level == "ELEVATED":
            return f"{phase} verhoogd: {current:.1f} A gedurende {duration_text(duration)}."
        if level == "HIGH":
            return f"{phase} langdurig hoog: {current:.1f} A gedurende {duration_text(duration)}."
        if level == "CRITICAL":
            return f"{phase} kritisch: {current:.1f} A; voorspeld {predicted:.1f} A over 60 s."
        return f"{phase} boven ingestelde grens: {current:.1f} A gedurende {duration_text(duration)}."

    @staticmethod
    def _recommended_action(level: str, phase: str) -> str:
        if level == "ELEVATED":
            return "avoid_new_load"
        if level == "HIGH":
            return f"reduce_load_{phase.lower()}"
        if level in {"CRITICAL", "OVERLOAD"}:
            return f"critical_load_{phase.lower()}"
        return "none"

    def _direction(
        self, net: Optional[float], imported: Optional[float], exported: Optional[float]
    ) -> str:
        # net_power convention is configurable; default: positive means import.
        signed: Optional[float] = None
        if imported is not None or exported is not None:
            signed = (imported or 0.0) - (exported or 0.0)
        elif net is not None:
            signed = net
            if not self._boolean(self.args.get("net_power_import_positive", True)):
                signed = -signed
        if signed is None:
            return "unknown"
        if abs(signed) <= self.direction_deadband_w:
            return "neutral"
        return "import" if signed > 0 else "export"

    # ---------------------------- aggregation -----------------------------

    def _finalize_periods(self, now: datetime, force: bool = False) -> None:
        if now.date() != self.current_day or force:
            summary = {"date": self.current_day.isoformat(), "phases": {}}
            for phase in PHASES:
                runtime = self.phase[phase]
                values = runtime.day_values
                if values:
                    summary["phases"][phase] = {
                        "samples": len(values),
                        "max": round(max(values), 3),
                        "mean": round(statistics.fmean(values), 3),
                        "median": round(statistics.median(values), 3),
                        "p95": round(percentile(values, 95) or 0.0, 3),
                        "histogram_0_1a": self._histogram(values),
                        **{f"seconds_above_{key}": round(runtime.today_above.get(key, 0.0), 1)
                           for key in self.thresholds},
                    }
                runtime.day_values = []
                runtime.today_above = {}
                runtime.maximum = 0.0
            if summary["phases"]:
                self.daily_history = [item for item in self.daily_history if item.get("date") != summary["date"]]
                self.daily_history.append(summary)
            self.current_day = now.date()
        hour = now.replace(minute=0, second=0, microsecond=0)
        if hour != self.current_hour or force:
            summary = {"hour": self.current_hour.isoformat(), "phases": {}}
            for phase in PHASES:
                values = self.phase[phase].hour_values
                if values:
                    summary["phases"][phase] = {
                        "samples": len(values), "mean": round(statistics.fmean(values), 3),
                        "max": round(max(values), 3), "p95": round(percentile(values, 95) or 0.0, 3),
                    }
                self.phase[phase].hour_values = []
            if summary["phases"]:
                self.hourly_history = [item for item in self.hourly_history if item.get("hour") != summary["hour"]]
                self.hourly_history.append(summary)
            self.current_hour = hour
        self._prune_history(now)

    def _add_historical_state(self, state: Dict[str, Any], today: date) -> None:
        results: Dict[int, Dict[str, Optional[float]]] = {}
        sample_counts: Dict[int, Dict[str, int]] = {}
        for days in (7, 30):
            cutoff = today - timedelta(days=days - 1)
            rows = [item for item in self.daily_history if self._date(item.get("date")) and self._date(item.get("date")) >= cutoff]
            results[days] = {}
            sample_counts[days] = {}
            for phase in PHASES:
                combined: Dict[str, int] = {}
                maxima: List[float] = []
                threshold_seconds = {key: self.phase[phase].today_above.get(key, 0.0) for key in self.thresholds}
                for row in rows:
                    phase_row = row.get("phases", {}).get(phase, {})
                    for raw_bin, count in phase_row.get("histogram_0_1a", {}).items():
                        combined[str(raw_bin)] = combined.get(str(raw_bin), 0) + int(count)
                    maximum = finite_float(phase_row.get("max"))
                    if maximum is not None:
                        maxima.append(maximum)
                    for threshold in self.thresholds:
                        threshold_seconds[threshold] += float(phase_row.get(f"seconds_above_{threshold}", 0.0) or 0.0)
                # Include today's live samples so a fresh installation starts learning immediately.
                for raw_bin, count in self._histogram(self.phase[phase].day_values).items():
                    combined[raw_bin] = combined.get(raw_bin, 0) + count
                if self.phase[phase].day_values:
                    maxima.append(max(self.phase[phase].day_values))
                count = sum(combined.values())
                sample_counts[days][phase] = count
                mean = (sum(int(raw_bin) * 0.1 * amount for raw_bin, amount in combined.items()) / count) if count else None
                median = histogram_percentile(combined, 50)
                p95 = histogram_percentile(combined, 95)
                results[days][phase] = p95
                prefix = phase.lower()
                state[f"{prefix}_mean_{days}d"] = self._round(mean, 2)
                state[f"{prefix}_median_{days}d"] = self._round(median, 2)
                state[f"{prefix}_p95_{days}d"] = self._round(p95, 2)
                state[f"{prefix}_max_{days}d"] = self._round(max(maxima) if maxima else None, 2)
                for threshold, seconds in threshold_seconds.items():
                    state[f"{prefix}_seconds_above_{threshold}_{days}d"] = round(seconds)
        use_30d = all(sample_counts[30].get(phase, 0) >= self.structural_min_samples for phase in PHASES)
        basis_days = 30 if use_30d else 7
        basis = results[basis_days]
        enough_data = all(sample_counts[basis_days].get(phase, 0) >= self.structural_min_samples for phase in PHASES)
        if enough_data and all(basis.get(phase) is not None for phase in PHASES):
            numeric = {phase: float(basis[phase]) for phase in PHASES}  # type: ignore[arg-type]
            most = max(numeric, key=numeric.get)  # type: ignore[arg-type]
            least = min(numeric, key=numeric.get)  # type: ignore[arg-type]
            spread = numeric[most] - numeric[least]
            status = "severe" if spread >= self.imbalance_severe else "significant" if spread >= self.imbalance_significant else "mild" if spread >= self.imbalance_mild else "balanced"
            state["structural_imbalance"] = status
            state["structural_imbalance_amp"] = round(spread, 2)
            state["historically_most_loaded_phase"] = most
            state["historically_least_loaded_phase"] = least
        else:
            state["structural_imbalance"] = "insufficient_data"
            state["structural_imbalance_amp"] = None
            state["historically_most_loaded_phase"] = "unknown"
            state["historically_least_loaded_phase"] = "unknown"

    # --------------------------- persistence ------------------------------

    def _load_data(self) -> None:
        if not self.storage_file.exists():
            return
        try:
            payload = json.loads(self.storage_file.read_text(encoding="utf-8"))
            self.daily_history = list(payload.get("daily", []))
            self.hourly_history = list(payload.get("hourly", []))
            now_local = datetime.now().astimezone()
            current_day = payload.get("current_day", {})
            if current_day.get("date") == now_local.date().isoformat():
                for phase in PHASES:
                    restored = current_day.get("phases", {}).get(phase, {})
                    values = [float(value) for value in restored.get("values", []) if finite_float(value) is not None]
                    self.phase[phase].day_values = values
                    self.phase[phase].today_above = {
                        key: float(value) for key, value in restored.get("today_above", {}).items()
                        if key in self.thresholds and finite_float(value) is not None
                    }
                    self.phase[phase].maximum = float(restored.get("maximum", max(values, default=0.0)))
            current_hour = payload.get("current_hour", {})
            if current_hour.get("hour") == self.current_hour.isoformat():
                for phase in PHASES:
                    self.phase[phase].hour_values = [
                        float(value) for value in current_hour.get("phases", {}).get(phase, [])
                        if finite_float(value) is not None
                    ]
            saved_at = self._timestamp(payload.get("saved_at"))
            age = (datetime.now(timezone.utc) - saved_at).total_seconds() if saved_at else float("inf")
            stresses = payload.get("fuse_stress", {})
            if age <= self.stress_tau * 4:
                decay = math.exp(-max(0.0, age) / self.stress_tau)
                for phase in PHASES:
                    self.phase[phase].stress = max(0.0, min(100.0, float(stresses.get(phase, 0.0)) * decay))
        except Exception as err:
            self.log("PhaseGuard opslag beschadigd of onleesbaar: %s", err, level="ERROR")
            try:
                corrupt = self.storage_file.with_suffix(self.storage_file.suffix + ".corrupt")
                os.replace(self.storage_file, corrupt)
            except OSError:
                pass

    def _save_data(self) -> None:
        try:
            self._prune_history(datetime.now().astimezone())
            payload = {
                "version": self.VERSION,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "daily": self.daily_history,
                "hourly": self.hourly_history,
                "fuse_stress": {phase: round(self.phase[phase].stress, 4) for phase in PHASES},
                "current_day": {
                    "date": self.current_day.isoformat(),
                    "phases": {
                        phase: {
                            "values": [round(value, 3) for value in self.phase[phase].day_values],
                            "today_above": self.phase[phase].today_above,
                            "maximum": self.phase[phase].maximum,
                        } for phase in PHASES
                    },
                },
                "current_hour": {
                    "hour": self.current_hour.isoformat(),
                    "phases": {phase: [round(value, 3) for value in self.phase[phase].hour_values] for phase in PHASES},
                },
            }
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            while len(encoded) > self.max_storage_bytes and self.hourly_history:
                self.hourly_history.pop(0)
                payload["hourly"] = self.hourly_history
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            while len(encoded) > self.max_storage_bytes and len(self.daily_history) > 7:
                self.daily_history.pop(0)
                payload["daily"] = self.daily_history
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(encoded) > self.max_storage_bytes:
                raise ValueError("opslaglimiet te klein voor minimale historie")
            self.storage_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.storage_file.with_suffix(self.storage_file.suffix + ".tmp")
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.storage_file)
        except Exception as err:
            self._rate_log("save_error", f"PhaseGuard opslagfout: {err}", 900, "ERROR")

    def _prune_history(self, now: datetime) -> None:
        day_cutoff = now.date() - timedelta(days=self.daily_retention)
        hour_cutoff = now - timedelta(hours=self.hourly_retention)
        self.daily_history = [item for item in self.daily_history if (self._date(item.get("date")) or date.min) >= day_cutoff]
        kept_hours = []
        for item in self.hourly_history:
            stamp = self._timestamp(item.get("hour"))
            if stamp and stamp.astimezone(now.tzinfo) >= hour_cutoff:
                kept_hours.append(item)
        self.hourly_history = kept_hours

    @staticmethod
    def _histogram(values: Iterable[float], bin_width: float = 0.1) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for value in values:
            bin_number = int(round(float(value) / bin_width))
            key = str(bin_number)
            result[key] = result.get(key, 0) + 1
        return result

    # ------------------------------- MQTT ---------------------------------

    def _publish_discovery(self) -> None:
        if not self.mqtt_enabled:
            return
        device = {
            "identifiers": [self.device_id], "name": self.device_name,
            "manufacturer": "Custom AppDaemon", "model": "3x25A Phase Guard", "sw_version": self.VERSION,
        }
        sensors = self._sensor_definitions()
        for key, definition in sensors.items():
            payload: Dict[str, Any] = {
                "name": definition["name"], "unique_id": f"{self.device_id}_{key}",
                "default_entity_id": f"sensor.phase_guard_{key}",
                "state_topic": f"{self.mqtt_base}/state", "value_template": self._value_template(key, "unknown"),
                "availability_topic": f"{self.mqtt_base}/availability",
                "payload_available": "online", "payload_not_available": "offline", "device": device,
            }
            for field in ("unit_of_measurement", "device_class", "state_class", "icon", "entity_category"):
                if definition.get(field) is not None:
                    payload[field] = definition[field]
            self._mqtt_publish(f"{self.discovery_prefix}/sensor/{self.device_id}/{key}/config", json.dumps(payload), True)
        binaries = {
            "warning": ("Fasewaarschuwing", "mdi:alert"),
            "critical": ("Fase kritisch", "mdi:alert-octagon"),
            "overload": ("Fase overbelasting", "mdi:flash-alert"),
            "data_valid": ("Meetdata geldig", "mdi:database-check"),
            "load_shedding_enabled": ("Load shedding actief", "mdi:power-plug-off"),
        }
        for key, (name, icon) in binaries.items():
            payload = {
                "name": name, "unique_id": f"{self.device_id}_{key}",
                "default_entity_id": f"binary_sensor.phase_guard_{key}",
                "state_topic": f"{self.mqtt_base}/state", "value_template": self._value_template(key, "OFF"),
                "payload_on": "ON", "payload_off": "OFF", "availability_topic": f"{self.mqtt_base}/availability",
                "payload_available": "online", "payload_not_available": "offline", "device": device, "icon": icon,
            }
            self._mqtt_publish(f"{self.discovery_prefix}/binary_sensor/{self.device_id}/{key}/config", json.dumps(payload), True)

    def _sensor_definitions(self) -> Dict[str, Dict[str, str]]:
        result: Dict[str, Dict[str, str]] = {}
        for phase in PHASES:
            key = phase.lower()
            for suffix, label in (
                ("current", "Stroom"), ("current_smoothed", "Stroom EMA"),
                ("average_1m", "Gemiddelde 1 minuut"), ("average_5m", "Gemiddelde 5 minuten"),
                ("average_15m", "Gemiddelde 15 minuten"), ("maximum", "Maximum vandaag"),
                ("headroom", "Resterende stroomruimte"), ("predicted_30s", "Voorspeld 30 seconden"),
                ("predicted_60s", "Voorspeld 60 seconden"), ("predicted_120s", "Voorspeld 120 seconden"),
            ):
                result[f"{key}_{suffix}"] = {"name": f"{phase} {label}", "unit_of_measurement": "A", "device_class": "current", "state_class": "measurement"}
            result[f"{key}_voltage"] = {"name": f"{phase} spanning", "unit_of_measurement": "V", "device_class": "voltage", "state_class": "measurement"}
            result[f"{key}_trend"] = {"name": f"{phase} trend", "unit_of_measurement": "A/min", "state_class": "measurement", "icon": "mdi:trending-up"}
            result[f"{key}_prediction_confidence"] = {"name": f"{phase} voorspellingszekerheid", "unit_of_measurement": "%", "state_class": "measurement", "icon": "mdi:chart-bell-curve"}
            result[f"{key}_fuse_stress"] = {"name": f"{phase} heuristische zekeringbelasting", "unit_of_measurement": "%", "state_class": "measurement", "icon": "mdi:thermometer-alert"}
            result[f"{key}_level"] = {"name": f"{phase} belastingsniveau", "icon": "mdi:gauge"}
            result[f"{key}_direction"] = {"name": f"{phase} energierichting", "icon": "mdi:swap-horizontal"}
            result[f"{key}_current_source"] = {"name": f"{phase} stroombron", "entity_category": "diagnostic"}
            result[f"{key}_data_error"] = {"name": f"{phase} databronfout", "entity_category": "diagnostic"}
            for days in (7, 30):
                for metric, label in (("mean", "gemiddelde"), ("median", "mediaan"), ("p95", "P95"), ("max", "maximum")):
                    result[f"{key}_{metric}_{days}d"] = {"name": f"{phase} {label} {days} dagen", "unit_of_measurement": "A", "device_class": "current", "state_class": "measurement"}
                for threshold in self.thresholds:
                    result[f"{key}_seconds_above_{threshold}_{days}d"] = {"name": f"{phase} boven {threshold} in {days} dagen", "unit_of_measurement": "s", "device_class": "duration", "state_class": "measurement"}
            for threshold in self.thresholds:
                result[f"{key}_{threshold}_duration"] = {"name": f"{phase} {threshold} aaneengesloten", "unit_of_measurement": "s", "device_class": "duration", "state_class": "measurement"}
                result[f"{key}_{threshold}_today"] = {"name": f"{phase} {threshold} vandaag", "unit_of_measurement": "s", "device_class": "duration", "state_class": "total_increasing"}
        globals_ = {
            "status": ("Status", None, "mdi:shield-home"), "reason": ("Reden", None, "mdi:text-box-outline"),
            "current_source": ("Stroombron", None, "mdi:source-branch"),
            "peak_phase_current": ("Hoogste fasestroom", "A", "mdi:current-ac"),
            "total_current": ("Som fasestromen", "A", "mdi:sigma"), "lowest_headroom": ("Laagste stroomruimte", "A", "mdi:gauge-low"),
            "most_loaded_phase": ("Zwaarst belaste fase", None, "mdi:numeric"), "least_loaded_phase": ("Lichtst belaste fase", None, "mdi:numeric"),
            "phase_imbalance_amp": ("Fase-onbalans", "A", "mdi:scale-unbalanced"),
            "phase_imbalance_percent": ("Fase-onbalans percentage", "%", "mdi:scale-unbalanced"),
            "structural_imbalance": ("Structurele fase-onbalans", None, "mdi:chart-timeline-variant"),
            "structural_imbalance_amp": ("Structurele fase-onbalans verschil", "A", "mdi:chart-timeline-variant"),
            "historically_most_loaded_phase": ("Historisch zwaarste fase", None, "mdi:history"),
            "historically_least_loaded_phase": ("Historisch lichtste fase", None, "mdi:history"),
            "recommended_action": ("Aanbevolen actie", None, "mdi:lightbulb-alert"),
            "samples": ("Aantal samples", None, "mdi:counter"), "last_update": ("Laatste update", None, "mdi:clock-check"),
        }
        for key, (name, unit, icon) in globals_.items():
            definition = {"name": name, "icon": icon}
            if unit:
                definition.update({"unit_of_measurement": unit, "state_class": "measurement"})
                if unit == "A":
                    definition["device_class"] = "current"
            result[key] = definition
        result["last_update"]["device_class"] = "timestamp"
        return result

    def _publish_state(self, state: Mapping[str, Any]) -> None:
        if self.mqtt_enabled:
            payload = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            self._mqtt_publish(f"{self.mqtt_base}/state", payload, True)
            if not self.mqtt_announced_online:
                self._mqtt_publish(f"{self.mqtt_base}/availability", "online", True)
                self.mqtt_announced_online = True

    @staticmethod
    def _value_template(key: str, fallback: str) -> str:
        """Render safely when an MQTT state message is empty or not JSON."""
        return (
            "{% if value_json is defined and '" + key + "' in value_json %}"
            "{{ value_json." + key + " }}"
            "{% else %}" + fallback + "{% endif %}"
        )

    def _mqtt_publish(self, topic: str, payload: str, retain: bool = False) -> None:
        if self.mqtt_enabled:
            self.call_service("mqtt/publish", topic=topic, payload=payload, qos=0, retain=retain)

    # ------------------------------ helpers -------------------------------

    def _log_transitions(self, status: str) -> None:
        if status != self.last_status:
            self.log("PhaseGuard status %s -> %s: %s", self.last_status, status, self.latest_state.get("reason", ""))
            self.last_status = status
        for phase in PHASES:
            level = str(self.latest_state.get(f"{phase.lower()}_level", "DATA_ERROR"))
            if level != self.last_phase_levels[phase]:
                current = self.latest_state.get(f"{phase.lower()}_current")
                self.log("PhaseGuard: %s status %s%s", phase, level, f" ({current} A)" if current is not None else "")
                self.last_phase_levels[phase] = level

    def _source_event(self, phase: str, valid: bool, error: str) -> None:
        if self.last_source_valid[phase] != valid:
            if valid:
                self.log("PhaseGuard: bron %s hersteld", phase)
            else:
                self.log("PhaseGuard: bron %s ongeldig: %s", phase, error, level="WARNING")
            self.last_source_valid[phase] = valid

    def _rate_log(self, key: str, message: str, interval: int, level: str = "WARNING") -> None:
        now = datetime.now().timestamp()
        if now - self.last_log.get(key, 0.0) >= interval:
            self.log(message, level=level)
            self.last_log[key] = now

    def _number(self, key: str, default: float, minimum: float, maximum: float) -> float:
        value = finite_float(self.args.get(key, default))
        if value is None:
            value = default
        return max(minimum, min(maximum, value))

    def _integer(self, key: str, default: int, minimum: int, maximum: int) -> int:
        return int(self._number(key, float(default), float(minimum), float(maximum)))

    def _optional_number(self, key: str) -> Optional[float]:
        value = finite_float(self.args.get(key))
        if value is None:
            return None
        return max(0.001, min(1000000.0, value))

    def _log_configuration(self) -> None:
        for phase in PHASES:
            cfg = self.entities[phase]
            configured = [f"{name}={entity}" for name, entity in cfg.items() if entity]
            if not any(cfg.get(name) for name in ("current", "apparent", "net", "import", "export")):
                self.log(
                    "PhaseGuard: %s heeft geen stroom- of vermogensbron; vul apps.yaml.example in",
                    phase, level="ERROR",
                )
            else:
                self.log("PhaseGuard configuratie %s: %s", phase, ", ".join(configured))

    @staticmethod
    def _boolean(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "ja"}

    def _entity_arg(self, key: str) -> Optional[str]:
        value = str(self.args.get(key, "")).strip()
        return value if value and "REPLACE_ME" not in value else None

    @staticmethod
    def _round(value: Any, digits: int) -> Optional[float]:
        parsed = finite_float(value)
        return round(parsed, digits) if parsed is not None else None

    @staticmethod
    def _timestamp(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            result = value
        elif value:
            try:
                result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)

    @staticmethod
    def _date(value: Any) -> Optional[date]:
        try:
            return date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
