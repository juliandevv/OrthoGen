#!/usr/bin/env bash
# Sample a container's CPU/memory (docker stats: excludes page cache) + GPU usage to CSV,
# from when the container appears until it exits. Pair with ORTHOGEN_CONTAINER_NAME.
#   Usage: tools/sample_resources.sh <container-name> <out.csv> [interval_s=5]
set -uo pipefail
NAME="$1"; OUT="$2"; INTERVAL="${3:-5}"
to_mib() {  # "1.5GiB" / "512MiB" / "800kB" -> MiB
  awk -v v="$1" 'BEGIN { n = v + 0; u = v; gsub(/[0-9.]/, "", u)
    f = (u ~ /^Gi?B$/) ? 1024 : (u ~ /^Ti?B$/) ? 1048576 : (u ~ /^[kK]i?B$/) ? 1/1024 : (u == "B") ? 1/1048576 : 1
    printf "%.0f", n * f }'
}
until docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true; do sleep 1; done
echo "utc,elapsed_s,cpu_pct,mem_mib,gpu_mem_mib,gpu_util_pct" > "$OUT"
T0=$(date +%s)
while docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true; do
  IFS=, read -r CPU MEM < <(docker stats --no-stream --format '{{.CPUPerc}},{{.MemUsage}}' "$NAME" 2>/dev/null)
  [ -z "${MEM:-}" ] && break
  IFS=', ' read -r GMEM GUTIL < <(nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1)
  echo "$(date -u +%FT%TZ),$(( $(date +%s) - T0 )),${CPU%\%},$(to_mib "${MEM%% / *}"),${GMEM:-},${GUTIL:-}" >> "$OUT"
  sleep "$INTERVAL"
done
