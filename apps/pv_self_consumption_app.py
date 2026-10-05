"""Standalone AppDaemon adapter for the advisory self-consumption planner.

Install alongside pv_self_consumption.py. It never controls a switch or appliance.
Do not enable this adapter and PVGridForecast's integrated planner together: they
publish the same MQTT Discovery identities.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta

import appdaemon.plugins.hass.hassapi as hass

from pv_self_consumption import SelfConsumptionPlanner, profile_helper_entities, resolve_device_profiles


class PVSelfConsumption(hass.Hass):
    VERSION = "1.0.0"
    DEVICE_FIELDS = {
        "best_start": "start", "best_end": "end", "window_start": "window_start",
        "window_end": "window_end", "score": "score", "expected_energy": "energy_kwh",
        "expected_pv_energy": "pv_kwh", "expected_grid_energy": "grid_kwh",
        "pv_share": "pv_share_pct", "expected_cost": "cost_eur",
        "grid_cost": "grid_cost_eur", "foregone_export": "foregone_export_eur",
        "savings": "savings_vs_now_eur", "extra_pv_vs_now": "extra_pv_vs_now_kwh",
        "confidence": "confidence", "status": "status", "reason": "reason",
    }

    def initialize(self):
        self.price_entity = self.args.get("price_quarter_entity", "sensor.zonneplan_current_quarter_hourly_electricity_tariff")
        self.solcast_entities = (
            self.args.get("solcast_today_entity", "sensor.solcast_pv_forecast_forecast_today"),
            self.args.get("solcast_tomorrow_entity", "sensor.solcast_pv_forecast_forecast_tomorrow"),
        )
        self.pv_entity = self.args.get("ecu_power_entity", "sensor.ecu_216000071532_current_power")
        self.import_entity = self.args.get("grid_import_entity", "sensor.power_consumption")
        self.export_entity = self.args.get("grid_export_entity", "sensor.power_production")
        self.threshold_entity = self.args.get("threshold_entity", "input_number.pv_negatieve_prijs_drempel")
        self.automatic_entity = self.args.get("automatic_entity", "input_boolean.pv_automatisch_afschakelen")
        self.manual_entity = self.args.get("manual_block_entity", "input_boolean.pv_handmatig_geblokkeerd")
        self.storage_file = str(self.args.get("storage_file", "/homeassistant/python_storage/pv_self_consumption_data.json"))
        self.base_topic = str(self.args.get("planner_mqtt_base_topic", "pv/self_consumption_planner")).strip("/")
        self.discovery_prefix = str(self.args.get("mqtt_discovery_prefix", "homeassistant")).strip("/")
        self.device_id = str(self.args.get("planner_mqtt_device_id", "pv_self_consumption_planner"))
        self.profiles = self.args.get("planner_devices", {}) or {}
        self.profile_helper_entities = profile_helper_entities(self.profiles)
        self.planner = SelfConsumptionPlanner(self.args, self._load_memory())
        self._last_sample_slot = None
        self._recalc_handle = None
        self._publish_discovery()
        for entity in (self.price_entity, *self.solcast_entities, self.pv_entity,
                       self.import_entity, self.export_entity, self.threshold_entity,
                       self.automatic_entity, self.manual_entity, *self.profile_helper_entities):
            if entity:
                self.listen_state(self._changed, entity, attribute="all")
        self.run_every(self._update, datetime.now().astimezone() + timedelta(seconds=10),
                       max(1, int(self.args.get("recalculate_interval_minutes", 15))) * 60)
        self.run_in(self._update, 5)
        self.run_every(self._sample_device_power,
                       datetime.now().astimezone() + timedelta(seconds=20), 60)

    def _sample_device_power(self, kwargs):
        profiles = resolve_device_profiles(self.profiles, self.get_state)
        changed = False
        now = datetime.now().astimezone()
        for key, profile in profiles.items():
            entity = profile.get("power_sensor_entity")
            if not entity:
                continue
            watts = self._state_number(entity)
            state = self._attributes(entity)
            unit = state.get("attributes", {}).get("unit_of_measurement") if isinstance(state, dict) else None
            if unit == "kW" and watts is not None:
                watts *= 1000
            elif unit != "W":
                watts = None
            changed = self.planner.observe_device_power(now, key, watts) or changed
        if changed:
            self._save_memory()

    @staticmethod
    def _datetime(value):
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str):
            try:
                result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        return result.astimezone() if result.tzinfo else result.replace(tzinfo=datetime.now().astimezone().tzinfo)

    @staticmethod
    def _number(value):
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    def _state_number(self, entity):
        return self._number(self.get_state(entity))

    def _attributes(self, entity):
        return self.get_state(entity, attribute="all") or {}

    def _slots(self, now):
        horizon = now + timedelta(hours=max(1, min(48, int(self.args.get("planning_horizon_hours", 24)))))
        solcast = {}
        for entity in self.solcast_entities:
            for item in self._attributes(entity).get("attributes", {}).get("detailedForecast", []) or []:
                start = self._datetime(item.get("period_start"))
                power = self._number(item.get("pv_estimate"))
                if start and power is not None:
                    solcast[start] = max(0.0, power)
        forecasts = sorted(solcast.items())
        threshold = self._state_number(self.threshold_entity)
        threshold = 0.0 if threshold is None else threshold
        bonus = self._number(self.args.get("zonnebonus_percent", 10))
        bonus = 0.0 if bonus is None else bonus
        slots = []
        for item in self._attributes(self.price_entity).get("attributes", {}).get("forecast", []) or []:
            start, end = self._datetime(item.get("start_date")), self._datetime(item.get("end_date"))
            if start is not None and end is None:
                end = start + timedelta(minutes=15)
            included = item.get("price_tax_included") or {}
            excluded = item.get("price_tax_excluded") or {}
            raw_price = self._number(included.get("amount") if isinstance(included, dict) else None)
            raw_export = self._number(excluded.get("amount") if isinstance(excluded, dict) else None)
            if start is None or raw_price is None or end is None or end <= now or start >= horizon:
                continue
            pv = next((power for when, power in reversed(forecasts)
                       if when <= start < when + timedelta(minutes=30)), None)
            if pv is None:
                continue
            price = raw_price / 10_000_000.0
            export = raw_export / 10_000_000.0 if raw_export is not None else 0.0
            if export > 0:
                export *= 1 + bonus / 100
            slots.append({"start": max(start, now), "end": min(end, horizon),
                          "pv_power_kw": pv, "price_eur_kwh": price,
                          "effective_export_price_eur_kwh": export,
                          "should_curtail": price <= threshold})
        return sorted(slots, key=lambda slot: slot["start"])

    def _changed(self, entity, attribute, old, new, **kwargs):
        if self._recalc_handle is None:
            self._recalc_handle = self.run_in(self._delayed_update, 3)

    def _delayed_update(self, kwargs):
        self._recalc_handle = None
        self._update({})

    def _update(self, kwargs):
        now = datetime.now().astimezone()
        pv = self._state_number(self.pv_entity)
        imported = self._state_number(self.import_entity)
        exported = self._state_number(self.export_entity)
        if pv is None or imported is None or exported is None:
            self._publish("status", "insufficient_data")
            self._publish("reason", "Actuele PV- of P1-meting ontbreekt")
            self._clear_advice()
            return
        grid_w = (imported - exported) * 1000
        live_load = max(0.0, pv + grid_w)
        sample_slot = (now.date(), now.hour, now.minute // 15)
        if sample_slot != self._last_sample_slot:
            self.planner.observe_load(now, pv, grid_w)
            self._last_sample_slot = sample_slot
            self._save_memory()
        try:
            profiles = self.planner.learned_power_profiles(
                resolve_device_profiles(self.profiles, self.get_state))
            plan = self.planner.plan(
                now, self._slots(now), profiles, live_load, pv, grid_w,
                self.get_state(self.automatic_entity) == "on",
                self.get_state(self.manual_entity) == "on",
            )
        except Exception as exc:
            self.log("Zelfstandige PV-planner: %s", exc, level="WARNING")
            self._publish("status", "data_error")
            self._publish("reason", str(exc))
            self._clear_advice()
            return
        self._publish("status", plan["status"])
        self._publish("reason", plan.get("reason", "Planning bijgewerkt"))
        for key, result_key in (("expected_pv", "expected_pv_w"),
                                ("expected_house_load", "expected_house_load_w"),
                                ("expected_surplus", "expected_surplus_w"),
                                ("forecast_horizon", "horizon_hours"),
                                ("confidence", "confidence")):
            self._publish(key, plan.get(result_key))
        self._publish("last_update", now.isoformat(timespec="seconds"))
        schedule = [{"device": row["device"], "start": row["start"].isoformat(),
                     "end": row["end"].isoformat()} for row in plan["schedule"]]
        self._publish("schedule", len(schedule), {"timeline": schedule})
        if plan["status"] == "insufficient_data":
            self._clear_advice()
        for device, item in plan["devices"].items():
            for field, result_key in self.DEVICE_FIELDS.items():
                value = item.get(result_key)
                if isinstance(value, datetime):
                    value = value.isoformat()
                self._publish(f"{device}_{field}", value)
        self._clear_inactive(profiles)
        self._save_memory()

    def _clear_inactive(self, profiles):
        for device, profile in profiles.items():
            if profile.get("enabled", False):
                continue
            for field in self.DEVICE_FIELDS:
                self._publish(f"{device}_{field}", None)
            self._publish(f"{device}_status", "disabled")
            self._publish(f"{device}_reason", "Geen vraag of helperwaarde ongeldig")

    def _clear_advice(self):
        for field in ("expected_pv", "expected_house_load", "expected_surplus", "forecast_horizon", "confidence"):
            self._publish(field, None)
        self._publish("schedule", "unknown", {"timeline": []})
        for device, profile in self.profiles.items():
            if profile.get("enabled", False) or profile.get("enabled_entity"):
                for field in self.DEVICE_FIELDS:
                    self._publish(f"{device}_{field}", None)
                self._publish(f"{device}_status", "insufficient_data")
                self._publish(f"{device}_reason", "Brondata ontbreekt")

    def _publish_discovery(self):
        device = {"identifiers": [self.device_id], "name": self.args.get("planner_mqtt_device_name", "PV Self-Consumption Planner"),
                  "manufacturer": "dkwolf1 / AppDaemon", "model": "PV planner", "sw_version": self.VERSION}
        fields = ["status", "reason", "expected_pv", "expected_house_load", "expected_surplus",
                  "forecast_horizon", "confidence", "last_update", "schedule"]
        for key, profile in self.profiles.items():
            if profile.get("enabled", False) or profile.get("enabled_entity"):
                fields += [f"{key}_{field}" for field in self.DEVICE_FIELDS]
        for key in fields:
            payload = {"name": key.replace("_", " ").title(), "unique_id": f"{self.device_id}_{key}",
                       "default_entity_id": f"sensor.pv_planner_{key}",
                       "state_topic": f"{self.base_topic}/{key}/state",
                       "json_attributes_topic": f"{self.base_topic}/{key}/attributes",
                       "availability_topic": f"{self.base_topic}/availability",
                       "payload_available": "online", "payload_not_available": "offline", "device": device}
            units = {"expected_pv": "W", "expected_house_load": "W", "expected_surplus": "W",
                     "forecast_horizon": "h", "expected_energy": "kWh", "expected_pv_energy": "kWh",
                     "expected_grid_energy": "kWh", "pv_share": "%", "expected_cost": "EUR",
                     "grid_cost": "EUR", "foregone_export": "EUR", "savings": "EUR",
                     "extra_pv_vs_now": "kWh"}
            unit = next((unit for suffix, unit in units.items() if key == suffix or key.endswith("_" + suffix)), None)
            if unit:
                payload["unit_of_measurement"] = unit
            if key.endswith(("_start", "_end")) or key == "last_update":
                payload["device_class"] = "timestamp"
            self._mqtt(f"{self.discovery_prefix}/sensor/{self.device_id}_{key}/config", json.dumps(payload))
        self._mqtt(f"{self.base_topic}/availability", "online")

    def _publish(self, key, value, attributes=None):
        self._mqtt(f"{self.base_topic}/{key}/state", "unknown" if value is None else str(value))
        self._mqtt(f"{self.base_topic}/{key}/attributes", json.dumps(attributes or {}, ensure_ascii=False))

    def _mqtt(self, topic, payload):
        self.call_service("mqtt/publish", topic=topic, payload=payload, qos=0, retain=True)

    def _load_memory(self):
        try:
            with open(self.storage_file, encoding="utf-8") as source:
                data = json.load(source)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_memory(self):
        try:
            os.makedirs(os.path.dirname(self.storage_file), exist_ok=True)
            temporary = self.storage_file + ".tmp"
            with open(temporary, "w", encoding="utf-8") as output:
                json.dump(self.planner.memory, output, ensure_ascii=False)
            os.replace(temporary, self.storage_file)
        except OSError as exc:
            self.log("Planneropslag mislukt: %s", exc, level="WARNING")

    def terminate(self):
        self._mqtt(f"{self.base_topic}/availability", "offline")
