# Profile Catalog

## Family model

`sensorium` uses a generic family/profile model:

- family:
  - groups a class of sensors with shared behavior
- profile:
  - selects the visible sensor identity, mode table, and default control ranges

The currently implemented family is:

- `imx`

## IMX catalog

The IMX backend currently exposes 61 selectable profiles:

`imx219`, `imx250`, `imx252`, `imx253`, `imx264`, `imx265`, `imx273`,
`imx287`, `imx290`, `imx294`, `imx296`, `imx297`, `imx304`, `imx305`,
`imx327`, `imx335`, `imx347`, `imx367`, `imx387`, `imx392`, `imx410`,
`imx412`, `imx415`, `imx420`, `imx421`, `imx422`, `imx425`, `imx426`,
`imx428`, `imx429`, `imx430`, `imx432`, `imx455`, `imx461`, `imx462`,
`imx464`, `imx477`, `imx485`, `imx492`, `imx515`, `imx519`, `imx530`,
`imx531`, `imx532`, `imx533`, `imx535`, `imx536`, `imx537`, `imx568`,
`imx571`, `imx577`, `imx585`, `imx662`, `imx664`, `imx675`, `imx676`,
`imx678`, `imx708`, `imx715`, `imx900`, `imx908`

List them directly from the repo:

```bash
./scripts/list-sensorium-sensors.sh
```

## Template groups

Many long-tail IMX profiles reuse representative mode templates so the simulator
can stay profile-rich without duplicating the whole pipeline implementation.

Current template families include:

- `imx708_wide`
- `imx477_12mp`
- `imx219_8mp`
- `imx8mp_wide`
- `imx5mp_wide`
- `imx4mp_wide`
- `imx5mp_43`
- `imx3mp_43`
- `imx2mp_fhd`
- `imx16mp_43`
- `imx20mp_43`
- `imx24mp_43`
- `imx26mp_43`
- `imx9mp_square`

These templates are intended to preserve:

- realistic-enough sensor identity
- camera-software-friendly mode negotiation
- validated raw and processed capture behavior

They are not meant to be perfect physical reproductions of each Sony sensor.

## Validation status

The catalog has been exercised with:

- full sweep:
  - detect
  - raw smoke capture
  - processed smoke capture
- targeted clean rechecks for the small set of profiles that initially failed

The first uninterrupted full run passed 56/61 profiles and isolated five misses.
After profile-default fixes and helper cleanup, those five were rerun cleanly and
passed.

For current validation procedure, see [Testing Guide](testing.md).

