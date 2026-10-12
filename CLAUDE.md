# Pool Pump — Home Assistant Custom Integration

HACS custom integration zur Steuerung einer Poolpumpe (DAB E.SWIM 150 o.ä.) über Shelly-Geräte, inkl. Mit-Steuerung nachgelagerter Geräte (Wärmepumpe, Dosieranlage). Installation per HACS als Custom Repository.

## Hardware-Abstraktion

Zwei Setup-Varianten (Auswahl im ersten Config-Flow-Schritt, änderbar per **Reconfigure**):

- **Normal**: drei Entities
  - **Power Switch** (`switch`, z.B. Shelly 1PM) — Netzspannung
  - **Speed Number** (`number` oder `light`, z.B. Shelly Dimmer 0/1-10V Gen3) — 0–100 % via 0-10 V
  - **Start Switch** (`switch`, z.B. Shelly 1) — Trockenkontakt Start/Stop
- **Simple Mode** (`CONF_SIMPLE_MODE`): nur Start Switch, keine Drehzahl (Speed gilt intern als 100 %, kein Speed-Number/-Sensor).

Optional: Außentemperatur-Sensoren (mehrere, gemittelt), Wassertemperatur, Raumtemperatur (nur Anzeige), Frischwasser-Ventil.

### Shelly-Treiber (`coordinator.py`)

- `_start_pool_pump_hardware(speed)`: Power an (falls aus) → `POWER_ON_DELAY = 5 s` → Speed setzen (`number.set_value` bzw. `light.turn_on brightness_pct`) → Start an.
- `_stop_pool_pump_hardware()`: Start aus → `STOP_DELAY = 1 s` → Speed 0.
- Darüber liegen `async_ensure_running(speed)` / `async_ensure_stopped(immediate=False)` mit Downstream-Gating + Nachlauf (s.u.). Nur diese beiden von außen benutzen.
- Alle Service-Calls laufen über `_call()`; im **Test Mode** werden sie nur geloggt.

## Zentrale Komponente: `PoolPumpCoordinator`

Hält Zustand, Scheduler-Loop, Shelly-Treiber und Downstream-Steuerung. Entities sind dünne Wrapper via `add_listener` / `_notify()` — **keine DataUpdateCoordinator-Basisklasse**.

Persistenter Zustand via `Store` (key `pool_pump_state`): `buffered_water_temp`, `last_backwash_date`, `active_program`, `heat_pump_enabled`, `dosing_enabled`, `running`, `target_speed`, `program_end_at`, `nachlauf_end_at`. Wird bei jedem Start/Stop/Speed-Wechsel geschrieben.

### Restart / Reload (`_async_reconcile_hardware`)

Shelly-Relais behalten ihren Zustand über HA-Restart und Entry-Reload. Deshalb wird nach `async_at_started` der echte Hardware-Zustand übernommen (Pumpe läuft → `_running=True`, Speed aus der Speed-Entity), danach wird beendet, was unterbrochen wurde:

- Nachlauf war aktiv → Restzeit abwarten, dann stoppen
- Timed Program, Pumpe läuft → Restlaufzeit-Timer neu starten (abgelaufen → sofort regulär stoppen, inkl. Backwash-Reset)
- Timed Program, Pumpe aus (Stromausfall) → **nicht** neu starten (Backwash braucht Ventilstellung von Hand), → manuell
- Dauerprogramm (`duration_min = 0`) → wiederherstellen, ggf. neu starten
- Manuell + lief vorher → wieder anwerfen
- Automatik → wird schon in `async_setup` restauriert; der Scheduler wartet (`_ready`) bis der Abgleich durch ist.

## Programm-Modell

Nur **ein Programm aktiv**. `_active_program`: `None` (manuell), `"automatik"` oder Name eines benutzerdefinierten Programms.

`async_activate_program()` ist ein **Smooth Transition**: es cancelt nur Program-Task/Frost-State (`_cancel_program_state`), die Pumpe läuft weiter und bekommt den neuen Speed. Gestoppt wird nur beim Deaktivieren (→ manuell) bzw. Pumpe-Aus. Timed Programs (`duration_min > 0`) stoppen sich über `_program_timed_stop()` und fallen immer auf manuell zurück.

