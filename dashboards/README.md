# Dashboardvoorbeelden

Deze bestanden zijn **Lovelace-views** voor de ruwe YAML-editor van een view,
geen volledige dashboardconfiguraties. Maak een nieuwe view, open de ruwe
configuratie van die view en plak de inhoud van één bestand. De oorspronkelijke
lokale theme- en achtergrondinstellingen zijn weggelaten. Kaarten met een
`replace_me`-entiteit of `REPLACE_WITH_...`-waarde moet je vóór gebruik aanpassen.
Gebruik in Home Assistant **Ontwikkelaarstools → Statussen** om je entity-ID's
te vinden. Een Home Assistant-installatie kan andere entity-ID's geven, ook
voor MQTT Discovery-entiteiten als een naam al bezet is.

| View | Bestand | Vereiste app / bron | Extra kaarten |
|---|---|---|---|
| PV Grid Forecast | [`pv_grid_forecast_view.yaml`](pv_grid_forecast_view.yaml) | PV Grid Forecast, MQTT-integratie en de bronentiteiten uit [`apps.yaml.example`](../examples/apps.yaml.example) | Mushroom, Layout Card, ApexCharts Card, Fold Entity Row, card-mod |
| PV-overzicht | [`pv_overview_view.yaml`](pv_overview_view.yaml) | Eigen PV-, P1-, prijs- en Shelly-entiteiten; de blueprint is alleen nodig voor automatisch schakelen | Mushroom, Solar Forecast Card, Restriction Card, card-mod |
| Phase Guard | [`phase_guard_view.yaml`](phase_guard_view.yaml) | Phase Peak Guard, MQTT-integratie en drie fasebronnen | Geen extra kaarten |
| Apparaten plannen | [`planner_devices_view.yaml`](planner_devices_view.yaml) | Planner, MQTT-integratie en de helpers uit [`planner_helpers.yaml.example`](../examples/planner_helpers.yaml.example) | Geen extra kaarten |

De extra kaarten zijn frontend-uitbreidingen. Als die niet geïnstalleerd zijn,
toont Home Assistant voor de betreffende kaart een fout. De kaart
`energy-distribution` in het PV-overzicht gebruikt de Home Assistant
Energy-configuratie. De Solar Forecast Card gebruikt daarnaast Forecast.Solar.

## 1. PV Grid Forecast: wat maakt de app zelf?

Alle `sensor.pv_*`, `sensor.netoverschot_score`, `sensor.netstatus`,
`sensor.netto_vermogen`, `sensor.beste_verbruiksmoment_*`,
`sensor.verbruiksadvies`, `sensor.zonneplan_historie_dagen`,
`sensor.zonneplan_verbruik_jaar`, `sensor.zonneplan_teruggeleverd_jaar`,
`binary_sensor.pv_afschakeling_*` en
`binary_sensor.stroom_verbruiken_gunstig` in deze view zijn **uitvoer van
PVGridForecast via MQTT Discovery**. Maak die niet zelf als helpers aan.
De forecast heeft wel de Zonneplan-, Solcast-, ECU- en P1-bronnen nodig die je
in `apps.yaml` invult. De blueprint is voor de forecast-view niet vereist.
De grafiek gebruikt het attribuut `slots` van `sensor.pv_forecast_tijdlijn`.

## 2. PV-overzicht: wat moet je zelf hebben of invullen?

Deze view is een voorbeeld van de oorspronkelijke installatie. De suite maakt
de volgende bronentiteiten **niet** aan. Vervang de voorbeeldnamen door jouw
entity-ID's. Zonder een bron kun je de bijbehorende kaart verwijderen.

