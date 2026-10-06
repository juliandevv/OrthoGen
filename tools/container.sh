#!/usr/bin/env bash
# Run a Python script from this project inside the WebODM NodeODX container, on the
# ODM engine's own Python (has numpy/opencv/rasterio/GDAL/scipy/opendm; ODM at /code).
# The project is mounted at /project (cwd), so relative paths and work/ resolve there.
#   Usage: tools/container.sh <script.py relative to project root> [args]
# Env: ORTHOGEN_IMAGE (default webodm/nodeodx:gpu), ORTHOGEN_GPUS (default all; "" = no GPU),
#      ORTHOGEN_CONTAINER_NAME (optional container name, e.g. for tools/sample_resources.sh)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${ORTHOGEN_IMAGE:-webodm/nodeodx:gpu}"
SCRIPT="$1"; shift

OPTS=(--rm --user "$(id -u):$(id -g)"
      -e HOME=/tmp -e PYTHONPYCACHEPREFIX=/tmp/pycache   # non-root: no writable $HOME or /code
      -v "$ROOT":/project -w /project --entrypoint python3)
GPUS="${ORTHOGEN_GPUS-all}"
[ -n "$GPUS" ] && OPTS+=(--gpus "$GPUS")
[ -n "${ORTHOGEN_CONTAINER_NAME:-}" ] && OPTS+=(--name "$ORTHOGEN_CONTAINER_NAME")
[ -t 0 ] && [ -t 1 ] && OPTS+=(-it)                     # Ctrl-C reaches ODM when interactive

exec docker run "${OPTS[@]}" "$IMAGE" "/project/$SCRIPT" "$@"
