# Testing Guide

## Goals

The repo ships validation flows for four distinct layers:

- kernel module build and reload
- libcamera discovery
- smoke capture and recording
- catalog-wide profile validation
- model and adapter validation

## Local checks

Shell syntax:

```bash
find scripts -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
```

Runtime/unit regression suite:

```bash
make test
```

Repo publishability checks:

```bash
make check
make check-ci
make check-release
```

`make check` keeps the fast local gate, while `make check-ci` enables the
stronger CI-oriented profile with benchmark regression checks and the lean QEMU
smoke gate. Both profiles allow the repo-root `.cache/` path because normal
reload, QEMU, and benchmark workflows persist local state there. Use
`make check-release` when you want the strict clean-tree gate that also rejects
repo-root cache content.

Model validation:

```bash
./scripts/runtime/sensoriumctl validate
```

Build helper tools:

```bash
./scripts/local/build-libcamera-capture.sh
```

Build the kernel module:

```bash
make module KDIR=/path/to/linux/build
```

## Remote checks

Single-cycle sanity:

```bash
./scripts/remote/remote-cycle.sh
```

Full regression:

```bash
./scripts/remote/remote-regression.sh
```

The default remote regression continues to gate camera support. Detect, stream,
and record coverage for the IMX camera path remains part of the required e2e
suite. The same regression now also includes the shipped IIO smoke paths for
`i2c`, `spi`, and `uart` by default, plus the shipped daemon-backed runtime
model.

To include the checksum-repair regression on a remote host, enable:

```bash
REMOTE_VERIFY_SYNC_REPAIR=1 ./scripts/remote/remote-regression.sh
```

The normal remote sync path now uses a manifest fingerprint gate and only runs
`rsync` when the sync manifest changes. It falls back to checksum verification
only when `REMOTE_SYNC_REPAIR=1` is set or a repair workflow explicitly
requests it.

`reload-sensorium.sh` now only pre-stops Sensorium-owned daemons and stream
injectors, and it refuses to evict non-Sensorium device holders during its
unload fallback path. Set `SENSORIUM_RELOAD_FORCE_EVICT=1` only when you
explicitly want broad holder eviction on a shared host.

## QEMU e2e

The local QEMU path reuses the same SSH-based remote scripts:

```bash
./scripts/qemu/qemu-e2e.sh
./scripts/qemu/qemu-ci-smoke.sh
```

The default QEMU e2e flow enables `QEMU_VERIFY_SYNC_REPAIR=1`, which
intentionally corrupts one guest runtime model and verifies that the ordinary
sync-plus-smoke flow repairs it before the guest smoke runs.

The QEMU wrappers also now keep a remote provisioning fingerprint by default.
If the guest was already provisioned from the same inputs, the next run skips
the full Ansible pass and goes straight to sync/reload/smoke. Set
`QEMU_SKIP_PROVISION_IF_CURRENT=0` when you want to force a fresh provision.

Extended burn-in:

```bash
./scripts/qemu/qemu-burnin.sh
```

That flow runs the full remote regression, includes runtime scale stress by
default, and then repeats camera reload plus graph/control-contract checks so
the repo has a repeatable confidence path beyond one single e2e pass.

The default lean QEMU guest is Debian `trixie` with distro-native libcamera
packages, and the default version gate now matches that path at
`libcamera >= 0.4.0`. Set `QEMU_CI_LIBCAMERA_APT_RELEASE=sid` and
`LIBCAMERA_MIN_VERSION=0.6.0` when you explicitly want the newer Debian `sid`
libcamera stream.

For explicit Linux 7 kernel compatibility evidence, run one of the dedicated
QEMU gates:

```bash
make qemu-linux7-ci-smoke
make qemu-linux7-e2e
```

Both targets use the Debian `trixie` QEMU image plus the Debian `sid` media
kernel stream and set `QEMU_EXPECT_KERNEL_MAJOR=7`, so the run fails before the
smoke/regression phase if the guest did not boot a Linux 7 kernel.

Provisioning now also installs a dma-heap udev rule so `/dev/dma_heap/system`
is usable by the non-root test user after boot and after kernel switches. That
is required for the packaged libcamera software-ISP path in QEMU.

