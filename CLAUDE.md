# Pool Pump — Home Assistant Custom Integration

HACS custom integration zur Steuerung einer Poolpumpe (DAB ESWIM 150 o.ä.) über Shelly-Geräte. Installation per HACS als Custom Repository.

## Hardware-Abstraktion

Die Pumpe wird über drei HA-Entities angesprochen, die in `config_flow.py` beim Setup ausgewählt werden:

- **Power Switch** (`switch`, z.B. Shelly 1PM) — Netzspannung
- **Speed Number** (`number` oder `light`, z.B. Shelly Dimmer 0/1-10V Gen3) — 0–100 % Drehzahl via 0-10 V
- **Start Switch** (`switch`, z.B. Shelly 1) — Trockenkontakt Start/Stop

Optional: Außentemperatur-Sensoren (mehrere, werden gemittelt), Wassertemperatur, Raumtemperatur, Frischwasser-Ventil.

### Einschalt-/Ausschalt-Sequenz (`coordinator.py`)

`async_ensure_running(speed)`:
1. Power Switch an (falls aus) → `POWER_ON_DELAY = 5 s` warten
2. Speed setzen (number.set_value oder light.turn_on mit brightness_pct je nach Domain)
3. Start Switch an

`async_ensure_stopped()`: Start Switch aus → `STOP_DELAY = 1 s` → Speed auf 0.

Alle Shelly-Calls laufen über `_call()`; im **Test Mode** werden sie nur geloggt (kein echter Service-Call).

## Zentrale Komponente: `PoolPumpCoordinator`

Hält den gesamten Zustand, den Scheduler-Loop und den Shelly-Treiber. Entities sind dünne Wrapper, die via `add_listener` / `_notify()` (Callback-Pattern) aktualisiert werden — **keine DataUpdateCoordinator-Basisklasse**, manuelle Listener.

Persistenter Zustand via `homeassistant.helpers.storage.Store` (key `pool_pump_state`): `buffered_water_temp`, `last_backwash_date`.

## Programm-Modell

Nur **ein Programm aktiv zur gleichen Zeit**. `_active_program` ist entweder:
- `None` → manuell (Nutzer steuert Speed/Pump direkt)
- `"automatik"` → Scheduler übernimmt (normal vs. frost_protection als Sub-Mode)
- Name eines benutzerdefinierten Programms (z.B. "Backwash", "Nachspülen", "Reinigen")

`async_activate_program()` stoppt erst das laufende Programm, dann startet es das neue. Timed Programs (duration_min > 0) stoppen sich via `_program_timed_stop()` selbst und fallen zurück auf manuell. duration_min = 0 → läuft bis manuell gestoppt.

### Default-Programme (`DEFAULT_PROGRAMS`)
- Backwash: 70 %, 3 min
- Nachspülen: 50 %, 1 min
- Reinigen: 40 %, 5 min

Pro Programm wird dynamisch ein `TimedProgramSwitch` erstellt (in `switch.py`, Slug für unique_id inkl. Umlaut-Mapping).

## Automatik-Scheduler

Läuft als Background-Task alle `SCHEDULER_INTERVAL = 60 s`. Aktiv nur wenn `_active_program == MODE_AUTOMATIK`. Ruft `_evaluate()` auf → entscheidet zwischen **Normal** und **Frost Protection**.

### Frostschutz

Trigger: Außentemperatur unter einer konfigurierten Schwelle **oder** `winter_override`-Switch.

`_find_threshold()` wählt die strengste Schwelle, die die aktuelle Temperatur unterschreitet (sortiert nach `below_temp` absteigend). Pro Schwelle: `interval_min`, `duration_min`, `speed`. `interval_min == 0 && duration_min == 0` → kontinuierlicher Betrieb.

Bei Schwellenwechsel während laufendem Zyklus: Speed wird angepasst, Timer ggf. neu gestartet (siehe `_handle_frost_mode`).