Default-Programme (`DEFAULT_PROGRAMS`): Backwash 70 %/3 min, Nachspülen 50 %/1 min, Reinigen 40 %/5 min. Pro Programm ein `TimedProgramSwitch` (Slug inkl. Umlaut-Mapping).

## Automatik-Scheduler

Background-Task alle `SCHEDULER_INTERVAL = 60 s`, aktiv nur bei `_active_program == "automatik"`. `_evaluate()` wählt **Normal** oder **Frostschutz** (`_auto_sub_mode`).

### Frostschutz

Aktiv wenn Außentemperatur unter einer Schwelle **oder** `winter_override`. Verlassen erst bei `FROST_HYSTERESIS_C = 1 °C` über der mildesten Schwelle.

`_frost_threshold_for(outside)`:
- strengste unterschrittene Schwelle (`_find_threshold`)
- keine unterschritten (Override oder Hysterese-Band) → **mildeste Schwelle** als Grund-Umwälzung
- kein Außensensor → **strengste Schwelle** (fail-safe)
- Wechsel zu einer milderen Schwelle ebenfalls erst mit 1 °C Hysterese

Pro Schwelle `interval_min`, `duration_min`, `speed`; `0/0` → kontinuierlich. Ein Zyklus läuft genau dann, wenn `_frost_cycle_start` gesetzt ist. Eine laufende Pumpe ohne Zyklus (z.B. nach Restart übernommen) wird vom nächsten Zyklus übernommen oder gestoppt.

Default-Thresholds (`DEFAULT_THRESHOLDS`): 4 °C 120/15/20 %, 2 °C 60/20/25 %, 0 °C 45/20/30 %, -3 °C 30/25/35 %, -7 °C kontinuierlich 40 %, -12 °C kontinuierlich 50 %.

### Normal-Modus

Tagesfenster `normal_window_start`…`normal_window_end` (default 08:00–22:00, **darf nicht über Mitternacht gehen**). `run_hours = max(1, water_temp / temp_divisor)` (default-Divisor 2.0, ohne Puffer-Wert 6 h). `_should_run_now()` verteilt auf 3/2/1 Blöcke mit Mindestpause `MIN_PAUSE_MINUTES = 30`, halbe Gaps am Rand. `_cached_run_hours` wird nur bei laufender Pumpe (nicht im Nachlauf) aktualisiert → kein Phantom-Restart durch Temp-Sample am Blockende.

### Wassertemperatur-Puffer

`_sample_water_temp_if_eligible()` übernimmt die Live-Temperatur erst nach `MIN_RUN_FOR_SAMPLE = 300 s` Laufzeit, und zwar direkt vor dem eigentlichen Stop. Plausibilisierung `-30…60 °C`.

## Downstream-Geräte (Wärmepumpe, Dosieranlage)

