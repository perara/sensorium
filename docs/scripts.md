# Script catalog

The `scripts/` directory is split by lifecycle. The root of `scripts/` should
contain category directories only; executable entrypoints live under those
categories. Python implementation code lives in `src/sensorium/`, while Python
files in `scripts/` are thin launchers or shell-facing tools.

Packaged installs ship only `scripts/runtime/`, `scripts/lib/sensorium-common.sh`,
the `src/sensorium/` Python package, `/usr/bin/sensoriumctl`,
`/usr/bin/sensoriumd`, and a Python import hook for
`/usr/share/sensorium/src`. Remote, QEMU, package-building, benchmark, and
local validation scripts are source-tree tooling and are not part of the
runtime package surface.

## Public CLI and daemon

- `runtime/sensoriumctl`: model, daemon, and live runtime control.
- `runtime/sensoriumd`: runtime daemon and bridge owner.
- `runtime/reload-sensorium.sh`: source-checkout module reload helper used by
  `sensoriumctl apply`.
- `runtime/runtime-controller-*.py`: packaged managed-worker examples.
- `runtime/stream-url-to-sensorium.sh`: local URL/file-to-inject-node stream
  helper.
- `runtime/list-sensorium-sensors.sh`: list the IMX profile catalog.

## Package Build

- `package/build-deb-package.sh`: build the Debian DKMS package.
- `package/dist-source.sh`: create a versioned source tarball.
- `package/render-package-templates.sh`: render Alpine and Arch package
  metadata.
- `lib/package-common.sh`: internal packaging library; source it from packaging
  scripts instead of running it directly.

## Local validation

- `local/check-repo.sh`: local publishability gate.
- `local/check-production-host-baseline.sh`: production host dependency
  baseline.
- `local/build-libcamera-capture.sh`: build repo-local helper tools.
- `local/install-deps-ubuntu.sh`, `local/install-systemd-service.sh`,
  `local/prepare-wsl-kernel-tree.sh`: host setup helpers.
- `local/assert-camera-contract.sh`, `local/verify-libcamera-detect.sh`,
  `local/verify-runtime-abi.py`: camera/libcamera/runtime contract checks.
- `local/smoke-iio-model.sh`, `local/smoke-runtime-model.sh`,
  `local/smoke-runtime-managed-workers.sh`, `local/stress-runtime-model.sh`:
  local model smoke and stress flows.

## Benchmarks

- `benchmarks/check-benchmark-host.sh`: benchmark host capability probe.
- `benchmarks/benchmark-check.sh`, `benchmarks/compare-benchmarks.py`,
  `benchmarks/record-benchmark-artifact.py`: benchmark artifact and regression
  helpers.

## Remote workflows

- Core sync and reload: `remote/remote-sync.sh`, `remote/remote-reload.sh`,
  `remote/remote-cycle.sh`, `remote/remote-verify.sh`,
  `remote/remote-verify-sync-repair.sh`.
- Camera and stream validation: `remote/remote-smoke-url-stream.sh`,
  `remote/remote-start-url-stream.sh`, `remote/remote-stop-url-stream.sh`,
  `remote/remote-record-url-video.sh`, `remote/remote-test-all-sensors.sh`,
  `remote/remote-camera-matrix.sh`, `remote/remote-assert-camera-contract.sh`.
- Runtime and IIO validation: `remote/remote-apply-model.sh`,
  `remote/remote-smoke-iio.sh`, `remote/remote-smoke-runtime.sh`,
  `remote/remote-smoke-runtime-managed.sh`, `remote/remote-stress-runtime.sh`.
- Benchmark and burn-in: `remote/remote-benchmark.sh`,
  `remote/remote-benchmark-matrix.sh`, `remote/remote-burnin.sh`.
- Host preparation and diagnostics: `remote/remote-build-libcamera-capture.sh`,
  `remote/remote-check-libcamera-version.sh`,
  `remote/remote-ensure-media-kernel.sh`,
  `remote/remote-ensure-stream-deps.sh`, `remote/remote-klogs.sh`.
- Utility: `remote/remote-set-sensor-fps.sh` adjusts sensor vertical blanking
  for a target FPS on the remote camera subdevice.
- `remote/provision-droplet.sh`: provision the configured remote host.
- `lib/remote-common.sh` is an internal remote workflow library.

## QEMU workflows

- `qemu/qemu-e2e.sh`, `qemu/qemu-ci-smoke.sh`: local VM validation gates.
- Set `QEMU_EXPECT_KERNEL_MAJOR=7`, or use `make qemu-linux7-e2e` /
  `make qemu-linux7-ci-smoke`, when the release needs explicit Linux 7 kernel
  compatibility evidence.
- `qemu/qemu-benchmark.sh`, `qemu/qemu-benchmark-matrix.sh`: VM benchmark
  flows.
- `qemu/qemu-burnin.sh`, `qemu/qemu-camera-matrix.sh`: extended VM
  validation.
- `qemu/qemu-start.sh`, `qemu/qemu-stop.sh`, `qemu/qemu-wait.sh`,
  `qemu/qemu-ssh.sh`: VM lifecycle and access helpers.
- `qemu/qemu-reset.sh`: utility to stop the VM, reset the overlay, and rewrite
  cloud-init seed data.
- `lib/qemu-common.sh` is an internal QEMU workflow library.

## Runtime Python internals

Runtime modules live under `src/sensorium/runtime/`. They are used by
`sensoriumd`, `sensoriumctl`, runtime workers, and tests. They are not dead
code even when they have few text references because they are imported through
the package.

- `src/sensorium/cli/sensoriumd.py`: daemon implementation behind
  `scripts/runtime/sensoriumd`.
- `src/sensorium/cli/sensoriumctl.py`: control CLI implementation behind
  `scripts/runtime/sensoriumctl`.
- `src/sensorium/runtime/client.py`: SDK-style client for controller apps.

## Controller examples

- `runtime/runtime-controller-eeprom.py`
- `runtime/runtime-controller-spi-flash.py`
- `runtime/runtime-controller-uart-mcu.py`

These stable `scripts/` entrypoints dispatch to
`src/sensorium/controllers/*.py` and are used by runtime model definitions for
managed worker/controller backends.

## General helpers

- `lib/sensorium-common.sh`: internal shell library shared by local workflows.
- `src/sensorium/model_control.py`: model-control implementation imported by
  `sensoriumctl`.
- `tools/compute-sync-manifest.py`: remote sync fingerprint helper backed by
  `src/sensorium/tools/compute_sync_manifest.py`.
