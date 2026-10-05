"""Advisory device scheduling over PVGridForecast's corrected quarter slots.

No Home Assistant services or independent PV/price forecasts live here.
All power is W; positive grid power means import, negative means export.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time
import math
from statistics import median


def _clock(value):
    return time.fromisoformat(str(value))


def _ceil_slot(now, minutes):
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (now - midnight).total_seconds() / 60
    return midnight + timedelta(minutes=int(-(-elapsed // minutes)) * minutes)


HELPER_FIELDS = ("enabled", "duration_minutes", "estimated_average_power_w",
                 "estimated_peak_power_w", "earliest_start", "latest_finish",
                 "power_sensor_entity")


def profile_helper_entities(profiles):
    """Return configured Home Assistant helper IDs for change subscriptions."""
    return {profile[field + "_entity"] for profile in profiles.values()
            for field in HELPER_FIELDS if profile.get(field + "_entity")}


def resolve_device_profiles(profiles, get_state):
    """Apply optional HA helper values without mutating the YAML defaults."""
    resolved = {}
    for key, original in profiles.items():
        profile = dict(original)
        for field in HELPER_FIELDS:
            entity = profile.get(field + "_entity")
            if not entity:
                continue
            value = get_state(entity)
            if field == "enabled":
                # An unavailable demand helper must never create a daily plan.
                profile[field] = value == "on"
            elif field == "power_sensor_entity":
                # The text helper contains the ID of a live power sensor.
                profile[field] = value.strip() if isinstance(value, str) and value.strip().startswith("sensor.") else None
            elif field in ("earliest_start", "latest_finish"):
                try:
                    profile[field] = _clock(value).strftime("%H:%M")
                except (TypeError, ValueError):
                    profile["enabled"] = False
            else:
                try:
                    number = float(value)
                    if not math.isfinite(number) or number <= 0:
                        raise ValueError(value)
                    if field == "duration_minutes" and int(number) <= 0:
                        raise ValueError(value)
                    profile[field] = int(number) if field == "duration_minutes" else number
                except (TypeError, ValueError):
                    profile["enabled"] = False
        resolved[key] = profile
    return resolved


class SelfConsumptionPlanner:
    def __init__(self, config, memory):
        self.config = config
        self.memory = memory if isinstance(memory, dict) else {}
        self.memory.setdefault("load_samples", {})
        self.memory.setdefault("plans", {})
        # Reserved for later comparison of predicted and measured cycles.
        self.memory.setdefault("evaluations", [])
        self.memory.setdefault("device_power_cycles", {})
        self.memory.setdefault("device_power_active", {})

    def observe_device_power(self, now, key, watts):
        """Learn average and peak W from complete measured appliance cycles."""
        active = self.memory["device_power_active"]
        if watts is None or not math.isfinite(watts) or watts < 0 or watts > 30000:
            active.pop(key, None)
            return False
        sample = active.get(key)
        if sample is None:
            if watts >= 10:
                active[key] = {"start": now.isoformat(), "last": now.isoformat(),
                               "last_w": watts, "energy_wh": 0.0, "peak_w": watts,
                               "idle_since": None}
                return True
            return False
        try:
            last = datetime.fromisoformat(sample["last"])
            start = datetime.fromisoformat(sample["start"])
            gap = (now - last).total_seconds()
        except (KeyError, TypeError, ValueError):
            active.pop(key, None)
            return False
        if gap <= 0 or gap > 600 or (now - start).total_seconds() > 12 * 3600:
            active.pop(key, None)
            return False
        sample["energy_wh"] += (float(sample["last_w"]) + watts) / 2 * gap / 3600
        sample["last"] = now.isoformat()
        sample["last_w"] = watts
        sample["peak_w"] = max(float(sample["peak_w"]), watts)
        if watts >= 10:
            sample["idle_since"] = None
        elif sample["idle_since"] is None:
            sample["idle_since"] = now.isoformat()
        elif (now - datetime.fromisoformat(sample["idle_since"])).total_seconds() >= 1200:
            duration_h = (datetime.fromisoformat(sample["idle_since"]) - start).total_seconds() / 3600
            energy_wh = float(sample["energy_wh"])
            if duration_h >= 0.25 and energy_wh >= 50:
                cycles = self.memory["device_power_cycles"].setdefault(key, [])
                cycles.append({"average_w": round(energy_wh / duration_h),
                               "peak_w": round(sample["peak_w"])})
                del cycles[:-5]
            active.pop(key, None)
        return True

    def learned_power_profiles(self, profiles):
        """Use complete measured cycles, retaining manual values as fallback."""
        result = {}
        for key, original in profiles.items():
            profile = dict(original)
            if profile.get("power_sensor_entity"):
                cycles = self.memory["device_power_cycles"].get(key, [])
                if cycles:
                    profile["estimated_average_power_w"] = median(x["average_w"] for x in cycles)
                    profile["estimated_peak_power_w"] = max(x["peak_w"] for x in cycles)
            result[key] = profile
        return result

    def observe_load(self, now, pv_w, grid_w):
        """Keep bounded, robust quarter-hour household baseload observations."""
        if pv_w is None or grid_w is None:
            return False
        key = f"{'weekend' if now.weekday() >= 5 else 'weekday'}:{now.hour:02d}:{now.minute // 15}"
        samples = self.memory["load_samples"].setdefault(key, [])
        # Exclude clear appliance spikes; still retain a conservative floor.
        load = max(0, min(float(pv_w) + float(grid_w), 12000))
        samples.append(round(load))
        del samples[:-32]
        return True

    def _load(self, when, live_load):
        key = f"{'weekend' if when.weekday() >= 5 else 'weekday'}:{when.hour:02d}:{when.minute // 15}"
        values = self.memory["load_samples"].get(key, [])
        return median(values) if len(values) >= 3 else live_load

    @staticmethod
    def _device_power(profile, minute, span=15):
        points = sorted(profile.get("power_profile", []), key=lambda x: int(x["minute"]))
        if points:
            # Integrate steps inside a slot, including changes at minute 10.
            boundaries = [minute] + [int(p["minute"]) for p in points if minute < int(p["minute"]) < minute + span] + [minute + span]
            watt_minutes = 0.0
            for left, right in zip(boundaries, boundaries[1:]):
                active = next((p for p in reversed(points) if int(p["minute"]) <= left), points[0])
                watt_minutes += max(0.0, float(active["power_w"])) * (right - left)
            return watt_minutes / span
        return max(0.0, float(profile.get("estimated_average_power_w", profile.get("estimated_power_w", 0))))

    def _duration(self, profile):
        if profile.get("mode") == "flexible_load":
            return int(profile.get("min_runtime_minutes", 0))
        return int(profile.get("duration_minutes", 0))

    def _limits(self, profile, now, duration):
        horizon = now + timedelta(hours=float(self.config.get("planning_horizon_hours", 24)))
        start_clock = _clock(profile.get("earliest_start", "00:00"))
        for offset in range(3):
            day = now.date() + timedelta(days=offset)
            day_start = datetime.combine(day, start_clock, now.tzinfo)
            earliest = max(day_start, now)
            latest = horizon
            if profile.get("latest_start"):
                end_clock = _clock(profile["latest_start"])
                end_day = day + timedelta(days=int(end_clock < start_clock))
                latest = min(latest, datetime.combine(end_day, end_clock, now.tzinfo))
            if profile.get("latest_finish"):
                end_clock = _clock(profile["latest_finish"])
                end_day = day + timedelta(days=int(end_clock <= start_clock))
                latest = min(latest, datetime.combine(end_day, end_clock, now.tzinfo) - timedelta(minutes=duration))
            if earliest <= latest:
                return earliest, latest
        return horizon + timedelta(minutes=1), horizon

    def _evaluate(self, start, profile, timeline, reservations):
        interval = int(self.config.get("planning_interval_minutes", 15))
        duration = self._duration(profile)
        end = start + timedelta(minutes=duration)
        energy = pv_use = import_kwh = export_reduction = import_cost = opportunity_cost = 0.0
        peak = 0.0
        for minute in range(0, duration, interval):
            when = start + timedelta(minutes=minute)
            slot = next((x for x in timeline if x["start"] <= when < x["end"]), None)
            if slot is None or slot["end"] < min(end, when + timedelta(minutes=interval)):
                return None
            hours = min(interval, duration - minute) / 60.0
            power = self._device_power(profile, minute, min(interval, duration - minute))
            reserved = reservations.get(when, 0.0)
            surplus = max(0.0, slot["pv_w"] - slot["load_w"] - reserved)
            solar = min(power, surplus)
            imported = power - solar
            energy += power * hours / 1000
            pv_use += solar * hours / 1000
            import_kwh += imported * hours / 1000
            export_reduction += solar * hours / 1000
            import_cost += imported * slot["import_price"] * hours / 1000
            opportunity_cost += solar * slot["export_price"] * hours / 1000
            peak = max(peak, imported + max(0.0, float(profile.get("estimated_peak_power_w", power)) - power))
        if energy <= 0:
            return None
        cost = import_cost + opportunity_cost
        weights = self.config.get("planner_weights", {})
        # Each term is in 0..1 except the bounded signed price term.
        price_scale = max(0.01, float(self.config.get("score_price_scale_eur_kwh", 0.50)))
        peak_scale = max(1.0, float(self.config.get("peak_guard_reference_w", 5000)))
        score = (
            float(weights.get("self_consumption", 1)) * pv_use / energy
            + float(weights.get("export_reduction", .6)) * export_reduction / energy
            - float(weights.get("grid_import", .8)) * import_kwh / energy
            - float(weights.get("electricity_cost", .8)) * max(-1, min(1, cost / energy / price_scale))
            - float(weights.get("peak_load", .4)) * min(1, peak / peak_scale)
        )
        return {"start": start, "end": end, "score": round(score, 5),
                "energy_kwh": round(energy, 3), "pv_kwh": round(pv_use, 3),
                "grid_kwh": round(import_kwh, 3), "export_reduction_kwh": round(export_reduction, 3),
                "cost_eur": round(cost, 3), "grid_cost_eur": round(import_cost, 3),
                "foregone_export_eur": round(opportunity_cost, 3),
                "pv_share_pct": round(100 * pv_use / energy, 1),
                "peak_grid_import_w": round(peak)}

    def plan(self, now, source_slots, profiles, live_load_w, actual_pv_w, grid_w,
             curtailment_enabled, manual_blocked, forecast_mape=0.0):
        interval = int(self.config.get("planning_interval_minutes", 15))
        if interval <= 0 or 15 % interval or 60 % interval:
            raise ValueError("planning_interval_minutes moet 1, 3, 5 of 15 zijn")
        margin = max(0, min(100, float(self.config.get("pv_safety_margin_pct", 10)))) / 100
        nowcast_minutes = int(self.config.get("nowcast_minutes", 60))
        timeline = []
        for source in source_slots:
            if source["end"] <= now:
                continue
            pv_w = max(0.0, float(source["pv_power_kw"]) * 1000 * (1 - margin))
            if curtailment_enabled and not manual_blocked and source["should_curtail"]:
                pv_w = 0.0
            elif nowcast_minutes > 0 and actual_pv_w is not None and source["start"] < now + timedelta(minutes=nowcast_minutes):
                # Taper the actual/forecast difference to zero over the nowcast window.
                remaining = max(0, 1 - max(0, (source["start"] - now).total_seconds()) / (nowcast_minutes * 60))
                pv_w = max(0, pv_w + (actual_pv_w - pv_w) * remaining)
            price = source.get("price_eur_kwh")
            if price is None:
                continue
            export = source.get("effective_export_price_eur_kwh")
            timeline.append({"start": source["start"], "end": source["end"],
                             "pv_w": pv_w, "load_w": self._load(source["start"], live_load_w),
                             "import_price": float(price), "export_price": float(export or 0)})
        timeline.sort(key=lambda x: x["start"])
        result = {"status": "ready" if timeline else "insufficient_data", "devices": {}, "schedule": []}
        if not timeline:
            result["reason"] = "Geen gezamenlijke Solcast- en Zonneplan-tijdlijn beschikbaar"
            return result
        result["horizon_hours"] = round((timeline[-1]["end"] - now).total_seconds() / 3600, 1)
        result["expected_pv_w"] = round(timeline[0]["pv_w"])
        result["expected_house_load_w"] = round(timeline[0]["load_w"])
        result["expected_surplus_w"] = round(max(0, timeline[0]["pv_w"] - timeline[0]["load_w"]))
        sample_count = len(self.memory.get("load_samples", {}).get(
            f"{'weekend' if now.weekday() >= 5 else 'weekday'}:{now.hour:02d}:{now.minute // 15}", []))
        result["confidence"] = "high" if sample_count >= 8 and forecast_mape < 20 else "medium" if sample_count >= 3 and forecast_mape < 35 else "low"
        if sample_count < 3:
            result["status"] = "degraded"
            result["reason"] = "Basisbelasting gebruikt actuele meting; historie wordt opgebouwd"
        reservations = {}
        for key, profile in sorted(profiles.items(), key=lambda item: -int(item[1].get("priority", 0))):
            if not profile.get("enabled", False):
                continue
            if profile.get("mode") == "variable_power":
                result["devices"][key] = {"status": "unsupported", "reason": "Variabel vermogen volgt na V1"}
                continue
            duration = self._duration(profile)
            if duration <= 0:
                result["devices"][key] = {"status": "data_error", "reason": "Ongeldige looptijd"}
                continue
            earliest, latest = self._limits(profile, now, duration)
            candidates = []
            cursor = _ceil_slot(earliest, interval)
            while cursor <= latest:
                evaluated = self._evaluate(cursor, profile, timeline, reservations)
                if evaluated:
                    candidates.append(evaluated)
                cursor += timedelta(minutes=interval)
            if not candidates:
                result["devices"][key] = {"status": "insufficient_forecast", "reason": "Geen volledig cyclusvenster binnen forecast en deadline"}
                continue
            best = max(candidates, key=lambda x: (x["score"], -x["start"].timestamp()))
            previous = self.memory["plans"].get(key, {})
            old_start = previous.get("start")
            old = next((x for x in candidates if x["start"].isoformat() == old_start), None)
            threshold = float(self.config.get("reschedule_min_score_improvement_pct", 10)) / 100
            if old and best["score"] - old["score"] <= max(abs(old["score"]), .1) * threshold:
                best = old
            now_candidate = next((x for x in candidates if x["start"] == _ceil_slot(now, interval)), None)
            eligible = [x for x in candidates if x["score"] >= best["score"] - max(abs(best["score"]), .1) * threshold]
            best["window_start"] = eligible[0]["start"]
            best["window_end"] = eligible[-1]["start"]
            best["savings_vs_now_eur"] = round(now_candidate["cost_eur"] - best["cost_eur"], 3) if now_candidate else None
            best["extra_pv_vs_now_kwh"] = round(best["pv_kwh"] - now_candidate["pv_kwh"], 3) if now_candidate else None
            best["status"] = "best_time_now" if best["start"] <= now + timedelta(minutes=interval) else "scheduled"
            if best["start"] == candidates[-1]["start"]:
                best["status"] = "deadline_forced"
            best["reason"] = (f"Nu starten; verwacht {best['pv_share_pct']:.0f}% directe PV-dekking" if best["status"] == "best_time_now"
                              else f"Wacht tot {best['start']:%H:%M}; verwacht {best['pv_share_pct']:.0f}% directe PV-dekking")
            best["confidence"] = result["confidence"]
            result["devices"][key] = best
            result["schedule"].append({"device": key, "start": best["start"], "end": best["end"]})
            self.memory["plans"][key] = {"start": best["start"].isoformat(), "score": best["score"]}
            for minute in range(0, duration, interval):
                when = best["start"] + timedelta(minutes=minute)
                reservations[when] = reservations.get(when, 0) + self._device_power(profile, minute, min(interval, duration - minute))
        return result
