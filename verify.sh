#!/usr/bin/env bash
# Verification battery for the portfolio site. It reads the site and writes only to
# $VERIFY_TMP (scratch, default: a fresh temp folder) and $QA_OUT (screenshots and the
# print PDF, default: a temp folder outside the site, so nothing it writes can be published).
# It never edits a page, never publishes and never fetches data.
#
#   ./verify.sh                                   # everything
#   SKIP_VISUAL=1 ./verify.sh                     # skip screenshots and the print PDF
#   VERIFY_TMP=/some/scratch QA_OUT=/some/qa ./verify.sh
#
# Steps (each prints PASS, FAIL or SKIP with the reason; the exit code is 1 if any FAILs):
#   1 forecast   data/forecast_lab.json reproduces exactly from data/fred/*.csv
#   2 replays    agent-demo.html is exactly what tools/sanitize_agent_demo.py builds
#   3 render     headless Chrome renders index.html (with test-driver.html injected into a
#                copy) and agent-demo.html; tools/check_driver.py compares what index.html
#                shows with the JSON it was built from
#   4 honesty    tools/check_site.py: banned freshness words (pages and served Markdown), every
#                data-fact figure equals its JSON, no local paths or .env in anything served,
#                every link resolves under a GitHub Pages project path, a real contact route is
#                configured, no stray file (qa/, a root PDF, a test page) unlisted in .gitignore,
#                the demo's outside requests only as encoded, and no sentence the page once made
#                that is false now ("no third-party requests", "dropped before analysis")
#   5 sweep      no TODO/lorem, no em dash, wordmark present
#   6 guard      tools/check_try_guard.js: the demo's AI number guard reads numbers exactly as the
#                owner's proxy does (../insight-proxy when present) and its payload is only
#                {objective, findings, story} inside the proxy's limits; needs Node, no browser
#     ui         tools/check_ui.js drives the page in headless Chrome (filters, Esc, drill, phone
#                layouts at 320 to 390 px, dark theme, accessible names, the "Try it" demo's
#                refusals, report, AI consent flow and a real engine run of the sample, which
#                prints SKIP with the reason when cdn.jsdelivr.net cannot be reached); needs Node
#                and Playwright (PLAYWRIGHT_MODULE=/path/to/node_modules/playwright), else SKIP
#   7 visual     full-page and narrow screenshots plus a print PDF into $QA_OUT
#   8 print      tools/check_print.py reads that PDF page by page (poppler tools, else SKIP): the
#                hero on page 1, no near-empty page, no chart caption left at the foot of a page
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-$DIR/../agent-demo/venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
TMPD="${VERIFY_TMP:-$(mktemp -d "${TMPDIR:-/tmp}/nl-verify.XXXXXX")}"
OUT="${QA_OUT:-${TMPDIR:-/tmp}/northledger-qa}"
mkdir -p "$TMPD" "$OUT"
RESULTS=()
FAILED=0

record() { # name status detail
  RESULTS+=("$(printf '%-9s %-4s %s' "$1" "$2" "$3")")
  [ "$2" = "FAIL" ] && FAILED=1
  return 0
}

dump_dom() { # url out budget_ms marker
  local url="$1" out="$2" budget="$3" marker="${4:-}" pid i
  rm -f "$out"
  "$CHROME" --headless=new --disable-gpu --no-first-run --user-data-dir="$TMPD/chrome-dump-$$-$RANDOM" \
    --window-size=1280,900 --virtual-time-budget="$budget" --dump-dom "$url" > "$out" 2>/dev/null &
  pid=$!
  for i in $(seq 1 90); do
    if [ -s "$out" ] && { [ -z "$marker" ] || grep -q "$marker" "$out" 2>/dev/null; }; then break; fi
    sleep 1
  done
  sleep 1; kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  [ -s "$out" ]
}

echo "== portfolio site verification =="
echo "   site: $DIR"
echo "   scratch: $TMPD    qa output: $OUT"

