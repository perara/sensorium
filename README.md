# sensorium

`sensorium` is a virtual Linux sensor simulation stack with adapter and
transport interfaces for Linux-facing device simulation.

Its scope is Linux interface fidelity for downstream software. It is not a
hardware-accurate emulator of electrical behavior, cycle timing, or internal
silicon state machines.

The current implementation ships:

- a camera adapter with the existing Sony IMX family backend
- an IIO adapter with a simple environmental sensor model
- a runtime adapter with a daemon-backed multi-device bus simulation path
- a model-driven `sensoriumctl` entrypoint that applies YAML definitions onto
  the kernel module

In practice that means you can still load one of the IMX profiles, push frames
into the inject node from userspace, and have camera software discover and
stream from the resulting virtual camera like a real sensor-backed device. You
can also load an IIO-backed sensor model over `i2c`, `spi`, or `uart`-shaped
transport selection.

## What it does

- builds as an out-of-tree kernel module named `sensorium`
- exposes adapter and transport module parameters:
  - `adapter=<camera|iio|runtime>`
  - `transport=<virtual|i2c|spi|uart>`
  - `instance=<name>`
  - `transport_device_name=<name>`
- keeps the existing camera graph and IMX catalog intact through the `camera`
  adapter
- adds a first non-camera `iio` adapter with temperature and pressure channels
- adds a daemon-backed `runtime` adapter for live multi-device I2C, SPI, and
  UART simulation
- supports model-driven control through `./scripts/runtime/sensoriumctl apply <model>`
- includes local and remote workflows for reload, smoke tests, recording,
  benchmarking, and catalog validation, with camera e2e still in the default
  regression path and all shipped IIO transports covered in the default remote
  regression

## Status

The repo is currently validated end to end on an Ubuntu DigitalOcean droplet
using an unmodified custom libcamera build plus repo-local runtime config.

Validated paths include:

- libcamera camera discovery
- raw smoke capture
- processed smoke capture
- MP4-backed streaming into the inject node
- raw recording to MP4
- raw cadence validation at `10`, `20`, and `30 fps`
- full IMX catalog sweep plus targeted rechecks for previously failing profiles

This is still a simulator, not a bit-for-bit electrical or physical emulation
of every Sony sensor. The profiles are intentionally production-shaped and
camera-software-friendly rather than exhaustive hardware reproductions.

## Support matrix

| Area | Fully supported | Approximated | Intentionally unsupported |
| --- | --- | --- | --- |
| Camera | libcamera detection, media graph, raw capture, processed capture, raw MP4 record, IMX profile sweep on the validated host/QEMU path | exact electrical sensor behavior, exact ISP throughput across every host | custom libcamera pipeline handler |
| IIO | temperature/pressure models over `i2c`, `spi`, and `uart`; `environment-plus` humidity/calibbias/threshold-event profile | broad real-world sensor diversity, full triggered/buffered IIO ecosystems | a second live multi-device runtime platform inside IIO |
| Runtime I2C | multi-device userspace bus simulation on one `i2c-N`, `i2c-tools`, `I2C_RDWR`, template and controller-backed devices | hardware bus timing and electrical fidelity | mixed-address combined transfers in one `I2C_RDWR` request |
| Runtime SPI | real `spi_controller` / `spi_device` path, `spidev` userspace compatibility, multi-CS simulation, per-transfer metadata | DMA/FIFO/controller timing fidelity | cycle-accurate SPI controller emulation |
| Runtime UART | shared tty families, `pyserial`, queued drain, modem bits, multi-port simulation | precise UART hardware timing and fault injection | electrical serial line emulation |

## Adapters, transports, and profiles

The public model contract is:

```bash
./scripts/runtime/sensoriumctl apply ./models/camera/imx708.yaml
./scripts/runtime/sensoriumctl apply ./models/iio/environment-i2c.yaml
./scripts/runtime/sensoriumctl runtime apply ./models/runtime/rpi-multibus.yaml
```

The direct module/runtime contract is still available:

```bash
SENSORIUM_ADAPTER=camera
SENSORIUM_TRANSPORT=virtual
SENSORIUM_FAMILY=imx
SENSORIUM_SENSOR=imx708
```

Transport-facing alias nodes can also be named explicitly:

```bash
SENSORIUM_ADAPTER=iio
SENSORIUM_TRANSPORT=spi
SENSORIUM_INSTANCE=env-spi
SENSORIUM_TRANSPORT_DEVICE_NAME=spidev0.0
```

