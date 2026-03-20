#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
dist_dir="${repo_root}/webui/dist"
static_dir="${repo_root}/src/minibot/server/static"
assets_dir="${static_dir}/assets"

if [ ! -f "${dist_dir}/index.html" ]; then
  echo "missing ${dist_dir}/index.html; build the WebUI first" >&2
  exit 1
fi

mkdir -p "${assets_dir}"
cp "${dist_dir}/index.html" "${static_dir}/index.html"
cp "${dist_dir}/assets/"* "${assets_dir}/"

refs=$(grep -oE '/assets/[^" ]+' "${static_dir}/index.html" | sed 's#^/assets/##')

for file in "${assets_dir}"/*; do
  [ -e "${file}" ] || continue
  base="$(basename "${file}")"
  keep=false
  for ref in ${refs}; do
    if [ "${base}" = "${ref}" ]; then
      keep=true
      break
    fi
  done
  if [ "${keep}" = false ]; then
    rm "${file}"
  fi
done

python - "${static_dir}/index.html" "${assets_dir}" <<'PY'
import pathlib
import re
import sys

index_path = pathlib.Path(sys.argv[1])
assets_dir = pathlib.Path(sys.argv[2])

refs = sorted(set(re.findall(r'/assets/([^" ]+)', index_path.read_text(encoding="utf-8"))))
actual = sorted(path.name for path in assets_dir.iterdir() if path.is_file())

if refs != actual:
    print("static asset mismatch", file=sys.stderr)
    print(f"index refs: {refs}", file=sys.stderr)
    print(f"assets dir: {actual}", file=sys.stderr)
    raise SystemExit(1)
PY

ls -1 "${assets_dir}"
cat "${static_dir}/index.html"
