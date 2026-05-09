KDIR ?= /lib/modules/$(shell uname -r)/build
MODULE_DIR := $(CURDIR)/kernel

.PHONY: help module tools test check check-ci check-release dist package-deb package-meta qemu-e2e qemu-ci-smoke qemu-linux7-e2e qemu-linux7-ci-smoke benchmark benchmark-matrix benchmark-compare benchmark-check burnin camera-matrix clean distclean tree

help:
	@echo "Targets:"
	@echo "  make module  - build the out-of-tree kernel module against KDIR"
	@echo "  make tools   - build local helper tools"
	@echo "  make test    - run the Python/unit runtime regression suite"
	@echo "  make check   - run repo-level publishability checks"
	@echo "  make check-ci - run the stronger CI-oriented check profile, including benchmark and lean QEMU smoke gates"
	@echo "  make check-release - run the strict release-oriented repo check profile, including cache cleanliness"
	@echo "  make dist    - create the versioned source tarball under dist/"
	@echo "  make package-deb - build the Debian DKMS package"
	@echo "  make package-meta - render Alpine and Arch package metadata"
	@echo "  make qemu-e2e - boot a local QEMU guest and run the SSH-based e2e flow"
	@echo "  make qemu-ci-smoke - boot a local QEMU guest and run the lean smoke gate"
	@echo "  make qemu-linux7-e2e - run the full QEMU e2e flow and require Linux kernel major 7"
	@echo "  make qemu-linux7-ci-smoke - run the lean QEMU smoke gate and require Linux kernel major 7"
	@echo "  make benchmark - boot a local QEMU guest and run the single-profile benchmark"
	@echo "  make benchmark-matrix - boot a local QEMU guest and run the fps benchmark matrix"
	@echo "  make benchmark-compare - compare the latest two benchmark artifacts"
	@echo "  make benchmark-check - compare benchmark artifacts and fail on configured regressions"
	@echo "  make burnin  - boot a local QEMU guest and run the extended burn-in flow"
	@echo "  make camera-matrix - boot a local QEMU guest and run the representative camera matrix"
	@echo "  make clean   - remove kernel build artifacts"
	@echo "  make distclean - remove kernel artifacts, local tool binaries, and cache"
	@echo "  make tree    - print the repo layout"
	@echo
	@echo "Override KDIR to point at a prepared kernel build tree if needed."

module:
	$(MAKE) -C $(KDIR) M=$(MODULE_DIR) modules

tools:
	./scripts/local/build-libcamera-capture.sh

test:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$(CURDIR)/src python3 -m unittest discover -s tests -p 'test_*.py'
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$(CURDIR)/src python3 ./scripts/local/verify-runtime-abi.py

check:
	./scripts/local/check-repo.sh

check-ci:
	SENSORIUM_CHECK_PROFILE=ci ./scripts/local/check-repo.sh

check-release:
	SENSORIUM_CHECK_PROFILE=release ./scripts/local/check-repo.sh

dist:
	./scripts/package/dist-source.sh

package-deb:
	./scripts/package/build-deb-package.sh

package-meta:
	./scripts/package/render-package-templates.sh

qemu-e2e:
	./scripts/qemu/qemu-e2e.sh

qemu-ci-smoke:
	./scripts/qemu/qemu-ci-smoke.sh

qemu-linux7-e2e:
	QEMU_DISTRO=debian-trixie QEMU_LIBCAMERA_APT_RELEASE=sid QEMU_EXPECT_KERNEL_MAJOR=7 ./scripts/qemu/qemu-e2e.sh

qemu-linux7-ci-smoke:
	QEMU_DISTRO=debian-trixie QEMU_CI_LIBCAMERA_APT_RELEASE=sid QEMU_EXPECT_KERNEL_MAJOR=7 ./scripts/qemu/qemu-ci-smoke.sh

benchmark:
	./scripts/qemu/qemu-benchmark.sh

benchmark-matrix:
	./scripts/qemu/qemu-benchmark-matrix.sh

benchmark-compare:
	./scripts/benchmarks/compare-benchmarks.py

benchmark-check:
	./scripts/benchmarks/benchmark-check.sh

burnin:
	./scripts/qemu/qemu-burnin.sh

camera-matrix:
	./scripts/qemu/qemu-camera-matrix.sh

clean:
	$(MAKE) -C $(KDIR) M=$(MODULE_DIR) clean

distclean: clean
	rm -rf .cache
	rm -rf __pycache__ scripts/__pycache__ src/__pycache__ src/sensorium/__pycache__ src/sensorium/*/__pycache__ tests/__pycache__
	rm -rf build src/*.egg-info
	rm -f .env.kernel .env.remote
	rm -f tools/libcamera-capture tools/libcamera-record tools/rgb24-to-rggb10

tree:
	@find . -maxdepth 3 -type f | sort