Supported adapters:

- `camera`
- `iio`
- `runtime`

Supported transports:

- `virtual`
- `i2c`
- `spi`
- `uart`

The shipped IMX catalog currently includes:

`imx219`, `imx250`, `imx252`, `imx253`, `imx264`, `imx265`, `imx273`,
`imx287`, `imx290`, `imx294`, `imx296`, `imx297`, `imx304`, `imx305`,
`imx327`, `imx335`, `imx347`, `imx367`, `imx387`, `imx392`, `imx410`,
`imx412`, `imx415`, `imx420`, `imx421`, `imx422`, `imx425`, `imx426`,
`imx428`, `imx429`, `imx430`, `imx432`, `imx455`, `imx461`, `imx462`,
`imx464`, `imx477`, `imx485`, `imx492`, `imx515`, `imx519`, `imx530`,
`imx531`, `imx532`, `imx533`, `imx535`, `imx536`, `imx537`, `imx568`,
`imx571`, `imx577`, `imx585`, `imx662`, `imx664`, `imx675`, `imx676`,
`imx678`, `imx708`, `imx715`, `imx900`, and `imx908`.

List the catalog directly:

```bash
./scripts/runtime/list-sensorium-sensors.sh
```

Example models live under:

```text
models/camera/
models/iio/
models/runtime/
```

For `i2c`, `spi`, and `uart`, models may set `config.transport.device_name` to
expose a matching Linux-facing bus node. The shipped defaults mirror common
Raspberry Pi-style names:

- `i2c`: `i2c-1`
- `spi`: `spidev0.0`
- `uart`: `ttyAMA0`

I2C models may also set `config.transport.address` to choose the simulated
7-bit target address on that bus. The shipped IIO environment model defaults to
`0x76`.

## Repository layout

```text
kernel/    out-of-tree kernel module
scripts/   stable local, remote, QEMU, packaging, and runtime workflow paths
tools/     small libcamera and conversion helpers
models/    YAML model definitions for adapters and transports
config/    IPA/tuning files used by validation flows
docs/      architecture, ABI, scripts, roadmap, and testing notes
ansible/   droplet provisioning
```

See [Script Catalog](docs/scripts.md) for the intended role of each script.

## Quick start

### Local dependencies

```bash
./scripts/local/install-deps-ubuntu.sh
```

That default now installs the smallest supported host footprint:

```bash
./scripts/local/install-deps-ubuntu.sh --profile driver
```

The installer also supports explicit larger profiles:

```bash
./scripts/local/install-deps-ubuntu.sh --profile runtime
./scripts/local/install-deps-ubuntu.sh --profile full
./scripts/local/install-deps-ubuntu.sh --profile full --ops
```

Environment overrides remain supported:

```bash
SENSORIUM_HOST_PROFILE=runtime ./scripts/local/install-deps-ubuntu.sh
SENSORIUM_INSTALL_OPS=1 ./scripts/local/install-deps-ubuntu.sh
```

Validate a production host baseline explicitly:

```bash
./scripts/local/check-production-host-baseline.sh --profile runtime
./scripts/local/check-production-host-baseline.sh --profile full --strict
```

Host dependency tiers are:

- `driver`:
  enough to build and load the out-of-tree kernel module. This is the closest
  thing to a drop-in "just install kernel drivers" path, but it still requires
  matching kernel headers or an external `KDIR`.
- `runtime`:
  `driver` plus the Python userspace pieces needed for `sensoriumctl`,
  `sensoriumd`, and runtime UART helpers.
- `full`:
  `runtime` plus libcamera/v4l-utils/ffmpeg/i2c-tools and the extra build tools
  used by local camera, IIO, and smoke workflows.
- `SENSORIUM_INSTALL_OPS=1`:
  adds optional remote/QEMU tooling such as Ansible, QEMU, cloud-image-utils,
  SSH, and rsync.

Practical host requirements by feature:

- module build/load only:
  `bc`, `gcc`, `g++`, `make`, `kmod`, `libc6-dev`, `libelf-dev`, `libssl-dev`,
  `pkg-config`, and matching kernel headers/build tree
- runtime adapter:
  module build/load requirements plus `python3`, `python3-yaml`, and
  `python3-serial`
- camera and local stream validation:
  runtime requirements plus `libcamera-*`, `v4l-utils`, `ffmpeg`, `curl`, and
  `i2c-tools`
- remote/QEMU workflows:
  local requirements plus `ansible-playbook`, `ssh`, `rsync`,
  `qemu-system-x86_64`, `qemu-img`, and `cloud-localds`

