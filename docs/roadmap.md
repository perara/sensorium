# Roadmap

## Milestone 1

- get the out-of-tree kernel module compiling against a chosen kernel tree
- register a visible media graph
- enumerate formats and sizes through the sensor subdevice
- stream buffers through inject and capture nodes

## Milestone 2

- reshape the media graph to mirror the existing family-compatible topology
- tighten entity identity and control behavior for automatic libcamera detection
- tighten cadence to match selected sensor mode FPS
- improve error handling and queue invalidation
- expose embedded data or metadata if consumers require it
- add tracepoints and debugfs counters

## Milestone 3

- validate that libcamera discovers the driver through existing supported paths with no
  repo-local libcamera code
- enable configuration and streaming through `cam` or a downstream camera app
- add CI jobs for kernel build and smoke tests

## Milestone 4

- add more sensor families behind the generic `sensorium` family/profile contract
- tighten per-profile realism where public sensor data is available
- publish reproducible CI or lab-host validation for the catalog sweep
- reduce remaining host-specific assumptions in the processed/viewfinder path
