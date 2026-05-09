# Troubleshooting

## Build fails because `/lib/modules/.../build` does not exist

Use a prepared kernel tree and point `KDIR` at it:

```bash
make module KDIR=/path/to/linux/build
```

On WSL, the helper script can prepare a matching tree:

```bash
./scripts/local/prepare-wsl-kernel-tree.sh
```

## Module reload succeeds but libcamera does not detect a camera

Check the media graph first:

```bash
./scripts/local/verify-libcamera-detect.sh
media-ctl -p
```

If media entities exist but `cam -l` is empty, confirm that:

- the custom libcamera runtime is installed
- the matching tuning file exists under `config/ipa/simple/`
- the selected sensor profile is one the current libcamera path can discover

## Raw capture works but processed capture is slow

That is usually host-side ISP throughput, not sensor cadence. Validate the raw
path first:

```bash
./scripts/remote/remote-benchmark.sh
./scripts/remote/remote-benchmark-matrix.sh
./scripts/benchmarks/compare-benchmarks.py
```

If raw timestamps are correct but processed FPS is lower, the bottleneck is
usually the userspace debayer/ISP path.

## Processed capture aborts in QEMU or another software-ISP setup

Start with the processed smoke path directly:

```bash
CAPTURE_ROLE=viewfinder ./scripts/remote/remote-smoke-url-stream.sh
```

On failure, the script now prints the remote libcamera runtime context:

- chosen `cam` binary
- `LIBCAMERA_IPA_CONFIG_PATH`
- resolved tuning YAML path
- `/dev/dma_heap/system` permissions
- recent remote kernel log

Common causes:

- the tuning YAML was installed into the wrong libcamera prefix
- `/dev/dma_heap/system` is present but not readable by the capture user
- raw Bayer ingress is mispacked

Current provisioning installs a udev rule that sets `/dev/dma_heap/*` to
`0660 root:video`. If the processed path regresses again on a fresh guest,
check that the capture user is still in the `video` group and that the rule is
present under `/etc/udev/rules.d/60-sensorium-dma-heap.rules`.

The kernel now warns once if `SRGGB10` ingress samples exceed `0x03ff`. That
warning means the 10-bit sample was shifted into the upper bits of the `u16`
instead of being stored unpacked in the low bits.

## Runtime daemon commands fail or the live multi-device buses do not appear

Start with the daemon itself:

```bash
./scripts/runtime/sensoriumctl daemon start
./scripts/runtime/sensoriumctl daemon status
./scripts/runtime/sensoriumctl runtime status
```

If `sensoriumd` will not start, check:

- `/dev/sensorium-runtime-bridge` exists
- the module was reloaded in `adapter=runtime transport=virtual`
- `/run/sensorium/sensoriumd.sock` is writable by your user
- recent kernel logs for bridge, bus-add, or device-add failures

If runtime traffic feels stuck or one simulated device seems to block others,
check `./scripts/runtime/sensoriumctl runtime status` and `./scripts/runtime/sensoriumctl
runtime stats` for:

- `state`, `generation`, `desync_reason`, and the top-level `health` summary
- `bridge_runtime.queue_depth` / `bridge.queue_depth`
- `bridge_runtime.inflight` / `bridge.inflight`
- `bridge_runtime.late_replies` / `bridge.late_replies`
- `bridge_runtime.kernel_timeout_ms` versus
  `bridge_runtime.controller_timeout_ms`
- `snapshot_loaded`, `last_snapshot_saved_ts`, and
  `last_snapshot_restored_ts`
- `trace_loaded` if you expected earlier JSONL trace history to be visible

Then verify the runtime ABI contract itself:

```bash
./scripts/local/verify-runtime-abi.py
```

Then run the shipped runtime smoke directly:

```bash
./scripts/local/smoke-runtime-model.sh
```

If one class of node is missing:

- I2C:
  - check `i2cdetect -l` still lists `i2c-1`
  - check the runtime model bus name is `i2c-N`
- SPI:
  - check the runtime model `device_name` is a valid `spidevB.C`-style name
  - check `./scripts/runtime/sensoriumctl runtime trace --limit 8` for malformed SPI
    bridge requests or template/controller errors
  - check the kernel log for `driver_override`/`spidev` binding failures or
    `sensorium runtime spi transfer failed` diagnostics
- UART:
  - check the runtime model `port_name` ends in a numeric tty suffix such as
    `ttyAMA0` or `ttyAMA1`
  - check the kernel log for tty driver registration failures
  - if runtime apply fails immediately, check for daemon-side fixed-width
    bridge validation errors around `BUS_ADD`, `DEVICE_ADD`, or
    `UART_SET_MODEM`

If an I2C controller app or test issues one combined request across two
different target addresses, the runtime now rejects it deliberately. Split that
traffic into one request per device address instead of expecting a mixed-address
`I2C_RDWR` sequence to route across multiple simulated devices.

