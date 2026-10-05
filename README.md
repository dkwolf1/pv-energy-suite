# PV Energy Suite

**Nederlands** · [English](README.en.md)

PV Energy Suite combineert een PV- en prijsverwachting, advies voor apparaten, fasebewaking en een blueprint voor schakelen bij negatieve stroomprijzen. Elk onderdeel is apart te gebruiken.

## Begin hier

**Wil je eerst alleen testen?** Gebruik PV Grid Forecast met de geïntegreerde planner. Je hebt AppDaemon, de Home Assistant MQTT-integratie en passende prijs-, PV- en netmetingen nodig. De planner geeft advies en schakelt geen apparaten.

1. Kopieer [apps/pv_grid_forecast.py](apps/pv_grid_forecast.py) en [apps/pv_self_consumption.py](apps/pv_self_consumption.py) naar de map waarin jouw AppDaemon `apps.yaml` staat.
2. Open je **bestaande** AppDaemon `apps.yaml`. Gebruik alleen het blok `pv_grid_forecast:` uit [examples/apps.yaml.example](examples/apps.yaml.example) als voorbeeld. Overschrijf je bestaande bestand niet. Zet `planner_enabled: true` en voeg **geen** apart blok `pv_self_consumption:` toe.
3. Vervang alle gebruikte voorbeeldentiteiten en `REPLACE_ME`-waarden door je eigen entity-ID's. Controleer in Home Assistant onder **Ontwikkelaarstools → Statussen** of ze bestaan. De belangrijkste bronnen staan in de tabel hieronder.
4. Kies één manier om apparaten in te stellen: **vaste waarden in YAML** voor een snelle test, of **Home Assistant-helpers** voor bediening via een dashboard. Beide routes staan hieronder.
5. Herstart AppDaemon. Controleer de log en daarna de door MQTT Discovery aangemaakte `sensor.pv_*` en `sensor.pv_planner_*` entiteiten.

### Welke bronnen vul je zelf in?

| Bron | Nodig voor | Verwachte waarde |
|---|---|---|
| Zonneplan kwartierprijzen | Forecast en planner | `forecast`-attribuut met kwartierprijzen |
| Solcast vandaag en morgen | Forecast en planner | `detailedForecast`-attribuut |
| ECU/omvormer actueel PV-vermogen | Forecast en planner | W |
| P1 netimport en netexport | Forecast en planner | kW |
| Drempel, automatische regeling en handmatige blokkering | Voorspelling van PV-afschakeling | Home Assistant-helpers |
| Extra ECU- en Zonneplan-dagsensoren | Learning- en financiële functies van de forecast | Zie de velden in het voorbeeldbestand |

De namen in het voorbeeld zijn geen universele Home Assistant-entity-ID's. AppDaemon maakt deze **bronentiteiten niet** aan. De forecast en planner publiceren hun **uitvoer** via MQTT Discovery; maak `sensor.pv_*` en `sensor.pv_planner_*` dus niet zelf als helpers aan.

## Kies hoe je apparaten invult

Het voorbeeld bevat vaatwasser, wasmachine en droger. Programmaduur en vermogen zijn voorbeeldwaarden; meet of controleer ze voor jouw apparaat.

### Optie A — snel testen met vaste YAML-waarden

Verwijder bij één apparaat alle velden die eindigen op `_entity` uit zijn `planner_devices`-blok. Vul `duration_minutes`, `estimated_average_power_w`, `estimated_peak_power_w`, `earliest_start` en `latest_finish` in en zet `enabled: true`. Met vaste YAML-waarden blijft het apparaat elke dag in het advies staan totdat je `enabled` weer uitzet. De overige apparaten mogen `enabled: false` blijven.

### Optie B — helpers en dashboard

1. Schakel Home Assistant-packages in. Voeg onder de **bestaande** `homeassistant:`-sectie van `configuration.yaml` de regel `packages: !include_dir_named packages` toe. Bestaat die sectie nog niet, dan ziet het er zo uit:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Kopieer [examples/planner_helpers.yaml.example](examples/planner_helpers.yaml.example) naar `packages/pv_planner_helpers.yaml` in de **Home Assistant-configuratiemap**; verwijder de extensie `.example`. Herstart Home Assistant en controleer of de helpers bestaan.
3. Houd de zeven `*_entity`-verwijzingen per apparaat uit [examples/apps.yaml.example](examples/apps.yaml.example) in je **AppDaemon** `apps.yaml`. De zes basishelpers regelen vraag aan/uit, duur, gemiddeld vermogen, piekvermogen, vroegste start en uiterste eindtijd. De zevende teksthelper is optioneel voor een vermogenssensor.
4. Voeg [dashboards/planner_devices_view.yaml](dashboards/planner_devices_view.yaml) als **view** toe in een Home Assistant-dashboard. Dit is geen complete dashboardconfiguratie.
5. Vul de helpers in. Zet **Plannen** alleen aan wanneer een programma echt moet draaien; zet de knop na starten of afronden weer uit. Anders blijft het apparaat in volgend advies verschijnen.

