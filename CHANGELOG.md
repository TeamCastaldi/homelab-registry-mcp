# Changelog

## [0.26.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.25.0...v0.26.0) (2026-09-03)


### Features

* Dockhand update webhook → staged proposals (ADR-010) ([bde1bee](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/bde1bee8aa26b83e8d4452d94847c821ba3293d3))


### Bug Fixes

* address Copilot review on the Dockhand webhook ([9ba6131](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/9ba6131d1e2231911f9d8d30f63547743a863d94))


### Documentation

* SOP for connecting Dockhand, plus a raw-payload debug setting ([d08ccc5](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d08ccc54b5fdab77e4d23aed5ff39924ac55c365))

## [0.25.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.24.0...v0.25.0) (2026-08-19)


### Features

* **chat:** add DOM builder for safe markdown rendering (Phase 2) ([4e7dfdf](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/4e7dfdf08fbcfd03d1e5fe1f2e7a4a22009eddfb))
* **chat:** add markdown parser for safe chat rendering (Phase 1) ([d11873c](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d11873cfb2de0c200d80a6916b93143d1f523371))
* **chat:** safe markdown rendering in /chat ([b765cee](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/b765cee35058a2780a92c0cf244da414915015c7))
* **chat:** wire safe markdown rendering into the message pipeline (Phase 3) ([96cf2d9](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/96cf2d97c98c9d7e685b9b225f76fbdfa2cffc42))


### Bug Fixes

