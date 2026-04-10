# Architecture

## Media graph

The current graph is:

```text
/dev/video-inject --> [ selected imx sensor subdev ] --> /dev/video-capture
```

Entity model:

- profile-selected sensor subdevice such as `imx708`, `imx477`, or `imx219`
- `sensorium-inject` OUTPUT video node
- `sensorium-capture` CAPTURE video node

The sensor carries the selected IMX profile identity and owns mode selection, controls,
and stream state. The inject and capture nodes mirror the active sensor mode rather than
inventing their own configuration model.

## Compatibility target

The repo does not carry a custom libcamera pipeline handler. The target is a kernel-only
solution whose graph is close enough to an existing supported camera pipeline that
libcamera can discover it automatically without libcamera source changes.

That means the driver must eventually match more than the sensor name alone:

- entity naming that looks like the expected sensor and receiver path
- controls and mode negotiation that behave like a real selected IMX sensor
- media-bus formats and frame sizes aligned with the existing camera stack
- graph topology close enough to an already-supported IMX708 path that no extra
  userspace matching code is needed

The current three-entity graph is the smallest shape that still preserves that
camera-like behavior. The reusable core is family/profile-driven, so most of the
pipeline code is shared and only the selected backend identity, mode table, and default
control ranges change per profile.

## Runtime model

The runtime split follows four logical units:

- `sensorium-core.c`
  Owns top-level lifetime, family/profile selection, media registration, links, and
  cadence work.
- `sensorium-sensor.c`
  Owns the subdevice, controls, and format negotiation.
- `sensorium-inject.c`
  Owns the OUTPUT node and ingress queue.
- `sensorium-capture.c`
  Owns the CAPTURE node and egress queue.

## Buffer flow

1. Userspace queues frames on the inject node.
2. The inject node accepts either packed RGB ingress or raw `SRGGB10`.
3. Packed RGB ingress is converted in-kernel to the active `SRGGB10` mode layout.
4. A cadence worker delivers frames into the capture queue.
5. If no new ingress frame is available, the newest held inject buffer may be repeated to
   keep camera clients moving.

The cadence logic lives in delayed work so the queue model stays simple while
still behaving like a clocked sensor instead of a pure push-through transport.

## Kernel policy

- The sensor subdevice is the authoritative owner of the active mode.
- Inject and capture formats must match the active sensor mode.
- Mode changes while queues are busy are rejected with `-EBUSY`.
- Timestamps use `CLOCK_MONOTONIC`.
- Sequence numbers advance on each delivered capture frame.
- Inject and capture queues use DMA-backed vb2 memory operations.
- The default underrun policy is repeat-last-frame, but module parameter
  `repeat_last_frame=0` switches to a stricter throughput mode.
- Raw-path cadence can be driven through the sensor `VBLANK` control, which is how the
  benchmark and record tools validate `10`, `20`, and `30` fps operation.
- Processed/viewfinder cadence depends on the libcamera software ISP throughput of the
  host in addition to the raw sensor cadence. On the validated 1 vCPU droplet, the
  processed path tops out at about `10 fps`.

## Next structural change

The current focus is stability and regression-proofing rather than another graph rewrite.
The main operational goals are:

- keep libcamera auto-detection stable
- keep the raw record path visually correct
- track performance regressions in the remote droplet loop

One compatibility detail remains intentional: the media-device `driver_name` still uses
the libcamera-compatible receiver identity expected by the current simple pipeline path,
even though the module, scripts, repo surface, and camera IDs are now under `sensorium`.