When the processed smoke step fails, inspect the auto-printed diagnostics from
`remote-smoke-url-stream.sh` first. That log now includes the selected remote
`cam` binary, `LIBCAMERA_IPA_CONFIG_PATH`, the resolved sensor tuning YAML,
`/dev/dma_heap/system` permissions, and the recent remote kernel log.

Performance and cadence:

```bash
./scripts/remote/remote-benchmark.sh
./scripts/remote/remote-benchmark-matrix.sh
./scripts/qemu/qemu-benchmark.sh
./scripts/qemu/qemu-benchmark-matrix.sh
make benchmark
make benchmark-matrix
make benchmark-compare
make benchmark-check
```

The QEMU benchmark wrappers now also save parsed JSON artifacts under
`.cache/benchmarks/`, so before/after comparisons do not depend on copied
stdout. Compare the latest two runs with:

```bash
./scripts/benchmarks/compare-benchmarks.py
BENCHMARK_FAIL_ON_REGRESSION=1 \
BENCHMARK_MAX_FIRST_FRAME_DELTA_MS=150 \
BENCHMARK_MIN_TIMESTAMP_FPS_RATIO=0.95 \
./scripts/benchmarks/benchmark-check.sh
```

Set `SENSORIUM_BENCHMARK_DIR` to use a different artifact directory.
`benchmark-check.sh` skips missing default baselines unless
`BENCHMARK_REQUIRE_BASELINE=1` is set.

`benchmark-check.sh` now treats `timestamp_fps` as the primary sustained-rate
gate and uses `record_fps` as a whole-run context metric. If you only set
`BENCHMARK_MIN_RECORD_FPS_RATIO`, that same threshold is also applied to
`timestamp_fps` by default.

Streaming and recording:

```bash
./scripts/remote/remote-smoke-url-stream.sh
./scripts/remote/remote-record-url-video.sh
```

Those remote URL-stream flows require `ffmpeg` on the remote host. The lean
QEMU smoke profile intentionally omits it, so use a full/ops-style remote
environment for stream and record validation.

You can provision those packages explicitly:

```bash
./scripts/remote/remote-ensure-stream-deps.sh
```

Or let the stream workflows install them on demand:

```bash
REMOTE_AUTO_INSTALL_STREAM_DEPS=1 ./scripts/remote/remote-smoke-url-stream.sh
REMOTE_AUTO_INSTALL_STREAM_DEPS=1 ./scripts/remote/remote-record-url-video.sh
REMOTE_AUTO_INSTALL_STREAM_DEPS=1 ./scripts/remote/remote-benchmark.sh
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
source ./scripts/lib/sensorium-common.sh
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
./scripts/remote/remote-test-all-sensors.sh
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
MAX_SENSORS=5 TEST_RECORD=0 ./scripts/remote/remote-test-all-sensors.sh
```

## IIO smoke test

Apply the first non-camera model and read the IIO sysfs channels:

```bash
./scripts/local/smoke-iio-model.sh
```

Remote equivalent:

```bash
./scripts/remote/remote-apply-model.sh models/iio/environment-i2c.yaml
./scripts/remote/remote-smoke-iio.sh
./scripts/remote/remote-apply-model.sh models/iio/environment-spi.yaml
./scripts/remote/remote-smoke-iio.sh models/iio/environment-spi.yaml
./scripts/remote/remote-apply-model.sh models/iio/environment-uart.yaml
./scripts/remote/remote-smoke-iio.sh models/iio/environment-uart.yaml
```

For `i2c`, `spi`, and `uart`, the smoke path also verifies that the configured
transport node exists under `/dev/`, for example `/dev/i2c-1`,
`/dev/spidev0.0`, or `/dev/ttyAMA0`.

Those transport smokes now exercise real consumer-facing entry points:

- `i2c`:
  - `i2cdetect -l` adapter listing and `i2cdetect` address scan
  - `i2cset`, `i2cget`, and `i2cdump`
  - direct `I2C_RDWR` combined transfer on `/dev/i2c-N`