### Default-Thresholds (`DEFAULT_THRESHOLDS`)
| below_temp | interval | duration | speed |
|---|---|---|---|
| 4 °C | 120 min | 15 min | 20 % |
| 2 °C | 60 min | 20 min | 25 % |
| 0 °C | 45 min | 20 min | 30 % |
| -3 °C | 30 min | 25 min | 35 % |
| -7 °C | kontinuierlich | — | 40 % |
| -12 °C | kontinuierlich | — | 50 % |

### Normal-Modus

Tagesfenster `normal_window_start` … `normal_window_end` (default 08:00–22:00). Laufzeit-Bedarf: `run_hours = max(1, water_temp / temp_divisor)`, default-divisor 2.0 → 26 °C Wasser ≙ 13 h/Tag.

`_should_run_now()` versucht die Laufzeit in 3, dann 2, dann 1 Block(s) mit Pausen zu verteilen. Mindestpause `MIN_PAUSE_MINUTES = 30` — ist die Pause kürzer, wird ein Block weniger probiert (am Ende: durchlaufen). Blöcke und Gaps sind gleichmäßig über das Fenster verteilt mit halben Gaps am Rand.

### Wassertemperatur-Puffer

Live-Wassertemperatur (z.B. im Filterkreis) ist nur dann aussagekräftig, wenn die Pumpe bereits läuft. Deshalb: `_sample_water_temp_if_eligible()` speichert die Live-Temperatur **nur nach `MIN_RUN_FOR_SAMPLE = 300 s` Laufzeit** in `_buffered_water_temp`. Diese gepufferte Temperatur ist die Basis für die Laufzeitberechnung im Normal-Modus.

Temperaturen werden plausibilisiert (`TEMP_MIN_PLAUSIBLE=-30`, `TEMP_MAX_PLAUSIBLE=60`), unplausible Werte → `None`.

## Rückspülungs-Erinnerung

Tägliche persistente Notification, wenn `days_since_backwash >= backwash_interval_days` (default 14). De-Duplizierung via `_last_notification_date`. Wird das konfigurierte Backwash-Programm (`backwash_program_name`, default "Backwash") ausgeführt, resettet sich der Zähler automatisch. Zusätzlich: **Backwash Reset Button** (`button.py`) und Konfiguration in Automatik-Einstellungen.

## Frischwasser

Optional. Schaltet einen Ventilschalter an → Timer (`freshwater_duration`, default 30 min) → schaltet automatisch wieder aus. Manuelles Abschalten bricht den Timer ab. Entity wird nur erzeugt, wenn `freshwater_switch` konfiguriert ist.

## HA-Entities

Alle Entities teilen sich ein einziges `device_info` (identifiers = `(DOMAIN, entry_id)`, primary entity = `PumpSwitch` mit Name/Manufacturer).

**switch.py** (`PLATFORMS` in `__init__.py`)
- `PumpSwitch` — manuelles Ein/Aus (Turn-Off cancelt aktives Programm)
- `AutomatikProgramSwitch` — aktiviert Scheduler
- `TimedProgramSwitch` — einer pro Programm, dynamisch erzeugt
- `FreshwaterSwitch` — nur wenn konfiguriert
- `WinterOverrideSwitch` — schreibt in `entry.options` (→ Reload via `async_update_listener`)

**number.py**
- `PoolPumpSpeed` — Slider 0–100 %. **Ignoriert Writes, solange Automatik aktiv** (Log-Message, kein Error).

**sensor.py**
- `PoolPumpStatus` — "stopped" oder "running (X%)"
- `PoolPumpMode` — "manual" / "automatik (normal|frost_protection)" / Programmname
- `TestModeSensor`, `BackwashCountdown` (mit extra_state_attributes), `BufferedWaterTemp`, ggf. `OutsideTemperature`, `LiveWaterTemperature`, `RoomTemperature`

**button.py**
- `BackwashResetButton` — setzt `last_backwash_date` auf heute