`DownstreamDevice` (dataclass): `entities`, `allowlist` (Programme), `enabled` („mit-schalten"-Switch, im Store statt in Options → kein Reload beim Umschalten), `shutdown_delay`. Co-controlled ⇔ konfiguriert ∧ enabled ∧ aktives Programm in Allowlist (manuell ist nie in der Allowlist).

- Start: Pool an → co-controlled Geräte an (turn_on immer, wegen stale states)
- Stop: co-controlled Geräte aus → **Nachlauf** `max(shutdown_delay)` → Pool aus. Während Nachlauf: `in_nachlauf`, Mode-Sensor „Nachlauf", Status bleibt an.
- `ensure_running` während Nachlauf cancelt den Stop.
- **Notaus** (`async_emergency_stop`): alles sofort aus, kein Nachlauf, → manuell.
- Neues Gerät: nur einen Eintrag in `_downstream_devices()` + Config/Switch/Strings ergänzen, keine Copy-Paste-Logik.

## Rückspülungs-Erinnerung / Frischwasser

- Tägliche persistente Notification wenn `days_since_backwash >= backwash_interval_days` (default 14), **nur solange Automatik aktiv** (sonst Winter-/Leerstand-Ruhe). Backwash-Programm-Ende oder Button resetten.
- Frischwasser: Ventil an → Timer `freshwater_duration` (default 30 min) → aus. Nur wenn konfiguriert.

## HA-Entities

Alle teilen ein `device_info` (identifiers `(DOMAIN, entry_id)`, Name/Manufacturer am `PumpSwitch`).

- **switch**: `PumpSwitch` (Aus → manuell; Ein wird bei aktiver Automatik ignoriert), `AutomatikProgramSwitch`, `TimedProgramSwitch` je Programm, `WinterOverrideSwitch` (schreibt `entry.options` → Reload), ggf. `FreshwaterSwitch`, `HeatPumpEnabledSwitch`, `DosingEnabledSwitch`
- **number**: `PoolPumpSpeed` (nicht im Simple Mode) — Writes werden bei aktiver Automatik ignoriert
- **sensor**: Mode (`Manuell`/`Automatik`/`Frostschutz`/`Nachlauf`/Programmname), Speed (Text `Aus`/`45 %`), Nächster Wechsel (Timestamp), Test mode, Backwash (Tage bis fällig), Wassertemp. gepuffert, ggf. Außen-/Live-Wasser-/Raumtemperatur
- **binary_sensor**: Status (running)
- **button**: Backwash reset, Notaus

## Config Flow (`config_flow.py`)

Setup: Normal vs. Simple → Entities. **Reconfigure** ändert die Hardware-Entities ohne Neuinstallation.

Options-Menü: Automatik-Einstellungen · Programme anzeigen/hinzufügen/bearbeiten/entfernen · Frostschutz-Schwellen anzeigen/hinzufügen/bearbeiten/entfernen · Wärmepumpen-Steuerung · Dosieranlagen-Steuerung · Testmodus. Anzeige-Steps liefern Markdown-Tabellen über `description_placeholders` (`{programs}` / `{thresholds}` müssen in `strings.json`/`translations/` existieren). `normal_temp_divisor` hat keine UI.

Jede Options-Änderung → `async_update_listener` → `async_reload`. Laufender Zustand überlebt das dank Reconcile.

## Wichtige Invarianten / Gotchas

- **Nur ein Programm aktiv.** Wechsel über `async_activate_program` / `async_deactivate_program`.
- **`_lock`** schützt die Shelly-Sequenzen; `_start/_stop_pool_pump_hardware` nur mit gehaltenem Lock aufrufen.
- **`entry.async_create_background_task`** für alle langen Tasks.
- **Test Mode loggt nur**: beim Entwickeln aktivieren, sonst schaltet die echte Pumpe.
- **Winter-Override / Test-Mode** leben in `entry.options` → `async_update_entry` (Reload). Mit-schalten-Flags leben im Store.
- Zeit immer über `dt_util.now()` (HA-Zeitzone), Laufzeiten/Intervalle über `time.monotonic()`.

## Projektstruktur

```
custom_components/pool_pump/
├── __init__.py       # Entry setup, Platforms vorgeladen (kein blocking import)
├── manifest.json     # version bumpen, damit HACS updatet
├── const.py          # Keys + Defaults
├── coordinator.py    # State, Scheduler, Shelly-Treiber, Downstream, Reconcile, Persistenz
├── config_flow.py    # Setup/Reconfigure + Options-Menü (DE-UI)
├── switch.py / number.py / sensor.py / binary_sensor.py / button.py
├── strings.json + translations/ (de, en)
└── brand/            # Icon
tools/                # lokale Hilfsskripte, nicht Teil der Integration
```

## Arbeiten am Code

- Python 3.10+, async durchgehend, keine externen Requirements, Logger `log = logging.getLogger(__name__)`.
- Keine Testsuite. Kein HA lokal installiert: Logik am besten mit gestubbten `homeassistant`-Modulen simulieren.
- Nach Änderungen an `const.py`/Options-Schema einmal Reload über die HA-UI durchspielen.