- `spi`:
  - `SPI_IOC_RD_*` and `SPI_IOC_WR_*` mode/bits/speed ioctls
  - `SPI_IOC_MESSAGE(1)` loopback with a small transfer
- `uart`:
  - `pyserial.Serial(...)` open on `/dev/<transport_device_name>`
  - write/flush/read loopback on the configured TTY alias

The default remote regression runs all three IIO transport models. To override
that list, set either:

- `IIO_MODELS="models/iio/environment-i2c.yaml models/iio/environment-spi.yaml"`
- `IIO_MODEL=models/iio/environment-i2c.yaml`

## Runtime multi-device smoke

Apply the shipped live runtime example:

```bash
./scripts/runtime/sensoriumctl daemon start
./scripts/runtime/sensoriumctl runtime apply ./models/runtime/rpi-multibus.yaml
./scripts/runtime/sensoriumctl runtime health
./scripts/runtime/sensoriumctl runtime inspect console-uart
./scripts/runtime/sensoriumctl runtime stats
./scripts/runtime/sensoriumctl runtime trace --limit 8
./scripts/local/smoke-runtime-model.sh
```

Remote equivalent:

```bash
./scripts/remote/remote-smoke-runtime.sh models/runtime/rpi-multibus.yaml
./scripts/remote/remote-smoke-runtime-managed.sh models/runtime/rpi-managed-workers.yaml
```

Larger shipped scale model:

```bash
./scripts/remote/remote-smoke-runtime.sh models/runtime/rpi-multibus-scale.yaml
```

Largest shipped burn-in model:

```bash
./scripts/remote/remote-smoke-runtime.sh models/runtime/rpi-multibus-burnin.yaml
```

Sparse high-index UART model:

```bash
./scripts/remote/remote-smoke-runtime.sh models/runtime/rpi-sparse-uart.yaml
```

Restart-aware burn-in:

```bash
./scripts/local/stress-runtime-model.sh
./scripts/local/stress-runtime-model.sh ./models/runtime/rpi-multibus-scale.yaml 3
./scripts/local/stress-runtime-model.sh ./models/runtime/rpi-multibus-burnin.yaml 3
./scripts/remote/remote-stress-runtime.sh models/runtime/rpi-multibus-scale.yaml 3
./scripts/remote/remote-stress-runtime.sh models/runtime/rpi-multibus-burnin.yaml 3
./scripts/remote/remote-burnin.sh
```

ABI guardrail:

```bash
./scripts/local/verify-runtime-abi.py
```

That runtime smoke verifies:

- `sensoriumd` is reachable on `/run/sensorium/sensoriumd.sock`
- `runtime status` reports `state=ready` and a nonzero generation
- the runtime model creates one `i2c-1` bus with visible `0x76` and `0x77`
  targets
- the runtime model creates `spidev0.0` and `spidev0.1` with scripted SPI
  responses
- the runtime model creates `ttyAMA0` and `ttyAMA1` ports that work with
  `pyserial`, including a low-baud `flush()`/`tcdrain()` check on the queued
  UART path
- SPI timing metadata from `SPI_IOC_MESSAGE(1)` reaches the runtime trace

The managed-worker runtime smoke additionally verifies:

- broker-managed controller subprocess startup
- worker crash and restart handling
- continued service from unrelated devices while one worker is restarted
- benchmark artifacts now include local host and QEMU capability metadata such
  as accelerator mode, host CPU count, and guest CPU count

For benchmark hosts, check the environment explicitly:

```bash
./scripts/benchmarks/check-benchmark-host.sh --mode local
BENCHMARK_STRICT_HOST_BASELINE=1 ./scripts/qemu/qemu-benchmark.sh
```
- malformed runtime SPI bridge payloads show up in `sensoriumctl runtime trace`
  instead of failing silently

The larger `rpi-multibus-scale` model extends that coverage to:

- four I2C targets on one `i2c-1` adapter
- four SPI chip selects exposed as `spidev0.0` through `spidev0.3`
- four UART ports exposed as `ttyAMA0` through `ttyAMA3`
- one low-baud `9600` UART path that keeps `flush()` / `tcdrain()` timing in
  the smoke even when the default model changes later