* **chat:** address Copilot review findings on PR [#106](https://github.com/TeamCastaldi/homelab-registry-mcp/issues/106) (Phase 1) ([0b4f1a2](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/0b4f1a229a571efc08e0885dfc319d57dc976b6e))
* **chat:** address remaining Copilot suppressed-comment findings ([20af287](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/20af2879b7347bfb50b675e73cc589e6d9792180))
* **chat:** address WCAG link contrast and test log noise findings ([1c9b4ea](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/1c9b4ea6eadf90382e09e73ef1d8ad2aa7fdd030))
* **chat:** escape &lt; and &gt; in the fake DOM test harness's outerHTML ([625348f](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/625348f9b6f8796510546cb5001a80b187bc6408))
* **chat:** handle unterminated ** consistently in markdown parser ([2010958](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/201095896e75d95079c134bbf6bb2f2b822fd81f))
* **chat:** require a pipe in the table separator line itself ([a5d9762](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/a5d9762dce839ab0d65aaaf1ef03da7d7aa5fe6a))
* **chat:** select the markdown-render test script block by content ([0cf2cfc](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/0cf2cfc2980101b5a594a840a2d8ba44f0b64e43))


### Documentation

* **chat:** add rollback plan note (Phase 6) ([e3e2641](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/e3e264189655c79f728ee915fe54c9f056c23064))
* **chat:** record Phase 5 manual QA results ([62ff057](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/62ff057eae1d010fe47b387769dc3c1608fd2a58))
* reflect safe markdown chat rendering in CLAUDE.md and ADR-009 ([8d12332](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/8d12332971786f0303876b50db776cfe882219a0))

## [0.24.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.23.0...v0.24.0) (2026-08-19)


### Features

* **normalization:** implement compose file normalization engine ([52a1794](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/52a1794e501770814b2dcb43663aadb0d18d9af8))
* **normalization:** implement compose file normalization engine ([8ccbe5e](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/8ccbe5ef9df91cd95519f2646922f51f611f40f9))


### Bug Fixes

* **normalization:** address Copilot review findings on PR [#104](https://github.com/TeamCastaldi/homelab-registry-mcp/issues/104) ([b01fe95](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/b01fe951a284593fd994dfa6313bda48056aeea1))

## [0.23.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.22.0...v0.23.0) (2026-08-19)


### Features

* **chat:** reword discovery chip, drop hardware capacity, alphabetize ([80cca98](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/80cca983462196f98c266a305d7ecd460a2e6fdb))


### Bug Fixes

* **chat:** improve troubleshooting guidance for first-line help desk tech ([9fe1e94](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/9fe1e94eeaa9378fac40fa2a058fb22dd3981723))
* **chat:** make discovery chip's fallback deterministic ([4d28618](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/4d28618e46c6b7310dc62c31cd8d1a12af7e1589))

## [0.22.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.21.0...v0.22.0) (2026-08-18)


### Features

* **chat:** dark mode toggle, mobile quick-actions menu, review fixes ([31221a5](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/31221a50812d9686633c01031e701c6e2a1d6aaa))
* **chat:** full-width layout, file attachments, code copy button ([366ba87](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/366ba87e72d5bd87373bdce34a559e0873b962b4))


### Bug Fixes

* **chat:** address second Copilot review pass (a11y + secrets hygiene) ([bcf1f22](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/bcf1f2227bb0e9be75ffff749210f8f6e2a6e794))

## [0.21.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.20.0...v0.21.0) (2026-08-18)


### Features

* **chat:** add a Troubleshoot chip with a structured debugging prompt ([377c77b](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/377c77b5b076ee187d23aa94d532cd1298a6df3b))
* **chat:** add thinking-dots bubble and quick-action chips ([51694a7](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/51694a75d74ff8347f01abbc85b835bfde475d27))

## [0.20.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.19.0...v0.20.0) (2026-08-18)


### Features

* **chat:** add web chat interface backed by operator-run Ollama ([f0297b5](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/f0297b5d06bb7b54b3f329c42cb0419c7866c0f3))


### Documentation

* **chat:** update example Ollama host to 10.0.0.203 ([98e7d69](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/98e7d69716854c7c489090848148f38392eabdf0))

## [0.19.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.18.2...v0.19.0) (2026-08-18)


### Features

* **logging:** log session/tool/outcome on every tools/call ([30336fd](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/30336fdbb068c92299746d6e23f04be8e19dfe93))


### Bug Fixes

* **hardware:** reject invalid role/status before hardware-update-node writes ([492d10d](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/492d10ded4da5f2800d7d9600aff676f20f1f914))
* **hardware:** reject invalid role/status before hardware-update-node writes ([c0b1746](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/c0b1746a9948afc59d7797c4ae616e3b9ba3f72f))
* **logging:** patch the tool manager, not FastMCP.call_tool ([4854129](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/4854129a79d64d611064b138365ae63bc5979533))


### Documentation

* address Copilot review feedback on ADR-008 ([fa4f82a](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/fa4f82acd2adecbebd591559eb4711cd8a3f1401))
* **adr:** record MCP tool organization decision and tier assignment ([24df128](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/24df1280ba07e5e84ba42078261723f5777ad7a6))

## [0.18.2](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.18.1...v0.18.2) (2026-08-16)


### Bug Fixes

* **komodo:** use GetVersion instead of invalid Health type ([86c2b0d](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/86c2b0d06a2580a1034876fb05c7d290c88dffbc))

## [0.18.1](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.18.0...v0.18.1) (2026-08-16)


### Bug Fixes

* **komodo:** correct API path, payload, and auth ([9173c29](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/9173c29909f2237a60512248c61c6d1ecb084334))

## [0.18.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.17.0...v0.18.0) (2026-08-15)


### Features

* add read-only Komodo API integration ([194c045](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/194c0458e41c2ed7533138809f869e3a80e4a60f))
* add read-only Komodo API integration ([5ef9a97](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/5ef9a97ad1e840c28045bfe4ea80a0d3dd404f59))
* add Traefik Docker labels to docker-compose.yml ([bab7e03](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/bab7e03332aa79463dceafcaf7c5dbffa687256a))
* add Traefik Docker labels to docker-compose.yml ([857e4b4](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/857e4b467bb396b6d17bbe0e41c153974cbbcbd3))


### Bug Fixes

* address Copilot review on Komodo integration ([ffcf7d3](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/ffcf7d3c13b6623e966fbcf564368264c694f140))
* don't ship a live placeholder certresolver label ([42b84a6](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/42b84a651360b70a50fcc4824e7bd11765688d9f))
* install.sh crashes when run via curl-pipe (BASH_SOURCE[0] unbound) ([e76ef01](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/e76ef01b3f849dbb14b461e862f0e85c706529b7))
* point removal-rationale links at ADR-007, not ADR-006 ([fd86d17](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/fd86d17a4af480d2ae314f29c5870e5271a255a7))
* sudo -n false-negative and GIT_BASE_URL infinite prompt loop ([3eaff91](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/3eaff91cbf8e1b5ad4e89eada7765f171e8e7583))

## [0.17.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.16.0...v0.17.0) (2026-08-11)


### Features

* **install:** fold Ansible inventory setup into install.sh ([7c01eab](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/7c01eab8fb430061632f021ac3204e7e06cfb512))
* **install:** fold homelab config repo creation into install.sh ([d92db0a](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d92db0aeb582caf2be03be540093e97487d02eae))
* **install:** offer gh auth login inline instead of skip-with-instructions ([d57c327](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d57c3270280671006550e7d8c1fe282b45344512))
* replace ADR-005 monitoring stack with Komodo + Traefik on the Pi (ADR-006) ([5036267](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/5036267c922238549763b02c1642ed129085e801))


### Bug Fixes

* pin komodo-mongo image, explicit label value, require CONTROL_PLANE_HOST ([281c7b2](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/281c7b25a7e2e1ddacd057114bb8d5e85f76d070))
* silent set -e death on gh repo create/clone failure, missing git identity ([e8797e5](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/e8797e57efc87dc37c6e23f6bfa9767c8d1e7b4d))
* step-number drift, non-root /opt/homelab, unignored git-crypt key ([dfa67f3](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/dfa67f37399e14e8123962389fe2841e35fbf621))
* sudo fallback for homelab-repo clone dir missed stale unwritable dirs ([7afe084](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/7afe0847844bdebd422bffb9e03051694c29c9a6))


### Documentation

* **vagrant:** fix stale step number and monitoring/ reference ([bb27b4d](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/bb27b4d6e73b2e0365aaec3bdd442ee34c274025))

## [0.16.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.15.4...v0.16.0) (2026-08-05)


### Features

* **install:** default git provider to github, reuse Step 2 static IP for Homepage ([b7c0406](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/b7c04064091aef03c1d4bca65c48a9f1d33ec71a))


### Bug Fixes

* **bootstrap:** skip re-asking network prompts on --network-only ([19d1927](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/19d1927e88feb173c648352928e3698fb41fca4d))
* **install:** drop undocumented 'none' synonym for skipping git provider ([9fc420b](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/9fc420b1f9fb519ff57deefd8d102098b3765633))

## [0.15.4](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.15.3...v0.15.4) (2026-08-05)


### Bug Fixes

* **deps:** patch 8 Dependabot alerts (mcp, cryptography, aiohttp, json-repair) ([e039a13](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/e039a13d86a6ca10e36a536704282d1ec9bcff08))
* **deps:** patch 8 Dependabot alerts across mcp, cryptography, aiohttp, json-repair ([e9d1b50](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/e9d1b50a7e1c3166964a84c1ac16ee21fad17577))

## [0.15.3](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.15.2...v0.15.3) (2026-08-05)


### Bug Fixes

* auto-remediate ifupdown-unmanaged eth0 in bootstrap.sh Phase 6 ([a6c737e](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/a6c737ede7fc4e93cfc79b8ad7a68d02f25344d0))
* default static IP to the current DHCP lease, not a hardcoded /24 ([1674ef1](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/1674ef1239a986e220e92f375ca3ba629c59564d))
* don't rely on \s in awk for NetworkManager.conf editing ([f1b2aff](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/f1b2affee0bde051af83c608b5bfe496d7f78787))

## [0.15.2](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.15.1...v0.15.2) (2026-08-04)


### Bug Fixes

* **ci:** use non-shallow checkout for install-validation ([ca83ccf](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/ca83ccfc7cad41a59ede104a30370d5e8a3f7b53))
* don't crash-loop beszel-agent when no hub key is configured ([4a3d81a](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/4a3d81a3721ed6e704adbeb17459a4f701c6b77d))
* don't write a blank GIT_PROVIDER when the git prompt is skipped ([8aaa106](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/8aaa106b7f897c14afd552c29d63595f49c10de7))
* VERSION must be exported for install.sh's own clone to see it ([883c8e0](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/883c8e04919512741d9fd63c7c1d1461b22ebe90))

## [0.15.1](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.15.0...v0.15.1) (2026-08-04)


### Bug Fixes

* detect ifupdown vs netplan before diagnosing unmanaged NetworkManager interface ([9806bdb](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/9806bdbcd3c16d861042dae6aee7c62a074ad291))

## [0.15.0](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.14.2...v0.15.0) (2026-07-31)


### Features

* **docs:** add ADR-005 for Monitoring, Alerting, Disaster Recovery, and Ingress Architecture ([fc48965](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/fc48965604ed04ed29986b68a65dd70a2dc8feb0))
* **docs:** renamed from ARD to ADR ([d3fcef7](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d3fcef7b30ebbb3e2a367fd93cfdeae68d64610e))
* implement ADR-005 WUD webhook listener and monitoring stack ([8f471c1](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/8f471c19a52ba00ee9f159be1de81282af7c029c))


### Bug Fixes

* address Copilot review on PR [#65](https://github.com/TeamCastaldi/homelab-registry-mcp/issues/65) ([b29ba05](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/b29ba053d209b5af81af964dd73e025905bb39b5))


### Documentation

* mark Phase 7 brownfield adoption complete ([fb0231f](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/fb0231f24a80c685576533ea7adbc4d2daf54030))

## [0.14.2](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.14.1...v0.14.2) (2026-07-11)


### Documentation

* add comprehensive setup guide (docs/SETUP.md) ([342447e](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/342447e10e6e9fefa149df392aefd55bc12c17f1))

## [0.14.1](https://github.com/TeamCastaldi/homelab-registry-mcp/compare/v0.14.0...v0.14.1) (2026-07-11)


### Bug Fixes

* address Copilot review on pre_update_compatibility_check wording ([ac335de](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/ac335dea7108f4bb3dec997abf8199b4cca5ec65))
* set explicit GHA cache scope so tag-triggered builds share it ([c3eb51c](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/c3eb51cb4c71aee8e74ab7fd5781b5dcc8b8923d))


### Performance Improvements

* **ci:** add GitHub Actions layer cache to Docker build ([d7ccfd4](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/d7ccfd4a3b65989c4898c6ad646c88ce80d94101), [52d59f6](https://github.com/TeamCastaldi/homelab-registry-mcp/commit/52d59f6be9869013c211757737142c4d08bc9a5d))

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
