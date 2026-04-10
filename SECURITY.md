# Security Policy

## Scope

`sensorium` is a kernel-facing simulation project. Bugs in this repo can affect:

- kernel stability
- media device exposure
- remote test hosts used for validation

Please treat crashes, memory corruption, or privilege-boundary issues as
security-relevant, especially if they can be triggered from userspace through
the inject or capture interfaces.

## Reporting

For now, report security-sensitive issues privately to the maintainers instead
of opening a public issue with a full exploit recipe.

Include:

- affected commit or branch
- host kernel version
- userspace stack details
- whether the issue reproduces locally, remotely, or both
- minimal reproduction steps

## Hardening expectations

Changes should prefer:

- strict queue and format validation
- safe teardown behavior
- bounded buffer access
- conservative behavior under underrun or mode-switch races

