# Build and verify the reviewed source bundle

`make_source_bundle.sh` packages the already-built, clean Penguin checkout at
the exact supplied 40-character commit. It never installs an npm release of the
CLI, starts a model, starts Harbor, or selects an experiment stage. The old
`tools/make_install_bundle.sh` remains unchanged.

Build in an independent checkout of the reviewed fork commit, using the pinned
pnpm version and frozen lockfile. Install dependencies, then build the runtime
packages; `pnpm -r build` also builds Web without running the repository's
top-level `link:cli` command, which could replace another environment's CLI.
The packaging command itself does not rebuild source or silently refresh a
stale build. Both the CLI and core must report the requested clean commit.

```bash
# Run inside the independent Penguin checkout after fetching the reviewed commit.
git checkout --detach <reviewed-40-character-commit>
pnpm install --frozen-lockfile
pnpm -r build

# Run from the benchmark checkout. Paths may contain spaces.
bash experiments/gdpevo/evo-harness/v1/make_source_bundle.sh \
  /absolute/path/to/reviewed-penguin-checkout \
  <reviewed-40-character-commit> \
  /absolute/path/to/bundles/evo-reviewed-source.tar.gz \
  24.18.0
```

The builder uses `pnpm --filter @prismshadow/penguin-cli deploy --prod --offline`
to include production dependencies and workspace plugins. Missing cached
dependencies are an error; explicitly prepare the checkout's dependencies and
retry. It reuses `agents/penguin_agent/node_install.sh` to install a separate,
checksum-verified official Node runtime. The optional fifth argument is that
script's Node distribution base URL; its checksum validation stays enabled.
Build on the same Linux/glibc architecture as the Harbor containers.

The archive has the adapter's `penguin-bench/` layout. Its `bin/penguin` invokes
`/opt/penguin-bench/node/bin/node` and the deployed CLI at an absolute
`/opt/penguin-bench/...` path. No source checkout, shared pnpm store, global CLI,
or old WikiSkill installation is required after extraction. Any escaping or
broken dependency symlink fails the build.

`bundle.json` records source commit/tree, source lockfile hash, package and Node
versions, architecture, CLI hash, plugin hash and file map, actual native plugin
loader results, and every payload file/symlink. The builder checks that CLI,
core and server dist files equal the source build and that every published
Evo-Harness plugin file equals the clean commit. It executes only `version`
and the native plugin loader. The archive's SHA-256 is written beside it as
`<archive>.sha256`; an existing output is never overwritten.

To verify an extracted bundle without starting a model:

```bash
sha256sum -c /absolute/path/to/bundles/evo-reviewed-source.tar.gz.sha256
mkdir -p /absolute/path/to/new-verification-directory
tar -xzf /absolute/path/to/bundles/evo-reviewed-source.tar.gz \
  -C /absolute/path/to/new-verification-directory
python3 -B experiments/gdpevo/evo-harness/v1/source_bundle.py verify \
  /absolute/path/to/new-verification-directory/penguin-bench \
  <reviewed-40-character-commit>
```

Before any trial, freeze the verified archive's absolute path and SHA-256 in
the experiment configuration and public Harbor launch. Pass
`--ak install_bundle=<absolute-archive>` plus
`--ak penguin_version=<bundle.json penguin_version>` and
`--ak node_version=<bundle.json node_version>`. The package version is normally
`0.2.16`; it is distinct from the git-describe string printed by `--version`.
For this experiment an absent or mismatched bundle stops dispatch: the shared
README's optional network/npm fallback is not permitted.

On gpu01, separately verify the host's absolute CLI entry and installed method
Skill, then inspect the actual container installation record and version/plugin
probe against this manifest. Preserve isolated `PENGUIN_HOME`, project, agent
and run directories. A successful source build alone does not establish that
the deployed server, host CLI or container is using it. These checks are still
model-free; launching the experiment requires its separate current budget.
