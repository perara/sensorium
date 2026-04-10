# sensorium

`sensorium` is a virtual Linux camera platform for simulating real,
sensor-shaped media-controller camera pipelines with a userspace-fed ingress
path and a libcamera-detectable capture path.

The current implementation ships a reusable generic core plus a Sony IMX family
backend. In practice that means you can load one of the IMX profiles, push
frames into the inject node from userspace, and have camera software discover
and stream from the resulting virtual camera like a real sensor-backed device.

## What it does

- builds as an out-of-tree kernel module named `sensorium`
- registers a camera-shaped media graph with:
  - an OUTPUT inject node
  - a CAPTURE node
  - a selected sensor subdevice
- exposes a profile-driven Sony IMX catalog through:
  - `family=imx`
  - `sensor=<profile>`
- accepts userspace-fed raw Bayer or packed RGB ingress
- converts packed RGB ingress to `SRGGB10` in-kernel
- supports libcamera discovery without modifying libcamera source
- includes local and remote workflows for reload, smoke tests, recording,
  benchmarking, and full catalog validation

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

## Supported families and profiles

The public runtime contract is:

```bash
SENSORIUM_FAMILY=imx
SENSORIUM_SENSOR=imx708
```

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
./scripts/list-sensorium-sensors.sh
```

## Repository layout

```text
kernel/    out-of-tree kernel module
scripts/   local and remote workflows
tools/     small libcamera and conversion helpers
config/    IPA/tuning files used by validation flows
docs/      architecture, ABI, roadmap, and testing notes
ansible/   droplet provisioning
```

## Quick start

### Local dependencies

```bash
./scripts/install-deps-ubuntu.sh
```

### Build the module

```bash
make module KDIR=/path/to/linux/build
```

### Fast local loop

```bash
./scripts/prepare-wsl-kernel-tree.sh
./scripts/reload-sensorium.sh
./scripts/verify-libcamera-detect.sh
```

### Select a different sensor profile

```bash
SENSORIUM_FAMILY=imx SENSORIUM_SENSOR=imx477 ./scripts/reload-sensorium.sh
SENSORIUM_FAMILY=imx SENSORIUM_SENSOR=imx477 ./scripts/verify-libcamera-detect.sh
```

### Throughput mode

Disable repeat-last-frame behavior:

```bash
SENSORIUM_INSMOD_ARGS='repeat_last_frame=0' ./scripts/reload-sensorium.sh
```

## Remote workflow

Copy `.env.remote.example` to `.env.remote`, fill in the host, then provision:

```bash
cp .env.remote.example .env.remote
./scripts/provision-droplet.sh
```

Fast remote loop:

```bash
./scripts/remote-sync.sh
./scripts/remote-reload.sh
./scripts/remote-verify.sh
./scripts/remote-cycle.sh
```

Run the loop against a selected sensor:

```bash
SENSORIUM_FAMILY=imx SENSORIUM_SENSOR=imx290 ./scripts/remote-cycle.sh
```

## Streaming, capture, and validation

Smoke and record flows:

```bash
./scripts/remote-smoke-url-stream.sh
./scripts/remote-record-url-video.sh
./scripts/remote-start-url-stream.sh
./scripts/remote-stop-url-stream.sh
```

Regression and performance:

```bash
./scripts/remote-regression.sh
./scripts/remote-benchmark.sh
./scripts/remote-benchmark-matrix.sh
```

Exhaustive catalog sweep:

```bash
./scripts/remote-test-all-sensors.sh
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
