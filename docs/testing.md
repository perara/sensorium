# Testing Guide

## Goals

The repo ships validation flows for four distinct layers:

- kernel module build and reload
- libcamera discovery
- smoke capture and recording
- catalog-wide profile validation

## Local checks

Shell syntax:

```bash
bash -n scripts/*.sh
```

Build helper tools:

```bash
./scripts/build-libcamera-capture.sh
```

Build the kernel module:

```bash
make module KDIR=/path/to/linux/build
```

## Remote checks

Single-cycle sanity:

```bash
./scripts/remote-cycle.sh
```

Full regression:

```bash
./scripts/remote-regression.sh
```

Performance and cadence:

```bash
./scripts/remote-benchmark.sh
./scripts/remote-benchmark-matrix.sh
```

Streaming and recording:

```bash
./scripts/remote-smoke-url-stream.sh
./scripts/remote-record-url-video.sh
```

## Exhaustive profile validation

Run the full profile sweep:

```bash
./scripts/remote-test-all-sensors.sh
```

The script writes a timestamped results directory under `.cache/` containing:

- `results.tsv`
- `summary.txt`
- per-sensor step logs

Useful environment knobs:

- `TEST_DETECT=0|1`
- `TEST_RAW=0|1`
- `TEST_PROCESSED=0|1`
- `TEST_RECORD=0|1`
- `RECORD_SECONDS=<n>`
- `MAX_SENSORS=<n>`

Example:

```bash
MAX_SENSORS=5 TEST_RECORD=0 ./scripts/remote-test-all-sensors.sh
```

## Recent validated result

The most recent full catalog run in this repo validated all 61 IMX profiles.
The first uninterrupted sweep exposed five misses, and all five were then
rerun cleanly and passed after default processed-size fixes and helper cleanup.

For a public rerun, prefer:

1. `./scripts/remote-test-all-sensors.sh`
2. inspect `results.tsv`
3. rerun only the failing profiles after any fix

