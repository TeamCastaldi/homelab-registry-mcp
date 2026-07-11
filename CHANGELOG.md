# Changelog

## [0.14.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.13.0...v0.14.0) (2026-07-11)


### Features

* **discovery:** move Traefik/Authentik connection out of initial install ([dc2829c](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/dc2829c5d0f92bc1c8d8783c7eb63a400e4c6f8a))


### Bug Fixes

* address Copilot review on discovery-connect PR ([29d0dcd](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/29d0dcdb919bdb7551a92ef55176ab39ba7a6192))

## [0.13.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.12.2...v0.13.0) (2026-07-11)


### Features

* implement Phase 7 brownfield adoption and secret interception ([657f5fb](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/657f5fb2a03799813ff2a420ce435c2346e51a29))
* Phase 7 brownfield adoption and secret interception ([98a5e9e](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/98a5e9e966415a7bb8f0a3b58b068d683c67a2df))


### Bug Fixes

* address Copilot review on Phase 7 adoption PR ([62a2598](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/62a259844dfdde49c945abcaad8303b4e516d161))


### Documentation

* update implementation plan with completed phases and add new phase for brownfield adoption ([d83b63b](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d83b63bf5de2f16983c415b67f29f7a7be0e7db2))

## [0.12.2](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.12.1...v0.12.2) (2026-07-10)


### Bug Fixes

* address Copilot review on bootstrap.sh distro fix ([49e05f9](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/49e05f9d71bed8c2f19236cd9da96a6b6461a135))
* bootstrap.sh supports Ubuntu control-plane nodes, not just Debian/Pi ([678c85f](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/678c85f78d4f8658c21f412874de736cd90e7777))
* make bootstrap.sh work on Ubuntu control-plane nodes, not just Debian/Pi ([0b87bad](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/0b87bad904d0b1300a56a78e1432d560574e3ec5))

## [0.12.1](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.12.0...v0.12.1) (2026-07-10)


### Bug Fixes

* address Copilot review on Phase 6 doc scrub ([f413b19](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/f413b19e6f1440708c48c587bb0584e3eacf1ad5))

## [0.12.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.11.0...v0.12.0) (2026-07-10)


### Features

* add SMTP notification provider for proactive proposal emails ([0866a5d](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/0866a5d1cd2dda90898b15fa3044e5a06bd1480c))


### Bug Fixes

* address Copilot review on SMTP notification provider ([e1eb6eb](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/e1eb6ebc3f6562111a613afe3bbb991fc0bb4e73))

## [0.11.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.10.0...v0.11.0) (2026-07-10)


### Features

* add Phase 4 GitOps CD deploy pipeline (docker-stack-deploy + reusable workflow) ([f27d016](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/f27d01610a447be2bd2e3dd424197a68a25f116e))


### Bug Fixes

* address Copilot review on install-instructions docs ([d4917eb](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d4917eba241c030b7e5f28d73b900fc92fce28ed))


### Documentation

* document the install.sh one-shot control-plane install ([db7c330](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/db7c3302f6562c589ce26bdb5d87d2953b335380))
* document the scripts/install.sh one-shot control-plane install ([d300ce7](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d300ce76a0391ebfb204e9bb67bb64aca094f86c))
* update phased implementation plan with completion status and new phases ([8c76f24](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/8c76f24493c4913f843faaec32c71d637900328b))

## [0.10.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.9.0...v0.10.0) (2026-07-09)


### Features

* add conversational GitOps loop for open proposal PRs (Phase 3) ([df452a8](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/df452a87b1adb0566562b795add07cac2d7c2d6a))


### Bug Fixes

* address Copilot review on the conversational GitOps loop ([2cc0ca6](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/2cc0ca6cf2f63866e75fcb9cad63d02035959e47))

## [0.9.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.8.0...v0.9.0) (2026-07-09)


### Features

* add startup health checks and read-only degradation (Phase 2) ([2c32617](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/2c3261788c07b96652fcd5800b6312f8e05c16f3))


