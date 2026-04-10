# ABI Notes

## Public contract

The intended stable public surface is:

- one OUTPUT video node for userspace-fed injection
- one CAPTURE video node for camera consumers
- one sensor subdevice node for sensor controls and format negotiation

Concrete device minor numbers are dynamic. Userspace should discover nodes
through the media graph, not hard-coded `/dev/videoN` assumptions.

## Module parameters

The active backend is selected at module load time:

- `family=<name>`
- `sensor=<name>`
- `repeat_last_frame=0|1`

The repo scripts expose that as:

- `SENSORIUM_FAMILY=<name>`
- `SENSORIUM_SENSOR=<name>`
- `SENSORIUM_INSMOD_ARGS='repeat_last_frame=0'`

Current supported family:

- `imx`

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
