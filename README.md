# PV Energy Suite

De vier voorbeeldviews en hun entiteitenchecklist staan in
[`dashboards/`](dashboards/). De publieke voorbeelden bevatten geen lokale
theme, achtergrond of apparaat-ID's.

Een samenhangend pakket van vier onafhankelijke Home Assistant-onderdelen:

| Onderdeel | Bestand | Werkt zelfstandig? | Samenwerking |
|---|---|---|---|
| PV-schakeling | `blueprints/automation/pv_negative_price_control.yaml` | Ja | Leest dezelfde drempel en blokkering als de forecast. Alleen dit onderdeel bedient de Shelly. |
| PV-forecast | `apps/pv_grid_forecast.py` | Ja | Levert voorspellingen en kan de planner intern draaien. |
| Verbruiksplanner | `apps/pv_self_consumption_app.py` + `apps/pv_self_consumption.py` | Ja | Kan zelfstandig Solcast, Zonneplan, ECU en P1 lezen, of als module in de forecast draaien. Geeft uitsluitend advies. |
| Phase guard | `apps/phase_peak_guard.py` | Ja | Publiceert fase-headroom voor dashboards en menselijke besluitvorming. Bedient niets. |

## Installeren

Kopieer alleen de onderdelen die je gebruikt naar de AppDaemon-appsmap. Voor de
zelfstandige planner zijn **beide** `pv_self_consumption*.py`-bestanden nodig.
Voor de forecast is `pv_self_consumption.py` alleen nodig als
`planner_enabled: true` staat. De blueprint wordt afzonderlijk in Home
Assistant geïmporteerd; er is geen AppDaemon-app voor de Shelly nodig.

Neem de benodigde blokken over uit [`examples/apps.yaml.example`](examples/apps.yaml.example).
Vul de entity-ID's en opslagpaden in. Gebruik bij de planner precies één modus:

1. **Geïntegreerd:** `pv_grid_forecast.planner_enabled: true`; laat het blok
   `pv_self_consumption:` weg.
2. **Zelfstandig:** `pv_grid_forecast.planner_enabled: false` of laat de
   forecast weg; gebruik [`standalone_planner.yaml.example`](examples/standalone_planner.yaml.example).
   De zelfstandige planner
   gebruikt ruwe Solcast P50 zonder de learning-correctie van PV Grid Forecast.

Beide modi publiceren dezelfde MQTT Discovery-ID's en topics voor de planner.
Gelijktijdig draaien veroorzaakt conflicten in Home Assistant. De planner
schakelt geen apparaat, ook niet als de forecast of phase guard is geïnstalleerd.

## Apparaten via Home Assistant instellen

