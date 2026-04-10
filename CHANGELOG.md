# Changelog

All notable changes to this project should be documented in this file.

## Unreleased

### Added

- generic `sensorium` platform surface with a family/profile architecture
- Sony IMX family backend with a 61-profile catalog
- userspace-fed inject path and libcamera-detectable capture path
- local and remote validation flows for reload, smoke tests, recording,
  benchmarking, and full-catalog sweeps
- GitHub workflow, issue templates, pull request template, and maintainer docs

### Changed

- renamed the public repo and module surface from earlier IMX-specific naming to
  `sensorium`
- moved packed RGB to Bayer conversion into the kernel ingress path
- tightened raw-path cadence so `10`, `20`, and `30 fps` validation is
  sensor-paced rather than burst-driven
- cleaned the public repo surface for publication

### Fixed

- helper teardown issues that left capture nodes busy between runs
- processed smoke-capture instability in several long-tail IMX profiles by
  shifting defaults onto safer processed sizes
- full-catalog validation misses uncovered during the first uninterrupted sweep