| In de view | Waar komt het vandaan? | Actie |
|---|---|---|
| `sensor.power_consumption`, `sensor.power_production` | P1-/slimme-meterintegratie, hier in kW | Kies jouw import- en exportsensor. |
| `sensor.zonneplan_current_electricity_tariff`, `sensor.zonneplan_terugleververgoeding` | Prijsintegratie | Kies de actuele importprijs en terugleververgoeding in €/kWh. |
| `sensor.energy_production_today`, `sensor.energy_production_tomorrow` | Forecast.Solar | Kies jouw prognosesensoren; deze kaart is optioneel. |
| `sensor.replace_me_ecu_current_power`, `sensor.replace_me_ecu_daily_energy_production`, `sensor.replace_me_ecu_daily_max_power`, `sensor.replace_me_ecu_inverters_online` | Eigen PV-/omvormerintegratie | Vervang alle vier door jouw sensoren. Het dagmaximum en online-aantal zijn alleen voor de hardwarekaart nodig. |
| `switch.replace_me_pv_switch`, `update.replace_me_pv_switch_firmware`, `sensor.replace_me_pv_switch_temperature`, `binary_sensor.replace_me_pv_switch_restart_required` | Eigen Shelly/relais | Vervang door jouw entiteiten. De schakelkaart bedient de switch; controleer de schakelrichting van je installatie. Firmware-, temperatuur- en herstartkaarten zijn optioneel. |
| `input_boolean.pv_automatisch_afschakelen`, `input_boolean.pv_handmatig_geblokkeerd` | Zelf aan te maken Home Assistant-helpers | Maak ze aan of vervang de ID's. Gebruik dezelfde helpers in de blueprint en de AppDaemon-configuratie. |
| `sensor.voltage_gemiddelde`, `sensor.live_stroom_saldo` | Eigen meting/template | Maak ze zelf of verwijder de bijbehorende kaarten. |
| `sensor.zonneplan_laagste_prijs_morgen`, `sensor.zonneplan_eerstvolgende_negatieve_prijs`, `binary_sensor.zonneplan_negatieve_prijs_nu`, `binary_sensor.zonneplan_negatieve_prijs_morgen`, `binary_sensor.zonneplan_negatieve_prijs_binnen_1_uur` | Eigen prijs-template of andere integratie | Maak deze extra afgeleide sensoren zelf of vervang/verwijder de kaarten. Ze worden niet door de PV-blueprint aangemaakt. |
| `sun.sun` | Ingebouwde Home Assistant Sun-integratie | Controleer of de integratie actief is. |
| `REPLACE_WITH_YOUR_FORECAST_SOLAR_DEVICE_ID` | Device-ID van jouw Forecast.Solar-device | Vul jouw ID in bij `custom:solar-forecast-card`, of verwijder die kaart. |

De waarden `inverter_max_kw: 5.0` en `solar_max_kwp: 6.0` in de Solar
Forecast Card zijn voorbeeldwaarden: pas ze aan je eigen installatie aan.
Voor de **blueprint zelf** zijn daarnaast een prijsdrempel
(`input_number`), laatste schakelmoment (`input_datetime`) en minimale
inschakeltijd (`input_number`) nodig. De blueprint staat in
[`blueprints/automation/`](../blueprints/automation/); maak de vereiste
helpers in Home Assistant aan.

## 3. Phase Guard: wat moet je zelf hebben?

Alle `sensor.phase_guard_*` en `binary_sensor.phase_guard_*` in deze view zijn
**uitvoer van PhasePeakGuard via MQTT Discovery**. Maak die niet handmatig aan.
Vul in de AppDaemon-configuratie drie fasebronnen in: bij voorkeur stroom in A,
of de gedocumenteerde vermogensfallback. De voorbeeldnamen
`sensor.REPLACE_ME_L1_CURRENT` enzovoort in
[`apps.yaml.example`](../examples/apps.yaml.example) zijn
niet echte entiteiten. Zie ook de [Phase Guard-documentatie](https://github.com/dkwolf1/phase-peak-guard#welke-entiteiten-moet-ik-invullen).

## Controle vóór gebruik

1. Zoek in de gekozen view naar `replace_me` en `REPLACE_WITH_` en vul die in.
2. Controleer of elke overige bronentity in **Ontwikkelaarstools → Statussen** bestaat.
3. Controleer de appstatus en het MQTT-device voordat je de view gebruikt.
4. Verifieer bij de PV-overzicht-view de fysieke PV-schakelrichting voordat je
   de schakelkaart bedient.
