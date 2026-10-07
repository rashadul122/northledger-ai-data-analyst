#!/usr/bin/env bash
# What a first visit costs on the wire, per file and in total, as curl sees it (compressed if the server
# compresses). No browser. Read-only GET requests to the host you name.
#
#   tools/measure_first_load.sh https://northledger.halosyncs.com            read the page + start the demo
#   tools/measure_first_load.sh https://<name>.<account>.workers.dev          the preview, before the domain
#   tools/measure_first_load.sh BASE --ttfb 20                               also: 20 requests for / (median, p95)
#   tools/measure_first_load.sh BASE --list                                  print the URLs, fetch nothing
#
# The URL set is derived from the repository, not typed: the page and its two icons ("read"), then what
# "Try the sample" fetches ("demo": the engine worker, the pinned Pyodide files, the packed engine, the
# sample). Baseline on GitHub Pages + jsDelivr, 6 Oct 2026 (plan/CLOUDFLARE-PLAN.md section 1): read = 3
# requests, 429,465 B; demo = 14 requests, 15,980,563 B (jsDelivr's 11 files in brotli: 15,435,330 B).
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${1:?usage: measure_first_load.sh BASE_URL [--ttfb N] [--list]}"; shift || true
BASE="${BASE%/}"
TTFB=0; LIST=0
while [ $# -gt 0 ]; do case "$1" in --ttfb) TTFB="$2"; shift 2 ;; --list) LIST=1; shift ;; *) echo "unknown: $1" >&2; exit 2 ;; esac; done
URLS="$(python3 - "$BASE" <<'PY'
import json, sys
base = sys.argv[1]
cfg = json.load(open('deploy/cloudflare.config.json'))
pins = json.load(open(cfg['runtime']['pins']))
for p in ['/', '/favicon.svg', '/favicon-32.png']:
    print('read', base + p)
print('demo', base + '/engine/worker.js')
if cfg['runtime']['mode'] == 'self':
    tag = pins['tag']
    for f in [x['name'] for x in pins['files']] + ['pyodide-lock.json']:
        print('demo', '%s/%s/%s/%s' % (base, cfg['runtime']['dir'].strip('/'), tag, f))
print('demo', base + '/engine/northledger-browser.zip')
print('demo', base + '/engine/sample-messy.csv')
PY
)"
if [ "$LIST" = 1 ]; then echo "$URLS"; exit 0; fi
printf '%-5s %-10s %9s %8s  %s\n' group bytes_wire ttfb_ms code url
echo "$URLS" | while read -r g u; do
  curl -s --compressed -o /dev/null -w "$g %{size_download} %{time_starttransfer} %{http_code} $u\n" --max-time 300 "$u"
done | awk '{ printf "%-5s %10d %8.0f  %s  %s\n", $1, $2, $3*1000, $4, $5; n[$1]++; b[$1]+=$2 }
  END { printf "\nread: %d requests, %d bytes on the wire\ndemo: %d requests, %d bytes on the wire\n", n["read"], b["read"], n["demo"], b["demo"] }'
if [ "$TTFB" -gt 0 ]; then
  echo; echo "TTFB of $BASE/ over $TTFB requests (ms):"
  for i in $(seq 1 "$TTFB"); do curl -s -o /dev/null -w '%{time_starttransfer}\n' --max-time 30 "$BASE/"; done \
    | sort -n | awk '{ v[NR]=$1*1000 } END { printf "median %.0f   p95 %.0f   max %.0f\n", v[int((NR+1)/2)], v[int(NR*0.95+0.999)], v[NR] }'
fi
