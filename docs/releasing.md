# Releasing

## Checklist

1. Run repository checks:

```bash
make check
./scripts/build-libcamera-capture.sh
```

2. Build the module against a representative kernel tree:

```bash
make module KDIR=/path/to/linux/build
```

3. Run remote validation:

```bash
./scripts/remote-regression.sh
./scripts/remote-benchmark-matrix.sh
./scripts/remote-test-all-sensors.sh
```

4. Build release artifacts:

```bash
make dist
make package-deb
make package-meta
```

5. Review the generated sweep results and rerun any failing profiles after
   fixes.

6. Update docs if:

- runtime contracts changed
- profile coverage changed
- known limitations changed

7. Create a changelog entry or release notes summarizing:

- major driver changes
- validation environment
- known caveats