# 1) forecast lab reproduces from its inputs
if [ -f "$DIR/data/forecast_lab.json" ]; then
  if "$PY" "$DIR/build_forecast.py" --check; then record forecast PASS "forecast_lab.json reproduces from data/fred"
  else record forecast FAIL "forecast_lab.json does not reproduce (run: build_forecast.py)"; fi
else
  record forecast FAIL "data/forecast_lab.json missing (run: tools/fetch_fred.py then build_forecast.py)"
fi

# 2) the replay page is the sanitizer's output
if [ -f "$DIR/../agent-demo/agent-demo.html" ]; then
  if "$PY" "$DIR/tools/sanitize_agent_demo.py" --check; then record replays PASS "agent-demo.html rebuilds from the recorded sessions"
  else record replays FAIL "agent-demo.html differs from the sanitizer's output (run: tools/sanitize_agent_demo.py)"; fi
else
  record replays SKIP "../agent-demo not present here, so the replay page cannot be rebuilt and compared"
fi

# 3) render both pages; judge the driver output on index.html
DOMS=()
if [ -x "$CHROME" ]; then
  "$PY" - "$DIR" "$TMPD" <<'EOF'
import os, sys
d, t = sys.argv[1], sys.argv[2]
page = open(os.path.join(d, "index.html"), encoding="utf-8").read()
driver = open(os.path.join(d, "test-driver.html"), encoding="utf-8").read()
if "</body>" not in page:
    sys.exit("index.html has no </body>")
# the copy sits in scratch, so rewrite relative links only for rendering, never on the site
open(os.path.join(t, "site_test.html"), "w", encoding="utf-8").write(
    page.replace("<head>", '<head><base href="file://%s/">' % d, 1).replace("</body>", driver + "</body>"))
EOF
  if dump_dom "file://$TMPD/site_test.html" "$TMPD/index.driver.html" 15000 TESTOUT; then
    if "$PY" "$DIR/tools/check_driver.py" "$TMPD/index.driver.html" --root "$DIR"; then record render PASS "index.html renders with no JS errors, a main landmark, named charts and no sideways scroll; figures are checked against their JSON in the honesty step"
    else record render FAIL "index.html driver assertions (see above)"; fi
    # rendered DOM for the honesty checks, without the harness output
    "$PY" - "$TMPD/index.driver.html" "$TMPD/index.dom.html" <<'EOF'
import re, sys
t = open(sys.argv[1], encoding="utf-8", errors="replace").read()
t = re.sub(r'<pre id="TESTOUT">.*?</pre>', "", t, flags=re.S)
t = re.sub(r'<base href="file://[^"]*">', "", t)
open(sys.argv[2], "w", encoding="utf-8").write(t)
EOF
    DOMS+=(--dom "index.html=$TMPD/index.dom.html")
  else
    record render FAIL "headless Chrome did not render index.html"
  fi
  if [ -f "$DIR/agent-demo.html" ] && dump_dom "file://$DIR/agent-demo.html" "$TMPD/agent-demo.dom.html" 12000 "session-card"; then
    DOMS+=(--dom "agent-demo.html=$TMPD/agent-demo.dom.html")
  fi
else
  record render FAIL "Chrome not found at \$CHROME; the rendered page could not be checked"
fi

# 4) honesty and shipping checks (built HTML plus rendered DOMs)
if "$PY" "$DIR/tools/check_site.py" --root "$DIR" ${DOMS[@]+"${DOMS[@]}"} --json "$TMPD/check_site.json"; then
  record honesty PASS "banned words, figures, shipped paths, links, contact, outside requests, no false promises"
else
  record honesty FAIL "see the CHECK lines above ($TMPD/check_site.json)"
fi

# 5) consistency sweep on the built page
if "$PY" - "$DIR/index.html" <<'EOF'
import sys
t = open(sys.argv[1], encoding="utf-8").read()
issues = [b for b in ("TODO", "lorem", "Lorem") if b in t]
if chr(0x2014) in t:
    issues.append("em dash present")
if t.count("NorthLedger") < 1:
    issues.append("wordmark missing")
