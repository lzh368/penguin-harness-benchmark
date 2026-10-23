#!/bin/bash
# Package an already-built, clean Penguin checkout. No model/server/Harbor run.
# Usage: make_source_bundle.sh SOURCE COMMIT OUTPUT.tar.gz [NODE_VERSION] [NODE_DIST_URL]
set -euo pipefail

if [ "$#" -lt 3 ] || [ "$#" -gt 5 ]; then
  echo "usage: $0 SOURCE COMMIT OUTPUT.tar.gz [NODE_VERSION] [NODE_DIST_URL]" >&2
  exit 2
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$(cd "$1" && pwd)"
COMMIT="$2"
OUTPUT="$(python3 -B -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$3")"
NODE_VERSION="${4:-24.18.0}"
NODE_DIST="${5:-https://nodejs.org/dist}"
python3 -B "$HERE/source_bundle.py" check-source "$SOURCE" "$COMMIT"
[ "$(uname -s)" = Linux ] || { echo "Linux/glibc build host required" >&2; exit 2; }
[ ! -e "$OUTPUT" ] && [ ! -e "$OUTPUT.sha256" ] || { echo "output already exists" >&2; exit 2; }
mkdir -p "$(dirname "$OUTPUT")"
mkdir "$OUTPUT.lock" || { echo "bundle build already running" >&2; exit 2; }
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/evo-source-bundle.XXXXXX")"
PARTIAL="$OUTPUT.partial.$$"
trap 'rm -rf "$STAGE" "$OUTPUT.lock"; rm -f "$PARTIAL"' EXIT
ROOT="$STAGE/penguin-bench"
APP="$ROOT/npm/lib/node_modules/@prismshadow/penguin-cli"
mkdir -p "$ROOT/bin" "$(dirname "$APP")"

# pnpm deploy produces a relocatable production dependency graph, including
# workspace plugin packages. Offline forbids a fallback to a registry CLI.
(cd "$SOURCE" && pnpm --filter @prismshadow/penguin-cli deploy --prod --offline "$APP")
sh "$HERE/../../../../agents/penguin_agent/node_install.sh" "$NODE_VERSION" "$ROOT/node" "$NODE_DIST"
cat > "$ROOT/bin/penguin" <<'SHIM'
#!/bin/sh
exec /opt/penguin-bench/node/bin/node /opt/penguin-bench/npm/lib/node_modules/@prismshadow/penguin-cli/dist/penguin.js "$@"
SHIM
chmod 755 "$ROOT/bin/penguin"

# Isolate version inspection from the operator's HMR/data root. No server starts.
PENGUIN_HOME="$STAGE/verification-home" "$ROOT/node/bin/node" "$APP/dist/penguin.js" \
  version --json --root "$STAGE/verification-home" > "$ROOT/cli-build.json"
"$ROOT/node/bin/node" --input-type=module - "$APP" > "$ROOT/native-plugin-probe.json" <<'JS'
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const app = process.argv[2];
const core = await import(pathToFileURL(path.join(app, 'node_modules/@prismshadow/penguin-core/dist/index.js')).href);
const plugin = core.loadLibraryPlugins().find(p => p.name === 'evo-harness-rsi-toolkit');
if (!plugin || plugin.category !== 'rsi' || plugin.skills.length !== 1 ||
    plugin.skills[0].name !== 'evo-harness-rsi-toolkit' ||
    !plugin.skills[0].files?.['scripts/evo.py'] || !plugin.skills[0].files?.['scripts/control.py'] ||
    !plugin.hooks?.files?.['guard.mjs'] || plugin.hooks.manifest.version !== plugin.skills[0].version ||
    ['user_prompt','pre_tool_use','stop'].some(point => !plugin.hooks.manifest[point]?.length))
  throw new Error('Evo-Harness native plugin and guard load failed');
console.log(JSON.stringify({build: core.buildInfo(), plugin: {
  name: plugin.name, version: plugin.version, category: plugin.category,
  hook_version: plugin.hooks.manifest.version, hook_files: Object.keys(plugin.hooks.files).sort(),
  skills: plugin.skills.map(s => ({name: s.name, version: s.version, support_files: Object.keys(s.files ?? {}).sort()}))
}}));
JS
python3 -B "$HERE/source_bundle.py" record "$SOURCE" "$COMMIT" "$ROOT" "$NODE_VERSION"
python3 -B "$HERE/source_bundle.py" verify-staged "$ROOT" "$COMMIT"
python3 -B "$HERE/source_bundle.py" check-source "$SOURCE" "$COMMIT"
# Normalize archive metadata only. pnpm may hardlink deployed files to the
# shared store/source build: chmod on staging would mutate those outside files.
# X preserves executable files and makes directories traversable; clear special
# bits explicitly (GNU tar otherwise preserves a directory's setgid bit).
tar --mode=u=rwX,go=rX,a-s,a-t -czf "$PARTIAL" -C "$STAGE" penguin-bench
python3 -B "$HERE/source_bundle.py" verify-archive "$PARTIAL" "$COMMIT"
# Publish only after all checks succeed. The exclusive lock protects this output.
mv "$PARTIAL" "$OUTPUT"
sha256sum "$OUTPUT" > "$OUTPUT.sha256"
cat "$OUTPUT.sha256"