Ontbreekt een vereiste helper of bevat een getal een ongeldige waarde, dan wordt dat apparaat niet gepland. De planner schakelt het apparaat nooit zelf in.

### Optionele vermogenssensor

Vul in de teksthelper een entity-ID zoals `sensor.vaatwasser_vermogen` in, of zet `power_sensor_entity: sensor.vaatwasser_vermogen` rechtstreeks in het apparaatprofiel. De sensor moet alleen dat apparaat meten en actueel vermogen in **W of kW** leveren. De planner bemonstert hem elke minuut. Na een afgeronde cyclus gebruikt hij het gemeten gemiddelde en waargenomen piekvermogen voor volgend advies. Tot dan gelden de handmatig ingevulde waarden; 0 W terwijl het apparaat uit staat wordt niet als programmaverbruik gebruikt. De programmaduur blijft handmatig, omdat programma's kunnen verschillen.

Voor een extra apparaat kopieer je een `planner_devices`-blok met een nieuwe sleutel. Voeg bij optie B zeven helpers met eigen ID's en een dashboardkaart toe. De sleutel wordt onderdeel van de MQTT-sensor-ID's. Kies een eindtijd binnen de ingestelde forecast-horizon.

## Andere installatiemogelijkheden

| Wat wil je gebruiken? | Kopieer naar AppDaemon | Configuratie |
|---|---|---|
| Alleen PV Grid Forecast | `pv_grid_forecast.py` | `pv_grid_forecast:` met `planner_enabled: false` |
| Forecast met planner | `pv_grid_forecast.py` en `pv_self_consumption.py` | `pv_grid_forecast:` met `planner_enabled: true` |
| Planner zonder forecast | `pv_self_consumption.py` en `pv_self_consumption_app.py` | [standalone_planner.yaml.example](examples/standalone_planner.yaml.example) als `pv_self_consumption:`-blok |
| Alleen Phase Guard | `phase_peak_guard.py` | `phase_peak_guard:` uit [apps.yaml.example](examples/apps.yaml.example) |

Gebruik **nooit** tegelijk de geïntegreerde en zelfstandige planner: ze publiceren dezelfde MQTT Discovery-ID's en topics. De zelfstandige planner leest zelf Zonneplan, Solcast, ECU en P1 en gebruikt ruwe Solcast P50 zonder de learning-correctie van PV Grid Forecast. Hij geeft eveneens alleen advies.

Phase Guard heeft drie fase-stroomsensoren in A nodig, of de gedocumenteerde vermogensfallback. Hij publiceert fasebelasting en headroom via MQTT Discovery, maar schakelt niets. De globale headroom is geen harde startbeveiliging voor een apparaat waarvan de fase onbekend is.

## Blueprint en dashboards

De [PV-blueprint](blueprints/automation/pv_negative_price_control.yaml) hoort in Home Assistant onder `blueprints/automation/`, **niet** in de AppDaemon-appsmap. Maak daarna in Home Assistant een automatisering op basis van die blueprint. Hiervoor kies je onder meer je prijssensor, PV-schakelaar, drempel, regelingshelpers, laatste schakelmoment en minimale schakeltijd. Controleer `inverted_switch` tegen je fysieke relais. De blueprint is het enige onderdeel dat de PV-schakelaar bedient.

De vier [dashboardvoorbeelden en hun entiteitenchecklist](dashboards/README.md) zijn optioneel. Ze zijn losse Lovelace-views. De forecast-view gebruikt extra frontend-kaarten; de apparaten- en Phase Guard-view niet.

## Na installatie

- Controleer de AppDaemon-log op fouten en kijk of de MQTT-apparaten online zijn.
- Controleer de bronentiteiten en hun eenheden: ECU-PV in W, P1-import/export in kW, Solcast `detailedForecast` in kW.
- Bewaar bestaande JSON-learningbestanden bij een upgrade. Geef zelfstandige apps elk hun eigen opslagpad.
- Een ontbrekende bron kan `insufficient_data` opleveren; de planner wist dan zijn actuele advies.

De losse projecten blijven beschikbaar als [PV-blueprint](https://github.com/dkwolf1/PV-blueprint), [PV-grid-forecast](https://github.com/dkwolf1/PV-grid-forecast) en [phase-peak-guard](https://github.com/dkwolf1/phase-peak-guard).