for i in issues:
    print("    - " + i)
sys.exit(1 if issues else 0)
EOF
then record sweep PASS "no TODO/lorem, no em dash, wordmark present"
else record sweep FAIL "see the lines above"; fi

# 6) the demo's AI number guard and payload (Node only, no browser, no network)
if command -v node >/dev/null 2>&1; then
  if node "$DIR/tools/check_try_guard.js"; then record guard PASS "the demo's AI number guard matches the proxy's; the payload is only the allowed fields, within the proxy's limits"
  else record guard FAIL "see the GUARD FAIL lines above"; fi
else
  record guard SKIP "node not found"
fi

# 6b) behaviour in a real browser engine (Playwright), when Node and Playwright are available
if command -v node >/dev/null 2>&1; then
  CHROME="$CHROME" node "$DIR/tools/check_ui.js" "$DIR/index.html"
  rc=$?
  if [ $rc = 0 ]; then record ui PASS "every tools/check_ui.js behaviour check passed"
  elif [ $rc = 2 ]; then record ui SKIP "Playwright not found (set PLAYWRIGHT_MODULE)"
  else record ui FAIL "see the UI FAIL lines above"; fi
else
  record ui SKIP "node not found"
fi

# 7) visual battery for the owner's eyes (not a pass/fail judgement of layout)
shot() { # out args...
  local out="$1"; shift
  rm -f "$out"
  "$CHROME" --headless=new --disable-gpu --no-first-run --hide-scrollbars "$@" >/dev/null 2>&1 &
  local pid=$! i
  for i in $(seq 1 90); do [ -s "$out" ] && break; sleep 1; done
  sleep 2; kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  [ -s "$out" ]
}
if [ "${SKIP_VISUAL:-0}" = "1" ]; then
  record visual SKIP "SKIP_VISUAL=1"
elif [ -x "$CHROME" ]; then
  ok=1
  shot "$OUT/page_tall.png" --user-data-dir="$TMPD/cb-tall" --window-size=1440,20000 --virtual-time-budget=15000 \
    --run-all-compositor-stages-before-draw --screenshot="$OUT/page_tall.png" "file://$DIR/index.html" || ok=0
  shot "$OUT/page_narrow.png" --user-data-dir="$TMPD/cb-narrow" --window-size=420,4600 --virtual-time-budget=15000 \
    --run-all-compositor-stages-before-draw --screenshot="$OUT/page_narrow.png" "file://$DIR/index.html" || ok=0
  shot "$OUT/export.pdf" --user-data-dir="$TMPD/cb-pdf" --virtual-time-budget=15000 --no-pdf-header-footer \
    --print-to-pdf="$OUT/export.pdf" "file://$DIR/index.html" || ok=0
  if [ $ok = 1 ]; then
    record visual PASS "page_tall.png, page_narrow.png (420 px window; Chrome lays out wider than a phone), export.pdf in $OUT"
  else record visual FAIL "a screenshot or the PDF was not written"; fi
  # 8) the print PDF, page by page: no blank first page, no near-empty page, no chart cut from its caption
  if [ -s "$OUT/export.pdf" ]; then
    TMPDIR="$TMPD" "$PY" "$DIR/tools/check_print.py" "$OUT/export.pdf" "$DIR/index.html" > "$TMPD/check_print.log"
    rc=$?
    grep -v '^  page' "$TMPD/check_print.log"
    if [ $rc = 0 ]; then record print PASS "$(tail -1 "$TMPD/check_print.log")"
    elif [ $rc = 2 ]; then record print SKIP "poppler tools (pdftotext, pdftoppm) not found"
    else record print FAIL "see the PRINT FAIL lines above ($TMPD/check_print.log)"; fi
  fi
else
  record visual SKIP "Chrome not found"
fi

echo
echo "== summary =="
for r in "${RESULTS[@]}"; do echo "   $r"; done
if [ $FAILED = 0 ]; then echo "VERIFY: ALL PASS"; else echo "VERIFY: FAILED"; fi
exit $FAILED