Das `select`-Platform wurde entfernt — Mode ist read-only Sensor (siehe Kommentar in `__init__.py`).

## Config Flow (`config_flow.py`)

**Setup-Step** (`async_step_user`): Pflicht = Power/Speed/Start-Entities; optional = Temperatur-Sensoren, Freshwater. Initiale Options werden mit `DEFAULT_THRESHOLDS` und `DEFAULT_PROGRAMS` vorbelegt.

**Options Flow** als Menu mit Untermenüs:
- **Automatik-Einstellungen** — Window-Zeiten, normal_speed, Backwash-Intervall/-Programmname, Freshwater-Dauer
- **Programme** — anzeigen / hinzufügen / bearbeiten (2-step: Auswahl → Werte) / entfernen
- **Frostschutz-Schwellen** — anzeigen / hinzufügen / bearbeiten (2-step) / entfernen
- **Testmodus**

Anzeige-Steps (`show_programs`, `winter_thresholds`) geben Markdown-Tabellen über `description_placeholders` aus — die müssen in `strings.json` / `translations/` als `{programs}` / `{thresholds}` platzhalter existieren.

## Options-Reload

`async_update_listener` ruft `async_reload(entry.entry_id)` → komplette Neu-Initialisierung. Das heißt auch: hinzugefügte Programme erzeugen beim Reload sofort neue Switch-Entities. Kein Hot-Reload ohne Entry-Reload.

## i18n

`strings.json` (source) und `translations/` (de, en). Labels und Step-Beschreibungen sind größtenteils deutsch (UI-Zielsprache).

## Wichtige Invarianten / Gotchas

- **Nur ein Programm aktiv.** `_stop_current_program()` ist Pflicht vor jedem Programmwechsel — canceled auch Program-Task und resettet Frost-State.
- **`_lock`** schützt die Shelly-Sequenz (`ensure_running`/`ensure_stopped`/`set_speed`), damit parallele Calls nicht das Timing zerschießen.
- **`entry.async_create_background_task`** wird für alle langen Tasks benutzt → HA kümmert sich ums Cleanup beim Unload.
- **Test Mode loggt nur** — beim Entwickeln unbedingt aktivieren, sonst wird die echte Pumpe geschaltet.
- **Water-Temp Buffer** überlebt HA-Restart via `Store`. Ohne Puffer-Wert: Fallback 6 h/Tag.
- **Winter-Override** und **Test-Mode** leben in `entry.options`, nicht im Coordinator-State → beim Schreiben `async_update_entry` benutzen (triggert Reload).
- **Speed-Slider während Automatik**: writes werden geschluckt (kein Error toast), Änderung nur über Options.

## Projektstruktur

```
custom_components/pool_pump/
├── __init__.py          # Entry setup, Platforms = [switch, number, sensor, button]
├── manifest.json        # version 0.6.1, iot_class local_push
├── const.py             # Alle Keys + Defaults
├── coordinator.py       # Brain: state, scheduler, Shelly driver, persist
├── config_flow.py       # Setup + Options-Menu (DE-UI)
├── switch.py            # Pump + Automatik + Programs + Freshwater + WinterOverride
├── number.py            # Speed slider
├── sensor.py            # Status/Mode/Temps/Backwash/TestMode
├── button.py            # Backwash reset
├── strings.json         # i18n source
├── translations/        # de, en
└── brand/               # Icon assets
hacs.json                # HACS manifest
```

## Arbeiten am Code

- Python 3, async/await durchgehend. Keine externen Requirements.
- Type hints sind pragmatisch (union `|`-syntax, Py 3.10+).
- Logger: `log = logging.getLogger(__name__)`, Domain `pool_pump`.
- Nach Änderungen an `const.py`/Options-Schema: Mindestens einmal `async_reload` durchspielen (über HA-UI Integration reload).
- Version in `manifest.json` bumpen, wenn HACS ein Update ziehen soll.
