#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
check_profile="${SENSORIUM_CHECK_PROFILE:-default}"

export PYTHONDONTWRITEBYTECODE=1

cd "${repo_root}"
export PYTHONPATH="${repo_root}/src${PYTHONPATH:+:${PYTHONPATH}}"

case "${check_profile}" in
default)
	benchmark_check="${CHECK_BENCHMARK_REGRESSIONS:-0}"
	qemu_smoke_check="${CHECK_QEMU_SMOKE:-0}"
	allow_repo_cache=1
	;;
ci)
	benchmark_check="${CHECK_BENCHMARK_REGRESSIONS:-1}"
	qemu_smoke_check="${CHECK_QEMU_SMOKE:-1}"
	allow_repo_cache=1
	;;
release)
	benchmark_check="${CHECK_BENCHMARK_REGRESSIONS:-1}"
	qemu_smoke_check="${CHECK_QEMU_SMOKE:-1}"
	allow_repo_cache=0
	;;
*)
	echo "Unknown SENSORIUM_CHECK_PROFILE: ${check_profile}" >&2
	exit 1
	;;
esac

echo "Checking shell syntax..."
while IFS= read -r path; do
	bash -n "${path}"
done < <(find scripts -type f -name '*.sh' | sort)

echo "Checking Python entrypoints..."
python3 - <<'PY'
from pathlib import Path

scripts_dir = Path("scripts")
paths = sorted(scripts_dir.rglob("*.py"))
paths.extend(sorted(Path("src").rglob("*.py")))
paths.extend(
    [
        scripts_dir / "runtime" / "sensoriumctl",
        scripts_dir / "runtime" / "sensoriumd",
    ]
)

for path in paths:
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
PY

echo "Checking for generated Python cache artifacts..."
if find . \
	-path './.git' -prune -o \
	-type d -name '__pycache__' -print -o \
	-type f \( -name '*.pyc' -o -name '*.pyo' \) -print | grep -q .; then
	echo "Generated Python cache artifacts are present in the worktree." >&2
	exit 1
fi

if command -v shellcheck >/dev/null 2>&1; then
	echo "Running shellcheck..."
	shellcheck \
		-e SC2034 \
		-e SC2046 \
		-e SC2154 \
		-e SC2178 \
		-e SC2317 \
		$(find scripts -type f -name '*.sh' | sort)
fi

echo "Validating Sensorium models..."
./scripts/runtime/sensoriumctl validate

echo "Running unit tests..."
python3 -m unittest discover -s tests -p 'test_*.py'

echo "Verifying runtime ABI contract..."
python3 ./scripts/local/verify-runtime-abi.py

if [[ "${benchmark_check}" == "1" ]]; then
	echo "Checking benchmark regressions..."
	BENCHMARK_REQUIRE_BASELINE="${BENCHMARK_REQUIRE_BASELINE:-0}" ./scripts/benchmarks/benchmark-check.sh
fi

if [[ "${qemu_smoke_check}" == "1" ]]; then
	echo "Running lean QEMU smoke..."
	./scripts/qemu/qemu-ci-smoke.sh
fi

echo "Checking for stale legacy names..."
if rg -n "imx708-sim|sony-imx-sim|camera-mock-drivers" . \
	--glob '!**/.git/**' \
	--glob '!dist/**' \
	--glob '!scripts/local/check-repo.sh'; then
	echo "Found stale legacy naming in the repo." >&2
	exit 1
fi

echo "Checking required top-level files..."
required_files=(
	README.md
	LICENSE
	VERSION
	CHANGELOG.md
	CONTRIBUTING.md
	SECURITY.md
	CODE_OF_CONDUCT.md
	.env.remote.example
	.gitignore
	.gitattributes
	.editorconfig
	pyproject.toml
)

for path in "${required_files[@]}"; do
	if [[ ! -f "${path}" ]]; then
		echo "Missing required file: ${path}" >&2
		exit 1
	fi
done

echo "Checking for generated artifacts in the repo root..."
repo_cache_path=".cache"
generated_paths=(
	build
	dist
	.env.kernel
	.env.remote
	tools/libcamera-capture
	tools/libcamera-record
	tools/rgb24-to-rggb10
)

for path in "${generated_paths[@]}"; do
	if [[ -e "${path}" ]]; then
		echo "Generated or local-only path should not be present: ${path}" >&2
		exit 1
	fi
done

if find . \
	-path './.git' -prune -o \
	-type d -name '*.egg-info' -print | grep -q .; then
	echo "Generated Python package metadata is present in the worktree." >&2
	exit 1
fi

echo "Checking for generated kernel build artifacts..."
if find kernel -maxdepth 1 \
	\( -name '*.o' \
	-o -name '*.ko' \
	-o -name '*.mod' \
	-o -name '*.mod.c' \
	-o -name '*.cmd' \
	-o -name '.*.cmd' \
	-o -name '.*.d' \
	-o -name 'Module.symvers' \
	-o -name 'modules.order' \
	-o -name 'Module.markers' \
	-o -name '.tmp_versions' \) \
	-print | grep -q .; then
	echo "Generated kernel build artifacts are present in kernel/." >&2
	echo "Run make clean or remove ignored kernel build outputs before publishing." >&2
	exit 1
fi

if [[ "${allow_repo_cache}" == "0" && -e "${repo_cache_path}" ]]; then
	echo "Generated or local-only path should not be present in release profile: ${repo_cache_path}" >&2
	exit 1
fi

if [[ "${allow_repo_cache}" == "1" && -e "${repo_cache_path}" ]]; then
	echo "Note: repo-root ${repo_cache_path} is present and allowed in the ${check_profile} profile." >&2
fi

echo "Checking package staging excludes..."
python3 - <<'PY'
import fnmatch
import subprocess
import sys
from pathlib import Path

repo_root = Path.cwd()
generated_patterns = [
    "kernel/*.o",
    "kernel/*.ko",
    "kernel/*.mod",
    "kernel/*.mod.c",
    "kernel/*.cmd",
    "kernel/.*.cmd",
    "kernel/.*.d",
    "kernel/Module.symvers",
    "kernel/modules.order",
    "kernel/Module.markers",
    "kernel/.tmp_versions",
    "build/*",
    "src/*.egg-info/*",
    "tools/libcamera-capture",
    "tools/libcamera-record",
    "tools/rgb24-to-rggb10",
]
cmd = [
    "bash",
    "-c",
    "source scripts/lib/package-common.sh; "
    "rsync -anv \"${sensorium_package_rsync_excludes[@]}\" ./ /tmp/sensorium-package-check/",
]
result = subprocess.run(
    cmd,
    cwd=repo_root,
    check=True,
    capture_output=True,
    text=True,
)
leaked = []
for raw_line in result.stdout.splitlines():
    path = raw_line.strip()
    if not path or path.endswith("/") or path.startswith("sending ") or path.startswith("sent "):
        continue
    if any(fnmatch.fnmatch(path, pattern) for pattern in generated_patterns):
        leaked.append(path)
if leaked:
    print("Package staging would include generated artifacts:", file=sys.stderr)
    for path in leaked:
        print(f"  {path}", file=sys.stderr)
    sys.exit(1)
PY

echo "Repository checks passed."
