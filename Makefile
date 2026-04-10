KDIR ?= /lib/modules/$(shell uname -r)/build
MODULE_DIR := $(CURDIR)/kernel

.PHONY: help module tools check dist package-deb package-meta clean distclean tree

help:
	@echo "Targets:"
	@echo "  make module  - build the out-of-tree kernel module against KDIR"
	@echo "  make tools   - build local helper tools"
	@echo "  make check   - run repo-level publishability checks"
	@echo "  make dist    - create the versioned source tarball under dist/"
	@echo "  make package-deb - build the Debian DKMS package"
	@echo "  make package-meta - render Alpine and Arch package metadata"
	@echo "  make clean   - remove kernel build artifacts"
	@echo "  make distclean - remove kernel artifacts, local tool binaries, and cache"
	@echo "  make tree    - print the repo layout"
	@echo
	@echo "Override KDIR to point at a prepared kernel build tree if needed."

module:
	$(MAKE) -C $(KDIR) M=$(MODULE_DIR) modules

tools:
	./scripts/build-libcamera-capture.sh

check:
	./scripts/check-repo.sh

dist:
	./scripts/dist-source.sh

package-deb:
	./scripts/build-deb-package.sh

package-meta:
	./scripts/render-package-templates.sh

clean:
	$(MAKE) -C $(KDIR) M=$(MODULE_DIR) clean

distclean: clean
	rm -rf .cache
	rm -f .env.kernel .env.remote
	rm -f tools/libcamera-capture tools/libcamera-record tools/rgb24-to-rggb10

tree:
	@find . -maxdepth 3 -type f | sort
