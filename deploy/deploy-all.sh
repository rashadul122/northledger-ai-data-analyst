#!/usr/bin/env bash
# Ship the site's analyst work to Cloudflare: patch and deploy insight-proxy, then switch "Ask your data" on and
# deploy the site. Run from this repository's root, on your own machine (wrangler logged in: npx wrangler login).
#
#   deploy/deploy-all.sh <path to your insight-proxy checkout>
#
# SITE_DEPLOY_CMD (default "npx wrangler deploy") is run from this repository's root after build.py, with the
# wrangler config you deploy northledger-site with. PROXY_URL defaults to site.config.json's ai_proxy_url.
set -euo pipefail
PROXY_DIR="${1:?usage: deploy/deploy-all.sh <insight-proxy checkout>}"
SITE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROXY_URL="${PROXY_URL:-$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["ai_proxy_url"])' "$SITE_DIR/site.config.json")}"
SITE_ORIGIN="${SITE_ORIGIN:-}"

echo "== 1/4 patch insight-proxy ($PROXY_DIR)"
node "$SITE_DIR/deploy/insight-proxy/apply-patch.mjs" "$PROXY_DIR"

echo "== 2/4 test and deploy insight-proxy"
( cd "$PROXY_DIR" && if [ -f package.json ] && grep -q '"test"' package.json; then npm test; fi && npx wrangler deploy )

echo "== 3/4 check /analyst answers"
if [ -n "$SITE_ORIGIN" ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' -X OPTIONS -H "Origin: $SITE_ORIGIN" "$PROXY_URL/analyst")
  [ "$code" = "204" ] || { echo "OPTIONS $PROXY_URL/analyst gave $code, not 204: is $SITE_ORIGIN in ALLOWED_ORIGIN? Stopping before the site."; exit 1; }
  echo "ok: 204 from $PROXY_URL/analyst"
else
  echo "skipped (set SITE_ORIGIN=https://<your site> to check CORS before the site goes out)"
fi

echo "== 4/4 switch on Ask your data, build and deploy the site"
python3 - "$SITE_DIR/site.config.json" <<'PY'
import json, sys
p = sys.argv[1]; c = json.load(open(p))
if c.get("ask_data") is not True:
    c["ask_data"] = True
    open(p, "w").write(json.dumps(c, indent=2, ensure_ascii=False) + "\n")
    print("site.config.json: ask_data true")
PY
( cd "$SITE_DIR" && python3 build.py && ${SITE_DEPLOY_CMD:-npx wrangler deploy} )
echo "Done. Commit site.config.json and the rebuilt pages, and commit the proxy's patched src/ in its own repository."
