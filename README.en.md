# PV Energy Suite

[Nederlands](README.md) · **English**

PV Energy Suite combines PV and price forecasting, appliance start-time advice, phase monitoring and a blueprint for switching PV during negative electricity prices. Each component can also be used on its own.

## Start here

**Want to try one setup first?** Use PV Grid Forecast with its integrated planner. You need AppDaemon, Home Assistant's MQTT integration, and suitable price, PV and grid sensors. The planner gives advice; it never starts appliances.

1. Copy [apps/pv_grid_forecast.py](apps/pv_grid_forecast.py) and [apps/pv_self_consumption.py](apps/pv_self_consumption.py) to the directory containing your AppDaemon `apps.yaml`.
2. Open your **existing** AppDaemon `apps.yaml`. Use only the `pv_grid_forecast:` block from [examples/apps.yaml.example](examples/apps.yaml.example) as a reference. Do not replace your whole file. Set `planner_enabled: true` and do **not** add a separate `pv_self_consumption:` block.
3. Replace the example entity IDs and every `REPLACE_ME` value you use with your own IDs. Check that they exist in Home Assistant under **Developer Tools → States**. The main inputs are listed below.
4. Choose one appliance setup: **fixed YAML values** for a quick test, or **Home Assistant helpers** for dashboard controls. Both routes are explained below.
5. Restart AppDaemon. Check its log, then check the `sensor.pv_*` and `sensor.pv_planner_*` entities created through MQTT Discovery.

### Which inputs do you provide?

| Input | Used by | Expected value |
|---|---|---|
| Zonneplan quarter-hour prices | Forecast and planner | `forecast` attribute with quarter-hour prices |
| Solcast today and tomorrow | Forecast and planner | `detailedForecast` attribute |
| ECU/inverter current PV power | Forecast and planner | W |
| P1 grid import and export | Forecast and planner | kW |
| Price threshold, automatic control and manual block | Curtailment prediction | Home Assistant helpers |
| Additional ECU and Zonneplan daily sensors | Forecast learning and financial features | See the example configuration |

Example names are not universal Home Assistant entity IDs. AppDaemon does **not** create these source entities. The forecast and planner publish **output** via MQTT Discovery; do not create `sensor.pv_*` or `sensor.pv_planner_*` manually as helpers.

## Choose how to enter appliances

The example includes dishwasher, washing machine and dryer profiles. Their program durations and power values are placeholders; check or measure values for your appliances.

### Option A — quick test with fixed YAML values

Remove every field ending in `_entity` from one appliance's `planner_devices` block. Enter `duration_minutes`, `estimated_average_power_w`, `estimated_peak_power_w`, `earliest_start` and `latest_finish`, then set `enabled: true`. With fixed YAML values, that appliance stays in the daily advice until you set `enabled` back to false. Other appliances can remain disabled.

### Option B — helpers and dashboard

1. Enable Home Assistant packages. Add `packages: !include_dir_named packages` under the **existing** `homeassistant:` section of `configuration.yaml`. If that section does not exist yet, use:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Copy [examples/planner_helpers.yaml.example](examples/planner_helpers.yaml.example) to `packages/pv_planner_helpers.yaml` in the **Home Assistant configuration directory**; remove the `.example` suffix. Restart Home Assistant and check that the helpers exist.
3. Keep the seven `*_entity` references per appliance from [examples/apps.yaml.example](examples/apps.yaml.example) in your **AppDaemon** `apps.yaml`. Six basic helpers cover planning request, duration, average power, peak power, earliest start and latest finish. The seventh text helper is optional for a power sensor.
4. Add [dashboards/planner_devices_view.yaml](dashboards/planner_devices_view.yaml) as a **view** in a Home Assistant dashboard. It is not a complete dashboard configuration.
5. Fill in the helpers. Turn **Plan** on only when a program needs to run; turn it off after starting or completing the cycle. Otherwise the appliance will continue to appear in new advice.

If a required helper is missing or a numeric value is invalid, that appliance is not planned. The planner never starts the appliance.

### Optional appliance power sensor

Enter an entity ID such as `sensor.dishwasher_power` in the text helper, or set `power_sensor_entity: sensor.dishwasher_power` directly in the appliance profile. The sensor must measure only that appliance and report current power in **W or kW**. The planner samples it once per minute. After a completed cycle, it uses the measured average and observed peak for future advice. Manual values remain the fallback until then; an idle 0 W reading is not used as predicted cycle demand. Duration remains manual because programs can differ.

To add an appliance, duplicate a `planner_devices` block with a unique key. With option B, add seven helpers with unique IDs and a dashboard card. The key becomes part of its MQTT sensor IDs. Choose a finish deadline within the forecast horizon.

## Other installation options

| What do you want to use? | Copy to AppDaemon | Configuration |
|---|---|---|
| PV Grid Forecast only | `pv_grid_forecast.py` | `pv_grid_forecast:` with `planner_enabled: false` |
| Forecast with planner | `pv_grid_forecast.py` and `pv_self_consumption.py` | `pv_grid_forecast:` with `planner_enabled: true` |
| Planner without forecast | `pv_self_consumption.py` and `pv_self_consumption_app.py` | The `pv_self_consumption:` block from [standalone_planner.yaml.example](examples/standalone_planner.yaml.example) |
| Phase Guard only | `phase_peak_guard.py` | The `phase_peak_guard:` block from [apps.yaml.example](examples/apps.yaml.example) |

**Never** run the integrated and standalone planners together: they publish the same MQTT Discovery IDs and topics. The standalone planner reads Zonneplan, Solcast, ECU and P1 itself and uses raw Solcast P50 without PV Grid Forecast's learning correction. It also gives advice only.

Phase Guard needs three phase-current sensors in A, or its documented power fallback. It publishes phase load and headroom through MQTT Discovery but switches nothing. Overall headroom is not a hard start safeguard for an appliance whose phase is unknown.

## Blueprint and dashboards

Install the [PV blueprint](blueprints/automation/pv_negative_price_control.yaml) in Home Assistant under `blueprints/automation/`, **not** in the AppDaemon apps directory. Then create an automation from that blueprint in Home Assistant. Select your price sensor, PV switch, threshold, control helpers, last switch time and minimum switch time, among other inputs. Verify `inverted_switch` against the physical relay. The blueprint is the only component that controls the PV switch.

The four [example dashboard views and entity checklist](dashboards/README.md) are optional. They are individual Lovelace views. The forecast view uses extra frontend cards; the appliance and Phase Guard views do not.

## After installation

- Check the AppDaemon log for errors and confirm the MQTT devices are online.
- Check source entities and units: ECU PV in W, P1 import/export in kW, Solcast `detailedForecast` in kW.
- Keep existing JSON learning files when upgrading. Give standalone apps separate storage paths.
- Missing source data can produce `insufficient_data`; the planner then clears its current advice.

The original projects remain available as [PV-blueprint](https://github.com/dkwolf1/PV-blueprint), [PV-grid-forecast](https://github.com/dkwolf1/PV-grid-forecast) and [phase-peak-guard](https://github.com/dkwolf1/phase-peak-guard).
