"""PVGridForecast - AppDaemon app for Home Assistant."""
from __future__ import annotations

import json
import hashlib
import math
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import appdaemon.plugins.hass.hassapi as hass


class PVGridForecast(hass.Hass):
    VERSION = "1.2.5"

    def initialize(self) -> None:
        self.log("PVGridForecast v%s start", self.VERSION)

        self.price_quarter_entity = self.args.get("price_quarter_entity", "sensor.zonneplan_current_quarter_hourly_electricity_tariff")
        self.price_hour_entity = self.args.get("price_hour_entity", "sensor.zonneplan_current_electricity_tariff")
        self.entsoe_entity = self.args.get("entsoe_entity", "sensor.average_electricity_price")

        self.solcast_today_entity = self.args.get("solcast_today_entity", "sensor.solcast_pv_forecast_forecast_today")
        self.solcast_tomorrow_entity = self.args.get("solcast_tomorrow_entity", "sensor.solcast_pv_forecast_forecast_tomorrow")
        self.solcast_remaining_entity = self.args.get("solcast_remaining_entity", "sensor.solcast_pv_forecast_forecast_remaining_today")
        self.solcast_next_hour_entity = self.args.get("solcast_next_hour_entity", "sensor.solcast_pv_forecast_forecast_next_hour")
        self.solcast_power_now_entity = self.args.get("solcast_power_now_entity", "sensor.solcast_pv_forecast_power_now")

        self.ecu_power_entity = self.args.get("ecu_power_entity", "sensor.ecu_216000071532_current_power")
        self.ecu_daily_entity = self.args.get("ecu_daily_entity", "sensor.ecu_216000071532_daily_energy_production")
        self.ecu_daily_max_entity = self.args.get("ecu_daily_max_entity", "sensor.ecu_216000071532_daily_max_power")

        self.grid_import_entity = self.args.get("grid_import_entity", "sensor.power_consumption")
        self.grid_export_entity = self.args.get("grid_export_entity", "sensor.power_production")
        self.export_price_entity = self.args.get("export_price_entity", "sensor.zonneplan_terugleververgoeding")
        self.live_cost_entity = self.args.get("live_cost_entity", "sensor.live_stroom_saldo")
        self.zonneplan_returned_today_entity = self.args.get("zonneplan_returned_today_entity", "sensor.zonneplan_teruggeleverd_vandaag")
        self.zonneplan_consumption_today_entity = self.args.get("zonneplan_consumption_today_entity", "sensor.zonneplan_verbruik_vandaag")

        self.threshold_entity = self.args.get("threshold_entity", "input_number.pv_negatieve_prijs_drempel")
        self.pv_switch_entity = self.args.get("pv_switch_entity", "switch.shelly_pro_1")
        self.automatic_entity = self.args.get("automatic_entity", "input_boolean.pv_automatisch_afschakelen")
        self.manual_block_entity = self.args.get("manual_block_entity", "input_boolean.pv_handmatig_geblokkeerd")
        self.pv_switch_inverted = self._as_bool(self.args.get("pv_switch_inverted", True))

        self.peak_power_w = float(self.args.get("peak_power_w", 6320))
        self.panel_count = int(self.args.get("panel_count", 16))
        self.panel_power_w = float(self.args.get("panel_power_w", 395))
        self.azimuth = float(self.args.get("azimuth", 201.4))
        self.tilt = float(self.args.get("tilt", 50.0))
        self.inverter_count = int(self.args.get("inverter_count", 4))
        self.inverter_model = str(self.args.get("inverter_model", "APsystems QS1"))

        self.horizon_hours = max(1, min(48, int(self.args.get("horizon_hours", 24))))
        self.update_minutes = max(1, int(self.args.get("update_minutes", 5)))
        self.min_pv_energy_kwh = float(self.args.get("min_pv_energy_for_relevant_curtailment_kwh", 0.05))

        self.relevant_pv_power_w = float(self.args.get("relevant_pv_power_w", 50))
        self.best_consumption_window_minutes = max(
            15, int(self.args.get("best_consumption_window_minutes", 60))
        )
        self.daily_stats_retention_days = max(
            7, min(365, int(self.args.get("daily_stats_retention_days", 90)))
        )
        self.learning_stability_samples = max(
            2, int(self.args.get("learning_stability_samples", 3))
        )
        self.learning_stability_pct = max(
            1.0, float(self.args.get("learning_stability_pct", 15.0))
        )
        self.learning_stability_min_spacing_sec = max(
            30, int(self.args.get("learning_stability_min_spacing_sec", 120))
        )
        self.clipping_min_samples = max(
            12, int(self.args.get("clipping_min_samples", 50))
        )
        self.clipping_headroom = max(
            1.0, float(self.args.get("clipping_headroom", 1.03))
        )
        self.zonneplan_purchase_fee_eur_kwh = float(self.args.get("zonneplan_purchase_fee_eur_kwh", 0.02))
        self.zonnebonus_percent = float(self.args.get("zonnebonus_percent", 10.0))
        self.finance_history_retention_days = max(31, min(730, int(self.args.get("finance_history_retention_days", 400))))
        self.history_bootstrap_enabled = self._as_bool(
            self.args.get("history_bootstrap_enabled", True)
        )
        self.history_bootstrap_days = max(
            1,
            min(
                730,
                int(
                    self.args.get(
                        "history_bootstrap_days",
                        self.finance_history_retention_days,
                    )
                ),
            ),
        )

        self.learning_enabled = self._as_bool(self.args.get("learning_enabled", True))
        self.learning_alpha = float(self.args.get("learning_alpha", 0.03))
        self.learning_min_samples = max(1, int(self.args.get("learning_min_samples", 12)))
        self.learning_min_forecast_w = float(self.args.get("learning_min_forecast_w", 300))
        self.learning_min_actual_w = float(self.args.get("learning_min_actual_w", 100))
        self.learning_ratio_min = float(self.args.get("learning_ratio_min", 0.50))
        self.learning_ratio_max = float(self.args.get("learning_ratio_max", 1.50))
        self.correction_min = float(self.args.get("correction_min", 0.70))
        self.correction_max = float(self.args.get("correction_max", 1.20))

        self.storage_file = str(self.args.get("storage_file", "/homeassistant/python_storage/pv_grid_forecast_data.json"))
        self.max_storage_mb = max(0.1, float(self.args.get("max_storage_mb", 1)))
        self.learning = self._load_learning()
        self.planner_enabled = self._as_bool(self.args.get("planner_enabled", False))
        self.planner_config = self.args
        self.planner_devices = self.args.get("planner_devices", {}) or {}
        self.planner = None
        if self.planner_enabled:
            # Keep the forecast app independently deployable when planning is off.
            from pv_self_consumption import SelfConsumptionPlanner, profile_helper_entities, resolve_device_profiles
            self.planner = SelfConsumptionPlanner(self.planner_config, self.learning.get("planner", {}))
            self._profile_helper_entities = profile_helper_entities(self.planner_devices)
            self._resolve_device_profiles = resolve_device_profiles
        self._planner_last_sample_slot = None
        self._planner_last_run = None

        self._stability_buffer = []
        self._last_timeline_hash = None
        self._finance_last_time = None
        self._finance_last_pv_kwh = None
        self._finance_last_export_kwh = None
        self._history_bootstrap_in_progress = False

        self.discovery_prefix = str(self.args.get("mqtt_discovery_prefix", "homeassistant")).strip("/")
        self.mqtt_base = str(self.args.get("mqtt_base_topic", "pv_grid_forecast")).strip("/")
        self.device_id = str(self.args.get("mqtt_device_id", "pv_grid_forecast"))
        self.device_name = str(self.args.get("mqtt_device_name", "PV Grid Forecast"))

        self._recalc_handle = None
        self._publish_discovery()
        if self.planner:
            self._publish_planner_discovery()

        watched = [
            self.price_quarter_entity, self.price_hour_entity, self.entsoe_entity,
            self.solcast_today_entity, self.solcast_tomorrow_entity,
            self.solcast_remaining_entity, self.solcast_next_hour_entity,
            self.solcast_power_now_entity, self.ecu_power_entity,
            self.ecu_daily_entity, self.ecu_daily_max_entity,
            self.grid_import_entity, self.grid_export_entity, self.live_cost_entity,
            self.zonneplan_returned_today_entity, self.zonneplan_consumption_today_entity,
            self.threshold_entity, self.pv_switch_entity,
            self.automatic_entity, self.manual_block_entity,
        ]
        if self.planner:
            watched.extend(self._profile_helper_entities)
        for entity in watched:
            self.listen_state(self._source_changed, entity, attribute="all")

        first = datetime.now().astimezone() + timedelta(seconds=15)
        self.run_every(self._periodic_update, first, self.update_minutes * 60)
        self.run_in(self._initial_update, 5)
        if self.history_bootstrap_enabled:
            self.run_in(self._start_history_bootstrap, 20)
        if self.planner:
            self.run_every(self._sample_planner_device_power,
                           datetime.now().astimezone() + timedelta(seconds=20), 60)

    def _sample_planner_device_power(self, kwargs):
        profiles = self._resolve_device_profiles(self.planner_devices, self.get_state)
        changed = False
        now = datetime.now().astimezone()
        for key, profile in profiles.items():
            entity = profile.get("power_sensor_entity")
            if not entity:
                continue
            watts = self._float_state(entity, None)
            state = self.get_state(entity, attribute="all") or {}
            unit = state.get("attributes", {}).get("unit_of_measurement") if isinstance(state, dict) else None
            if unit == "kW" and watts is not None:
                watts *= 1000
            elif unit != "W":
                watts = None
            changed = self.planner.observe_device_power(now, key, watts) or changed
        if changed:
            self._save_learning()

    def _initial_update(self, kwargs: Dict[str, Any]) -> None:
        self._calculate_and_publish()

    def _periodic_update(self, kwargs: Dict[str, Any]) -> None:
        self._calculate_and_publish()

    def _source_changed(self, entity: str, attribute: str, old: Any, new: Any, **kwargs: Any) -> None:
        if self.planner and entity in self._profile_helper_entities:
            self._planner_last_run = None
        elif self.planner and entity in {
            self.price_quarter_entity, self.solcast_today_entity,
            self.solcast_tomorrow_entity, self.automatic_entity,
            self.manual_block_entity, self.pv_switch_entity,
        }:
            self._planner_last_run = None
        elif self.planner and entity in {self.ecu_power_entity, self.grid_import_entity, self.grid_export_entity}:
            try:
                old_value = old.get("state") if isinstance(old, dict) else old
                new_value = new.get("state") if isinstance(new, dict) else new
                multiplier = 1 if entity == self.ecu_power_entity else 1000
                if abs((float(new_value) - float(old_value)) * multiplier) >= float(self.args.get("planner_live_change_trigger_w", 500)):
                    self._planner_last_run = None
            except (TypeError, ValueError):
                pass
        try:
            if self._recalc_handle is not None:
                self.cancel_timer(self._recalc_handle, silent=True)
        except Exception:
            pass
        self._recalc_handle = self.run_in(self._debounced_update, 4)

    def _debounced_update(self, kwargs: Dict[str, Any]) -> None:
        self._recalc_handle = None
        self._calculate_and_publish()

    def _calculate_and_publish(self) -> None:
        try:
            now = datetime.now().astimezone()
            horizon_end = now + timedelta(hours=self.horizon_hours)

            threshold = self._float_state(self.threshold_entity, 0.0) or 0.0
            current_quarter_tariff = self._float_state(
                self.price_quarter_entity, None
            )
            current_zp_prices = self._current_zonneplan_prices(now)
            current_price_included = current_zp_prices.get("included")
            current_price_excluded = current_zp_prices.get("excluded")
            current_export_price = self._effective_export_price(current_price_excluded)
            entsoe_current = self._current_entsoe_price(now)

            quarter_prices = self._read_zonneplan_quarters(now, horizon_end)
            solcast = self._read_solcast_forecast(now, horizon_end)

            # Learn observed AC ceiling independently from forecast learning.
            actual_pv_w = self._float_state(self.ecu_power_entity, 0.0) or 0.0
            daily_max_w = self._float_state(self.ecu_daily_max_entity, 0.0) or 0.0
            self._update_observed_ceiling(max(actual_pv_w, daily_max_w))

            if self.learning_enabled:
                self._update_learning(now)

            current_correction = self._correction_factor_for_time(now)
            global_mape = self._learning_metric("mape_ewma_pct")
            global_bias = self._learning_metric("bias_ewma_pct")
            global_mae = self._learning_metric("mae_ewma_w")
            global_rmse = math.sqrt(
                max(0.0, self._learning_metric("mse_ewma_w2"))
            )
            learning_samples = int(self.learning.get("samples", 0) or 0)

            corrected_solcast = []
            for item in solcast:
                factor = self._correction_factor_for_time(item["start"])
                ceiling_kw = self._forecast_ceiling_kw()

                p50 = max(0.0, item["pv_estimate_kw"] * factor)
                p10 = max(0.0, item["pv_estimate10_kw"] * factor)
                p90 = max(0.0, item["pv_estimate90_kw"] * factor)

                if ceiling_kw is not None:
                    p50 = min(p50, ceiling_kw)
                    p10 = min(p10, ceiling_kw)
                    p90 = min(p90, ceiling_kw)

                corrected_solcast.append({
                    **item,
                    "correction_factor": factor,
                    "pv_estimate_kw_corrected": p50,
                    "pv_estimate10_kw_corrected": p10,
                    "pv_estimate90_kw_corrected": p90,
                })

            slots = self._build_quarter_slots(
                quarter_prices, corrected_solcast, threshold
            )
            if self.planner:
                self._update_planner(
                    now, slots, corrected_solcast,
                    global_mape if learning_samples >= self.learning_min_samples else 100.0,
                )

            # Price-only versus PV-relevant curtailment are deliberately separate.
            price_negative_slots = [s for s in slots if s["should_curtail"]]
            relevant_slots = [s for s in slots if s["relevant_curtail"]]

            windows = self._find_curtailment_windows(slots)
            relevant_windows = [
                w for w in windows
                if w["pv_energy_kwh"] >= self.min_pv_energy_kwh
            ]
            next_window = relevant_windows[0] if relevant_windows else None

            negative_quarters = len(price_negative_slots)
            relevant_quarters = len(relevant_slots)

            missed_p50 = sum(s["pv_energy_kwh"] for s in relevant_slots)
            missed_p10 = sum(s["pv_energy10_kwh"] for s in relevant_slots)
            missed_p90 = sum(s["pv_energy90_kwh"] for s in relevant_slots)

            def forecast_curtailment_value(slot, energy_key):
                ep = slot.get("effective_export_price_eur_kwh")
                return slot[energy_key] * (-ep) if ep is not None and ep < 0 else 0.0

            financial_p50 = sum(forecast_curtailment_value(s, "pv_energy_kwh") for s in relevant_slots)
            financial_p10 = sum(forecast_curtailment_value(s, "pv_energy10_kwh") for s in relevant_slots)
            financial_p90 = sum(forecast_curtailment_value(s, "pv_energy90_kwh") for s in relevant_slots)

            grid_import_kw = self._float_state(
                self.grid_import_entity, 0.0
            ) or 0.0
            grid_export_kw = self._float_state(
                self.grid_export_entity, 0.0
            ) or 0.0
            net_power_kw = grid_export_kw - grid_import_kw

            solcast_power_now_w = self._float_state(
                self.solcast_power_now_entity, 0.0
            ) or 0.0
            sustainability = self._current_sustainability_score(now)

            net_score = self._calculate_net_surplus_score(
                market_price=entsoe_current,
                forecast_power_w=solcast_power_now_w * current_correction,
                grid_export_kw=grid_export_kw,
                sustainability=sustainability,
            )
            net_status = self._net_status(net_score)

            favorable = bool(
                (
                    current_quarter_tariff is not None
                    and current_quarter_tariff <= threshold
                )
                or net_score >= 70
            )
            advice = self._consumption_advice(
                favorable,
                net_score,
                current_quarter_tariff,
                threshold,
                grid_export_kw,
            )

            best_window = self._best_consumption_window(slots)

            curtailment_expected = next_window is not None
            curtailment_within_hour = bool(
                next_window
                and 0 <= (next_window["start"] - now).total_seconds() <= 3600
            )

            forecast_today = self._float_state(
                self.solcast_today_entity, None
            )
            forecast_tomorrow = self._float_state(
                self.solcast_tomorrow_entity, None
            )
            forecast_remaining = self._float_state(
                self.solcast_remaining_entity, None
            )
            confidence_today = self._solcast_confidence(
                self.solcast_today_entity
            )
            confidence_tomorrow = self._solcast_confidence(
                self.solcast_tomorrow_entity
            )

            automatic_enabled = self._is_on(self.automatic_entity)
            manual_blocked = self._is_on(self.manual_block_entity)
            pv_is_on = self._pv_is_on()

            # Bounded actual curtailment statistics.
            self._update_daily_stats(
                now=now,
                automatic_enabled=automatic_enabled,
                manual_blocked=manual_blocked,
                pv_is_on=pv_is_on,
                price=current_quarter_tariff,
                threshold=threshold,
                forecast_power_w=solcast_power_now_w * current_correction,
            )
            self._update_zonneplan_daily_history_today(now)
            year_totals = self._zonneplan_year_totals(now)
            today_stats = self._today_stats(now)

            finance_today = self._update_daily_finance(now, current_price_included, current_price_excluded, pv_is_on, automatic_enabled, manual_blocked, threshold, solcast_power_now_w * current_correction, grid_import_kw)

            data_quality, missing = self._data_quality(
                quarter_prices, solcast, current_quarter_tariff
            )
            status = self._status_text(
                data_quality,
                curtailment_expected,
                next_window,
                automatic_enabled,
                manual_blocked,
            )

            compact_windows = [{
                "start": self._iso(w["start"]),
                "end": self._iso(w["end"]),
                "duration_min": w["duration_min"],
                "quarters": w["quarters"],
                "min_price_eur_kwh": round(w["min_price"], 5),
                "pv_p10_kwh": round(w["pv_energy10_kwh"], 3),
                "pv_p50_kwh": round(w["pv_energy_kwh"], 3),
                "pv_p90_kwh": round(w["pv_energy90_kwh"], 3),
                "financial_p50_eur": round(
                    w["financial_effect_eur"], 3
                ),
            } for w in relevant_windows[:8]]

            timeline = self._compact_timeline(slots)

            status_attrs = {
                "versie": self.VERSION,
                "data_quality": data_quality,
                "ontbrekende_bronnen": missing,
                "horizon_uren": self.horizon_hours,
                "afschakeldrempel_eur_kwh": round(threshold, 5),
                "huidig_zonneplan_tarief_eur_kwh":
                    self._round_or_none(current_quarter_tariff, 5),
                "huidige_entsoe_marktprijs_eur_kwh":
                    self._round_or_none(entsoe_current, 5),
                "zonneplan_prijs_incl_belasting_eur_kwh": self._round_or_none(current_price_included, 5),
                "zonneplan_prijs_excl_belasting_eur_kwh": self._round_or_none(current_price_excluded, 5),
                "effectieve_terugleverprijs_eur_kwh": self._round_or_none(current_export_price, 5),
                "pv_automatisch": automatic_enabled,
                "pv_handmatig_geblokkeerd": manual_blocked,
                "pv_aan": pv_is_on,
                "inverted_switch": self.pv_switch_inverted,
                "pv_werkelijk_w": round(actual_pv_w, 1),
                "pv_solcast_nu_w": round(solcast_power_now_w, 1),
                "net_import_kw": round(grid_import_kw, 3),
                "net_export_kw": round(grid_export_kw, 3),
                "netto_vermogen_kw": round(net_power_kw, 3),
                "sustainability_score": sustainability,
                "learning_samples": learning_samples,
                "learning_minimum_samples": self.learning_min_samples,
                "learning_voldoende_data":
                    learning_samples >= self.learning_min_samples,
                "learning_dagdeel": self._daypart(now),
                "learning_global_factor_diagnostics_only": True,
                "learning_reset_reason": self.learning.get(
                    "learning_reset_reason"
                ),
                "learning_reset_at": self.learning.get(
                    "learning_reset_at"
                ),
                "forecast_correctiefactor":
                    round(current_correction, 4),
                "forecast_mape_pct":
                    round(global_mape, 2) if learning_samples else None,
                "forecast_bias_pct":
                    round(global_bias, 2) if learning_samples else None,
                "forecast_mae_w":
                    round(global_mae, 1) if learning_samples else None,
                "forecast_rmse_w":
                    round(global_rmse, 1) if learning_samples else None,
                "observed_ac_ceiling_w":
                    self.learning.get("observed_ac_ceiling_w"),
                "storage_file": self.storage_file,
                "max_storage_mb": self.max_storage_mb,
                "history_bootstrap": self.learning.get(
                    "history_bootstrap", {}
                ),
                "zonneplan_rollover": self.learning.get(
                    "zonneplan_rollover", {}
                ),
                "zonneplan_historie_dagen": len(
                    self.learning.get(
                        "zonneplan_daily_history", {}
                    )
                    if isinstance(
                        self.learning.get(
                            "zonneplan_daily_history", {}
                        ),
                        dict,
                    )
                    else {}
                ),
                "zonneplan_verbruik_jaar_kwh": round(
                    year_totals["consumption_kwh"], 3
                ),
                "zonneplan_teruggeleverd_jaar_kwh": round(
                    year_totals["returned_kwh"], 3
                ),
                "afschakelvensters_24h": compact_windows,
                "beste_verbruiksmoment": (
                    {
                        "start": self._iso(best_window["start"]),
                        "end": self._iso(best_window["end"]),
                        "score": round(best_window["score"], 1),
                    }
                    if best_window else None
                ),
                "installatie": {
                    "piekvermogen_wp": int(self.peak_power_w),
                    "panelen": self.panel_count,
                    "paneel_wp": int(self.panel_power_w),
                    "azimuth_deg": self.azimuth,
                    "tilt_deg": self.tilt,
                    "omvormers": self.inverter_count,
                    "omvormer_model": self.inverter_model,
                },
                "laatste_berekening": self._iso(now),
            }

            # Core
            self._publish_sensor("status", status, status_attrs)
            self._publish_sensor("netoverschot_score", round(net_score, 1))
            self._publish_sensor("netstatus", net_status)
            self._publish_sensor("verbruiksadvies", advice)
            self._publish_binary("stroom_verbruiken_gunstig", favorable)
            self._publish_sensor(
                "netto_vermogen",
                round(net_power_kw, 3),
                {"betekenis": "positief = teruglevering, negatief = netafname"},
            )
            self._publish_sensor("pv_effectieve_terugleverprijs", self._round_or_none(current_export_price, 5), {"basis_excl_belasting_eur_kwh": self._round_or_none(current_price_excluded, 5), "importprijs_incl_belasting_eur_kwh": self._round_or_none(current_price_included, 5), "zonnebonus_pct": self.zonnebonus_percent})

            # Curtailment
            self._publish_binary(
                "pv_afschakeling_verwacht", curtailment_expected
            )
            self._publish_binary(
                "pv_afschakeling_binnen_1_uur",
                curtailment_within_hour,
            )
            self._publish_sensor(
                "pv_volgende_afschakeling",
                self._iso(next_window["start"]) if next_window else "None",
            )
            self._publish_sensor(
                "pv_afschakeling_einde",
                self._iso(next_window["end"]) if next_window else "None",
            )
            self._publish_sensor(
                "pv_afschakeling_duur",
                next_window["duration_min"] if next_window else 0,
            )
            self._publish_sensor(
                "pv_negatieve_kwartieren_24h", negative_quarters
            )
            self._publish_sensor(
                "pv_relevante_afschakelkwartieren_24h",
                relevant_quarters,
            )

            # Solcast totals
            self._publish_sensor(
                "pv_forecast_vandaag",
                self._round_or_none(forecast_today, 3),
                {
                    "confidence": confidence_today,
                    "gecorrigeerde_schatting_kwh":
                        self._round_or_none(
                            forecast_today * current_correction
                            if forecast_today is not None else None, 3
                        ),
                },
            )
            self._publish_sensor(
                "pv_forecast_morgen",
                self._round_or_none(forecast_tomorrow, 3),
                {
                    "confidence": confidence_tomorrow,
                },
            )
            self._publish_sensor(
                "pv_forecast_resterend_vandaag",
                self._round_or_none(forecast_remaining, 3),
            )

            # P10/P50/P90 curtailment estimate
            self._publish_sensor(
                "pv_productie_tijdens_afschakeling",
                round(missed_p50, 3),
                {
                    "p10_kwh": round(missed_p10, 3),
                    "p50_kwh": round(missed_p50, 3),
                    "p90_kwh": round(missed_p90, 3),
                },
            )
            self._publish_sensor(
                "pv_gemiste_productie_verwacht",
                round(missed_p50, 3),
                {
                    "p10_kwh": round(missed_p10, 3),
                    "p50_kwh": round(missed_p50, 3),
                    "p90_kwh": round(missed_p90, 3),
                },
            )
            self._publish_sensor(
                "pv_gemiste_productie_p10", round(missed_p10, 3)
            )
            self._publish_sensor(
                "pv_gemiste_productie_p90", round(missed_p90, 3)
            )

            self._publish_sensor(
                "pv_financieel_effect_verwacht",
                round(financial_p50, 3),
                {
                    "p10_eur": round(financial_p10, 3),
                    "p50_eur": round(financial_p50, 3),
                    "p90_eur": round(financial_p90, 3),
                    "uitleg": (
                        "Positief = verwacht financieel voordeel van "
                        "afschakelen; contractspecifieke terugleverkosten "
                        "zijn nog niet apart gemodelleerd."
                    ),
                },
            )
            self._publish_sensor(
                "pv_financieel_effect_p10", round(financial_p10, 3)
            )
            self._publish_sensor(
                "pv_financieel_effect_p90", round(financial_p90, 3)
            )

            # Best consumption window
            self._publish_sensor(
                "beste_verbruiksmoment_start",
                self._iso(best_window["start"]) if best_window else "None",
            )
            self._publish_sensor(
                "beste_verbruiksmoment_einde",
                self._iso(best_window["end"]) if best_window else "None",
            )
            self._publish_sensor(
                "beste_verbruiksmoment_score",
                round(best_window["score"], 1) if best_window else 0.0,
                {
                    "venster_minuten": self.best_consumption_window_minutes,
                    "uitleg": (
                        "Eigen score op basis van prijs, PV-forecast en "
                        "Zonneplan sustainability score."
                    ),
                },
            )

            # Timeline for ApexCharts / dashboards.
            self._publish_timeline_if_changed(
                "pv_forecast_tijdlijn",
                len(timeline),
                {
                    "slots": timeline,
                    "interval_minuten": 15,
                    "horizon_uren": self.horizon_hours,
                },
            )

            bootstrap_meta = self.learning.get(
                "history_bootstrap", {}
            )
            daily_history = self.learning.get(
                "zonneplan_daily_history", {}
            )
            history_days = (
                len(daily_history)
                if isinstance(daily_history, dict)
                else 0
            )

            self._publish_sensor(
                "zonneplan_historie_dagen",
                history_days,
                {
                    "bootstrap_completed": bool(
                        bootstrap_meta.get("completed", False)
                        if isinstance(bootstrap_meta, dict)
                        else False
                    ),
                    "bootstrap_completed_at": (
                        bootstrap_meta.get("completed_at")
                        if isinstance(bootstrap_meta, dict)
                        else None
                    ),
                    "bootstrap_days_requested":
                        self.history_bootstrap_days,
                    "bootstrap_last_error": (
                        bootstrap_meta.get("last_error")
                        if isinstance(bootstrap_meta, dict)
                        else None
                    ),
                },
            )
            self._publish_sensor(
                "zonneplan_verbruik_jaar",
                round(year_totals["consumption_kwh"], 3),
                {
                    "jaar": now.year,
                    "dagen_beschikbaar": year_totals["days"],
                },
            )
            self._publish_sensor(
                "zonneplan_teruggeleverd_jaar",
                round(year_totals["returned_kwh"], 3),
                {
                    "jaar": now.year,
                    "dagen_beschikbaar": year_totals["days"],
                },
            )

            # Learning diagnostics
            daypart = self._daypart(now)
            daypart_model = self._daypart_model(daypart)
            self._publish_sensor(
                "pv_forecast_correctiefactor",
                round(current_correction, 4),
                {
                    "dagdeel": daypart,
                    "samples": learning_samples,
                    "minimum_samples": self.learning_min_samples,
                    "voldoende_data":
                        learning_samples >= self.learning_min_samples,
                    "dagdeel_samples": int(
                        daypart_model.get("samples", 0) or 0
                    ),
                    "dagdeel_voldoende_data": (
                        int(daypart_model.get("samples", 0) or 0)
                        >= self.learning_min_samples
                    ),
                    "fallback_bij_onvoldoende_data": 1.0,
                    "globale_factor_alleen_diagnostiek": True,
                    "dagdeel_factor":
                        self._round_or_none(
                            daypart_model.get("factor"), 4
                        ),
                    "factoren_per_dagdeel":
                        self._daypart_factor_attributes(),
                    "begrenzing": [
                        self.correction_min, self.correction_max
                    ],
                },
            )

            accuracy_state = "None"
            if learning_samples >= self.learning_min_samples:
                accuracy_state = round(
                    max(0.0, 100.0 - global_mape), 1
                )
            self._publish_sensor(
                "pv_forecast_nauwkeurigheid",
                accuracy_state,
                {
                    "mape_pct":
                        round(global_mape, 2)
                        if learning_samples else None,
                    "bias_pct":
                        round(global_bias, 2)
                        if learning_samples else None,
                    "mae_w":
                        round(global_mae, 1)
                        if learning_samples else None,
                    "rmse_w":
                        round(global_rmse, 1)
                        if learning_samples else None,
                    "samples": learning_samples,
                    "minimum_samples": self.learning_min_samples,
                },
            )
            self._publish_sensor(
                "pv_forecast_learning_samples",
                learning_samples,
                {
                    "minimum_samples": self.learning_min_samples,
                    "voldoende_data":
                        learning_samples >= self.learning_min_samples,
                    "dagdelen": self._daypart_factor_attributes(),
                    "opslagbestand": self.storage_file,
                    "max_storage_mb": self.max_storage_mb,
                },
            )
            self._publish_sensor(
                "pv_forecast_bias",
                round(global_bias, 2)
                if learning_samples else "None",
            )
            self._publish_sensor(
                "pv_forecast_mae",
                round(global_mae, 1)
                if learning_samples else "None",
            )
            self._publish_sensor(
                "pv_forecast_rmse",
                round(global_rmse, 1)
                if learning_samples else "None",
            )
            self._publish_sensor(
                "pv_observed_ac_ceiling",
                self._round_or_none(
                    self.learning.get("observed_ac_ceiling_w"), 0
                ),
                {
                    "clipping_actief":
                        self._forecast_ceiling_kw() is not None,
                    "headroom_factor": self.clipping_headroom,
                },
            )

            # Bounded actual daily stats
            self._publish_sensor(
                "pv_afgeschakeld_vandaag_minuten",
                round(float(today_stats.get("curtailed_minutes", 0.0)), 1),
            )
            self._publish_sensor(
                "pv_afgeschakeld_vandaag_kwh",
                round(float(today_stats.get("missed_kwh", 0.0)), 3),
            )
            self._publish_sensor(
                "pv_afschakeling_voordeel_vandaag",
                round(float(today_stats.get("benefit_eur", 0.0)), 3),
            )
            self._publish_sensor(
                "pv_afschakel_events_vandaag",
                int(today_stats.get("events", 0) or 0),
            )
            self._publish_sensor("pv_waarde_vandaag", round(float(finance_today.get("pv_value_eur",0)),3), {"eigen_verbruik_besparing_eur": round(float(finance_today.get("self_consumption_saving_eur",0)),3), "terugleveropbrengst_eur": round(float(finance_today.get("export_revenue_eur",0)),3), "pv_productie_kwh": round(float(finance_today.get("pv_generated_kwh",0)),3), "eigen_verbruik_pv_kwh": round(float(finance_today.get("self_consumption_kwh",0)),3), "teruglevering_pv_kwh": round(float(finance_today.get("export_kwh",0)),3)})
            self._publish_sensor("pv_eigen_verbruik_besparing_vandaag", round(float(finance_today.get("self_consumption_saving_eur",0)),3))
            self._publish_sensor("pv_terugleveropbrengst_vandaag", round(float(finance_today.get("export_revenue_eur",0)),3))
            self._publish_sensor("pv_afschakeling_besparing_vandaag", round(float(finance_today.get("curtailment_saving_eur",0)),3))
            self._publish_sensor("pv_afschakeling_kosten_vandaag", round(float(finance_today.get("curtailment_cost_eur",0)),3))
            self._publish_sensor("pv_afschakeling_netto_vandaag", round(float(finance_today.get("curtailment_net_eur",0)),3), {"counterfactual_pv_kwh": round(float(finance_today.get("curtailment_counterfactual_kwh",0)),3)})

        except Exception as exc:
            self.log(
                "PVGridForecast berekening mislukt: %s",
                exc,
                level="ERROR",
            )
            self._publish_sensor(
                "status",
                "Fout",
                {
                    "fout": str(exc),
                    "laatste_berekening":
                        self._iso(datetime.now().astimezone()),
                },
            )

    def _read_zonneplan_quarters(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        raw = self._attrs(self.price_quarter_entity).get("forecast") or []
        result = []
        for item in raw:
            try:
                dt = self._parse_dt(item.get("start_date")); dt_end = self._parse_dt(item.get("end_date"))
                if dt is None: continue
                if dt_end is None: dt_end = dt + timedelta(minutes=15)
                if dt_end <= start or dt >= end: continue
                included = item.get("price_tax_included", {})
                excluded = item.get("price_tax_excluded", {})
                amount = included.get("amount") if isinstance(included, dict) else None
                amount_excl = excluded.get("amount") if isinstance(excluded, dict) else None
                if amount is None: continue
                price = float(amount) / 10_000_000.0
                price_excl = float(amount_excl) / 10_000_000.0 if amount_excl is not None else None
                sobj = item.get("sustainability_score", {})
                sustainability = sobj.get("permille") if isinstance(sobj, dict) else sobj
                # Only count the part of a tariff slot that is still inside
                # the requested forecast horizon. This matters for the
                # quarter that is already in progress.
                result.append({"start": max(dt, start), "end": min(dt_end, end), "price": price, "price_excl_tax": price_excl, "sustainability": self._float_or_none(sustainability)})
            except Exception:
                continue
        result.sort(key=lambda x: x["start"])
        return result

    def _current_zonneplan_prices(self, now):
        attrs = self._attrs(self.price_quarter_entity)
        for item in attrs.get("forecast") or []:
            start = self._parse_dt(item.get("start_date")); end = self._parse_dt(item.get("end_date"))
            if start is None: continue
            if end is None: end = start + timedelta(minutes=15)
            if not (start <= now < end): continue
            inc = item.get("price_tax_included", {}); exc = item.get("price_tax_excluded", {})
            ia = inc.get("amount") if isinstance(inc, dict) else None; ea = exc.get("amount") if isinstance(exc, dict) else None
            return {"included": float(ia)/10_000_000.0 if ia is not None else None, "excluded": float(ea)/10_000_000.0 if ea is not None else None}
        return {"included": self._float_state(self.price_quarter_entity, None), "excluded": None}

    def _effective_export_price(self, price_excl_tax):
        value = self._float_or_none(price_excl_tax)
        if value is None: return None
        return value * (1.0 + self.zonnebonus_percent / 100.0) if value > 0 else value

    def _current_entsoe_price(self, now: datetime) -> Optional[float]:
        prices = self._attrs(self.entsoe_entity).get("prices") or []
        parsed = []
        for item in prices:
            dt = self._parse_dt(item.get("time")); price = self._float_or_none(item.get("price"))
            if dt is None or price is None: continue
            parsed.append((dt, price))
        parsed.sort(key=lambda item: item[0])
        for index, (start, price) in enumerate(parsed):
            end = (
                parsed[index + 1][0]
                if index + 1 < len(parsed)
                else start + timedelta(hours=1)
            )
            if start <= now < end:
                return price
        return self._float_state("sensor.current_electricity_market_price", None)

    def _current_sustainability_score(self, now: datetime) -> float:
        raw = self._attrs(self.price_quarter_entity).get("forecast") or []
        for item in raw:
            start = self._parse_dt(item.get("start_date"))
            end = self._parse_dt(item.get("end_date"))
            if start is None: continue
            if end is None: end = start + timedelta(minutes=15)
            if not (start <= now < end): continue
            obj = item.get("sustainability_score", {})
            value = obj.get("permille") if isinstance(obj, dict) else obj
            score = self._float_or_none(value)
            if score is not None: return float(score)
        return 0.0

    def _read_solcast_forecast(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        combined = {}
        for entity in (self.solcast_today_entity, self.solcast_tomorrow_entity):
            raw = self._attrs(entity).get("detailedForecast") or []
            for item in raw:
                dt = self._parse_dt(item.get("period_start"))
                if dt is None or dt + timedelta(minutes=30) <= start or dt >= end: continue
                p50 = self._float_or_none(item.get("pv_estimate")); p10 = self._float_or_none(item.get("pv_estimate10")); p90 = self._float_or_none(item.get("pv_estimate90"))
                if p50 is None: continue
                combined[self._iso(dt)] = {"start": dt, "end": dt + timedelta(minutes=30), "pv_estimate_kw": max(0.0, p50), "pv_estimate10_kw": max(0.0, p10 if p10 is not None else p50), "pv_estimate90_kw": max(0.0, p90 if p90 is not None else p50)}
        result = list(combined.values()); result.sort(key=lambda x: x["start"]); return result

    def _solcast_confidence(self, entity: str) -> Optional[float]:
        analysis = self._attrs(entity).get("analysis") or {}
        if isinstance(analysis, dict): return self._round_or_none(self._float_or_none(analysis.get("confidence")), 4)
        return None

    def _build_quarter_slots(self, quarter_prices, solcast, threshold):
        slots = []
        for p in quarter_prices:
            duration_h = max(
                0.0,
                (p["end"] - p["start"]).total_seconds() / 3600.0,
            )
            f = self._solcast_at(p["start"], solcast)

            pv_kw = f["pv_estimate_kw_corrected"] if f else 0.0
            p10_kw = f["pv_estimate10_kw_corrected"] if f else 0.0
            p90_kw = f["pv_estimate90_kw_corrected"] if f else 0.0

            should_curtail = p["price"] <= threshold
            relevant = (
                should_curtail
                and pv_kw * 1000.0 >= self.relevant_pv_power_w
            )

            slots.append({
                "start": p["start"],
                "end": p["end"],
                "price_eur_kwh": p["price"],
                "price_excl_tax_eur_kwh": p.get("price_excl_tax"),
                "effective_export_price_eur_kwh": self._effective_export_price(p.get("price_excl_tax")),
                "sustainability": p.get("sustainability"),
                "pv_power_kw": pv_kw,
                "pv_energy_kwh": pv_kw * duration_h,
                "pv_energy10_kwh": p10_kw * duration_h,
                "pv_energy90_kwh": p90_kw * duration_h,
                "should_curtail": should_curtail,
                "relevant_curtail": relevant,
                "consumption_score": self._slot_consumption_score(
                    price=p["price"],
                    pv_kw=pv_kw,
                    sustainability=p.get("sustainability"),
                    threshold=threshold,
                ),
            })
        return slots

    @staticmethod
    def _solcast_at(when, solcast):
        for item in solcast:
            if item["start"] <= when < item["end"]:
                return item
        return None

    def _find_curtailment_windows(self, slots):
        windows = []
        current = []

        def flush():
            nonlocal current
            if not current:
                return
            start = current[0]["start"]
            end = current[-1]["end"]
            windows.append({
                "start": start,
                "end": end,
                "duration_min": int(
                    round((end - start).total_seconds() / 60)
                ),
                "quarters": len(current),
                "min_price":
                    min(s["price_eur_kwh"] for s in current),
                "max_price":
                    max(s["price_eur_kwh"] for s in current),
                "pv_energy_kwh":
                    sum(s["pv_energy_kwh"] for s in current),
                "pv_energy10_kwh":
                    sum(s["pv_energy10_kwh"] for s in current),
                "pv_energy90_kwh":
                    sum(s["pv_energy90_kwh"] for s in current),
                "financial_effect_eur":
                    sum(
                        (
                            -s["pv_energy_kwh"]
                            * s["effective_export_price_eur_kwh"]
                        )
                        if (
                            s.get("effective_export_price_eur_kwh")
                            is not None
                            and s["effective_export_price_eur_kwh"] < 0
                        )
                        else 0.0
                        for s in current
                    ),
            })
            current = []

        for slot in slots:
            if slot["relevant_curtail"]:
                if current and slot["start"] != current[-1]["end"]:
                    flush()
                current.append(slot)
            else:
                flush()
        flush()
        return windows

    def _slot_consumption_score(
        self, price, pv_kw, sustainability, threshold
    ):
        # 0..100, intentionally heuristic.
        if price <= threshold:
            price_score = 100.0
        elif price <= 0.05:
            price_score = 85.0
        elif price <= 0.12:
            price_score = 60.0
        elif price <= 0.20:
            price_score = 35.0
        elif price <= 0.30:
            price_score = 15.0
        else:
            price_score = 0.0

        pv_score = max(
            0.0,
            min(100.0, pv_kw * 1000.0 / max(self.peak_power_w, 1.0) * 100.0),
        )
        sust = self._float_or_none(sustainability)
        sust_score = max(
            0.0,
            min(100.0, (sust or 0.0) / 10.0),
        )
        return (
            price_score * 0.55
            + pv_score * 0.30
            + sust_score * 0.15
        )

    def _best_consumption_window(self, slots):
        if not slots:
            return None
        quarters = max(
            1,
            int(math.ceil(self.best_consumption_window_minutes / 15.0)),
        )
        if len(slots) < quarters:
            return None

        best = None
        for idx in range(0, len(slots) - quarters + 1):
            group = slots[idx: idx + quarters]
            # Require continuous quarter-hour slots.
            continuous = all(
                group[i]["end"] == group[i + 1]["start"]
                for i in range(len(group) - 1)
            )
            if not continuous:
                continue
            score = sum(s["consumption_score"] for s in group) / len(group)
            candidate = {
                "start": group[0]["start"],
                "end": group[-1]["end"],
                "score": score,
            }
            if best is None or score > best["score"]:
                best = candidate
        return best

    def _compact_timeline(self, slots):
        # Intentionally compact to limit MQTT/Recorder attribute size.
        return [{
            "t": self._iso(s["start"]),
            "p": round(s["price_eur_kwh"], 5),
            "px": self._round_or_none(s.get("price_excl_tax_eur_kwh"), 5),
            "xp": self._round_or_none(s.get("effective_export_price_eur_kwh"), 5),
            "pv": round(s["pv_power_kw"], 3),
            "e": round(s["pv_energy_kwh"], 3),
            "c": bool(s["should_curtail"]),
            "r": bool(s["relevant_curtail"]),
            "s": (
                round(float(s["sustainability"]), 0)
                if s.get("sustainability") is not None
                else None
            ),
            "v": round(s["consumption_score"], 1),
        } for s in slots[:192]]

    def _daypart(self, when):
        hour = when.hour
        if 5 <= hour < 9:
            return "vroeg"
        if 9 <= hour < 12:
            return "ochtend"
        if 12 <= hour < 15:
            return "middag"
        if 15 <= hour < 18:
            return "namiddag"
        if 18 <= hour < 22:
            return "avond"
        return "nacht"

    def _empty_model(self):
        return {
            "factor": 1.0,
            "mape_ewma_pct": 0.0,
            "bias_ewma_pct": 0.0,
            "mae_ewma_w": 0.0,
            "mse_ewma_w2": 0.0,
            "samples": 0,
        }

    def _daypart_model(self, name):
        models = self.learning.setdefault("dayparts", {})
        if name not in models or not isinstance(models[name], dict):
            models[name] = self._empty_model()
        return models[name]

    def _update_learning(self, now) -> None:
        if not self.learning_enabled or not self._pv_is_on():
            return

        forecast_w = self._float_state(
            self.solcast_power_now_entity, None
        )
        actual_w = self._float_state(
            self.ecu_power_entity, None
        )
        if (
            forecast_w is None
            or actual_w is None
            or forecast_w < self.learning_min_forecast_w
            or actual_w < self.learning_min_actual_w
        ):
            return

        # Add one stability sample at a controlled cadence.
        if self._stability_buffer:
            last_t = self._stability_buffer[-1]["time"]
            if (
                now - last_t
            ).total_seconds() < self.learning_stability_min_spacing_sec:
                return

        self._stability_buffer.append({
            "time": now,
            "forecast_w": forecast_w,
            "actual_w": actual_w,
        })
        self._stability_buffer = self._stability_buffer[
            -self.learning_stability_samples:
        ]

        if len(self._stability_buffer) < self.learning_stability_samples:
            return

        fvals = [x["forecast_w"] for x in self._stability_buffer]
        avals = [x["actual_w"] for x in self._stability_buffer]
        favg = sum(fvals) / len(fvals)
        aavg = sum(avals) / len(avals)

        fspread = (
            (max(fvals) - min(fvals)) / max(favg, 1.0) * 100.0
        )
        aspread = (
            (max(avals) - min(avals)) / max(aavg, 1.0) * 100.0
        )
        if (
            fspread > self.learning_stability_pct
            or aspread > self.learning_stability_pct
        ):
            return

        ratio = actual_w / forecast_w
        if not math.isfinite(ratio):
            return
        ratio = max(
            self.learning_ratio_min,
            min(self.learning_ratio_max, ratio),
        )

        error_w = actual_w - forecast_w
        ape = abs(error_w) / max(forecast_w, 1.0) * 100.0
        bias_pct = error_w / max(forecast_w, 1.0) * 100.0

        self._update_model(self.learning, ratio, ape, bias_pct, error_w)

        part = self._daypart(now)
        model = self._daypart_model(part)
        self._update_model(model, ratio, ape, bias_pct, error_w)

        self.learning.update({
            "last_sample": self._iso(now),
            "last_ratio": round(ratio, 4),
            "last_actual_w": round(actual_w, 1),
            "last_forecast_w": round(forecast_w, 1),
            "last_daypart": part,
        })
        self._save_learning()

    def _update_model(self, model, ratio, ape, bias_pct, error_w):
        samples = int(model.get("samples", 0) or 0)
        alpha = self.learning_alpha

        def ewma(key, value, default=0.0):
            old = self._float_or_none(model.get(key))
            if samples <= 0 or old is None:
                return value
            return alpha * value + (1.0 - alpha) * old

        model["factor"] = round(ewma("factor", ratio, 1.0), 6)
        model["mape_ewma_pct"] = round(
            ewma("mape_ewma_pct", ape), 4
        )
        model["bias_ewma_pct"] = round(
            ewma("bias_ewma_pct", bias_pct), 4
        )
        model["mae_ewma_w"] = round(
            ewma("mae_ewma_w", abs(error_w)), 3
        )
        model["mse_ewma_w2"] = round(
            ewma("mse_ewma_w2", error_w * error_w), 3
        )
        model["samples"] = samples + 1

    def _learning_metric(self, key):
        value = self._float_or_none(self.learning.get(key))
        return value if value is not None else 0.0

    def _correction_factor_for_time(self, when):
        """
        Use only a sufficiently trained daypart-specific correction.

        The global learning model remains available for diagnostics, but is
        deliberately never used as a forecast fallback.
        """
        part = self._daypart(when)
        model = self._daypart_model(part)
        part_samples = int(model.get("samples", 0) or 0)
        part_factor = self._float_or_none(model.get("factor"))

        if (
            part_samples >= self.learning_min_samples
            and part_factor is not None
        ):
            return max(
                self.correction_min,
                min(self.correction_max, part_factor),
            )

        return 1.0

    def _current_correction_factor(self) -> float:
        return self._correction_factor_for_time(
            datetime.now().astimezone()
        )

    def _daypart_factor_attributes(self):
        result = {}
        for name in (
            "vroeg", "ochtend", "middag", "namiddag", "avond", "nacht"
        ):
            model = self._daypart_model(name)
            result[name] = {
                "factor": self._round_or_none(model.get("factor"), 4),
                "samples": int(model.get("samples", 0) or 0),
                "mape_pct":
                    self._round_or_none(model.get("mape_ewma_pct"), 2),
                "bias_pct":
                    self._round_or_none(model.get("bias_ewma_pct"), 2),
            }
        return result

    def _update_observed_ceiling(self, observed_w):
        observed_w = self._float_or_none(observed_w)
        if observed_w is None or observed_w <= 0:
            return
        old = self._float_or_none(
            self.learning.get("observed_ac_ceiling_w")
        ) or 0.0
        if observed_w > old:
            self.learning["observed_ac_ceiling_w"] = round(
                observed_w, 1
            )
            self._save_learning()

    def _forecast_ceiling_kw(self):
        samples = int(self.learning.get("samples", 0) or 0)
        ceiling_w = self._float_or_none(
            self.learning.get("observed_ac_ceiling_w")
        )
        if (
            samples < self.clipping_min_samples
            or ceiling_w is None
            or ceiling_w < self.peak_power_w * 0.60
        ):
            return None
        return ceiling_w * self.clipping_headroom / 1000.0

    def _default_learning(self):
        data = self._empty_model()
        data.update({
            "version": 3,
            "dayparts": {},
            "daily_stats": {},
            "runtime_curtailing": False,
            "last_runtime_sample": None,
            "observed_ac_ceiling_w": 0.0,
            "finance_today": {},
            "finance_history": {},
            "zonneplan_daily_history": {},
            "history_bootstrap": {
                "completed": False,
                "completed_at": None,
                "days_requested": 0,
                "days_reconstructed": 0,
                "consumption_days": 0,
                "returned_days": 0,
                "missing_sources": [],
                "last_error": None,
            },
            "zonneplan_rollover": {
                "date": None,
                "last_consumption_kwh": None,
                "last_returned_kwh": None,
                "consumption_reset_seen": False,
                "returned_reset_seen": False,
            },
            "planner": {},
        })
        return data

    def _max_storage_bytes(self):
        return int(self.max_storage_mb * 1024 * 1024)

    def _load_learning(self):
        default = self._default_learning()
        try:
            if not os.path.exists(self.storage_file):
                return default
            size = os.path.getsize(self.storage_file)
            if size > self._max_storage_bytes():
                self.log(
                    "Learning-bestand te groot: %s > %s bytes; reset.",
                    size,
                    self._max_storage_bytes(),
                    level="WARNING",
                )
                self._write_learning_payload(default)
                return default

            with open(self.storage_file, "r", encoding="utf-8") as h:
                data = json.load(h)
            if not isinstance(data, dict):
                return default

            # v1 -> v2 migration without losing learned global factor.
            merged = {**default, **data}

            # v1.2.4 migration:
            # Older models could use the GLOBAL learning factor as fallback
            # for a daypart that had insufficient samples. A factor learned
            # mostly around sunrise could therefore contaminate later hours.
            #
            # Preserve financial/history data, but reset the statistical
            # learning model once when upgrading to model version 3.
            stored_model_version = int(data.get("version", 0) or 0)
            if stored_model_version < 3:
                preserved_keys = {
                    "finance_today",
                    "finance_history",
                    "zonneplan_daily_history",
                    "history_bootstrap",
                    "zonneplan_rollover",
                    "daily_stats",
                    "observed_ac_ceiling_w",
                    "runtime_curtailing",
                    "last_runtime_sample",
                }
                preserved = {
                    key: data.get(key)
                    for key in preserved_keys
                    if key in data
                }
                merged = {**default, **preserved}
                merged["version"] = 3
                merged["learning_reset_reason"] = (
                    "v1.2.4: globale forecast-fallback verwijderd"
                )
                merged["learning_reset_at"] = self._iso(
                    datetime.now().astimezone()
                )
            if not isinstance(merged.get("dayparts"), dict):
                merged["dayparts"] = {}
            if not isinstance(merged.get("daily_stats"), dict):
                merged["daily_stats"] = {}
            if not isinstance(merged.get("finance_today"), dict): merged["finance_today"] = {}
            if not isinstance(merged.get("finance_history"), dict): merged["finance_history"] = {}
            if not isinstance(merged.get("zonneplan_daily_history"), dict): merged["zonneplan_daily_history"] = {}
            if not isinstance(merged.get("history_bootstrap"), dict):
                merged["history_bootstrap"] = {
                    "completed": False,
                    "completed_at": None,
                    "days_requested": 0,
                    "days_reconstructed": 0,
                    "consumption_days": 0,
                    "returned_days": 0,
                    "missing_sources": [],
                    "last_error": None,
                }
            if not isinstance(merged.get("zonneplan_rollover"), dict):
                merged["zonneplan_rollover"] = {
                    "date": None,
                    "last_consumption_kwh": None,
                    "last_returned_kwh": None,
                    "consumption_reset_seen": False,
                    "returned_reset_seen": False,
                }
            return self._compact_learning_payload(merged)

        except Exception as exc:
            self.log(
                "Learning-bestand kon niet worden gelezen: %s",
                exc,
                level="WARNING",
            )
            return default

    def _compact_learning_payload(self, data):
        allowed_scalar = {
            "version", "factor", "mape_ewma_pct", "bias_ewma_pct",
            "mae_ewma_w", "mse_ewma_w2", "samples",
            "last_sample", "last_ratio", "last_actual_w",
            "last_forecast_w", "last_daypart",
            "observed_ac_ceiling_w", "runtime_curtailing",
            "last_runtime_sample", "learning_reset_reason",
            "learning_reset_at",
        }
        compact = {
            k: v for k, v in data.items() if k in allowed_scalar
        }

        # Fixed set of six dayparts: bounded.
        dayparts = {}
        raw_parts = data.get("dayparts", {})
        if isinstance(raw_parts, dict):
            for name in (
                "vroeg", "ochtend", "middag",
                "namiddag", "avond", "nacht"
            ):
                model = raw_parts.get(name)
                if isinstance(model, dict):
                    dayparts[name] = {
                        k: model.get(k)
                        for k in (
                            "factor", "mape_ewma_pct",
                            "bias_ewma_pct", "mae_ewma_w",
                            "mse_ewma_w2", "samples"
                        )
                        if k in model
                    }
        compact["dayparts"] = dayparts

        finance_today = data.get("finance_today", {})
        compact["finance_today"] = self._compact_finance_day(finance_today) if isinstance(finance_today, dict) else {}
        raw_fh = data.get("finance_history", {})
        fh = {}
        if isinstance(raw_fh, dict):
            for key in sorted(raw_fh.keys())[-self.finance_history_retention_days:]:
                value = raw_fh.get(key)
                if isinstance(value, dict): fh[key] = self._compact_finance_day(value)
        compact["finance_history"] = fh

        raw_zdh = data.get("zonneplan_daily_history", {})
        zdh = {}
        if isinstance(raw_zdh, dict):
            for key in sorted(raw_zdh.keys())[-self.history_bootstrap_days:]:
                value = raw_zdh.get(key)
                if isinstance(value, dict):
                    zdh[key] = {
                        "consumption_kwh": round(
                            float(value.get("consumption_kwh", 0.0) or 0.0),
                            6,
                        ),
                        "returned_kwh": round(
                            float(value.get("returned_kwh", 0.0) or 0.0),
                            6,
                        ),
                    }
        compact["zonneplan_daily_history"] = zdh

        raw_bootstrap = data.get("history_bootstrap", {})
        if not isinstance(raw_bootstrap, dict):
            raw_bootstrap = {}
        compact["history_bootstrap"] = {
            "completed": bool(raw_bootstrap.get("completed", False)),
            "completed_at": raw_bootstrap.get("completed_at"),
            "days_requested": int(
                raw_bootstrap.get("days_requested", 0) or 0
            ),
            "days_reconstructed": int(
                raw_bootstrap.get("days_reconstructed", 0) or 0
            ),
            "consumption_days": int(
                raw_bootstrap.get("consumption_days", 0) or 0
            ),
            "returned_days": int(
                raw_bootstrap.get("returned_days", 0) or 0
            ),
            "missing_sources": list(
                raw_bootstrap.get("missing_sources", []) or []
            )[:4],
            "last_error": raw_bootstrap.get("last_error"),
        }

        raw_rollover = data.get("zonneplan_rollover", {})
        if not isinstance(raw_rollover, dict):
            raw_rollover = {}
        compact["zonneplan_rollover"] = {
            "date": raw_rollover.get("date"),
            "last_consumption_kwh": self._float_or_none(
                raw_rollover.get("last_consumption_kwh")
            ),
            "last_returned_kwh": self._float_or_none(
                raw_rollover.get("last_returned_kwh")
            ),
            "consumption_reset_seen": bool(
                raw_rollover.get("consumption_reset_seen", False)
            ),
            "returned_reset_seen": bool(
                raw_rollover.get("returned_reset_seen", False)
            ),
        }

        # Hard bounded daily statistics.
        raw_daily = data.get("daily_stats", {})
        daily = {}
        if isinstance(raw_daily, dict):
            keys = sorted(raw_daily.keys())[-self.daily_stats_retention_days:]
            for key in keys:
                value = raw_daily.get(key)
                if isinstance(value, dict):
                    daily[key] = {
                        "curtailed_minutes":
                            float(value.get("curtailed_minutes", 0.0) or 0.0),
                        "missed_kwh":
                            float(value.get("missed_kwh", 0.0) or 0.0),
                        "benefit_eur":
                            float(value.get("benefit_eur", 0.0) or 0.0),
                        "events":
                            int(value.get("events", 0) or 0),
                    }
        compact["daily_stats"] = daily
        # The planner stores only bounded load medians and the latest advice.
        planner = data.get("planner", {})
        if isinstance(planner, dict):
            load = planner.get("load_samples", {})
            plans = planner.get("plans", {})
            evaluations = planner.get("evaluations", [])
            compact["planner"] = {
                "load_samples": {str(k): list(v)[-32:] for k, v in load.items() if isinstance(v, list)} if isinstance(load, dict) else {},
                "plans": {str(k): v for k, v in plans.items() if isinstance(v, dict)} if isinstance(plans, dict) else {},
                "evaluations": evaluations[-90:] if isinstance(evaluations, list) else [],
            }
        return compact

    def _save_learning(self):
        try:
            if self.planner:
                self.learning["planner"] = self.planner.memory
            compact = self._compact_learning_payload(self.learning)
            self.learning = {**self._default_learning(), **compact}
            if self.planner:
                self.planner.memory = self.learning["planner"]
            self._write_learning_payload(compact)
        except Exception as exc:
            self.log(
                "Learning-bestand kon niet worden opgeslagen: %s",
                exc,
                level="WARNING",
            )

    def _write_learning_payload(self, payload):
        directory = os.path.dirname(self.storage_file)
        if directory:
            os.makedirs(directory, exist_ok=True)

        encoded = json.dumps(
            payload, ensure_ascii=False, indent=2
        ).encode("utf-8")
        if len(encoded) > self._max_storage_bytes():
            raise ValueError(
                f"Learning-payload groter dan opslaglimiet: "
                f"{len(encoded)} > {self._max_storage_bytes()} bytes"
            )

        temp = f"{self.storage_file}.tmp"
        with open(temp, "wb") as h:
            h.write(encoded)
        os.replace(temp, self.storage_file)

    @staticmethod
    def _empty_finance_day(date_key=None):
        return {"date": date_key, "pv_generated_kwh": 0.0, "export_kwh": 0.0, "self_consumption_kwh": 0.0, "export_revenue_eur": 0.0, "self_consumption_saving_eur": 0.0, "pv_value_eur": 0.0, "curtailment_saving_eur": 0.0, "curtailment_cost_eur": 0.0, "curtailment_net_eur": 0.0, "curtailment_counterfactual_kwh": 0.0}

    def _compact_finance_day(self, value):
        result = self._empty_finance_day(value.get("date") if isinstance(value, dict) else None)
        if isinstance(value, dict):
            for key in result:
                if key != "date": result[key] = round(float(value.get(key, 0.0) or 0.0), 6)
        return result

    def _start_history_bootstrap(self, kwargs=None):
        """
        One-time bootstrap from Home Assistant Recorder.

        Reads only the two Zonneplan daily kWh entities, reconstructs one
        compact total per calendar day, then stores those totals locally.
        Future AppDaemon restarts skip the Recorder query once bootstrap is
        marked completed.
        """
        if not self.history_bootstrap_enabled:
            return

        meta = self.learning.setdefault("history_bootstrap", {})
        if bool(meta.get("completed", False)):
            self.log(
                "Zonneplan history-bootstrap overgeslagen: al uitgevoerd op %s",
                meta.get("completed_at"),
            )
            return

        if self._history_bootstrap_in_progress:
            return

        self._history_bootstrap_in_progress = True
        self.log(
            "Start eenmalige Zonneplan Recorder-bootstrap voor %s dagen",
            self.history_bootstrap_days,
        )

        try:
            self.get_history(
                entity_id=[
                    self.zonneplan_consumption_today_entity,
                    self.zonneplan_returned_today_entity,
                ],
                days=self.history_bootstrap_days,
                minimal_response=True,
                no_attributes=True,
                significant_changes_only=False,
                callback=self._history_bootstrap_callback,
            )
        except TypeError:
            # Compatibility fallback for AppDaemon versions that do not accept
            # a list of entity IDs in get_history().
            self.log(
                "get_history(list) niet ondersteund; gebruik compatibiliteitsbootstrap",
                level="WARNING",
            )
            try:
                self.get_history(
                    entity_id=self.zonneplan_consumption_today_entity,
                    days=self.history_bootstrap_days,
                    minimal_response=True,
                    no_attributes=True,
                    significant_changes_only=False,
                    callback=self._history_consumption_callback,
                )
            except Exception as exc:
                self._finish_history_bootstrap_error(exc)
        except Exception as exc:
            self._finish_history_bootstrap_error(exc)

    def _history_bootstrap_callback(self, future):
        try:
            result = future.result() if hasattr(future, "result") else future
            history = self._normalize_history_result(
                result,
                [
                    self.zonneplan_consumption_today_entity,
                    self.zonneplan_returned_today_entity,
                ],
            )
            self._finish_history_bootstrap(history)
        except Exception as exc:
            self._finish_history_bootstrap_error(exc)

    def _history_consumption_callback(self, future):
        try:
            result = future.result() if hasattr(future, "result") else future
            normalized = self._normalize_history_result(
                result,
                [self.zonneplan_consumption_today_entity],
            )
            self._history_bootstrap_partial = normalized
            self.get_history(
                entity_id=self.zonneplan_returned_today_entity,
                days=self.history_bootstrap_days,
                minimal_response=True,
                no_attributes=True,
                significant_changes_only=False,
                callback=self._history_returned_callback,
            )
        except Exception as exc:
            self._finish_history_bootstrap_error(exc)

    def _history_returned_callback(self, future):
        try:
            result = future.result() if hasattr(future, "result") else future
            normalized = self._normalize_history_result(
                result,
                [self.zonneplan_returned_today_entity],
            )
            merged = dict(
                getattr(self, "_history_bootstrap_partial", {}) or {}
            )
            merged.update(normalized)
            self._history_bootstrap_partial = {}
            self._finish_history_bootstrap(merged)
        except Exception as exc:
            self._finish_history_bootstrap_error(exc)

    def _normalize_history_result(self, result, requested_entities):
        """
        Normalize AppDaemon get_history responses to:
            {entity_id: [state_dict, ...]}

        Handles both modern list-of-entity history responses and several
        older/flattened response shapes.
        """
        normalized = {entity: [] for entity in requested_entities}

        if result is None:
            return normalized

        # Some APIs/wrappers may return a mapping keyed by entity_id.
        if isinstance(result, dict):
            for entity in requested_entities:
                value = result.get(entity)
                if isinstance(value, list):
                    normalized[entity].extend(value)
            return normalized

        if not isinstance(result, list):
            return normalized

        # Modern response: list[list[dict]], one list per requested entity.
        nested_lists = [
            item for item in result
            if isinstance(item, list)
        ]
        if nested_lists:
            for group in nested_lists:
                entity = self._history_group_entity(group)
                if entity in normalized:
                    normalized[entity].extend(
                        item for item in group
                        if isinstance(item, dict)
                    )

            # If minimal responses omit entity_id, preserve request ordering.
            if all(not normalized[e] for e in requested_entities):
                for index, group in enumerate(nested_lists):
                    if index >= len(requested_entities):
                        break
                    normalized[requested_entities[index]].extend(
                        item for item in group
                        if isinstance(item, dict)
                    )
            return normalized

        # Flat list of dicts.
        if all(isinstance(item, dict) for item in result):
            for item in result:
                entity = item.get("entity_id")
                if entity in normalized:
                    normalized[entity].append(item)

            # Single-entity query with minimal response may omit entity_id.
            if (
                len(requested_entities) == 1
                and not normalized[requested_entities[0]]
            ):
                normalized[requested_entities[0]] = list(result)

        return normalized

    @staticmethod
    def _history_group_entity(group):
        for item in group:
            if isinstance(item, dict) and item.get("entity_id"):
                return item.get("entity_id")
        return None

    def _history_state_time(self, item):
        for key in ("last_updated", "last_changed"):
            parsed = self._parse_dt(item.get(key))
            if parsed is not None:
                return parsed
        return None

    def _daily_max_from_history(self, items):
        """
        Daily Zonneplan sensors reset at midnight. The maximum valid state
        observed during a calendar day is therefore used as that day's total.
        """
        result = {}
        for item in items:
            if not isinstance(item, dict):
                continue

            value = self._float_or_none(item.get("state"))
            when = self._history_state_time(item)

            if value is None or value < 0 or when is None:
                continue

            key = when.astimezone().date().isoformat()
            old = result.get(key)
            if old is None or value > old:
                result[key] = value

        return result

    def _finish_history_bootstrap(self, history):
        now = datetime.now().astimezone()

        consumption = self._daily_max_from_history(
            history.get(self.zonneplan_consumption_today_entity, [])
        )
        returned = self._daily_max_from_history(
            history.get(self.zonneplan_returned_today_entity, [])
        )

        dates = sorted(set(consumption) | set(returned))
        compact = {}

        if not dates:
            self._finish_history_bootstrap_error(
                "geen bruikbare Recorder-history ontvangen"
            )
            return

        for date_key in dates[-self.history_bootstrap_days:]:
            compact[date_key] = {
                "consumption_kwh": round(
                    float(consumption.get(date_key, 0.0) or 0.0),
                    6,
                ),
                "returned_kwh": round(
                    float(returned.get(date_key, 0.0) or 0.0),
                    6,
                ),
            }

        # Do not overwrite locally collected data with an empty query.
        existing = self.learning.get("zonneplan_daily_history", {})
        if not isinstance(existing, dict):
            existing = {}

        merged = dict(existing)
        merged.update(compact)
        merged = {
            key: merged[key]
            for key in sorted(merged.keys())[-self.history_bootstrap_days:]
        }

        self.learning["zonneplan_daily_history"] = merged
        missing_sources = []
        if not consumption:
            missing_sources.append(self.zonneplan_consumption_today_entity)
        if not returned:
            missing_sources.append(self.zonneplan_returned_today_entity)

        self.learning["history_bootstrap"] = {
            "completed": True,
            "completed_at": self._iso(now),
            "days_requested": self.history_bootstrap_days,
            "days_reconstructed": len(compact),
            "consumption_days": len(consumption),
            "returned_days": len(returned),
            "missing_sources": missing_sources,
            "last_error": (
                "gedeeltelijke historie: " + ", ".join(missing_sources)
                if missing_sources
                else None
            ),
        }

        self._history_bootstrap_in_progress = False
        self._save_learning()

        self.log(
            "Zonneplan Recorder-bootstrap gereed: %s dagen gereconstrueerd",
            len(compact),
        )

        # Refresh MQTT diagnostics after bootstrap.
        self.run_in(self._initial_update, 1)

    def _finish_history_bootstrap_error(self, exc):
        self._history_bootstrap_in_progress = False
        self.learning["history_bootstrap"] = {
            "completed": False,
            "completed_at": None,
            "days_requested": self.history_bootstrap_days,
            "days_reconstructed": 0,
            "consumption_days": 0,
            "returned_days": 0,
            "missing_sources": [],
            "last_error": str(exc),
        }
        self._save_learning()
        self.log(
            "Zonneplan Recorder-bootstrap mislukt: %s",
            exc,
            level="WARNING",
        )

    def _update_zonneplan_daily_history_today(self, now):
        """
        Keep compact Zonneplan daily totals without copying yesterday's final
        value into the new day before the Zonneplan source sensors reset.
        """
        consumption = self._float_state(
            self.zonneplan_consumption_today_entity, None
        )
        returned = self._float_state(
            self.zonneplan_returned_today_entity, None
        )
        if consumption is None and returned is None:
            return

        date_key = now.date().isoformat()
        history = self.learning.setdefault("zonneplan_daily_history", {})
        if not isinstance(history, dict):
            history = {}

        rollover = self.learning.setdefault("zonneplan_rollover", {})
        if not isinstance(rollover, dict):
            rollover = {}

        previous_date = rollover.get("date")
        previous_consumption = self._float_or_none(
            rollover.get("last_consumption_kwh")
        )
        previous_returned = self._float_or_none(
            rollover.get("last_returned_kwh")
        )

        # Migration / first run: current day is already active.
        if previous_date is None:
            rollover = {
                "date": date_key,
                "last_consumption_kwh": consumption,
                "last_returned_kwh": returned,
                "consumption_reset_seen": True,
                "returned_reset_seen": True,
            }

        # Date changed: hold yesterday's terminal values as reset references.
        elif previous_date != date_key:
            rollover = {
                "date": date_key,
                "last_consumption_kwh": previous_consumption,
                "last_returned_kwh": previous_returned,
                "consumption_reset_seen": False,
                "returned_reset_seen": False,
            }

        def reset_seen(current_value, previous_value):
            if current_value is None:
                return False
            if current_value <= 0.25:
                return True
            if previous_value is None or previous_value <= 0:
                return True
            return current_value <= previous_value * 0.50

        if not rollover.get("consumption_reset_seen", False):
            if reset_seen(consumption, previous_consumption):
                rollover["consumption_reset_seen"] = True

        if not rollover.get("returned_reset_seen", False):
            if reset_seen(returned, previous_returned):
                rollover["returned_reset_seen"] = True

        current = history.get(date_key, {})
        if not isinstance(current, dict):
            current = {}

        # Once reset is observed, repair a duplicated previous-day value if
        # present, then continue today's monotonic total.
        if rollover.get("consumption_reset_seen", False) and consumption is not None:
            stored = self._float_or_none(current.get("consumption_kwh"))
            if stored is not None and stored > max(1.0, consumption * 2.0):
                current["consumption_kwh"] = 0.0
            current["consumption_kwh"] = max(
                float(current.get("consumption_kwh", 0.0) or 0.0),
                float(consumption),
            )
            rollover["last_consumption_kwh"] = consumption

        if rollover.get("returned_reset_seen", False) and returned is not None:
            stored = self._float_or_none(current.get("returned_kwh"))
            if stored is not None and stored > max(1.0, returned * 2.0):
                current["returned_kwh"] = 0.0
            current["returned_kwh"] = max(
                float(current.get("returned_kwh", 0.0) or 0.0),
                float(returned),
            )
            rollover["last_returned_kwh"] = returned

        history[date_key] = current
        self.learning["zonneplan_rollover"] = rollover
        self.learning["zonneplan_daily_history"] = {
            key: history[key]
            for key in sorted(history.keys())[-self.history_bootstrap_days:]
        }

    def _zonneplan_year_totals(self, now):
        history = self.learning.get("zonneplan_daily_history", {})
        if not isinstance(history, dict):
            return {
                "consumption_kwh": 0.0,
                "returned_kwh": 0.0,
                "days": 0,
            }

        prefix = f"{now.year:04d}-"
        consumption = 0.0
        returned = 0.0
        days = 0

        for date_key, value in history.items():
            if not str(date_key).startswith(prefix):
                continue
            if not isinstance(value, dict):
                continue
            consumption += float(
                value.get("consumption_kwh", 0.0) or 0.0
            )
            returned += float(
                value.get("returned_kwh", 0.0) or 0.0
            )
            days += 1

        return {
            "consumption_kwh": consumption,
            "returned_kwh": returned,
            "days": days,
        }

    def _finance_today(self, now):
        date_key = now.date().isoformat(); current = self.learning.get("finance_today", {})
        if not isinstance(current, dict) or current.get("date") != date_key:
            if isinstance(current, dict) and current.get("date"):
                self.learning.setdefault("finance_history", {})[current["date"]] = self._compact_finance_day(current)
            current = self._empty_finance_day(date_key); self.learning["finance_today"] = current
            self._finance_last_time = self._finance_last_pv_kwh = self._finance_last_export_kwh = None
            self._save_learning()
        return current

    def _update_daily_finance(self, now, price_included, price_excluded, pv_is_on, automatic_enabled, manual_blocked, threshold, forecast_power_w, grid_import_kw):
        finance = self._finance_today(now)
        pv_total = self._float_state(self.ecu_daily_entity, None); export_total = self._float_state(self.zonneplan_returned_today_entity, None)
        if pv_total is None or export_total is None: return finance
        rollover = self.learning.get("zonneplan_rollover", {})
        if (
            isinstance(rollover, dict)
            and rollover.get("date") == now.date().isoformat()
            and not rollover.get("returned_reset_seen", False)
        ):
            # Zonneplan can retain yesterday's total shortly after midnight.
            export_total = 0.0
        if self._finance_last_time is None or self._finance_last_pv_kwh is None or self._finance_last_export_kwh is None or pv_total < self._finance_last_pv_kwh or export_total < self._finance_last_export_kwh:
            self._finance_last_time = now; self._finance_last_pv_kwh = pv_total; self._finance_last_export_kwh = export_total
            finance["pv_generated_kwh"] = max(0.0, pv_total)
            finance["export_kwh"] = max(0.0, min(export_total, pv_total))
            finance["self_consumption_kwh"] = max(0.0, pv_total-finance["export_kwh"])
            self.learning["finance_today"] = finance; self._save_learning(); return finance
        elapsed_h = max(0.0, min((now-self._finance_last_time).total_seconds()/3600.0, self.update_minutes*2.5/60.0))
        delta_pv = max(0.0, pv_total-self._finance_last_pv_kwh); delta_export = max(0.0, export_total-self._finance_last_export_kwh)
        export_price = self._effective_export_price(price_excluded)
        if delta_pv > 0:
            if price_included is not None:
                finance["self_consumption_saving_eur"] += delta_pv*price_included
        if delta_export > 0:
            # The Zonneplan daily sensor often updates after the ECU daily
            # sensor. Process its delta independently so a delayed update is
            # not discarded by a per-poll plausibility cap.
            if export_price is not None:
                finance["export_revenue_eur"] += delta_export*export_price
            if price_included is not None:
                finance["self_consumption_saving_eur"] = max(
                    0.0,
                    finance["self_consumption_saving_eur"]
                    - delta_export*price_included,
                )
        finance["pv_generated_kwh"] = max(0.0, pv_total)
        finance["export_kwh"] = max(0.0, min(export_total, pv_total))
        finance["self_consumption_kwh"] = max(0.0, pv_total-finance["export_kwh"])
        finance["pv_value_eur"] = finance["export_revenue_eur"] + finance["self_consumption_saving_eur"]
        low_price = price_included is not None and price_included <= threshold
        curtailed = automatic_enabled and not manual_blocked and low_price and not pv_is_on and forecast_power_w >= self.relevant_pv_power_w
        if curtailed and elapsed_h > 0:
            cf_pv = max(0.0, forecast_power_w)/1000.0*elapsed_h; load = max(0.0, grid_import_kw)*elapsed_h
            cf_self = min(cf_pv, load); cf_export = max(0.0, cf_pv-cf_self)
            saving = cf_export*(-export_price) if export_price is not None and export_price < 0 else 0.0
            cost = cf_self*max(0.0, price_included) if price_included is not None else 0.0
            finance["curtailment_saving_eur"] += saving; finance["curtailment_cost_eur"] += cost; finance["curtailment_net_eur"] += saving-cost; finance["curtailment_counterfactual_kwh"] += cf_pv
        self._finance_last_time=now; self._finance_last_pv_kwh=pv_total; self._finance_last_export_kwh=export_total; self.learning["finance_today"]=finance; self._save_learning(); return finance

    def _update_daily_stats(
        self, now, automatic_enabled, manual_blocked,
        pv_is_on, price, threshold, forecast_power_w
    ):
        date_key = now.date().isoformat()
        daily = self.learning.setdefault("daily_stats", {})
        stats = daily.setdefault(date_key, {
            "curtailed_minutes": 0.0,
            "missed_kwh": 0.0,
            "benefit_eur": 0.0,
            "events": 0,
        })

        low_price = (
            price is not None
            and price <= threshold
        )
        curtailing = bool(
            automatic_enabled
            and not manual_blocked
            and low_price
            and not pv_is_on
            and forecast_power_w >= self.relevant_pv_power_w
        )

        last = self._parse_dt(
            self.learning.get("last_runtime_sample")
        )
        previous_curtailing = bool(
            self.learning.get("runtime_curtailing", False)
        )

        if curtailing and not previous_curtailing:
            stats["events"] = int(stats.get("events", 0) or 0) + 1

        if last is not None and curtailing:
            elapsed_min = max(
                0.0,
                min(
                    (now - last).total_seconds() / 60.0,
                    self.update_minutes * 2.5,
                ),
            )
            energy = (
                max(0.0, forecast_power_w) / 1000.0
                * elapsed_min / 60.0
            )
            stats["curtailed_minutes"] = (
                float(stats.get("curtailed_minutes", 0.0) or 0.0)
                + elapsed_min
            )
            stats["missed_kwh"] = (
                float(stats.get("missed_kwh", 0.0) or 0.0)
                + energy
            )
            if price is not None:
                stats["benefit_eur"] = (
                    float(stats.get("benefit_eur", 0.0) or 0.0)
                    + (-energy * price)
                )

        self.learning["runtime_curtailing"] = curtailing
        self.learning["last_runtime_sample"] = self._iso(now)
        self.learning["daily_stats"] = daily
        self._save_learning()

    def _today_stats(self, now):
        daily = self.learning.get("daily_stats", {})
        if not isinstance(daily, dict):
            return {}
        return daily.get(now.date().isoformat(), {}) or {}

    def _calculate_net_surplus_score(self, market_price, forecast_power_w, grid_export_kw, sustainability):
        if market_price is None: price_score=0.0
        elif market_price <= 0: price_score=100.0
        elif market_price <= 0.03: price_score=85.0
        elif market_price <= 0.08: price_score=60.0
        elif market_price <= 0.12: price_score=35.0
        elif market_price <= 0.18: price_score=15.0
        else: price_score=0.0
        pv_score=max(0.0,min(100.0,forecast_power_w/max(self.peak_power_w,1.0)*100.0))
        export_score=max(0.0,min(100.0,grid_export_kw/4.0*100.0))
        sustainability_score=max(0.0,min(100.0,sustainability/10.0))
        return max(0.0,min(100.0,price_score*0.35+pv_score*0.30+export_score*0.20+sustainability_score*0.15))

    @staticmethod
    def _net_status(score):
        if score >= 81: return "Zeer hoog overschot"
        if score >= 61: return "Hoog overschot"
        if score >= 41: return "Verhoogd overschot"
        if score >= 21: return "Licht overschot"
        return "Normaal"

    @staticmethod
    def _consumption_advice(favorable, net_score, current_price, threshold, grid_export_kw):
        if current_price is not None and current_price <= threshold: return "Zeer gunstig voor flexibel verbruik: het actuele tarief ligt op of onder de PV-afschakeldrempel."
        if grid_export_kw >= 2.0 and net_score >= 60: return "Gunstig voor flexibel verbruik: hoge lokale teruglevering en een verhoogde netoverschot-score."
        if favorable: return "Gunstig moment voor flexibel elektrisch verbruik wanneer dit praktisch uitkomt."
        if net_score >= 40: return "Neutraal tot licht gunstig. Geen sterke reden om verbruik speciaal naar dit moment te verschuiven."
        return "Geen bijzonder overschot. Flexibel verbruik hoeft niet naar dit moment te worden verschoven."

    def _data_quality(self, quarter_prices, solcast, current_price) -> Tuple[str,List[str]]:
        missing=[]
        if not quarter_prices: missing.append("Zonneplan kwartierforecast")
        if not solcast: missing.append("Solcast detailedForecast")
        if current_price is None: missing.append("actueel Zonneplan kwartiertarief")
        if not missing: return "goed",[]
        if len(missing)==1: return "beperkt",missing
        return "onvoldoende",missing

    @staticmethod
    def _status_text(data_quality, curtailment_expected, next_window, automatic_enabled, manual_blocked):
        if data_quality=="onvoldoende": return "Onvoldoende data"
        if manual_blocked: return "Handmatig geblokkeerd"
        if not automatic_enabled: return "Automatische regeling uit"
        if curtailment_expected and next_window is not None: return "Afschakeling verwacht"
        if data_quality=="beperkt": return "Beperkte forecastdata"
        return "Normaal"

    def _update_planner(self, now, slots, corrected_solcast, forecast_mape):
        """Use the same corrected slots and curtailment policy as PVGridForecast."""
        if not corrected_solcast:
            self._planner_publish("status", "insufficient_data")
            self._planner_publish("reason", "Gecorrigeerde Solcast-forecast ontbreekt")
            self._planner_clear_advice()
            return
        import_kw = self._float_state(self.grid_import_entity, None)
        export_kw = self._float_state(self.grid_export_entity, None)
        actual_pv = self._float_state(self.ecu_power_entity, None)
        grid_w = (import_kw - export_kw) * 1000 if import_kw is not None and export_kw is not None else None
        live_load = max(0.0, actual_pv + grid_w) if actual_pv is not None and grid_w is not None else None
        sample_slot = (now.date().isoformat(), now.hour, now.minute // 15)
        if sample_slot != self._planner_last_sample_slot and live_load is not None:
            self.planner.observe_load(now, actual_pv, grid_w)
            self._planner_last_sample_slot = sample_slot
            self._save_learning()
        recalc = max(1, int(self.args.get("recalculate_interval_minutes", 15)))
        if self._planner_last_run and (now - self._planner_last_run).total_seconds() < recalc * 60:
            return
        self._planner_last_run = now
        if live_load is None:
            self._planner_publish("status", "insufficient_data")
            self._planner_publish("reason", "Actuele ECU- of P1-meting ontbreekt")
            self._planner_clear_advice()
            return
        planner_slots = [dict(slot) for slot in slots]
        # Current actual export compensation takes precedence over the
        # forecast estimate. Future quarters retain PVGridForecast's model.
        current_export = self._float_state(self.export_price_entity, None)
        if current_export is not None:
            for slot in planner_slots:
                if slot["start"] <= now < slot["end"]:
                    slot["effective_export_price_eur_kwh"] = current_export
        price_fallback = False
        if not planner_slots and corrected_solcast:
            # PV-only advice from the *same* corrected Solcast data. No price
            # forecast is invented; zero is a neutral scoring placeholder.
            price_fallback = True
            cursor = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
            end = now + timedelta(hours=int(self.args.get("planning_horizon_hours", 24)))
            while cursor < end:
                forecast = self._solcast_at(cursor, corrected_solcast)
                if forecast:
                    planner_slots.append({"start": max(cursor, now), "end": cursor + timedelta(minutes=15),
                                          "pv_power_kw": forecast["pv_estimate_kw_corrected"],
                                          "price_eur_kwh": 0.0, "effective_export_price_eur_kwh": 0.0,
                                          "should_curtail": False})
                cursor += timedelta(minutes=15)
        try:
            profiles = self.planner.learned_power_profiles(
                self._resolve_device_profiles(self.planner_devices, self.get_state))
            plan = self.planner.plan(
                now, planner_slots, profiles, live_load, actual_pv, grid_w,
                self._is_on(self.automatic_entity), self._is_on(self.manual_block_entity),
                forecast_mape,
            )
        except Exception as exc:
            self.log("PVPlanner berekening mislukt: %s", exc, level="WARNING")
            self._planner_publish("status", "data_error")
            self._planner_publish("reason", str(exc))
            self._planner_clear_advice()
            return
        if price_fallback and plan["status"] != "insufficient_data":
            plan["status"] = "degraded"
            plan["reason"] = "Zonneplan-prijsforecast ontbreekt; advies uitsluitend op PV"
            for item in plan["devices"].values():
                item["cost_eur"] = None
                item["grid_cost_eur"] = None
                item["foregone_export_eur"] = None
                item["savings_vs_now_eur"] = None
        elif any(slot.get("effective_export_price_eur_kwh") is None for slot in planner_slots) and plan["status"] == "ready":
            plan["status"] = "degraded"
            plan["reason"] = "Terugleverprijs ontbreekt voor een deel van de forecast"
        self._planner_publish("status", plan["status"])
        self._planner_publish("reason", plan.get("reason", "Planning bijgewerkt"))
        self._planner_publish("expected_pv", plan.get("expected_pv_w"))
        self._planner_publish("expected_house_load", plan.get("expected_house_load_w"))
        self._planner_publish("expected_surplus", plan.get("expected_surplus_w"))
        self._planner_publish("forecast_horizon", plan.get("horizon_hours"))
        self._planner_publish("confidence", plan.get("confidence", "low"))
        self._planner_publish("last_update", self._iso(now))
        schedule = [{"device": x["device"], "start": self._iso(x["start"]), "end": self._iso(x["end"])} for x in plan["schedule"]]
        self._planner_publish("schedule", len(schedule), {"timeline": schedule})
        if plan["status"] == "insufficient_data":
            self._planner_clear_advice()
        for key, item in plan["devices"].items():
            prefix = f"{key}_"
            fields = {
                "best_start": self._iso(item["start"]) if item.get("start") else None,
                "best_end": self._iso(item["end"]) if item.get("end") else None,
                "window_start": self._iso(item["window_start"]) if item.get("window_start") else None,
                "window_end": self._iso(item["window_end"]) if item.get("window_end") else None,
                "score": item.get("score"), "expected_energy": item.get("energy_kwh"),
                "expected_pv_energy": item.get("pv_kwh"), "expected_grid_energy": item.get("grid_kwh"),
                "pv_share": item.get("pv_share_pct"), "expected_cost": item.get("cost_eur"),
                "grid_cost": item.get("grid_cost_eur"), "foregone_export": item.get("foregone_export_eur"),
                "savings": item.get("savings_vs_now_eur"), "extra_pv_vs_now": item.get("extra_pv_vs_now_kwh"),
                "confidence": item.get("confidence", "low"), "status": item.get("status"),
                "reason": item.get("reason"),
            }
            for field, value in fields.items():
                self._planner_publish(prefix + field, value)
        self._planner_clear_inactive(profiles)
        self._save_learning()

    def _planner_clear_inactive(self, profiles):
        fields = ("best_start", "best_end", "window_start", "window_end", "score",
                  "expected_energy", "expected_pv_energy", "expected_grid_energy",
                  "pv_share", "expected_cost", "grid_cost", "foregone_export",
                  "savings", "extra_pv_vs_now", "confidence")
        for key, profile in profiles.items():
            if profile.get("enabled", False):
                continue
            for field in fields:
                self._planner_publish(f"{key}_{field}", None)
            self._planner_publish(f"{key}_status", "disabled")
            self._planner_publish(f"{key}_reason", "Geen vraag of helperwaarde ongeldig")

    def _planner_clear_advice(self):
        for field in ("expected_pv", "expected_house_load", "expected_surplus", "forecast_horizon", "confidence"):
            self._planner_publish(field, None)
        self._planner_publish("schedule", "unknown", {"timeline": []})
        for key, profile in self.planner_devices.items():
            if profile.get("enabled", False) or profile.get("enabled_entity"):
                for field in ("best_start", "best_end", "window_start", "window_end", "score",
                              "expected_energy", "expected_pv_energy", "expected_grid_energy",
                              "pv_share", "expected_cost", "grid_cost", "foregone_export", "savings", "extra_pv_vs_now", "confidence"):
                    self._planner_publish(f"{key}_{field}", None)
                self._planner_publish(f"{key}_status", "insufficient_data")
                self._planner_publish(f"{key}_reason", "Brondata ontbreekt")

    def _planner_publish(self, key, state, attributes=None):
        base = str(self.args.get("planner_mqtt_base_topic", "pv/self_consumption_planner")).strip("/")
        self._mqtt_publish(f"{base}/{key}/state", "unknown" if state is None else str(state), True)
        self._mqtt_publish(f"{base}/{key}/attributes", json.dumps(attributes or {}, ensure_ascii=False), True)

    def _publish_planner_discovery(self):
        prefix = str(self.args.get("mqtt_discovery_prefix", "homeassistant")).strip("/")
        base = str(self.args.get("planner_mqtt_base_topic", "pv/self_consumption_planner")).strip("/")
        device_id = str(self.args.get("planner_mqtt_device_id", "pv_self_consumption_planner"))
        device = {"identifiers": [device_id], "name": self.args.get("planner_mqtt_device_name", "PV Self-Consumption Planner"), "manufacturer": "dkwolf1 / AppDaemon", "model": "PV planner", "sw_version": self.VERSION}
        sensors = {
            "status": ("Status", None), "reason": ("Reden", None),
            "expected_pv": ("Verwachte PV", "W"), "expected_house_load": ("Verwachte basisbelasting", "W"),
            "expected_surplus": ("Verwacht PV-overschot", "W"), "forecast_horizon": ("Forecast horizon", "h"),
            "confidence": ("Betrouwbaarheid", None), "last_update": ("Laatste update", None),
            "schedule": ("Planning", None),
        }
        labels = {"best_start": "Beste start", "best_end": "Beste einde", "window_start": "Venster start",
                  "window_end": "Venster einde", "score": "Score", "expected_energy": "Verwachte energie",
                  "expected_pv_energy": "Verwachte PV-energie", "expected_grid_energy": "Verwachte netenergie",
                  "pv_share": "PV-aandeel", "expected_cost": "Economische kosten", "grid_cost": "Netkosten", "foregone_export": "Gemiste terugleveropbrengst", "savings": "Besparing t.o.v. nu",
                  "extra_pv_vs_now": "Extra PV t.o.v. nu", "confidence": "Betrouwbaarheid", "status": "Status", "reason": "Reden"}
        units = {"expected_energy": "kWh", "expected_pv_energy": "kWh", "expected_grid_energy": "kWh",
                 "pv_share": "%", "expected_cost": "EUR", "grid_cost": "EUR", "foregone_export": "EUR", "savings": "EUR", "extra_pv_vs_now": "kWh"}
        for key, profile in self.planner_devices.items():
            if not profile.get("enabled", False) and not profile.get("enabled_entity"):
                continue
            name = profile.get("name", key)
            sensors.update({f"{key}_{field}": (f"{name} {label}", units.get(field)) for field, label in labels.items()})
        for key, (name, unit) in sensors.items():
            payload = {"name": name, "unique_id": f"{device_id}_{key}", "default_entity_id": f"sensor.pv_planner_{key}",
                       "state_topic": f"{base}/{key}/state", "json_attributes_topic": f"{base}/{key}/attributes",
                       "availability_topic": f"{base}/availability", "payload_available": "online", "payload_not_available": "offline", "device": device}
            if unit:
                payload["unit_of_measurement"] = unit
            if key.endswith(("_start", "_end")) or key == "last_update":
                payload["device_class"] = "timestamp"
            self._mqtt_publish(f"{prefix}/sensor/{device_id}_{key}/config", json.dumps(payload), True)
        self._mqtt_publish(f"{base}/availability", "online", True)

    def _publish_discovery(self) -> None:
        device={"identifiers":[self.device_id],"name":self.device_name,"manufacturer":"dkwolf1 / AppDaemon","model":"PVGridForecast","sw_version":self.VERSION}
        sensors={
            "status":{"name":"Status","icon":"mdi:solar-power-variant"},
            "netoverschot_score":{"name":"Netoverschot score","unit_of_measurement":"%","state_class":"measurement","icon":"mdi:transmission-tower"},
            "netstatus":{"name":"Netstatus","icon":"mdi:transmission-tower"},
            "verbruiksadvies":{"name":"Verbruiksadvies","icon":"mdi:lightbulb-on-outline"},
            "pv_volgende_afschakeling":{"name":"PV volgende afschakeling","device_class":"timestamp","icon":"mdi:solar-power"},
            "pv_afschakeling_einde":{"name":"PV afschakeling einde","device_class":"timestamp","icon":"mdi:solar-power"},
            "pv_afschakeling_duur":{"name":"PV afschakeling duur","unit_of_measurement":"min","device_class":"duration","state_class":"measurement","icon":"mdi:timer-outline"},
            "pv_negatieve_kwartieren_24h":{"name":"PV negatieve kwartieren 24h","unit_of_measurement":"kwartieren","state_class":"measurement","icon":"mdi:clock-alert-outline"},
            "pv_relevante_afschakelkwartieren_24h":{"name":"PV relevante afschakelkwartieren 24h","unit_of_measurement":"kwartieren","state_class":"measurement","icon":"mdi:weather-sunny-alert"},
            "pv_forecast_vandaag":{"name":"PV forecast vandaag","unit_of_measurement":"kWh","device_class":"energy","icon":"mdi:solar-power"},
            "pv_forecast_morgen":{"name":"PV forecast morgen","unit_of_measurement":"kWh","device_class":"energy","icon":"mdi:solar-power"},
            "pv_forecast_resterend_vandaag":{"name":"PV forecast resterend vandaag","unit_of_measurement":"kWh","device_class":"energy","icon":"mdi:solar-power"},
            "pv_productie_tijdens_afschakeling":{"name":"PV productie tijdens afschakeling","unit_of_measurement":"kWh","device_class":"energy","icon":"mdi:solar-power"},
            "pv_gemiste_productie_verwacht":{"name":"PV gemiste productie verwacht","unit_of_measurement":"kWh","device_class":"energy","icon":"mdi:solar-power"},
            "pv_gemiste_productie_p10":{"name":"PV gemiste productie P10","unit_of_measurement":"kWh","device_class":"energy","icon":"mdi:solar-power"},
            "pv_gemiste_productie_p90":{"name":"PV gemiste productie P90","unit_of_measurement":"kWh","device_class":"energy","icon":"mdi:solar-power"},
            "pv_financieel_effect_verwacht":{"name":"PV financieel effect verwacht","unit_of_measurement":"EUR","device_class":"monetary","icon":"mdi:currency-eur"},
            "pv_financieel_effect_p10":{"name":"PV financieel effect P10","unit_of_measurement":"EUR","device_class":"monetary","icon":"mdi:currency-eur"},
            "pv_financieel_effect_p90":{"name":"PV financieel effect P90","unit_of_measurement":"EUR","device_class":"monetary","icon":"mdi:currency-eur"},
            "beste_verbruiksmoment_start":{"name":"Beste verbruiksmoment start","device_class":"timestamp","icon":"mdi:clock-start"},
            "beste_verbruiksmoment_einde":{"name":"Beste verbruiksmoment einde","device_class":"timestamp","icon":"mdi:clock-end"},
            "beste_verbruiksmoment_score":{"name":"Beste verbruiksmoment score","unit_of_measurement":"%","icon":"mdi:lightning-bolt-circle"},
            "pv_forecast_tijdlijn":{"name":"PV forecast tijdlijn","unit_of_measurement":"slots","icon":"mdi:chart-timeline-variant"},
            "pv_forecast_correctiefactor":{"name":"PV forecast correctiefactor","state_class":"measurement","icon":"mdi:chart-bell-curve-cumulative"},
            "pv_forecast_nauwkeurigheid":{"name":"PV forecast nauwkeurigheid","unit_of_measurement":"%","icon":"mdi:target"},
            "zonneplan_historie_dagen":{"name":"Zonneplan historie dagen","unit_of_measurement":"d","icon":"mdi:database-clock"},
            "zonneplan_verbruik_jaar":{"name":"Zonneplan verbruik jaar","unit_of_measurement":"kWh","device_class":"energy","state_class":"total_increasing","icon":"mdi:transmission-tower-import"},
            "zonneplan_teruggeleverd_jaar":{"name":"Zonneplan teruggeleverd jaar","unit_of_measurement":"kWh","device_class":"energy","state_class":"total_increasing","icon":"mdi:transmission-tower-export"},
            "pv_forecast_learning_samples":{"name":"PV forecast learning samples","unit_of_measurement":"samples","icon":"mdi:counter"},
            "pv_forecast_bias":{"name":"PV forecast bias","unit_of_measurement":"%","icon":"mdi:chart-bell-curve"},
            "pv_forecast_mae":{"name":"PV forecast MAE","unit_of_measurement":"W","device_class":"power","icon":"mdi:chart-line"},
            "pv_forecast_rmse":{"name":"PV forecast RMSE","unit_of_measurement":"W","device_class":"power","icon":"mdi:chart-line-variant"},
            "pv_observed_ac_ceiling":{"name":"PV observed AC ceiling","unit_of_measurement":"W","device_class":"power","state_class":"measurement","icon":"mdi:speedometer"},
            "pv_afgeschakeld_vandaag_minuten":{"name":"PV afgeschakeld vandaag","unit_of_measurement":"min","device_class":"duration","state_class":"total_increasing","icon":"mdi:timer-off-outline"},
            "pv_afgeschakeld_vandaag_kwh":{"name":"PV gemiste productie vandaag","unit_of_measurement":"kWh","device_class":"energy","state_class":"total_increasing","icon":"mdi:solar-power"},
            "pv_afschakeling_voordeel_vandaag":{"name":"PV afschakeling voordeel vandaag","unit_of_measurement":"EUR","device_class":"monetary","state_class":"total","icon":"mdi:cash-plus"},
            "pv_afschakel_events_vandaag":{"name":"PV afschakel events vandaag","unit_of_measurement":"events","state_class":"total_increasing","icon":"mdi:counter"},
            "netto_vermogen":{"name":"Netto vermogen","unit_of_measurement":"kW","device_class":"power","state_class":"measurement","icon":"mdi:transmission-tower-import"},
            "pv_effectieve_terugleverprijs":{"name":"PV effectieve terugleverprijs","unit_of_measurement":"EUR/kWh","state_class":"measurement","icon":"mdi:cash-sync"},
            "pv_waarde_vandaag":{"name":"PV waarde vandaag","unit_of_measurement":"EUR","device_class":"monetary","state_class":"total","icon":"mdi:cash-multiple"},
            "pv_eigen_verbruik_besparing_vandaag":{"name":"PV eigen verbruik besparing vandaag","unit_of_measurement":"EUR","device_class":"monetary","state_class":"total_increasing","icon":"mdi:home-lightning-bolt-outline"},
            "pv_terugleveropbrengst_vandaag":{"name":"PV terugleveropbrengst vandaag","unit_of_measurement":"EUR","device_class":"monetary","state_class":"total","icon":"mdi:transmission-tower-export"},
            "pv_afschakeling_besparing_vandaag":{"name":"PV afschakeling besparing vandaag","unit_of_measurement":"EUR","device_class":"monetary","state_class":"total_increasing","icon":"mdi:cash-plus"},
            "pv_afschakeling_kosten_vandaag":{"name":"PV afschakeling kosten vandaag","unit_of_measurement":"EUR","device_class":"monetary","state_class":"total_increasing","icon":"mdi:cash-minus"},
            "pv_afschakeling_netto_vandaag":{"name":"PV afschakeling netto vandaag","unit_of_measurement":"EUR","device_class":"monetary","state_class":"total","icon":"mdi:scale-balance"},
        }
        binaries={
            "pv_afschakeling_verwacht":{"name":"PV afschakeling verwacht","icon":"mdi:solar-power"},
            "pv_afschakeling_binnen_1_uur":{"name":"PV afschakeling binnen 1 uur","icon":"mdi:clock-alert-outline"},
            "stroom_verbruiken_gunstig":{"name":"Stroom verbruiken gunstig","icon":"mdi:flash"},
        }
        # v1.1.1 migration: remove retained discovery config for the old
        # generic status entity (unique_id pv_grid_forecast_status).
        self._mqtt_publish(
            f"{self.discovery_prefix}/sensor/{self.device_id}_status/config",
            "",
            True,
        )

        for key,extra in sensors.items():
            if key == "status":
                discovery_object = f"{self.device_id}_main_status"
                unique_id = f"{self.device_id}_main_status"
                default_entity_id = "sensor.pv_grid_forecast_status"
            else:
                discovery_object = f"{self.device_id}_{key}"
                unique_id = f"{self.device_id}_{key}"
                default_entity_id = f"sensor.{key}"

            payload={
                "name":extra["name"],
                "unique_id":unique_id,
                "default_entity_id":default_entity_id,
                "state_topic":f"{self.mqtt_base}/{key}/state",
                "json_attributes_topic":f"{self.mqtt_base}/{key}/attributes",
                "availability_topic":f"{self.mqtt_base}/availability",
                "payload_available":"online",
                "payload_not_available":"offline",
                "device":device
            }
            for opt in ("unit_of_measurement","device_class","state_class","icon"):
                if opt in extra: payload[opt]=extra[opt]
            self._mqtt_publish(
                f"{self.discovery_prefix}/sensor/{discovery_object}/config",
                json.dumps(payload),
                True,
            )
        for key,extra in binaries.items():
            payload={"name":extra["name"],"unique_id":f"{self.device_id}_{key}","default_entity_id":f"binary_sensor.{key}","state_topic":f"{self.mqtt_base}/{key}/state","json_attributes_topic":f"{self.mqtt_base}/{key}/attributes","payload_on":"ON","payload_off":"OFF","availability_topic":f"{self.mqtt_base}/availability","payload_available":"online","payload_not_available":"offline","device":device,"icon":extra.get("icon")}
            self._mqtt_publish(f"{self.discovery_prefix}/binary_sensor/{self.device_id}_{key}/config",json.dumps(payload),True)
        self._mqtt_publish(f"{self.mqtt_base}/availability","online",True)

    def _publish_timeline_if_changed(self, key, state, attributes=None):
        """Publish the large timeline only when state/attributes really changed."""
        attributes = attributes or {}
        canonical = json.dumps(
            {"state": state, "attributes": attributes},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()

        if digest == self._last_timeline_hash:
            return

        self._last_timeline_hash = digest
        self._publish_sensor(key, state, attributes)

    def _publish_sensor(self,key,state,attributes=None):
        if state is None: state="None"
        self._mqtt_publish(f"{self.mqtt_base}/{key}/state",str(state),True)
        self._mqtt_publish(f"{self.mqtt_base}/{key}/attributes",json.dumps(attributes or {},ensure_ascii=False,default=str),True)

    def _publish_binary(self,key,state,attributes=None):
        self._mqtt_publish(f"{self.mqtt_base}/{key}/state","ON" if state else "OFF",True)
        self._mqtt_publish(f"{self.mqtt_base}/{key}/attributes",json.dumps(attributes or {},ensure_ascii=False,default=str),True)

    def _mqtt_publish(self,topic,payload,retain=False):
        self.call_service("mqtt/publish",topic=topic,payload=payload,qos=0,retain=bool(retain))

    def _attrs(self,entity):
        try:
            result=self.get_state(entity,attribute="all")
            if isinstance(result,dict) and isinstance(result.get("attributes"),dict): return result["attributes"]
        except Exception: pass
        return {}

    def _float_state(self,entity,default):
        try:
            value=self._float_or_none(self.get_state(entity)); return default if value is None else value
        except Exception: return default

    @staticmethod
    def _float_or_none(value):
        try:
            if value in (None,"","unknown","unavailable","None"): return None
            result=float(value); return result if math.isfinite(result) else None
        except (TypeError,ValueError): return None

    def _is_on(self,entity):
        try: return str(self.get_state(entity)).lower()=="on"
        except Exception: return False

    def _pv_is_on(self):
        switch_on=self._is_on(self.pv_switch_entity); return (not switch_on) if self.pv_switch_inverted else switch_on

    @staticmethod
    def _as_bool(value):
        return value if isinstance(value,bool) else str(value).strip().lower() in {"1","true","yes","on","ja"}

    @staticmethod
    def _parse_dt(value):
        if value is None: return None
        if isinstance(value,datetime): dt=value
        else:
            text=str(value).strip()
            if not text: return None
            if text.endswith("Z"): text=text[:-1]+"+00:00"
            try: dt=datetime.fromisoformat(text)
            except ValueError: return None
        if dt.tzinfo is None: dt=dt.astimezone()
        return dt.astimezone()

    @staticmethod
    def _iso(value): return value.astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _round_or_none(value,digits): return None if value is None else round(float(value),digits)