De voorbeelden bevatten drie apparaatprofielen: vaatwasser, wasmachine en
droger. Hun waarden zijn **plaatsaanduidingen**, geen metingen van jouw
apparaten. Met [`planner_helpers.yaml.example`](examples/planner_helpers.yaml.example)
maak je per apparaat zeven helpers aan: vraag aan/uit, programmaduur in minuten,
gemiddeld vermogen in W, piekvermogen in W, vroegste start, uiterste eindtijd
en optioneel de entity-ID van een vermogenssensor. Het voorbeeld is een Home Assistant-package. Voeg dit toe aan
`configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Kopieer het helperbestand daarna naar `packages/pv_planner_helpers.yaml`
zonder `.example` en herstart Home Assistant.
Kopieer daarna de [apparatenview](dashboards/planner_devices_view.yaml) naar
een dashboard en gebruik de `*_entity`-velden in je AppDaemon-configuratie.
Gebruik de pakketversies van de Python-bestanden voor deze helperfunctie.

Een vermogenssensor is optioneel. Vul in de teksthelper een `sensor.*`-ID in,
of zet rechtstreeks `power_sensor_entity: sensor.jouw_apparaat_vermogen` in
het apparaatprofiel. De sensor moet actueel vermogen in **W** of **kW** meten.
De planner bemonstert hem elke minuut en gebruikt pas na een afgeronde cyclus
het gemeten gemiddelde en piekvermogen voor volgend advies. Tot dan gelden
de handmatig ingevulde waarden. Een momentane 0 W terwijl het apparaat uit
staat wordt dus niet als voorspeld programmavermogen gebruikt. De
programmaduur blijft een handmatige instelling, omdat verschillende
programma's op hetzelfde apparaat anders kunnen duren. Kies per profiel
een sensor die alleen dat apparaat meet.

Zet de vraagknop alleen aan wanneer een programma echt gepland moet worden.
Zolang de knop aan staat, verschijnt het apparaat opnieuw in het dagelijkse
advies; zet hem na starten of afronden weer uit. De planner bedient geen
apparaten. Als een helper ontbreekt of een getal ongeldig is, wordt dat
apparaat niet gepland. Een profiel zonder `*_entity`-velden blijft werken met
vaste YAML-waarden; `enabled: true` betekent dan blijvende vraag.

Voor een **vierde apparaat**: kopieer één `planner_devices`-blok, geef het een
nieuwe unieke sleutel, voeg zeven helpers met eigen ID's toe in het
helpervoorbeeld, pas de zeven `*_entity`-verwijzingen aan en kopieer de
bijbehorende kaart in de apparatenview. De sleutel is ook onderdeel van de
MQTT-sensor-ID's. Gebruik voor `duration_minutes` de duur van het gekozen
programma. Schat `estimated_average_power_w` uit gemeten kWh × 1000 gedeeld
door programmaduur in uren; meet het piekvermogen indien mogelijk apart.
Kies een deadline die binnen de ingestelde forecast-horizon valt.

## Welke entiteiten moet je zelf invullen?

De waarden in [`apps.yaml.example`](examples/apps.yaml.example) zijn
**voorbeelden**, geen automatisch aangemaakte entiteiten. Vervang elke
`REPLACE_ME`-waarde. Controleer ook de overige voorbeeld-ID's in Home Assistant:
integraties kunnen andere entity-ID's toekennen.

| Onderdeel | Zelf benodigde bron of helper | Wordt door het pakket gemaakt? |
|---|---|---|
| Blueprint | Actuele prijs (€/kWh), PV-schakelaar, automatische-regeling- en handmatige-blokkering-helpers, prijsdrempel, laatste schakelmoment, minimale schakeltijd; temperatuursensor en notificatie optioneel | Nee: kies eigen integraties en maak de helpers zelf. |
| PV Grid Forecast | Zonneplan-kwartierprijzen (`forecast`), Solcast vandaag/morgen (`detailedForecast`), ECU actueel vermogen (W), P1-import en -export (kW); voor learning en financiën ook Solcast actueel vermogen, ECU dagenergie/dagmaximum en Zonneplan dagmeters | Nee: vul deze onder `pv_grid_forecast:` in. |
| Zelfstandige verbruiksplanner | Zonneplan-kwartierprijzen, Solcast vandaag/morgen, ECU actueel vermogen en P1-import/-export; gedeelde regeling-helpers voor verwachte PV-afschakeling | Nee: vul deze onder `pv_self_consumption:` in. Een apparaatprofiel is pas bruikbaar na controle van duur, vermogen en deadline. |
| Phase Guard | Drie fase-stroomsensoren (A), of per fase een gedocumenteerde vermogensfallback | Nee: vul `current_l1/l2/l3` of de fallback in. |
| MQTT-dashboarduitvoer | `sensor.pv_*`, `sensor.pv_planner_*`, `sensor.phase_guard_*` en de bijbehorende binary sensors | **Ja**, via MQTT Discovery wanneer de betreffende app en MQTT-integratie actief zijn. Maak ze niet als helpers aan. |

De drie [dashboardviews](dashboards/README.md) hebben een aparte checklist
voor alle extra entiteiten en frontend-kaarten.

## Gedeelde contracten

- **PV-schakeling:** de blueprint is de enige schrijver van de PV-Shelly.
  De forecast en planner lezen `input_number.pv_negatieve_prijs_drempel`,
  `input_boolean.pv_automatisch_afschakelen` en
  `input_boolean.pv_handmatig_geblokkeerd` voor hun voorspelling.
- **Metingen:** ECU-PV is W; P1-import en -export zijn kW. Positief netvermogen
  betekent import. Solcast `detailedForecast` is kW. Zonneplan
  `forecast[].price_tax_included.amount` wordt gedeeld door 10.000.000 voor €/kWh.
- **Phase guard:** houdt eigen opslag en MQTT-device. Er is geen directe
  fase-toewijzing aan de apparaatprofielen; gebruik de globale headroom niet
  als harde startbeveiliging voor een onbekende fase.
- **Opslag:** elke zelfstandige app heeft een eigen JSON-bestand. Deel deze
  paden niet tussen apps. Bewaar bestaande learningbestanden bij upgrades.

## Ingebruikname

Controleer de AppDaemon-log op fouten, daarna de status van de MQTT-devices.
Verifieer de blueprint-schakelrichting (`inverted_switch`) met de werkelijke
Shelly-bedrading. De voorbeeldapparaten in de planner zijn **adviezen**;
controleer programmaduur, energie en deadline voordat je ze inschakelt.
De standalone planner heeft Zonneplan-kwartierprijzen, Solcast vandaag/morgen,
actuele ECU-productie en P1-import/-export nodig. Bij ontbrekende brondata
publiceert hij `insufficient_data` en wist hij de planning.

De originele repositories blijven bruikbaar als losse onderdelen:
[PV-blueprint](https://github.com/dkwolf1/PV-blueprint),
[PV-grid-forecast](https://github.com/dkwolf1/PV-grid-forecast) en
[phase-peak-guard](https://github.com/dkwolf1/phase-peak-guard).
Deze map is de gezamenlijke distributie. De actieve lokale configuratie staat
in `../appdaemon_apps/apps.yaml` en wordt niet als generiek voorbeeld gebruikt.
