# Changelog

All notable changes to this project should be documented in this file.

## Unreleased

## 0.1.2

### Changed

- fixed GitHub release asset publishing so nested package artifacts are attached
  correctly

### Fixed

- Alpine `.apk` artifacts now have a clean path to the GitHub Release page via
  the release uploader workflow

## 0.1.1

### Added

- direct local-file and public-URL media examples in the README and testing
  guide

### Changed

- consolidated identical IMX IPA files into a single canonical
  `config/ipa/simple/imx-generic.yaml` source
- updated the default demo source to a public sample MP4
- hardened GitHub Actions package jobs for hosted container environments

### Fixed

- local and remote stream helpers now accept both local files and `http(s)` URLs
- raw camera retrieval docs now use the tested `v4l2-ctl` capture flow instead
  of an unreliable direct `ffmpeg` V4L2 Bayer input example
- CI now installs the right runner dependencies and resolves a usable generic
  kernel header tree for module builds
- Alpine and Arch packaging workflows now tolerate container-user setup quirks
  and artifact-first package output behavior

## 0.1.0

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
