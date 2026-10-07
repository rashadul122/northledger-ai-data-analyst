#!/usr/bin/env bash
# Deploy the NorthLedger site (dist/) to Cloudflare Workers Static Assets.
#
# PREPARED, NEVER RUN BY AN AGENT WITHOUT THE OWNER'S IN-THE-MOMENT "SHIP IT". It refuses unless every gate passes:
#   1. the flag --i-have-the-owners-go is given (or --dry-run: every gate, then wrangler's own dry run, no deploy)
#   2. git: a clean tree (untracked files count) on branch main, so what ships is what is committed
#   3. PLAYWRIGHT_MODULE names an installed Playwright (the browser checks cannot be skipped)
#   4. ./verify.sh passes with NL_REQUIRE_PYODIDE=1, and its summary shows ui PASS and pyodide PASS (not SKIP)
#   5. dist/ is built fresh (tools/make_dist.py: Pyodide fetched and verified against the pins), then
#      tools/check_dist.py --js-crosscheck and tools/test_dist_tools.py pass
#   6. tools/check_dist_csp.mjs passes: the real _headers, in a real browser, the whole demo with zero violations
#   7. the AI worker already allows this site's origin (a read-only OPTIONS request): the worker is deployed first
#   8. wrangler is logged in (wrangler whoami); `wrangler deploy --dry-run` accepts wrangler.jsonc
# Only then: npx wrangler@4.145.0 deploy. It never sets a route or touches DNS: the custom domain is attached
# afterwards (plan/CLOUDFLARE-LAUNCH.md), and no StyleCast name appears in any command here.
#
#   PLAYWRIGHT_MODULE=/path/to/node_modules/playwright ./deploy.sh --i-have-the-owners-go
#   PLAYWRIGHT_MODULE=... ./deploy.sh --dry-run
set -euo pipefail
cd "$(dirname "$0")"
WRANGLER="npx --yes wrangler@4.145.0"
GO=0; DRY=0
for a in "$@"; do
  case "$a" in
    --i-have-the-owners-go) GO=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n 2,22p "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done
refuse() { echo "REFUSED: $*" >&2; exit 2; }
step() { echo; echo "== $* =="; }

if [ "$GO" = 0 ] && [ "$DRY" = 0 ]; then
  refuse "this deploys to Cloudflare and needs the owner's in-the-moment go. Pass --i-have-the-owners-go (or --dry-run to run every gate and stop)."
fi
if [ "$GO" = 1 ] && [ "$DRY" = 1 ]; then refuse "--i-have-the-owners-go and --dry-run together make no sense; choose one."; fi

step "1. git"
[ -z "$(git status --porcelain --untracked-files=all)" ] || refuse "the working tree is not clean (commit or remove: $(git status --porcelain --untracked-files=all | head -3 | tr '\n' ' '))"
BR="$(git rev-parse --abbrev-ref HEAD)"
if [ "$DRY" = 0 ] && [ "$BR" != "main" ]; then refuse "deploy from main (this is $BR): merge the reviewed branch first"; fi
echo "clean tree on $BR at $(git rev-parse --short HEAD)"

step "2. Playwright"
[ -n "${PLAYWRIGHT_MODULE:-}" ] || refuse "PLAYWRIGHT_MODULE is not set (the browser checks are not optional)"
[ -d "$PLAYWRIGHT_MODULE" ] || refuse "PLAYWRIGHT_MODULE=$PLAYWRIGHT_MODULE is not a folder"
export PLAYWRIGHT_MODULE

step "3. verify.sh (NL_REQUIRE_PYODIDE=1)"
LOG="$(mktemp "${TMPDIR:-/tmp}/nl-deploy-verify.XXXXXX")"
if ! NL_REQUIRE_PYODIDE=1 ./verify.sh 2>&1 | tee "$LOG"; then refuse "verify.sh failed (log: $LOG)"; fi
grep -q '^VERIFY: ALL PASS' "$LOG" || refuse "verify.sh did not print VERIFY: ALL PASS (log: $LOG)"
grep -Eq '^ +ui +PASS' "$LOG" || refuse "verify.sh: the ui step did not PASS (a SKIP is not a pass; log: $LOG)"
grep -Eq '^ +pyodide +PASS' "$LOG" || refuse "verify.sh: the pyodide step did not PASS (a SKIP is not a pass; log: $LOG)"

step "4. dist/"
python3 tools/make_dist.py
python3 tools/check_dist.py --js-crosscheck
python3 tools/test_dist_tools.py

step "5. the CSP, in a browser, on the real dist/"
node tools/check_dist_csp.mjs dist

step "6. the AI worker allows this site's origin (read-only OPTIONS request)"
SITE="$(python3 -c "import json;print(json.load(open('deploy/cloudflare.config.json'))['site_url'])")"
WORKER="$(python3 -c "import json;print(json.load(open('site.config.json'))['ai_proxy_url'])")"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X OPTIONS -H "Origin: $SITE" -H 'Access-Control-Request-Method: POST' "$WORKER/plan" || true)"
if [ "$CODE" = "403" ] || [ -z "$CODE" ] || [ "$CODE" = "000" ]; then
  if [ "$DRY" = 1 ]; then echo "WARNING (dry run): the worker answered $CODE for Origin $SITE: deploy the worker (cf-origins) first"; else refuse "the worker answered $CODE for Origin $SITE: deploy the worker with the new origin first"; fi
else
  echo "worker answered $CODE for Origin $SITE"
fi

step "7. wrangler"
WHO="$($WRANGLER whoami 2>&1 || true)"      # captured first: under pipefail, `| grep -q` can fail on SIGPIPE even when it matches
case "$WHO" in *"You are logged in"*) ;; *) refuse "wrangler is not logged in (the owner runs: npx wrangler login). An agent never does." ;; esac
$WRANGLER deploy --dry-run >/dev/null || refuse "wrangler deploy --dry-run rejected wrangler.jsonc"
echo "wrangler accepts the config"

if [ "$DRY" = 1 ]; then
  echo; echo "DRY RUN COMPLETE: every gate passed; nothing was deployed."
  exit 0
fi

step "8. DEPLOY"
$WRANGLER deploy
echo
echo "Deployed. Next (plan/CLOUDFLARE-LAUNCH.md): test on the workers.dev address, then attach the custom domain."