### Build the module

```bash
make module KDIR=/path/to/linux/build
```

### Fast local loop

```bash
./scripts/local/prepare-wsl-kernel-tree.sh
./scripts/runtime/reload-sensorium.sh
./scripts/local/verify-libcamera-detect.sh
```

`reload-sensorium.sh` now only pre-stops Sensorium-owned daemons and stream
injectors, and it refuses to kill non-Sensorium device holders during the
unload fallback path. Use `SENSORIUM_RELOAD_FORCE_EVICT=1` only when you
intentionally want broad holder eviction on a shared host.

### Select a different sensor profile

```bash
SENSORIUM_FAMILY=imx SENSORIUM_SENSOR=imx477 ./scripts/runtime/reload-sensorium.sh
SENSORIUM_FAMILY=imx SENSORIUM_SENSOR=imx477 ./scripts/local/verify-libcamera-detect.sh
```

### Apply a model

```bash
./scripts/runtime/sensoriumctl list
./scripts/runtime/sensoriumctl apply ./models/camera/imx708.yaml
./scripts/runtime/sensoriumctl apply ./models/iio/environment-i2c.yaml
./scripts/runtime/sensoriumctl daemon start
./scripts/runtime/sensoriumctl runtime apply ./models/runtime/rpi-multibus.yaml
```

If a non-runtime model is already active and the local kernel-source
fingerprint still matches the last successful build, `sensoriumctl apply` now
skips the reload entirely. Set `SENSORIUM_FORCE_RELOAD=1` to force a full
reload anyway.

The QEMU wrappers now also skip full reprovisioning by default when the remote
provisioning fingerprint still matches. Set `QEMU_SKIP_PROVISION_IF_CURRENT=0`
to force a fresh Ansible provision on every run.

### Live runtime daemon

The runtime adapter is managed by `sensoriumd`, which exposes:

- a public Unix socket at `/run/sensorium/sensoriumd.sock`
- a kernel bridge device at `/dev/sensorium-runtime-bridge`

For production-style controlled-host deployment, set the runtime state root
explicitly instead of relying on per-user defaults:

```bash
SENSORIUM_STATE_DIR=/var/lib/sensorium
```

The repo also ships example systemd deployment artifacts under:

```text
packaging/systemd/sensoriumd.service.example
packaging/systemd/sensoriumd.env.example
```

See [docs/production.md](docs/production.md) for the deployment model and
service assumptions.

For a source checkout on a systemd host, you can install a supervised local
service directly:

```bash
sudo ./scripts/local/install-systemd-service.sh --enable
```

`./scripts/runtime/sensoriumctl` only auto-adopts a `sensoriumd.service` unit when its
`ExecStart` points at the same checkout's `scripts/runtime/sensoriumd`. The packaged
and example units default `SENSORIUM_STATE_DIR=/var/lib/sensorium` plus
`/run/sensorium/sensoriumd.sock` and `/run/sensorium/sensoriumd.pid`, while
`/etc/default/sensoriumd` remains available for overrides.

Useful commands:

```bash
./scripts/runtime/sensoriumctl daemon start
./scripts/runtime/sensoriumctl daemon status
./scripts/runtime/sensoriumctl runtime status
./scripts/runtime/sensoriumctl runtime health
./scripts/runtime/sensoriumctl runtime buses
./scripts/runtime/sensoriumctl runtime devices
./scripts/runtime/sensoriumctl runtime inspect console-uart
./scripts/runtime/sensoriumctl runtime stats
./scripts/runtime/sensoriumctl runtime trace --limit 8
./scripts/runtime/sensoriumctl runtime resync
./scripts/local/verify-runtime-abi.py
```

The runtime daemon now also tracks per-device stats and recent request traces,
supports live device updates over JSON-RPC, and ships a small Python client
SDK-style wrapper in `src/sensorium/runtime/client.py` for controller apps,
plus stable example controller entrypoints in `scripts/runtime/runtime-controller-*.py`.
Packaged installs add `/usr/share/sensorium/src` to Python's import path, so
external controller apps can import `sensorium.runtime.client` directly.
`runtime status` now reports the active `state`, `generation`, schema versions,
and any `desync_reason`, while `runtime health` summarizes the daemon's
operator-facing condition. `runtime stats` still includes bridge-worker health
such as the effective kernel/controller timeout budget, queue depth, in-flight
request count, late replies, and average/max bridge latency. The daemon also
persists a live runtime snapshot and JSONL trace history under the runtime
state root (`$SENSORIUM_STATE_DIR`, otherwise the default user state
directory), so a daemon restart can replay the last applied runtime model plus
live device updates and backend attachments instead of coming back empty.

