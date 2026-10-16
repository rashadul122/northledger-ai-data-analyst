# Wiring the analyst work into insight-proxy

The live site (the `northledger-site` Worker) calls `northledger-insight-proxy` for its AI. Until that Worker is
patched, the new page works but its AI half doesn't:

- **"Ask your data"** posts to `/analyst`, and the live proxy answers `404 not_found`. The page then says "Questions
  are not switched on for this site yet", which is why `site.config.json` keeps `"ask_data": false` until then.
- **The AI report** gets `results.analyst_insights` (the analyst loop's sentences). The live proxy's
  `validateResults` is a whitelist, so it drops them silently and the model never sees them. The analyst's charts
  (ids `viz.50` and up) reach the model, but kinds the proxy doesn't know are shown as their tables (`sanitizeChart`).

## One command (on your machine)

```sh
npx wrangler login                      # once
SITE_ORIGIN=https://<your site's address> deploy/deploy-all.sh ../insight-proxy
```

It runs these steps, stopping at the first failure:

1. Patches the proxy (`apply-patch.mjs`).
2. Runs the proxy's tests and `npx wrangler deploy` in the proxy's checkout. That uses its own `wrangler.toml`, so the
   SPEND Durable Object, the SHARES KV and the rate limiters stay bound.
3. Checks that `OPTIONS /analyst` answers 204 for your site's origin.
4. Sets `"ask_data": true`, runs `python3 build.py` and deploys the site (`SITE_DEPLOY_CMD`, default
   `npx wrangler deploy`).

## What the patch changes

Run `node deploy/insight-proxy/apply-patch.mjs --check ../insight-proxy` to see what is already applied. Each step
needs an anchor found exactly once in `src/*.js`. If any anchor is missing, nothing is written.

| Step | Where | Change |
|---|---|---|
| copy | `src/analyst.js`, `src/step-schema.json` | from `docs/ai-analyst/` |
| route | the entry `fetch(request, env, ctx)` | first line: `/analyst` → `analystFetch(request, env)` (before the path whitelist) |
| keep | `validateResults` | `analyst_insights: strList(r.analyst_insights, 400, 10)` (the engine's `ANALYST_INSIGHT_MAX`, `ANALYST_INSIGHTS_MAX`) |
| ground | `buildIndex` | `addList(r.analyst_insights)`, so a figure the report copies from an insight passes the figure guard |
| name | `INTERNAL_NAMES` | `"analyst_insights"` |
| prompt | `reportSystemPrompt` | insights are descriptive, never graded; figures copied as written; used under "Other findings" |

`analystFetch` uses the proxy's own controls:

- the `ALLOWED_ORIGIN` CORS list;
- `RATE_LIMITER` (key `analyst:<ip>`) and `GLOBAL_LIMITER`;
- one `SPEND` reservation per model call, refused the same way as `/report` (`busy` 429, `daily_cap_reached` and
  `daily_counter_failed` 503);
- `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` (default `deepseek-flash`) and `DEEPSEEK_BASE_URL`.

No new secret or binding is needed.

## How it was tested

The patch was applied to a copy of the deployed `northledger-insight-proxy` bundle and run in Node 22 against:

- mocked bindings (limiters, the SPEND Durable Object, KV);
- a mocked DeepSeek;
- real engine output (`tools/fixtures/review/run2_replan.csv`: the analyst's request body and `results_for_ai`).

**Patched proxy:**

- `OPTIONS /analyst`: 204 with CORS, and 403 from another origin.
- `POST /analyst` returns the valid step and drops a bad column and an unknown op. It makes one model call
  (`deepseek-flash`), reserves one spend and sends no row.
- Refusals: over the cap 503 `daily_cap_reached` with no model call; an odd counter reply 503 `daily_counter_failed`;
  rate limited 429.
- `/report`: the insights reach the model with the prompt line. A report citing a figure found only in an insight is
  accepted with the figure kept.

**Unpatched bundle, same run:** `/analyst` 404, and the insights never reach the model.

## Not in this patch

The four new chart kinds (`lines`, `area`, `hbar`, `multiples`) are not ported. The port is ready in
`tools/fixtures/viz/worker-batch2.mjs` (it goes in `sanitizeChart`), but the shared viewer and the PDF also need to
draw them. Until then they show as their tables in `/report` and in shared links. On the page itself they are drawn.
