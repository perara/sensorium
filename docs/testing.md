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

## Direct local stream loop

Feed an MP4 into the inject node:

```bash
ffmpeg \
  -hide_banner \
  -loglevel warning \
  -stream_loop -1 \
  -re \
  -i input.mp4 \
  -an -sn -dn \
  -vf "fps=30,scale=1536:864:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1536:864:(ow-iw)/2:(oh-ih)/2:black,format=bgr0" \
  -pix_fmt bgr0 \
  -f rawvideo - | \
v4l2-ctl \
  -d /dev/video0 \
  --set-fmt-video-out=width=1536,height=864,pixelformat=BGR4 \
  --stream-out-mmap=4 \
  --stream-from=-
```

Read the simulated camera back through libcamera:

```bash
source ./scripts/sensorium-common.sh
sensorium_export_libcamera_runtime

./tools/libcamera-record \
  --role raw \
  --width 1536 \
  --height 864 \
  --frames 150 \
  --fps 30 \
  --output output-rggb10.raw
```

Low-level raw capture from the camera node itself:

```bash
v4l2-ctl \
  -d /dev/video1 \
  --set-fmt-video=width=1536,height=864,pixelformat=RG10 \
  --stream-mmap=4 \
  --stream-count=150 \
  --stream-to=output-rggb10.raw
```

See [README](../README.md) for the end-to-end round-trip examples, including
MP4 re-encoding.

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