The daemon runtime states are:

- `empty`
- `applying`
- `ready`
- `degraded`
- `desynced`

If a late bridge failure prevents the daemon from proving that its in-memory
view matches the kernel, the runtime is marked `desynced` instead of pretending
the inventory is empty. Use `./scripts/runtime/sensoriumctl runtime resync` to force a
repair/reapply of the current generation.

The runtime bridge contract is now pinned by an independent ABI verifier in
`scripts/local/verify-runtime-abi.py` plus dedicated unit tests. The verifier checks
the shared constants, wire-struct sizes, and the explicitly packed SPI transfer
descriptor against the kernel source, daemon parser, and stable expected
values. The bridge now runs ABI v5 over a shared-memory transport: the daemon
and kernel negotiate ring sizes and payload-arena limits with setup ioctls,
share request and reply descriptors through an `mmap` region, and use eventfd
notifications plus explicit submit ioctls to hand work across the boundary.
Runtime status/health surfaces session id, in-flight counts, per-ring queue
depths, trace drops, generation-scoped bridge `EBUSY` counters, and
generation-scoped RPC busy rejections. ABI v5 also carries per-device SPI
defaults (`mode`, `bits_per_word`, and `max_speed_hz`) through the device-add
contract so the kernel-created `spidev*` nodes now reflect the runtime model
instead of falling back to hard-coded defaults. The built-in descriptor
ceilings remain `256` I2C messages and `256` SPI transfers per request.

Controller-backed runtime devices can now also run behind broker-managed worker
subprocesses. A controller backend may still use the existing external
JSON-RPC polling path, or it can declare `backend.worker.command` in the model
so `sensoriumd` launches and monitors an isolated controller process for that
device.

The shipped runtime models now carry `schema_version: 2`, and runtime snapshots
use snapshot schema version `2`. Snapshot restore discards older snapshot
versions instead of replaying them optimistically.

The shipped runtime smoke model now proves more than one endpoint per transport:

- one `i2c-1` bus with devices at `0x76` and `0x77`
- two SPI chip selects exposed as `spidev0.0` and `spidev0.1`
- two UART ports exposed as `ttyAMA0` and `ttyAMA1`

The repo also ships `models/runtime/rpi-managed-workers.yaml`, which exercises
broker-managed controller subprocesses and validates worker restart behavior
without dropping unrelated runtime devices.

For broader scale and restart coverage, the repo also ships
`models/runtime/rpi-multibus-scale.yaml`, which expands that to:

- four I2C targets on `i2c-1`
- four SPI chip selects on `spi0`
- four UART ports on one shared `ttyAMA*` family, including a `9600` baud path
  for `flush()` / `tcdrain()` validation

For the longest restart-aware stress runs, the repo also ships
`models/runtime/rpi-multibus-burnin.yaml`, which pushes further with:

- two I2C buses and six total I2C targets
- two SPI buses and four chip selects
- five UART ports on one shared `ttyAMA*` family
- shipped template-side realism such as I2C write side effects / clear-on-read,
  stateful SPI flash status handling, and UART modem-line mirroring

For sparse tty numbering, the repo also ships
`models/runtime/rpi-sparse-uart.yaml`, which exposes a single
`/dev/ttyAMA511` runtime port for high-index validation.

Runtime support matrix:

- I2C:
  fully supported for multi-device Linux userspace bus simulation on one
  `i2c-N` adapter
- SPI:
  fully supported for `spidev`-style Linux userspace consumers; approximated
  for hardware timing/electrical fidelity
- UART:
  fully supported for tty/`pyserial`-style Linux userspace consumers;
  approximated for hardware timing/fault fidelity

The runtime UART family size and per-port queue depths are now tunable through
module parameters. For example, to allow higher sparse tty numbering or larger
queued serial bursts:

```bash
SENSORIUM_INSMOD_ARGS='runtime_uart_lines=2048 runtime_uart_tx_capacity=32768 runtime_uart_rx_capacity=32768' \
./scripts/runtime/reload-sensorium.sh
```