The `rpi-multibus-burnin` model extends it further with:

- two I2C buses
- four SPI endpoints across two buses
- five UART ports on one shared family
- shipped template-side checks for I2C side effects, stateful SPI flash
  behavior, and UART modem mirroring

The `rpi-sparse-uart` model keeps the transport narrower and proves that a
high-index UART alias such as `ttyAMA511` survives model normalization,
daemon bridge encoding, kernel registration, and the `pyserial` smoke path.

`stress-runtime-model.sh` runs the same smoke repeatedly and, by default,
restarts `sensoriumd` before later iterations. After the first iteration it
sets `RUNTIME_SMOKE_SKIP_APPLY=1`, so the later passes validate snapshot
restore and daemon restart recovery instead of only reapplying the model each
time.

The local runtime unit suite additionally verifies:

- runtime-model normalization for device settings, template options, and faults
- I2C 8-bit and 16-bit register-bank behavior
- SPI exact and prefix scripted responses
- bridge-worker concurrency, so a slow controller-backed request does not block
  a fast template-backed request
- SPI invalid lane-width requests are rejected and traced
- UART echo, binary response, line response, and modem-default behavior
- UART invalid control/request flags are rejected and traced
- live device updates, runtime stats, and recent trace reporting
- runtime snapshot persistence and automatic restore of live device state
- JSONL trace history reload on daemon restart
- late controller replies are counted in bridge stats instead of disappearing
  silently
- UART config propagation from the kernel bridge contract into daemon state
- mixed-address I2C combined transfers are rejected and traced
- malformed fixed-width control commands are rejected before they hit the
  kernel bridge

The standalone ABI verifier checks:

- the stable frame, payload, I2C, and SPI bridge limits
- the kernel SPI transfer descriptor stays explicitly packed
- daemon and independent Python struct sizes remain aligned with the contract
- kernel fixed-width command guards and daemon prevalidation stay aligned

To fold the scale burn-in into the normal remote/QEMU regression, set:

- `RUNTIME_STRESS_ITERATIONS=<n>`
- `RUNTIME_STRESS_MODEL=models/runtime/rpi-multibus-scale.yaml`
- `RUNTIME_STRESS_MODEL=models/runtime/rpi-multibus-burnin.yaml`

Example:

```bash
RUNTIME_STRESS_ITERATIONS=3 ./scripts/remote/remote-regression.sh
```

For a broader remote confidence run that combines the normal regression, runtime
stress, sync-repair verification, and extra camera restart/contract loops, use:

```bash
BURNIN_RUNTIME_STRESS_ITERATIONS=5 \
BURNIN_CAMERA_CYCLES=3 \
BURNIN_CAMERA_MATRIX_SENSORS="imx708 imx219 imx477" \
./scripts/remote/remote-burnin.sh
```

To run only that representative camera breadth check:

```bash
./scripts/remote/remote-camera-matrix.sh
./scripts/qemu/qemu-camera-matrix.sh
```

When checking restart behavior manually, `./scripts/runtime/sensoriumctl runtime status`
now reports:

- the runtime `state`, `generation`, `desync_reason`, and health summary
- the snapshot file path and whether restore is enabled
- whether the current daemon instance restored a saved snapshot
- the last snapshot save/restore timestamps
- the trace file path plus how many trace entries were preloaded

If a late bridge failure leaves the daemon `desynced`, repair it with:

```bash
./scripts/runtime/sensoriumctl runtime resync
```

## Recent validated result

The most recent full catalog run in this repo validated all 61 IMX profiles.
The first uninterrupted sweep exposed five misses, and all five were then
rerun cleanly and passed after default processed-size fixes and helper cleanup.

One additional regression class is worth watching in processed/QEMU runs:
mispacked Bayer. The inject and capture contract is unpacked low-bit-aligned
`SRGGB10`, so a warning about samples above `0x03ff` points to Bayer packing
rather than a libcamera pipeline mismatch.

For a public rerun, prefer:

1. `./scripts/remote/remote-test-all-sensors.sh`
2. inspect `results.tsv`
3. rerun only the failing profiles after any fix
