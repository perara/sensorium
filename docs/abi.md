# ABI Notes

## Public contract

The intended stable public surface is:

- one model-driven control tool: `./scripts/runtime/sensoriumctl`
- one live runtime daemon: `./scripts/runtime/sensoriumd`
- one adapter selection contract:
  - `adapter=<camera|iio|runtime>`
- one transport selection contract:
  - `transport=<virtual|i2c|spi|uart>`
- adapter-specific Linux device surfaces such as the camera media graph or an
  IIO device

Concrete device minor numbers are dynamic. Userspace should discover nodes
through the media graph, not hard-coded `/dev/videoN` assumptions.

## Module parameters

The active backend is selected at module load time:

- `adapter=<name>`
- `transport=<name>`
- `instance=<name>`
- `transport_device_name=<name>`
- `fault_mode=<none|stale-data|timeout>`
- `family=<name>`
- `sensor=<name>`
- `repeat_last_frame=0|1`

The repo scripts expose that as:

- `SENSORIUM_ADAPTER=<name>`
- `SENSORIUM_TRANSPORT=<name>`
- `SENSORIUM_INSTANCE=<name>`
- `SENSORIUM_TRANSPORT_DEVICE_NAME=<name>`
- `SENSORIUM_FAULT_MODE=<name>`
- `SENSORIUM_FAMILY=<name>`
- `SENSORIUM_SENSOR=<name>`
- `SENSORIUM_INSMOD_ARGS='repeat_last_frame=0'`

Current built-in adapters:

- `camera`
- `iio`
- `runtime`

## Runtime daemon surface

The runtime adapter exposes two additional public endpoints:

- daemon socket: `/run/sensorium/sensoriumd.sock`
- kernel bridge device: `/dev/sensorium-runtime-bridge`

The daemon-only bridge contract is now ABI v5, shared-memory, and covered by
an explicit repo-level verifier:

- setup ioctls negotiate ring sizes, payload arena size, inflight credits, and
  the bridge session id
- transport uses `mmap`-backed control, transport, and reply rings plus a
  shared payload arena
- descriptors carry session id, generation, request id, queue class, opcode,
  device handle, payload offset, payload length, and status
- I2C max messages per bridge request: `256`
- SPI max transfers per bridge request: `256`
- bridge session id: negotiated at setup time and surfaced in daemon status
- verifier: `./scripts/local/verify-runtime-abi.py`

The socket API is JSON-RPC and is used for:

- runtime status
- runtime health
- runtime resync
- bus list
- device list/get/update
- runtime stats and recent trace reads
- controller backend attach/detach
- controller heartbeats
- controller event polling and replies
- UART RX injection and modem-signal updates

The kernel bridge is a daemon-only binary protocol that forwards synchronous
I2C, SPI, and UART operations between the Linux-visible bus nodes and
`sensoriumd`. The stable contract is checked in three places:

- hard-coded expectations in `tests/test_runtime_abi.py`
- the standalone verifier in `scripts/local/verify-runtime-abi.py`
- the shared Python constants in `src/sensorium/runtime/common.py`

Runtime bridge semantics that are now intentionally enforced:

- fixed-size bridge commands such as `BUS_ADD`, `DEVICE_ADD`, and the remove
  commands must carry the exact expected payload length
- `sensoriumd` now prevalidates those same fixed-size control commands before
  writing them to `/dev/sensorium-runtime-bridge`
- `sensoriumd` aligns controller wait deadlines to the kernel module's
  `runtime_timeout_ms` value with a small safety margin, and reports the
  effective timeout budget in runtime status/stats
- I2C combined transfers must stay on one 7-bit target address; mixed-address
  combined requests are rejected
- SPI transfer metadata now forwards `delay_usecs`, `word_delay_usecs`, lane
  width, and `cs_change` into the daemon/controller event surface
- SPI lane width defaults are normalized to single-lane when callers leave
  them unset, and invalid lane-width values are rejected instead of being
  forwarded ambiguously
- the daemon bridge reader can process multiple requests concurrently through a
  bounded worker pool, and runtime status/stats expose queue depth, in-flight
  requests, late replies, and latency summaries
- runtime status exposes daemon `state`, `generation`, and `desync_reason`
- runtime snapshots use schema version `2`, and runtime models normalize to
  schema version `2`

Current supported camera family:

- `imx`

## Transport alias nodes

For non-virtual transports, the module can expose a transport-facing Linux bus
node under `/dev/<transport_device_name>`.

Current defaults:

- `i2c`:
  - default alias: `i2c-1`
- `spi`:
  - default alias: `spidev0.0`
- `uart`:
  - default alias: `ttyAMA0`

These names are configurable through either:

- model config: `config.transport.device_name`
- runtime env/module param: `SENSORIUM_TRANSPORT_DEVICE_NAME` or `transport_device_name`

I2C models also support:

- model config: `config.transport.address`
- runtime env/module param: `SENSORIUM_I2C_ADDRESS` or `i2c_address`

The transport node is intended to provide stable Linux-facing naming for
transport selection and a minimal consumer-facing bus surface:

- `i2c`:
  - registers a real `i2c-dev` adapter node such as `/dev/i2c-1`
  - supports standard Linux I2C tools such as `i2cdetect`, `i2cget`,
    `i2cset`, and `i2cdump`
  - supports direct `I2C_RDWR` combined transfers on the configured 7-bit
    target address

