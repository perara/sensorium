# Production deployment

Sensorium's production target is a controlled-host deployment.

That means:

- the host or service account is trusted
- Sensorium owns its runtime state, logs, and socket path
- the goal is Linux-facing device simulation fidelity, not hostile multi-user
  hardening or hardware emulation

A tree should only be called production-ready after the validation evidence in
the final section has been collected for that exact revision.

## Required production configuration

Set the runtime state root explicitly:

```bash
SENSORIUM_STATE_DIR=/var/lib/sensorium
```

That state root contains runtime artifacts such as:

- `sensoriumd-runtime-snapshot.json`
- `sensoriumd-trace.jsonl`
- runtime benchmark artifacts

In detached `--daemonize` mode, the bounded daemon log defaults to:

```text
/var/log/sensorium/sensoriumd.log
```

The default runtime socket remains:

```text
/run/sensorium/sensoriumd.sock
```

Validate the host before deployment:

```bash
./scripts/local/check-production-host-baseline.sh --profile runtime
./scripts/local/check-production-host-baseline.sh --profile full --strict
```

## systemd example

The repo ships production artifacts under:

```text
packaging/systemd/sensoriumd.service
packaging/systemd/sensoriumd.service.example
packaging/systemd/sensoriumd.env.example
```

Adjust at least:

- `WorkingDirectory`
- `ExecStart`
- `RuntimeDirectory` and any custom socket/pidfile path overrides
- service user/group policy for your host

The example intentionally runs `sensoriumd` in foreground mode and lets
systemd supervise it directly.

The packaged and example units now carry their own default runtime paths:

- `SENSORIUM_STATE_DIR=/var/lib/sensorium`
- `SENSORIUM_SOCKET_PATH=/run/sensorium/sensoriumd.sock`
- `SENSORIUM_PIDFILE_PATH=/run/sensorium/sensoriumd.pid`

`/etc/default/sensoriumd` is optional and only needed when you want to
override those defaults.

For a source checkout on a systemd host, install a local unit with:

```bash
sudo ./scripts/local/install-systemd-service.sh --enable
```

`./scripts/runtime/sensoriumctl` only auto-manages `sensoriumd.service` when the
unit's `ExecStart` points at the same checkout. That keeps a source tree from
silently starting or stopping a different packaged daemon.

## Architectural boundary

The current production model keeps:

- one broker process owning the runtime bridge
- Linux-visible requests synchronous by contract

That is intentional. It is the main scale and latency ceiling, but it is not a
known correctness or production-readiness defect for the current scope.

## Validation evidence

Use the following matrix for release evidence. Record the host, kernel,
libcamera version, command, and result for each row.

| Area | Command | Required before release |
| --- | --- | --- |
| Repo hygiene | `make check` | yes |
| Release hygiene | `make check-release` from a clean tree | yes |
| Unit/runtime ABI | `make test` | yes |
| Helper tools | `make tools` | yes |
| Source tarball | `make dist` and inspect `dist/sensorium-*.tar.gz` for generated artifacts | yes |
| Debian package | `make package-deb` and inspect `dist/deb/*.deb` contents | yes |
| Package metadata | `make package-meta` | yes |
| Fresh package install | install `dist/deb/*.deb`, apply a runtime model, and list runtime devices | yes for distribution releases |
| Production host baseline | `./scripts/local/check-production-host-baseline.sh --profile full --strict` | yes on deployment class hosts |
| QEMU smoke | `./scripts/qemu/qemu-ci-smoke.sh` | yes when QEMU is part of support evidence |
| QEMU e2e | `./scripts/qemu/qemu-e2e.sh` | yes when claiming full VM e2e support |
| Kernel-major-7 QEMU | `make qemu-linux7-ci-smoke` or `make qemu-linux7-e2e` | yes when claiming current kernel-7 support |
| Remote regression | `./scripts/remote/remote-regression.sh` | yes on the controlled deployment class host |
| Benchmark evidence | `./scripts/qemu/qemu-benchmark-matrix.sh` or `./scripts/remote/remote-benchmark-matrix.sh` | yes for performance-sensitive releases |

Benchmark artifacts default to `.cache/benchmarks/` unless
`SENSORIUM_BENCHMARK_DIR` is set. `benchmark-check.sh` skips gracefully when no
baseline exists unless `BENCHMARK_REQUIRE_BASELINE=1` is set.

Record exact platform evidence alongside that matrix:

| Field | Example |
| --- | --- |
| Host / environment | Debian trixie QEMU genericcloud, controlled remote host, WSL2 dev host |
| Kernel | `uname -r`, for example `7.0.4+deb14-amd64` |
| Kernel source/build tree | `/lib/modules/$(uname -r)/build` or explicit `KDIR` |
| Git revision | `git rev-parse HEAD` |
| Dirty state | `git status --short` |
| libcamera version | required for camera validation evidence |
| Command and result | exact command, exit status, and artifact path where applicable |
