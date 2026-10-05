import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
from pv_self_consumption import SelfConsumptionPlanner, resolve_device_profiles


class PowerLearningTests(unittest.TestCase):
    def test_completed_cycle_replaces_fallback_not_idle_zero(self):
        planner = SelfConsumptionPlanner({}, {})
        now = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
        profile = {"dishwasher": {"estimated_average_power_w": 440,
                                  "estimated_peak_power_w": 2100,
                                  "power_sensor_entity": "sensor.dishwasher_power"}}
        self.assertEqual(planner.learned_power_profiles(profile)["dishwasher"]["estimated_average_power_w"], 440)
        for minute in range(61):
            planner.observe_device_power(now + timedelta(minutes=minute), "dishwasher", 500)
        for minute in range(61, 82):
            planner.observe_device_power(now + timedelta(minutes=minute), "dishwasher", 0)
        learned = planner.learned_power_profiles(profile)["dishwasher"]
        self.assertGreater(learned["estimated_average_power_w"], 450)
        self.assertEqual(learned["estimated_peak_power_w"], 500)

    def test_missing_sensor_and_bad_helper_do_not_override_manual_power(self):
        profiles = {"dryer": {"enabled": True, "estimated_average_power_w": 800,
                              "power_sensor_entity_entity": "input_text.dryer_sensor"}}
        resolved = resolve_device_profiles(profiles, lambda entity: "unknown")
        self.assertTrue(resolved["dryer"]["enabled"])
        self.assertIsNone(resolved["dryer"]["power_sensor_entity"])
        planner = SelfConsumptionPlanner({}, {})
        self.assertEqual(planner.learned_power_profiles(resolved)["dryer"]["estimated_average_power_w"], 800)


if __name__ == "__main__":
    unittest.main()