- `spi`:
  - supports the common `spidev` mode/bits-per-word/max-speed ioctls
  - supports `SPI_IOC_MESSAGE(N)` loopback transfers
- `uart`:
  - registers a tty device that supports standard termios open/read/write
  - is suitable for `pyserial`-style consumers

These nodes are still simulation-oriented. They are meant to make Linux bus
tools and test harnesses behave as if named I2C, SPI, or UART endpoints
exist, not to model a hardware-accurate peripheral protocol stack.

For the `runtime` adapter, the equivalent contract is live and multi-device:

- many I2C target addresses can exist on one `i2c-N` bus
- many SPI nodes can exist at once through multiple `spidevB.C` names
- many UART ports can exist at once through multiple tty-style names

Runtime devices also carry a live daemon-owned state surface:

- `metadata`:
  arbitrary user annotations
- `settings`:
  - `spi`: `mode`, `bits_per_word`, `max_speed_hz`
  - `uart`: `baud_rate`, `data_bits`, `parity`, `stop_bits`, `xonxoff`,
    `rtscts`
- `faults`:
  - modes: `none`, `timeout`, `errno`, `disconnect`, `short-reply`
  - optional fields: `errno`, `reply_data`, `remaining`

Template backends support:

- `i2c-register-bank`:
  `size`, `pointer_width`, `auto_increment`, `registers`
- `spi-script`:
  `responses`, `prefix_responses`, `default_response`, `echo`
- `uart-script`:
  `echo`, `binary_responses`, `line_responses`, `default_response`,
  `control_defaults`

The kernel UART runtime path now emits a dedicated configuration request when a
TTY consumer changes termios state, so daemon-side controller backends can see
updated baud/parity/stop-bit/flow-control settings. The runtime UART path also
tracks queued TX bytes explicitly so `write_room()`, `chars_in_buffer()`, and
`wait_until_sent()` reflect queued serial traffic instead of only the lifetime
of one synchronous bridge RPC.

## Node semantics

### Inject node

- V4L2 direction: OUTPUT
- purpose: userspace frame ingress
- accepted pixel formats:
  - `V4L2_PIX_FMT_BGR32`
  - `V4L2_PIX_FMT_RGB32`
  - `V4L2_PIX_FMT_BGR24`
  - `V4L2_PIX_FMT_RGB24`
  - `V4L2_PIX_FMT_SRGGB10`

Packed RGB ingress is converted in-kernel to the active `SRGGB10` layout of the
selected sensor mode.

Raw `SRGGB10` ingress uses unpacked little-endian `u16` samples with the 10-bit
value stored in the low bits. Values above `0x03ff` violate the contract and
will now trigger a one-time kernel warning.

### Capture node

- V4L2 direction: CAPTURE
- delivered pixel format:
  - `V4L2_PIX_FMT_SRGGB10`
- ownership model:
  - the active sensor profile owns mode selection
  - capture inherits that mode rather than configuring independently

### Sensor subdevice

- owns mode selection
- exposes the active sensor identity such as `imx708`, `imx477`, or `imx219`
- is the authoritative source of cadence-related controls

## Sensor-side format model

The sensor and capture side stay intentionally narrow and profile-driven:

- media bus code:
  - `MEDIA_BUS_FMT_SRGGB10_1X10`
- video pixel format:
  - `V4L2_PIX_FMT_SRGGB10`

Representative built-in profiles:

- `imx708`
  - `4608x2592`
  - `2304x1296`
  - `1536x864`
- `imx477`
  - `4056x3040`
  - `2028x1520`
  - `1332x990`
- `imx219`
  - `3280x2464`
  - `1640x1232`
  - `1280x720`

Long-tail profiles in the IMX catalog are backed by representative mode
templates chosen to keep libcamera-compatible behavior and validation practical.

## Controls

The subdevice exposes the first control slice expected by camera software:

- `V4L2_CID_CAMERA_ORIENTATION`
- `V4L2_CID_CAMERA_SENSOR_ROTATION`
- `V4L2_CID_EXPOSURE`
- `V4L2_CID_ANALOGUE_GAIN`
- `V4L2_CID_VBLANK`
- `V4L2_CID_HBLANK`
- `V4L2_CID_PIXEL_RATE`
- `V4L2_CID_TEST_PATTERN`
- `V4L2_CID_HFLIP`
- `V4L2_CID_VFLIP`

Important semantics:

- `VBLANK` drives raw-path cadence
- `PIXEL_RATE` and `HBLANK` are read-only timing inputs for FPS calculations
- profile-specific ranges are selected at module load based on the active sensor

## Streaming model

- userspace queues RGB or raw Bayer frames to the inject node
- the active sensor mode validates the ingress layout
- the internal cadence worker delivers `SRGGB10` frames to the capture queue
- converted RGB ingress is normalized into low-bit-aligned unpacked 10-bit Bayer
- timestamps use `CLOCK_MONOTONIC`
- sequence numbers advance per delivered capture frame

Underrun policy:

- default: repeat last frame
- throughput mode: load with `repeat_last_frame=0`

Cadence behavior:

- raw path:
  - changing `V4L2_CID_VBLANK` changes effective sensor cadence
- processed path:
  - delivered frame rate may be lower than requested if the host-side ISP path
    cannot keep up with the raw cadence

## Stability note

The repo intends the camera-shaped V4L2/MC surface to stay stable, but per-profile
identity details may continue to evolve as additional families and higher-fidelity
profile data are added.