### Bug Fixes

* address Copilot review on health checks ([546dc65](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/546dc65b104ef732a3e843c872b8c82c969849fa))

## [0.8.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.7.3...v0.8.0) (2026-07-09)


### Features

* add curl-bash installer for the control-plane node (Phase 1) ([b25ea56](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/b25ea56da92e899d1688d70b2583f6a29df3638a))


### Bug Fixes

* address Copilot review on install pipeline ([8fec9cf](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/8fec9cf7ac7f02e133f8fa1d98c7bd7661c2154c))

## [0.7.3](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.7.2...v0.7.3) (2026-07-01)


### Bug Fixes

* guard importlib.metadata lookup with fallback ([ed0252f](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/ed0252fc3c6863f1b8de17a4e7c10565a531da5f))
* read version dynamically from package metadata ([7b5378b](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/7b5378bca2cc5307a71e1ca8e06736439425ae8a))
* read version dynamically from package metadata ([0c98420](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/0c98420e590409233d99576b90475b50ff966585))

## [0.7.2](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.7.1...v0.7.2) (2026-07-01)


### Bug Fixes

* **ci:** allow manual publish dispatch and fix release-please token ([62f9749](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/62f974957b6db88f8ebbf577bb028d2a0ab908a9))
* **ci:** allow manual publish dispatch and fix release-please token ([127cddc](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/127cddc93d2fbb97f869e952db2a813539033ff9))
* **ci:** guard publish job to tag refs only ([1186379](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/1186379823862de62c6af76c5453a2a70e25c4c5))

## [0.7.1](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.7.0...v0.7.1) (2026-07-01)


### Bug Fixes

* **ci:** build multi-arch Docker image for amd64 and arm64 ([d94a6d6](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d94a6d675c16a551463566ae1dfc3ad5242671c5))
* **ci:** build multi-arch Docker image for amd64 and arm64 ([cfef722](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/cfef722e434d92e42b6f7ef5016170c5a891b1e0))

## [0.7.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.6.2...v0.7.0) (2026-07-01)


### Features

* add GitHub git provider and real hardware-discovery-status ([c577e00](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/c577e003ae5e3afbc0320d86f91e587a031a4109))
* add ruthless code review prompt for enhanced code evaluation ([fae579e](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/fae579e5bb6f7a2088aabcc00c128715c88d36a0))
* **agents:** add Python Security Reviewer for PII detection and security audits ([f3290d8](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/f3290d806ffd02d5ba8b45d3a25665f5b24a6f6b))
* initial public release — clean migration from ncastaldi/homelab-registry-mcp ([3f8c073](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/3f8c0739195f041924572c3412a7d04dbb57e878))
* **phase-c:** git-crypt secrets tools + homelab repo bootstrap ([4ce78f3](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/4ce78f3be415da2c56e67a01f358d6172f861995))
* **phase-c:** git-crypt secrets tools + homelab repo bootstrap ([c2d980e](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/c2d980e6b087a326ca74ae78eff9f652ae145bcd))
* **phase-d:** document service migration from Heimdall to Watchtower with detailed steps ([975e394](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/975e394efce199cb098bbc1038503941f9aea4d7))
* **phase-d:** migrate to Watchtower — port binding + Traefik static backend ([130869d](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/130869df6c6630872b3177918af438a4b3a93d7c))
* **phase-d:** migrate to Watchtower — port binding + Traefik static backend ([3d23b19](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/3d23b1971409fc895af66ff216e51564d3ad3726))
* **prompts:** add structured prompt files for GitHub Copilot workflows ([15128c1](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/15128c114aeda727375335646b4bffe7abd98490))
* pull image from GHCR instead of building from source ([77392b1](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/77392b19ecc51ffd9963f9eaf5de399ace583110))
* pull image from GHCR, drop build-from-source requirement ([b3eff47](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/b3eff4750eb5cd3481fd485703e406c8a3a5c8aa))
* **security:** add comprehensive secret scanning report with findings and recommendations ([0c5cc43](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/0c5cc43118b1ef0b11d5c9d7d74f5c5b429ffb7f))


