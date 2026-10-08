import importlib.util
import sys
import types
import unittest
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


# AppDaemon is not installed in the development runtime. Supply only the base
# class needed to import and unit-test the app's pure calculation methods.
hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
hassapi.Hass = object
sys.modules.setdefault("appdaemon", types.ModuleType("appdaemon"))
sys.modules.setdefault("appdaemon.plugins", types.ModuleType("appdaemon.plugins"))
sys.modules.setdefault("appdaemon.plugins.hass", types.ModuleType("appdaemon.plugins.hass"))
sys.modules["appdaemon.plugins.hass.hassapi"] = hassapi

PROJECT_ROOT = Path(__file__).parents[1]
MODULE_PATH = PROJECT_ROOT / "phase_peak_guard.py"
if not MODULE_PATH.exists():
    MODULE_PATH = PROJECT_ROOT / "apps" / "phase_peak_guard.py"
SPEC = importlib.util.spec_from_file_location("phase_peak_guard", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class PhasePeakGuardTests(unittest.TestCase):
    def make_app(self):
        app = module.PhasePeakGuard.__new__(module.PhasePeakGuard)
        app.thresholds = {"elevated": 17.5, "high": 20.0, "critical": 22.5, "limit": 25.0}
        app.max_plausible_current = 80.0
        app.outlier_jump = 15.0
        app.outlier_confirmations = 2
        app.outlier_tolerance = 3.0
        app.prediction_window = 120
        app.min_prediction_samples = 4
        app.prediction_max_change = 6.0
        app.current_multiplier = None
        app.voltage_multiplier = None
        app.active_power_multiplier = None
        app.apparent_power_multiplier = None
        app._unknown_units_logged = set()
        app.log = lambda *args, **kwargs: None
        app.direction_deadband_w = 25.0
        app.args = {}
        app.phase = {name: module.PhaseRuntime(samples=deque(maxlen=400)) for name in module.PHASES}
        return app

    def test_finite_float_rejects_ha_unknown_and_non_finite(self):
        self.assertIsNone(module.finite_float("unavailable"))
        self.assertIsNone(module.finite_float("NaN"))
        self.assertIsNone(module.finite_float(float("inf")))
        self.assertEqual(module.finite_float("12.5"), 12.5)

    def test_percentile_is_linearly_interpolated(self):
        self.assertEqual(module.percentile([1, 2, 3, 4, 5], 95), 4.8)

    def test_histogram_percentile_combines_compact_history(self):
        histogram = {"10": 90, "200": 10}
        self.assertEqual(module.histogram_percentile(histogram, 95), 20.0)

    def test_linear_trend_reports_slope_and_fit(self):
        slope, confidence = module.linear_trend([(0, 1), (10, 2), (20, 3), (30, 4)])
        self.assertAlmostEqual(slope, 0.1)
        self.assertAlmostEqual(confidence, 1.0)

    def test_threshold_boundaries(self):
        app = self.make_app()
        expected = [(0, "NORMAL"), (17.5, "ELEVATED"), (20, "HIGH"), (22.5, "CRITICAL"), (25, "OVERLOAD")]
        for current, level in expected:
            self.assertEqual(app._level(current), level)

    def test_single_large_jump_is_held_until_confirmed(self):
        app = self.make_app()
        self.assertEqual(app._filter_current("L1", 9.0), 9.0)
        self.assertEqual(app._filter_current("L1", 40.0), 9.0)
        self.assertEqual(app._filter_current("L1", 41.0), 41.0)

    def test_impossible_current_is_rejected(self):
        app = self.make_app()
        self.assertIsNone(app._filter_current("L1", 97.0))

    def test_prediction_is_clamped_after_step_change(self):
        app = self.make_app()
        runtime = app.phase["L1"]
        for index, value in enumerate((5.0, 5.0, 14.0, 14.0, 14.0)):
            runtime.samples.append((index * 30.0, value, value))
        runtime.ema = 14.0
        _slope, predicted, _confidence = app._prediction(runtime, 120.0)
        self.assertLessEqual(predicted[60], 20.0)
        self.assertLessEqual(predicted[120], 26.0)

    def test_direction_uses_normalized_watts(self):
        app = self.make_app()
        self.assertEqual(app._direction(None, 200.0, 0.0), "import")
        self.assertEqual(app._direction(None, 0.0, 200.0), "export")
        self.assertEqual(app._direction(None, 10.0, 0.0), "neutral")

    def test_measurement_units_are_detected_automatically(self):
        app = self.make_app()
        self.assertEqual(app._measurement_multiplier("current", "mA"), 0.001)
        self.assertEqual(app._measurement_multiplier("current", "A"), 1.0)
        self.assertEqual(app._measurement_multiplier("active_power", "kW"), 1000.0)
        self.assertEqual(app._measurement_multiplier("apparent_power", "kVA"), 1000.0)

    def test_explicit_multiplier_overrides_home_assistant_unit(self):
        app = self.make_app()
        app.active_power_multiplier = 1000.0
        self.assertEqual(app._measurement_multiplier("active_power", "W"), 1000.0)

    def test_full_sample_publishes_normal_state_without_control_service(self):
        with TemporaryDirectory() as directory:
            app = module.PhasePeakGuard()
            app.args = {
                "current_l1": "sensor.test_current_l1",
                "current_l2": "sensor.test_current_l2",
                "current_l3": "sensor.test_current_l3",
                "voltage_l1": "sensor.test_voltage_l1",
                "voltage_l2": "sensor.test_voltage_l2",
                "voltage_l3": "sensor.test_voltage_l3",
                "storage_file": str(Path(directory) / "phase.json"),
            }
            service_calls = []
            app.log = lambda *args, **kwargs: None
            app.run_every = lambda *args, **kwargs: None
            app.call_service = lambda service, **kwargs: service_calls.append((service, kwargs))

            currents = {"l1": 8.4, "l2": 12.1, "l3": 6.2}

            def get_state(entity, attribute=None):
                phase = entity.rsplit("_", 1)[-1]
                value = 230.0 if "voltage" in entity else currents[phase]
                # Reproduce HA behaviour from the live report: L2 can remain
                # unchanged for hours while sibling entities prove the DSMR
                # meter is still reporting.
                stamp = datetime.now(timezone.utc)
                if phase == "l2" and "current" in entity:
                    stamp -= timedelta(hours=4)
                unit = "V" if "voltage" in entity else "A"
                return {"state": str(value), "last_updated": stamp.isoformat(), "attributes": {"unit_of_measurement": unit}}

            app.get_state = get_state
            app.initialize()
            startup_calls = list(service_calls)
            app._sample({})

            self.assertEqual(app.latest_state["status"], "normal")
            self.assertEqual(app.latest_state["data_valid"], "ON")
            self.assertEqual(app.latest_state["most_loaded_phase"], "L2")
            self.assertAlmostEqual(app.latest_state["lowest_headroom"], 12.9)
            self.assertTrue(service_calls)
            self.assertEqual({service for service, _kwargs in service_calls}, {"mqtt/publish"})
            self.assertFalse(any(
                kwargs.get("topic") == "home/phase_guard/state" and kwargs.get("payload") == ""
                for _service, kwargs in startup_calls
            ))
            self.assertEqual(startup_calls[-1][1]["topic"], "home/phase_guard/availability")
            self.assertEqual(startup_calls[-1][1]["payload"], "offline")
            state_index = next(
                index for index, (_service, kwargs) in enumerate(service_calls)
                if kwargs.get("topic") == "home/phase_guard/state"
            )
            online_index = next(
                index for index, (_service, kwargs) in enumerate(service_calls)
                if kwargs.get("topic") == "home/phase_guard/availability" and kwargs.get("payload") == "online"
            )
            self.assertLess(state_index, online_index)

    def test_discovery_templates_tolerate_non_json_state(self):
        app = self.make_app()
        sensor_template = app._value_template("l1_mean_30d", "unknown")
        binary_template = app._value_template("overload", "OFF")

        self.assertIn("value_json is defined", sensor_template)
        self.assertIn("'l1_mean_30d' in value_json", sensor_template)
        self.assertTrue(sensor_template.endswith("unknown{% endif %}"))
        self.assertTrue(binary_template.endswith("OFF{% endif %}"))

    def test_shared_heartbeat_does_not_accept_when_every_source_is_stale(self):
        app = self.make_app()
        app.max_source_age = 30
        app.shared_source_freshness = True
        app.entities = {
            phase: {"current": f"sensor.current_{phase.lower()}", "voltage": None,
                    "apparent": None, "net": None, "import": None, "export": None}
            for phase in module.PHASES
        }
        old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        app.get_state = lambda entity, attribute=None: {"state": "0", "last_updated": old, "attributes": {}}
        app._state_cache = {}
        app._shared_source_is_fresh = app._detect_shared_source_freshness(datetime.now().astimezone())
        value, error = app._read_entity("sensor.current_l2", datetime.now().astimezone())
        self.assertFalse(app._shared_source_is_fresh)
        self.assertIsNone(value)
        self.assertIn("oud", error)

    def test_persistent_file_is_pruned_below_configured_limit(self):
        with TemporaryDirectory() as directory:
            app = self.make_app()
            now = datetime.now().astimezone()
            app.storage_file = Path(directory) / "phase.json"
            app.max_storage_bytes = 50_000
            app.daily_retention = 35
            app.hourly_retention = 168
            app.current_day = now.date()
            app.current_hour = now.replace(minute=0, second=0, microsecond=0)
            app.last_log = {}
            app.daily_history = []
            for day in range(35):
                phase_data = {
                    phase: {
                        "samples": 1000,
                        "max": 25.0,
                        "mean": 5.0,
                        "median": 4.0,
                        "p95": 12.0,
                        "histogram_0_1a": {str(index): 10 for index in range(200)},
                    }
                    for phase in module.PHASES
                }
                app.daily_history.append({
                    "date": (now.date() - timedelta(days=day)).isoformat(),
                    "phases": phase_data,
                })
            app.hourly_history = [
                {
                    "hour": (now - timedelta(hours=hour)).isoformat(),
                    "phases": {phase: {"samples": 10, "mean": 5, "max": 8, "p95": 7} for phase in module.PHASES},
                }
                for hour in range(168)
            ]

            app._save_data()

            self.assertTrue(app.storage_file.exists())
            self.assertLessEqual(app.storage_file.stat().st_size, app.max_storage_bytes)
            self.assertTrue(len(app.hourly_history) < 168 or len(app.daily_history) < 35)


if __name__ == "__main__":
    unittest.main()
