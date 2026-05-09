# Releasing

## Checklist

1. Run repository checks:

```bash
make check
git status --short
./scripts/local/build-libcamera-capture.sh
```

2. Classify every untracked source file before release. Add intended source,
   model, package, script, and test files to the release commit; remove only
   generated/local artifacts.

3. Build the module against a representative kernel tree:

```bash
make module KDIR=/path/to/linux/build
```

4. Run remote validation:

```bash
./scripts/remote/remote-regression.sh
./scripts/remote/remote-benchmark-matrix.sh
./scripts/remote/remote-test-all-sensors.sh
```

5. Build release artifacts:

```bash
make dist
make package-deb
make package-meta
```

6. Review the generated sweep results and rerun any failing profiles after
   fixes.

7. Update docs if:

- runtime contracts changed
- profile coverage changed
- known limitations changed

8. Create a changelog entry or release notes summarizing:

- major driver changes
- validation environment
- known caveats