### Bug Fixes

* **.gitignore:** clean up Ansible runtime entries and ensure proper ignore patterns ([6c8de52](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/6c8de528dc57afff52b29bc69add3c3d5e877627))
* add ANTHROPIC_API_KEY to .env.example ([6d8ad05](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/6d8ad0594eb828bc1bc21dfef20b5d18f3632a0c))
* add ANTHROPIC_API_KEY to .env.example ([a34499a](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/a34499af993f87bbd619136447947d4d8e42d25d))
* add missing uv.lock for Docker build ([87f524e](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/87f524e66bf8673e3cbaec8bf035419a4baae209))
* address Copilot review — version pinning and seed docs ([49138fd](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/49138fdec3964f9a23d224c9d864be4389a3aeef))
* restore .gitignore lost during rebase conflict ([1b20d9a](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/1b20d9a7d2798212758d17bfce765fd732f77cd8))
* **secrets:** block absolute paths to prevent arbitrary file read/write ([370dc6f](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/370dc6fa12917cbd009bc1b9f9c5c4ac63b53fa0))
* **setup-script:** generic password manager instructions + base64 how-to ([e9edb97](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/e9edb978248b9030463dd80459cfe33f93d56e08))
* **setup-script:** generic password manager instructions + base64 how-to ([c1d189c](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/c1d189cd5fe7e8e70075f28d6c3222a546294b3a))
* **setup:** cross-platform compatibility for setup-homelab-repo.sh ([01a65de](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/01a65de6e514e81045fe0da72ca907ebac57845c))
* **setup:** cross-platform compatibility for setup-homelab-repo.sh ([d11c06a](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d11c06ac5f8038b76601084e407415841a1e44af))
* **setup:** update stale header defaults and clarify .env path expansion ([7ffa483](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/7ffa483f937a03c7a092e69d2c962eafa2c1bb00))


### Documentation

* add governance, contribution, and project structure guides ([6bcc2bc](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/6bcc2bc5ff12df6026d2d9f18cc34121c2ef2a73))
* add MIT license ([23fc77e](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/23fc77e65628b919f26bc39bdcd6200b6d6a9609))
* add project-template governance + doc-structure artifacts and align prompts ([fa39c04](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/fa39c04f28bb46eabf4aca40a7f4ddd7d124d179))
* add ruthless code review (2026-06-30) ([b1fbe44](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/b1fbe44bf738a9da72243dd2bc89740201b15195))
* reflect secrets path-validation fix and cross-platform setup script ([c4b44f1](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/c4b44f1710dcc0086b04e827ad3166a2fb0f883f))
* reflect secrets path-validation fix and cross-platform setup script ([6668258](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/6668258660ebc4e4bce103bbae17fbc0a8f5351d))
* resolve merge conflict in README Documentation section ([a07262d](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/a07262da2c730a498aec98b2e1c6057c3e61455b))
* sanitize personal details and fix README documentation links ([39c6837](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/39c683793bb253ef2d83ed8b1cc7f12e336d024c))
* sync CLAUDE.md and README with hardware module and current phase status ([0de21cf](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/0de21cf99a3ff4d2ed9271faac6c3baebbad6d35))
* sync CLAUDE.md and README with hardware module and current phase status ([ace2f4d](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/ace2f4dd2883220c0a7c61aaef14bb776bf45f98))
* **tests:** note pytest-cov requirement for the coverage example ([7c8bb57](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/7c8bb57b5c84945d52a6682f4c2259750527ac0c))
* update ADR-001 to reflect changes in control plane requirements and repository structure ([70d681e](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/70d681eddbe2ab671184ffde092a36f67c1b1e20))
* update NFS mount instructions for control plane to reflect new volume paths and options ([89f707b](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/89f707b7176738479b6ce8727e41c14ada239c10))