If the daemon restarts and comes back empty when you expected it to restore the
last runtime:

- check `${SENSORIUM_STATE_DIR:-$HOME/.local/state/sensorium}/sensoriumd-runtime-snapshot.json` exists and is readable
- check `./scripts/runtime/sensoriumctl runtime status` for `snapshot_restore_enabled`
  and `snapshot_loaded`
- check `daemon status` / `runtime stats` for `last_snapshot_error` if restore
  failed

If the daemon reports `state: desynced`, do not trust the live inventory until
you repair it:

```bash
./scripts/runtime/sensoriumctl runtime resync
./scripts/runtime/sensoriumctl runtime health
```

If you want to prove the repo still survives repeated runtime and camera churn
after a repair, run the burn-in path instead of only one smoke:

```bash
./scripts/remote/remote-burnin.sh
./scripts/qemu/qemu-burnin.sh
./scripts/remote/remote-camera-matrix.sh
./scripts/qemu/qemu-camera-matrix.sh
```

If the guest looks stale after a packaging or provisioning change, force one
full reprovision instead of relying on the provisioning fingerprint cache:

```bash
QEMU_SKIP_PROVISION_IF_CURRENT=0 ./scripts/qemu/qemu-ci-smoke.sh
```

## I2C, SPI, or UART transport node exists but real consumer tools still fail

Start with the transport smoke directly:

```bash
./scripts/remote/remote-smoke-iio.sh models/iio/environment-i2c.yaml
./scripts/remote/remote-smoke-iio.sh models/iio/environment-spi.yaml
./scripts/remote/remote-smoke-iio.sh models/iio/environment-uart.yaml
```

What the smoke checks now:

- I2C:
  - `/dev/<transport_device_name>` exists
  - `i2cdetect -l` lists the adapter
  - `i2cdetect`, `i2cset`, `i2cget`, and `i2cdump` succeed
  - a direct `I2C_RDWR` readback succeeds on `/dev/i2c-N`
- SPI:
  - `/dev/<transport_device_name>` exists
  - `SPI_IOC_RD_*` and `SPI_IOC_WR_*` ioctls succeed
  - `SPI_IOC_MESSAGE(1)` loopback echoes the transmit buffer
- UART:
  - `/dev/<transport_device_name>` exists
  - `pyserial` can open the TTY node
  - a write/read loopback returns the original payload

If the alias node exists but userspace still cannot open it, check:

- the udev rule under `/etc/udev/rules.d/61-sensorium-transport.rules`
- the capture user is in `dialout`
- `ls -l /dev/i2c-* /dev/spidev* /dev/ttyAMA* /dev/ttyS*`

If module load fails for a custom I2C or UART alias, check the recent kernel
log. The module now emits explicit errors when the configured I2C alias is not
an `i2c-N` name, when the configured UART alias is not a tty-style name such
as `ttyAMA0`, or when the transport device registration fails.

## Full catalog sweep reports a few failures

Start with the generated logs:

```bash
./scripts/remote/remote-test-all-sensors.sh
```

Inspect:

- `results.tsv`
- `summary.txt`
- per-sensor logs under the generated `.cache/all-sensors-*/logs/`

Then rerun only the failing profiles after any fix.

## Remote workflows fail with intermittent SSH errors

The remote scripts already retry transient failures, but long flaps can still
break a run. Rerun the command once connectivity is stable. For large catalog
sweeps, prefer a host with stable networking and avoid concurrent manual probes.

## Remote runtime smoke fails on a guest file that looks corrupted or stale

If a remote model or script looks wrong on the guest, start with a fresh sync:

```bash
./scripts/remote/remote-sync.sh
./scripts/remote/remote-verify-sync-repair.sh models/runtime/rpi-sparse-uart.yaml
```

Remote repo sync now uses a manifest fingerprint gate by default, so unchanged
trees skip the copy entirely without a full preview walk. If you need an
explicit repair pass for stale or corrupted guest files, rerun with
`REMOTE_SYNC_REPAIR=1`; that forces checksum verification before the smoke
continues. The sync path also preserves remote kernel build artifacts by
default so repeated guest reloads can reuse an unchanged module; set
`REMOTE_SYNC_PRUNE_KERNEL_BUILD=1` only when you intentionally want to drop the
remote kernel build state before syncing.

## A repeated local apply did not reload the module

`./scripts/runtime/sensoriumctl apply` now skips the reload when the requested
non-runtime model already matches the live module parameters and the local
kernel-source fingerprint still matches the last successful build. If you want
to force a full rebuild/reload anyway, rerun with:

```bash
SENSORIUM_FORCE_RELOAD=1 ./scripts/runtime/sensoriumctl apply ./models/camera/imx708.yaml
```
