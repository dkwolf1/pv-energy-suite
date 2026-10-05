# PV Energy Suite

[Nederlands](README.md) · **English**

A modular Home Assistant toolkit for PV forecasting, self-consumption planning,
phase peak monitoring, and negative electricity price control. The components
can run independently or work together. The four example dashboard views are
in [`dashboards/`](dashboards/).

| Component | File | Purpose |
|---|---|---|
| PV switching blueprint | [`blueprints/automation/pv_negative_price_control.yaml`](blueprints/automation/pv_negative_price_control.yaml) | Controls the PV switch according to electricity prices and safety settings. It is the only component that switches the PV relay. |
| PV Grid Forecast | [`apps/pv_grid_forecast.py`](apps/pv_grid_forecast.py) | Publishes PV and price forecasts through MQTT Discovery. It can run the appliance planner internally. |
| Self-consumption planner | [`apps/pv_self_consumption.py`](apps/pv_self_consumption.py) | Calculates advisory start times for appliances. It never switches an appliance. |
| Standalone planner adapter | [`apps/pv_self_consumption_app.py`](apps/pv_self_consumption_app.py) | Runs the planner without PV Grid Forecast, using its own Solcast, Zonneplan, ECU and P1 inputs. |
| Phase Guard | [`apps/phase_peak_guard.py`](apps/phase_peak_guard.py) | Publishes phase load and remaining capacity for monitoring. It does not switch anything. |

## Install

Copy only the Python files you use into your AppDaemon apps directory:

- **Forecast only:** `pv_grid_forecast.py`.
- **Forecast with integrated planner:** `pv_grid_forecast.py` and
  `pv_self_consumption.py`; set `planner_enabled: true` under
  `pv_grid_forecast:` in `apps.yaml`.
- **Standalone planner:** `pv_self_consumption.py` and
  `pv_self_consumption_app.py`; use
  [`examples/standalone_planner.yaml.example`](examples/standalone_planner.yaml.example).
- **Phase Guard:** `phase_peak_guard.py`.

Use [`examples/apps.yaml.example`](examples/apps.yaml.example) as a starting
point for the integrated setup. Replace every `REPLACE_ME` value and check all
other example entity IDs against your Home Assistant installation. Copy the
required blocks into your own `apps.yaml`; do not replace an existing file
containing other apps. The blueprint is installed separately as a Home
Assistant automation blueprint.

Run **either** the integrated **or** the standalone planner. Both publish the
same MQTT Discovery IDs and topics, so running both creates conflicting
entities. The standalone planner uses raw Solcast P50 without PV Grid
Forecast's learning correction.

## Configure appliances in Home Assistant

The examples contain dishwasher, washing machine and dryer profiles. Their
durations and power values are placeholders: enter values for your actual
programs. The optional
[`examples/planner_helpers.yaml.example`](examples/planner_helpers.yaml.example)
defines seven helpers per appliance: a planning request toggle, duration,
average power, peak power, earliest start, latest finish, and a text field for
an optional power sensor entity ID.

To load the helpers as a Home Assistant package, add this to
`configuration.yaml` if packages are not already enabled:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Copy the helper example to `packages/pv_planner_helpers.yaml` in the Home
Assistant configuration directory, remove the `.example` suffix, and restart
Home Assistant. Add the matching `*_entity` fields from the AppDaemon example
to each appliance profile. Then add
[`dashboards/planner_devices_view.yaml`](dashboards/planner_devices_view.yaml)
as a dashboard view. Use the Python files from this package for helper support.

Turn an appliance's planning request on only when a cycle needs to run. Turn
it off after starting or completing the cycle; while it remains on, the
appliance appears in new daily advice. The planner never starts appliances.
Missing helpers or invalid numeric values prevent that appliance from being
planned. Profiles without helper references continue to use fixed YAML
values; `enabled: true` then means a standing planning request.

### Optional appliance power sensor

Enter a `sensor.*` entity ID in the text helper or set
`power_sensor_entity: sensor.your_appliance_power` directly in the appliance
profile. The sensor must report current power in **W** or **kW** and measure
only that appliance. The planner samples it once a minute and learns average
and observed peak power from completed cycles. Manual power values remain the
fallback until a full cycle has been recorded. An idle reading of 0 W is not
treated as the appliance's predicted cycle demand. Duration remains a manual
setting because programs on the same appliance can differ.

To add another appliance, duplicate a `planner_devices` profile with a unique
key, add seven helpers with unique IDs, update its seven `*_entity` references,
and duplicate the corresponding dashboard card. The profile key becomes part
of its MQTT sensor IDs. Set a deadline within the forecast horizon.

## Required inputs and generated entities

| Component | Inputs you provide | Created by this package |
|---|---|---|
| Blueprint | Current electricity price, PV switch, automatic-control and manual-block helpers, price threshold, last switch time, minimum switch time; optional temperature sensor and notification settings | No |
| PV Grid Forecast | Zonneplan quarter-hour prices, Solcast forecasts, ECU current power in W, P1 import/export in kW; additional ECU and Zonneplan day sensors for learning and finance features | MQTT Discovery forecast sensors |
| Standalone planner | Zonneplan quarter-hour prices, Solcast today/tomorrow, ECU current power, P1 import/export and shared curtailment helpers | MQTT Discovery planner sensors |
| Phase Guard | Three phase current sensors in A, or its documented power fallback | MQTT Discovery phase sensors |
| Appliance configuration | Duration, power estimate, time window and optional appliance power sensor | Planner advice sensors; the input helpers are created from the supplied Home Assistant package |

The example dashboard views and their extra frontend card requirements are
listed in [`dashboards/README.md`](dashboards/README.md). MQTT Discovery output
such as `sensor.pv_planner_*` must **not** be created manually as helpers.

## Shared behavior and verification

- The blueprint is the only writer of the PV switch. The forecast and planner
  read the shared threshold and block helpers when calculating predictions.
- ECU PV power is in W; P1 import and export are in kW. Positive grid power
  means import. Solcast `detailedForecast` is in kW.
- Phase Guard's overall headroom is informational; appliance profiles do not
  assign loads to individual phases.
- Each standalone app has its own JSON storage file. Keep existing learning
  files when upgrading, and do not share a storage path between apps.

After installing, check the AppDaemon log and the MQTT devices. Verify the
blueprint's `inverted_switch` setting against the physical relay wiring.
If the standalone planner lacks source data, it publishes
`insufficient_data` and clears its advice.

The original components remain available separately in
[PV-blueprint](https://github.com/dkwolf1/PV-blueprint),
[PV-grid-forecast](https://github.com/dkwolf1/PV-grid-forecast) and
[phase-peak-guard](https://github.com/dkwolf1/phase-peak-guard).