Mixed-address I2C combined transfers are rejected on purpose instead of being
misrouted, and SPI timing metadata such as `delay_usecs`,
`word_delay_usecs`, and `cs_change` is now forwarded into the runtime trace and
controller event surface. Runtime control commands such as `BUS_ADD`,
`DEVICE_ADD`, and `UART_SET_MODEM` are now prevalidated in `sensoriumd` before
they ever reach the kernel bridge, so malformed fixed-width control frames fail
early during local/unit testing as well as in-kernel. The daemon bridge reader
now feeds a bounded worker pool so one slow controller backend does not
serialize unrelated template-backed traffic, and controller deadlines are kept
slightly under the kernel module's `runtime_timeout_ms` budget to reduce
late-reply drift. The UART runtime drain path is now timer-driven, so low-baud
`flush()` / `tcdrain()` behavior is validated in the shipped runtime smoke
instead of returning as if the port drained instantly. The daemon also now
keeps an O(1) device-handle index for bridge dispatch, and JSONL trace
persistence runs on a background writer thread instead of doing file I/O inline
on request handling.

For restart-aware burn-in, use:

```bash
./scripts/local/stress-runtime-model.sh
./scripts/local/stress-runtime-model.sh ./models/runtime/rpi-multibus-scale.yaml 3
./scripts/local/stress-runtime-model.sh ./models/runtime/rpi-multibus-burnin.yaml 3
```

For benchmark environment checks and stricter reproducibility on benchmark
hosts:

```bash
./scripts/benchmarks/check-benchmark-host.sh --mode local
BENCHMARK_STRICT_HOST_BASELINE=1 ./scripts/qemu/qemu-benchmark.sh
```

That loop runs the runtime smoke repeatedly and, by default, restarts
`sensoriumd` between later iterations so snapshot restore and bridge recovery
are exercised instead of only reapply behavior. The remote equivalent is:

```bash
./scripts/remote/remote-stress-runtime.sh models/runtime/rpi-multibus-scale.yaml 3
./scripts/remote/remote-stress-runtime.sh models/runtime/rpi-multibus-burnin.yaml 3
```

### Throughput mode

Disable repeat-last-frame behavior:

```bash
SENSORIUM_INSMOD_ARGS='repeat_last_frame=0' ./scripts/runtime/reload-sensorium.sh
```

## Remote workflow

Copy `.env.remote.example` to `.env.remote`, fill in the host, then provision:

```bash
cp .env.remote.example .env.remote
./scripts/remote/provision-droplet.sh
```

Fast remote loop:

```bash
./scripts/remote/remote-sync.sh
./scripts/remote/remote-reload.sh
./scripts/remote/remote-verify.sh
./scripts/remote/remote-cycle.sh
```

Remote sync now uses a manifest fingerprint gate by default and skips the copy
entirely when the local and remote sync manifests match. Normal syncs use
standard `rsync -az` only after the manifest changes, while explicit repair
runs use checksum mode to repair stale or corrupted guest content. Set
`REMOTE_SYNC_REPAIR=1` to force that repair path.

To prove that repair path explicitly on a remote host, run:

```bash
./scripts/remote/remote-verify-sync-repair.sh models/runtime/rpi-sparse-uart.yaml
```

Model-driven remote flow:

```bash
./scripts/remote/remote-apply-model.sh models/camera/imx708.yaml
./scripts/remote/remote-apply-model.sh models/iio/environment-i2c.yaml
./scripts/remote/remote-apply-model.sh models/iio/environment-spi.yaml
./scripts/remote/remote-apply-model.sh models/iio/environment-uart.yaml
```

Those transport models now create Linux-facing bus nodes such as `/dev/i2c-1`,
`/dev/spidev0.0`, and `/dev/ttyAMA0`, and you can override the names per
model. The I2C path registers a real `i2c-dev` adapter node, the SPI alias
speaks the common `spidev` ioctls used by Linux SPI tools, and the UART alias
is a real TTY surface that works with `pyserial`.

The runtime path is separate from those legacy one-model-at-a-time aliases. It
keeps a live inventory of buses and devices, so one runtime config can expose:

- many I2C target addresses on one `i2c-N` adapter
- many `spidevB.C` nodes at once
- many tty-style UART ports at once through a shared per-family tty driver

Runtime devices can now carry:

- transport settings such as SPI mode/bits/speed and UART baud/parity/flow control
- live fault injection for timeout, disconnect, errno, and short-reply cases
- richer built-in templates:
  - I2C register banks with configurable register-map size and pointer width
  - SPI exact-match and prefix-match scripted responses
  - UART echo, binary-response, and line-response scripting

Run the loop against a selected sensor:

```bash
SENSORIUM_FAMILY=imx SENSORIUM_SENSOR=imx290 ./scripts/remote/remote-cycle.sh
```

## Streaming, capture, and validation

Smoke and record flows:

```bash
./scripts/remote/remote-smoke-url-stream.sh
./scripts/remote/remote-record-url-video.sh
./scripts/remote/remote-start-url-stream.sh
./scripts/remote/remote-stop-url-stream.sh
./scripts/remote/remote-smoke-iio.sh models/iio/environment-i2c.yaml
./scripts/remote/remote-smoke-iio.sh models/iio/environment-spi.yaml
./scripts/remote/remote-smoke-iio.sh models/iio/environment-uart.yaml
./scripts/remote/remote-smoke-runtime.sh models/runtime/rpi-multibus.yaml
./scripts/remote/remote-smoke-runtime-managed.sh models/runtime/rpi-managed-workers.yaml
```

Remote URL-stream and record flows require `ffmpeg` on the remote host. The
lean QEMU CI guest does not install it by default, so those flows target a
`full`/ops-style remote environment rather than the lean smoke profile.

To bootstrap those dependencies on an existing Debian/Ubuntu remote host:

```bash
./scripts/remote/remote-ensure-stream-deps.sh
```

Or let the stream workflows install them on demand:

```bash
REMOTE_AUTO_INSTALL_STREAM_DEPS=1 ./scripts/remote/remote-smoke-url-stream.sh
REMOTE_AUTO_INSTALL_STREAM_DEPS=1 ./scripts/remote/remote-record-url-video.sh
REMOTE_AUTO_INSTALL_STREAM_DEPS=1 ./scripts/remote/remote-benchmark.sh
```

The default `./scripts/remote/remote-regression.sh` and `./scripts/qemu/qemu-e2e.sh` runs
now cover all three shipped IIO transports plus the shipped runtime
multi-device model.

For the shipped I2C, SPI, and UART models, the smoke path now verifies actual
bus-consumer behavior instead of only checking that `/dev/...` exists:

- I2C:
  - confirms the adapter is listed by `i2cdetect -l`
  - scans the configured address with `i2cdetect`
  - verifies `i2cset`, `i2cget`, and `i2cdump`
  - verifies a direct `I2C_RDWR` combined transfer on `/dev/i2c-N`

- SPI:
  - reads and writes mode, bits-per-word, and max-speed through `SPI_IOC_*`
  - performs a `SPI_IOC_MESSAGE(1)` loopback transfer
- UART:
  - opens `/dev/<transport_device_name>` through `pyserial`
  - writes a payload and reads the same bytes back

For the shipped runtime model, the smoke path additionally verifies:

- `i2c-1` with a responding `0x76` target
- `spidev0.0` with a JEDEC-style scripted SPI response
- `ttyAMA0` and `ttyAMA1` with `pyserial` echo/line-response handling plus
  `flush()`/`tcdrain()` on the queued UART path

The runtime unit suite also now covers live device updates, transport fault
injection, runtime stats/trace reporting, and UART termios-to-daemon config
updates, plus malformed fixed-width bridge control commands.

Regression and performance:

```bash
./scripts/remote/remote-regression.sh
./scripts/remote/remote-benchmark.sh
./scripts/remote/remote-benchmark-matrix.sh
make benchmark
make benchmark-matrix
make benchmark-compare
make benchmark-check
make check
make check-ci
make check-release
```

`make check` runs the fast repo-level publishability gate. `make check-ci`
enables the stronger CI-oriented profile, which adds benchmark regression
checks and the lean QEMU smoke gate on top of the normal repository checks.
Both profiles allow the repo-root `.cache/` path because normal reload, QEMU,
and benchmark workflows persist local state there. Use `make check-release`
when you want the strict clean-tree gate that also rejects repo-root cache
content.

The QEMU benchmark wrappers now also persist parsed JSON artifacts under
`.cache/benchmarks/`, including the git commit, dirty state, remote kernel, and
raw stdout for each run. Compare the latest two saved runs with:

```bash
./scripts/benchmarks/compare-benchmarks.py
./scripts/benchmarks/compare-benchmarks.py .cache/benchmarks/old.json .cache/benchmarks/new.json
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

Optional runtime scale burn-in can also be folded into the normal remote
regression:

```bash
RUNTIME_STRESS_ITERATIONS=3 ./scripts/remote/remote-regression.sh
RUNTIME_STRESS_MODEL=models/runtime/rpi-multibus-burnin.yaml \
RUNTIME_STRESS_ITERATIONS=5 \
./scripts/remote/remote-regression.sh
```

## QEMU workflow

The QEMU path reuses the same SSH-based remote scripts. The guest is treated as
an ephemeral remote host on `127.0.0.1` with an auto-generated SSH key and
cloud-init seed.

The default `./scripts/qemu/qemu-e2e.sh` run also enables a checksum-repair gate:
it corrupts one guest runtime model on purpose, then verifies that the ordinary
sync-plus-smoke flow repairs it automatically. Set `QEMU_VERIFY_SYNC_REPAIR=0`
to skip that step for a faster local loop.

The provisioning step also now uses a remote fingerprint cache by default, so a
warm guest can skip the full Ansible run when the provisioning inputs have not
changed. Set `QEMU_SKIP_PROVISION_IF_CURRENT=0` when you want to force a fresh
provision.

The default lean QEMU smoke guest is Debian `trixie` with distro-native
`libcamera-tools` packages. The default minimum gate now matches that lean path
at `libcamera >= 0.4.0`. If you want the newer Debian `sid` libcamera stream,
set `QEMU_CI_LIBCAMERA_APT_RELEASE=sid` and optionally raise
`LIBCAMERA_MIN_VERSION`.

The remote helpers now resolve the libcamera install path inside the guest
before installing the tuning YAML, so the same QEMU flow works with either the
system `cam` binary or a custom `/opt/libcamera-custom` build.

There are now matching QEMU benchmark entrypoints for the existing remote
benchmark scripts, so performance checks use the same local workflow shape as
`qemu-e2e` and `qemu-burnin`:

```bash
./scripts/qemu/qemu-benchmark.sh
./scripts/qemu/qemu-benchmark-matrix.sh
make benchmark-compare
make benchmark-check
```

For a leaner local gate than the full end-to-end regression, use:

```bash
./scripts/qemu/qemu-ci-smoke.sh
make qemu-ci-smoke
```

To make Linux 7 kernel compatibility an explicit release gate, use:

```bash
make qemu-linux7-ci-smoke
make qemu-linux7-e2e
```

Those targets run the Debian `trixie` QEMU guest with the Debian `sid` media
kernel stream and fail unless the remote guest is actually booted into kernel
major `7`.

The current runtime protocol is documented in
[runtime-bridge-v5.md](docs/runtime-bridge-v5.md).

Provisioning also installs a udev rule for `/dev/dma_heap/*` so the guest's
non-root capture user can open the system dma-heap. That keeps the packaged
libcamera software-ISP path working in fresh QEMU guests instead of silently
dropping processed capture back to raw-only behavior.

Provisioning also installs transport udev rules for `i2c-*`, `spidev*`, and
`tty*` aliases so the non-root test user can open the simulated bus devices
through the same `dialout` group path that typical host tools use.

Run the full end-to-end flow:

```bash
./scripts/qemu/qemu-e2e.sh
```

Run the extended burn-in flow:

```bash
./scripts/qemu/qemu-burnin.sh
make burnin
```

Run the representative camera profile breadth check:

```bash
./scripts/qemu/qemu-camera-matrix.sh
make camera-matrix
```

Useful burn-in knobs:

```bash
QEMU_RUNTIME_STRESS_MODEL=models/runtime/rpi-multibus-burnin.yaml \
QEMU_RUNTIME_STRESS_ITERATIONS=5 \
QEMU_CAMERA_CYCLE_ITERATIONS=3 \
QEMU_CAMERA_MATRIX_SENSORS="imx708 imx219 imx477" \
./scripts/qemu/qemu-burnin.sh
```

Or drive the guest manually:

```bash
./scripts/qemu/qemu-start.sh
./scripts/qemu/qemu-wait.sh
./scripts/qemu/qemu-ssh.sh 'uname -a'
make qemu-e2e
```

Useful QEMU overrides:

```bash
QEMU_DISTRO=ubuntu-noble ./scripts/qemu/qemu-e2e.sh
QEMU_DISTRO=debian-sid ./scripts/qemu/qemu-e2e.sh
QEMU_LIBCAMERA_APT_RELEASE=sid ./scripts/qemu/qemu-e2e.sh
```

If the processed smoke step fails, `remote-smoke-url-stream.sh` now dumps the
remote `cam` binary choice, `LIBCAMERA_IPA_CONFIG_PATH`, the resolved tuning
file path, `dma_heap` permissions, and the recent remote kernel log.

Exhaustive catalog sweep:

```bash
./scripts/remote/remote-test-all-sensors.sh
```

## Direct `ffmpeg` examples

The simulator has two camera-facing sides:

- inject side: queue frames into the OUTPUT node, usually `/dev/video0`
- capture side: read frames back from the CAPTURE node, usually `/dev/video1`

You can confirm the current node numbers with:

```bash
v4l2-ctl --list-devices
```

### 1. Send an MP4 into the simulated camera

This example loops an MP4, converts it to packed `BGR32`, and feeds it
directly into the inject node:

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

The repo helper script does the same thing for a URL or local file:

```bash
./scripts/runtime/stream-url-to-sensorium.sh ./input.mp4
```

### 2. Retrieve that same stream as a camera

#### Option A: pull raw frames from the CAPTURE node with `v4l2-ctl`

The kernel capture node exposes raw Bayer as `RG10`. The most reliable
low-level readback path is to dump frames from the capture node with
`v4l2-ctl` and then hand that raw file to `ffmpeg`:

```bash
v4l2-ctl \
  -d /dev/video1 \
  --set-fmt-video=width=1536,height=864,pixelformat=RG10 \
  --stream-mmap=4 \
  --stream-count=150 \
  --stream-to=output-rggb10.raw
```

If you want to turn that raw dump into an MP4 for inspection, convert it after
capture:

```bash
ffmpeg \
  -hide_banner \
  -loglevel warning \
  -f rawvideo \
  -pixel_format bayer_rggb16le \
  -video_size 1536x864 \
  -framerate 30 \
  -i output-rggb10.raw \
  -c:v libx264 \
  -pix_fmt yuv420p \
  output.mp4
```

This path was validated directly against the live `sensorium` capture node.
Some `ffmpeg` builds do not accept the raw Bayer V4L2 fourcc as a direct
`-f v4l2` input format, which is why the docs prefer `v4l2-ctl` for the raw
dump step.

#### Option B: retrieve it through libcamera as a real camera client

This is the most camera-shaped path. It asks libcamera for frames from the
detected `sensorium` camera and writes the raw payload:

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

Then convert that raw output to MP4:

```bash
ffmpeg \
  -hide_banner \
  -loglevel warning \
  -f rawvideo \
  -pixel_format bayer_rggb16le \
  -video_size 1536x864 \
  -framerate 30 \
  -i output-rggb10.raw \
  -c:v libx264 \
  -pix_fmt yuv420p \
  output.mp4
```

If you want the repo to handle the full remote loop for you, including pulling
the MP4 back locally, use:

```bash
./scripts/remote/remote-record-url-video.sh ./input.mp4
```

## Packaging

Versioned release source:

```bash
make dist
```

Debian DKMS package:

```bash
make package-deb
```

Rendered Alpine and Arch packaging metadata:

```bash
make package-meta
```

## Runtime notes

- Inject node ingress formats:
  - `BGR32`
  - `RGB32`
  - `BGR24`
  - `RGB24`
  - `SRGGB10`
- Capture node format:
  - `SRGGB10`
- `SRGGB10` ingress and capture samples use unpacked low-bit-aligned 10-bit
  values inside each little-endian `u16`.
- If raw ingress samples exceed `0x03ff`, the kernel logs a one-time warning so
  processed-capture failures are easier to trace back to Bayer packing.
- The sensor subdevice owns active mode selection and cadence.
- The raw path is the most reliable validation path.
- Processed/viewfinder throughput depends on the host-side ISP path as well as
  the simulated sensor cadence.
- On kernels without `VIDEOBUF2_DMA_SG`, `sensorium` falls back to
  `vb2_vmalloc` so the module still builds and runs.

## Compatibility notes

- The repo does not ship a custom libcamera pipeline handler.
- The validated setup uses an unchanged custom libcamera build plus repo-side
  tuning/runtime configuration.
- One internal compatibility detail remains intentional: the media-device
  `driver_name` keeps the receiver identity that the current libcamera path
  expects, even though the public module and repo surface are named
  `sensorium`.

## Documentation

- [Architecture](docs/architecture.md)
- [ABI Notes](docs/abi.md)
- [Profile Catalog](docs/profile-catalog.md)
- [Script Catalog](docs/scripts.md)
- [Testing Guide](docs/testing.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Releasing](docs/releasing.md)
- [Roadmap](docs/roadmap.md)
- [Release Notes Draft](docs/releases/v0.1.0.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)

## License

This repository is licensed under the GNU General Public License v2. See
[LICENSE](LICENSE).
