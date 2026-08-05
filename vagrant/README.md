# Vagrant fixtures

Disposable Vagrant + libvirt VMs used for local testing. Each fixture lives
in its own subdirectory with its own `Vagrantfile` and README — nothing here
is meant to persist between runs.

| Directory | Purpose |
|---|---|
| [`slow-loop/`](slow-loop/README.md) | High-fidelity Debian VM for hand-testing `scripts/install.sh` / `scripts/bootstrap.sh` — the slow tier of the [two-tier installer validation strategy](../CLAUDE.md#installer-validation-two-tier). |
| [`workload-node/`](workload-node/README.md) | Live Traefik + demo services fixture for developing/testing registry-mcp's discovery and linking code against something real. Unrelated to installer validation. |

Add new fixtures the same way: a new subdirectory with its own `Vagrantfile`
and README, and a row in the table above.

## Prerequisites (all fixtures)

- [Vagrant](https://developer.hashicorp.com/vagrant/downloads)
- The `vagrant-libvirt` plugin: `vagrant plugin install vagrant-libvirt`
- A working libvirt/KVM setup (Linux host only — check with
  `virt-host-validate qemu`). There's no VirtualBox/macOS/Windows variant of
  any fixture here today; open a PR if you add one.

## Working with more than one Vagrantfile

Vagrant only ever looks for a file literally named `Vagrantfile` in the
current directory (walking up to parent directories if it isn't found there)
— there's no way to point it at an alternately-named file in normal use. So
"more than one Vagrantfile" always means more than one *directory*, each
holding its own `Vagrantfile`: `cd` into the one you want before running any
`vagrant` command. Each directory gets fully independent `.vagrant/` state
(box cache, machine ID, snapshots), and under the libvirt provider the domain
name defaults to `<directory-basename>_<machine-name>`, so fixtures here
don't collide with each other even when run at the same time.
