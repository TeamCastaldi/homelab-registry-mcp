# Vagrant slow loop

A disposable, high-fidelity VM for testing changes to `scripts/install.sh` and
`scripts/bootstrap.sh` by hand. This is the *slow* tier of this project's
two-tier installer testing strategy — see
[CLAUDE.md's "Installer validation (two-tier)" section](../CLAUDE.md#installer-validation-two-tier)
for the full rationale and how this relates to the *fast* CI loop
(`.github/workflows/install-validation.yml`).

Use this when a change might depend on things the fast loop structurally
can't reproduce on a hosted `ubuntu-latest` runner:

- Real systemd and real network-interface ownership (the class of bug that
  motivated this: `ubuntu-latest` ships netplan, not ifupdown, so it can't
  reproduce a Debian/Raspberry-Pi-OS-specific `bootstrap.sh` Phase 6 bug)
- Actually applying the static IP (`bootstrap.sh --network-only` / `install.sh`
  Step 6) — the fast loop always sets `INSTALL_SKIP_NETWORK=true` and never
  exercises this at all
- Anything you want to watch happen interactively, prompt by prompt

For everything else — logic bugs, env-var plumbing, whether a container comes
up healthy — the fast loop is quicker and doesn't need any of this set up.
Prefer it first; reach for this when you need the fidelity above.

## Prerequisites

- [Vagrant](https://developer.hashicorp.com/vagrant/downloads)
- The `vagrant-libvirt` plugin: `vagrant plugin install vagrant-libvirt`
- A working libvirt/KVM setup (Linux host only — check with
  `virt-host-validate qemu`). There's no VirtualBox/macOS/Windows variant of
  this Vagrantfile today; open a PR if you add one.

## Usage

This loop clones from GitHub, the same as a real operator's
`curl -fsSL .../install.sh | bash` — it does **not** use your local working
tree (`synced_folder` is deliberately disabled; see the Vagrantfile). Push
your branch first:

```bash
git push -u origin your-branch-name
```

Then, from this directory:

```bash
vagrant up          # boots the VM, installs curl
vagrant ssh          # connects
```

Inside the VM, run the installer pointed at your branch:

```bash
export VERSION=your-branch-name
bash -c "$(curl -fsSL https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/scripts/install.sh)"
```

Must be two lines, and `VERSION` must be `export`ed, not just assigned:
`${VERSION}` in the `curl` URL is expanded by *this* shell as it builds the
command, which works either way as long as the assignment happened first —
but only an *exported* `VERSION` is inherited by the `bash -c` subprocess
that actually runs `install.sh`. Skip `export` (or collapse it back to one
line) and the URL still fetches the right `install.sh`, but that script
won't see `VERSION` in its own environment, so its internal clone silently
falls back to `main`.

`VERSION` here does double duty: it picks which revision of `install.sh` the
`curl` fetches *and* which ref `install.sh` itself then clones for
everything else (`bootstrap.sh`, `scripts/`, `monitoring/`) — so this
actually tests your branch end-to-end, not just its top-level `install.sh`.

Answer the prompts by hand — that's the point of this loop. Every prompt can
still be pre-seeded via an environment variable of the same name if you only
want to exercise a subset interactively (see `scripts/install.sh`'s header
comment). Never set `INSTALL_SKIP_NETWORK=true` here — that's a CI-only
escape hatch (see its comment in `scripts/install.sh`); skipping it defeats
the point of this loop, since the static-IP step is exactly the kind of thing
it exists to exercise.

When you're done with a round:

```bash
exit                 # leave the VM
vagrant destroy -f   # wipe it — install.sh/bootstrap.sh assume a fresh node
vagrant up           # next round starts clean
```

## Recording what you found

If a run surfaces a bug, fix it and validate the fix with the fast CI loop
before spending another slow-loop round:

```bash
gh workflow run install-validation.yml --ref your-branch-name
```

Reserve re-running this VM for changes the fast loop can't verify (see
above).
