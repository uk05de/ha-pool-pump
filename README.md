# Pool Pump

Home Assistant custom integration for controlling a pool pump via Shelly devices.

## Features

- Power sequencing (mains power → speed → start signal)
- Variable speed via 0-10V Shelly Dimmer
- Multiple programs: normal, backwash, rinse, winter, manual
- Configurable temperature-based winter mode with custom thresholds
- Configurable schedules for daily operation

## Hardware

Designed for a DAB ESWIM 150 pool pump with external control, but works with any pump that has:
- A mains power switch (e.g. Shelly 1 PM)
- A 0-10V speed input (e.g. Shelly Dimmer 0/1-10V PM Gen3)
- A dry contact start/stop input (e.g. Shelly 1)

## Installation

Install via HACS as a custom repository.
