#!/usr/bin/env bash
# Empaqueta solo el frontend para arrastrarlo a Netlify.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$ROOT" << 'PY'
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
static = root / "static"
out = root / "deploy_netlify.zip"
names = ("index.html", "style.css", "script.js")
missing = [name for name in names if not (static / name).is_file()]
if missing:
    raise SystemExit("Faltan archivos: " + ", ".join(missing))
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for name in names:
        archive.write(static / name, arcname=name)
print(out)
PY
