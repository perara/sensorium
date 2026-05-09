# Contributing

## Scope

`sensorium` is a kernel-facing camera simulation project. Changes should favor:

- stable media-controller behavior
- libcamera compatibility
- reproducible validation flows
- clear, scriptable operator workflows

## Development setup

Install dependencies:

```bash
./scripts/local/install-deps-ubuntu.sh
```

Build local helper tools:

```bash
./scripts/local/build-libcamera-capture.sh
```

Build the module:

```bash
make module KDIR=/path/to/linux/build
```

## Before sending changes

At minimum, run:

```bash
make check
./scripts/local/build-libcamera-capture.sh
make module KDIR=/path/to/linux/build
```

If you have a configured remote host, also run:

```bash
./scripts/remote/remote-cycle.sh
./scripts/remote/remote-regression.sh
```

For profile-heavy or catalog-wide changes, run:

```bash
./scripts/remote/remote-test-all-sensors.sh
```

## Style notes

- Keep the public repo surface generic under `sensorium`.
- Avoid reintroducing old environment names or legacy module names.
- Prefer small, scriptable workflows over manual operator steps.
- Keep profile additions data-driven where possible instead of duplicating core
  pipeline logic.

## Pull request guidance

Include:

- what changed
- what host/kernel/libcamera environment you tested with
- which validation scripts you ran
- any remaining caveats or known regressions
