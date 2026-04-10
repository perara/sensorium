# Troubleshooting

## Build fails because `/lib/modules/.../build` does not exist

Use a prepared kernel tree and point `KDIR` at it:

```bash
make module KDIR=/path/to/linux/build
```

On WSL, the helper script can prepare a matching tree:

```bash
./scripts/prepare-wsl-kernel-tree.sh
```

## Module reload succeeds but libcamera does not detect a camera

Check the media graph first:

```bash
./scripts/verify-libcamera-detect.sh
media-ctl -p
```

If media entities exist but `cam -l` is empty, confirm that:

- the custom libcamera runtime is installed
- the matching tuning file exists under `config/ipa/simple/`
- the selected sensor profile is one the current libcamera path can discover

## Raw capture works but processed capture is slow

That is usually host-side ISP throughput, not sensor cadence. Validate the raw
path first:

```bash
./scripts/remote-benchmark.sh
./scripts/remote-benchmark-matrix.sh
```

If raw timestamps are correct but processed FPS is lower, the bottleneck is
usually the userspace debayer/ISP path.

## Full catalog sweep reports a few failures

Start with the generated logs:

```bash
./scripts/remote-test-all-sensors.sh
```

Inspect:

- `results.tsv`
- `summary.txt`
- per-sensor logs under the generated `.cache/all-sensors-*/logs/`

Then rerun only the failing profiles after any fix.

## Remote workflows fail with intermittent SSH errors

The remote scripts already retry transient failures, but long flaps can still
break a run. Rerun the command once connectivity is stable. For large catalog
sweeps, prefer a host with stable networking and avoid concurrent manual probes.

