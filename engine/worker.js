/* NorthLedger "Try it on your own file": the engine in a Web Worker.

   src/js/50-try.js starts this worker only after a visitor picks a file or the sample. It loads
   Pyodide (Python compiled to WebAssembly) from cdn.jsdelivr.net, the one outside origin the site
   allows at runtime (tools/check_site.py, "requests" check, allows it in this file only), then
   numpy, pandas and sqlite3 from the same place, unpacks the engine snapshot packed next to this
   file (northledger-browser.zip, written by tools/pack_engine.py) into Pyodide's in-memory file
   system and runs nl_browser.run_json on the visitor's file. The file stays in this browser tab.

   Messages in
     {type: 'scan', id, name, buffer, options}  load the engine if needed, run it with every
                                                flagged column withheld (the engine's default),
                                                and report which columns it flagged
     {type: 'run',  id, options}                the visitor's decisions: the same report if every
                                                flagged column stays withheld, else a new run
       options: {name, objective, decisions: {column: 'withhold'|'code'|'keep'}, as_of}
   Messages out
     {type: 'stage', id, stage, state: 'start'|'done', seconds, cached}
     {type: 'scanned', id, result: {ok, error, flagged: [{column, kind}]}}
     {type: 'result', id, report}               THE REPORT CONTRACT
     {type: 'error', id, code: 'runtime'|'engine_missing'|'engine', message, detail}
   The engine entry (engine/pack.json "runtime.entry"):
     nl_browser.run_json(data, name, objective, decisions_json, as_of) -> JSON text
   If a later nl_browser.run_json also takes progress=callable(stage, state, seconds), each
   stage is announced as it runs; without it, each stage's time comes from the report.
*/
'use strict';
var PYODIDE_URL = 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/';
var ENGINE_ZIP = 'northledger-browser.zip';
var ENGINE_DIR = '/nl';
var BEFORE = ['read', 'profile'];                         // what the visitor waits for before deciding
var AFTER = ['decide', 'clean', 'analyze', 'forecast', 'story'];
var py = null, nl = null, booted = false, hasProgress = false;
var file = null, first = null;                           // the visitor's bytes, and the first report

function post(m) { self.postMessage(m); }
function clock() { return performance.now() / 1000; }
function fail(id, code, message, detail) { post({ type: 'error', id: id, code: code, message: message || '', detail: detail || '' }); }
function lastLine(e) {
  var s = String(e && (e.message || e) || '').trim().split('\n').filter(function (x) { return x.trim(); });
  return s.length ? s[s.length - 1].trim() : '';
}

async function boot(id) {
  if (booted) { post({ type: 'stage', id: id, stage: 'load', state: 'done', seconds: 0, cached: true }); return; }
  var t0 = clock();
  post({ type: 'stage', id: id, stage: 'load', state: 'start' });
  if (!py) {
    try {
      importScripts(PYODIDE_URL + 'pyodide.js');
      py = await self.loadPyodide({ indexURL: PYODIDE_URL, stdout: function () {}, stderr: function () {} });
      await py.loadPackage(['numpy', 'pandas', 'sqlite3'], { messageCallback: function () {}, errorCallback: function () {} });
    } catch (e) {
      py = null;
      throw { code: 'runtime', message: '', detail: lastLine(e) };
    }
  }
  var res;
  try { res = await fetch(ENGINE_ZIP, { cache: 'no-cache' }); } catch (e) { res = null; }
  if (!res || !res.ok) throw { code: 'engine_missing', message: '', detail: res ? 'HTTP ' + res.status : 'no answer' };
  try {
    py.unpackArchive(await res.arrayBuffer(), 'zip', { extractDir: ENGINE_DIR });
    py.runPython('import sys\nif "' + ENGINE_DIR + '" not in sys.path:\n    sys.path.insert(0, "' + ENGINE_DIR + '")');
    nl = py.pyimport('nl_browser');
    hasProgress = !!py.runPython('import inspect, nl_browser\n"progress" in inspect.signature(nl_browser.run_json).parameters');
  } catch (e) {
    throw { code: 'engine_missing', message: 'The packed engine did not load.', detail: lastLine(e) };
  }
  booted = true;
  post({ type: 'stage', id: id, stage: 'load', state: 'done', seconds: clock() - t0 });
}

// one engine run; `show` lists the stages this phase announces as they run
function engine(id, options, decisions, show) {
  var fn = nl.run_json, args = [file.bytes, String(options.name || file.name), String(options.objective || ''),
    JSON.stringify(decisions || {}), options.as_of ? String(options.as_of) : null];
  var progress = function (stage, state, seconds) {
    stage = String(stage);
    if (show.indexOf(stage) < 0) return;
    post({ type: 'stage', id: id, stage: stage, state: String(state),
      seconds: (seconds === null || seconds === undefined || !isFinite(Number(seconds))) ? null : Number(seconds) });
  };
  if (!hasProgress) show.forEach(function (s) { post({ type: 'stage', id: id, stage: s, state: 'start' }); });
  try {
    var text = hasProgress ? fn.callKwargs.apply(fn, args.concat([{ progress: progress }])) : fn.apply(null, args);
    return JSON.parse(String(text));
  } finally {
    if (fn.destroy) fn.destroy();
  }
}
function timesOf(report, stages, id) {
  var t = {};
  (report && report.timings || []).forEach(function (x) { t[x.stage] = x.seconds; });
  stages.forEach(function (s) { post({ type: 'stage', id: id, stage: s, state: 'done', seconds: t[s] === undefined ? null : t[s] }); });
}

async function onScan(m) {
  await boot(m.id);
  file = { name: m.name, bytes: new Uint8Array(m.buffer) };
  first = null;
  var report = engine(m.id, m.options || {}, {}, BEFORE);
  if (!report || !report.ok) {
    post({ type: 'scanned', id: m.id, result: { ok: false, error: (report && report.error) || 'The engine gave no answer.' } });
    return;
  }
  first = { report: report, options: m.options || {} };
  timesOf(report, BEFORE, m.id);
  var flagged = ((report.privacy || {}).flagged || []).map(function (f) { return { column: f.column, kind: f.kind }; });
  var profile = null;
  if ((m.options || {}).want_profile && nl.profile_json) {
    try {
      var fm = {};
      flagged.forEach(function (f) { fm[f.column] = f.kind; });
      profile = JSON.parse(nl.profile_json(file.bytes, String(m.name || ''), JSON.stringify(fm)));
      if (!profile || !profile.ok) profile = null;
    } catch (e) { profile = null; }
  }
  post({ type: 'scanned', id: m.id, result: { ok: true, error: null, flagged: flagged, profile: profile } });
}

async function onRun(m) {
  if (!booted || !file || !first) { fail(m.id, 'engine', 'There is no file to work on; pick it again.'); return; }
  var d = (m.options && m.options.decisions) || {}, report;
  var changed = Object.keys(d).some(function (k) { return d[k] !== 'withhold'; }) ||
    String((m.options || {}).objective || '') !== String(first.options.objective || '');
  if (!changed) {
    report = first.report;                                // every flagged column withheld: the run already made
  } else {
    var opts = {};
    Object.keys(first.options).forEach(function (k) { opts[k] = first.options[k]; });
    report = engine(m.id, opts, d, AFTER);
  }
  timesOf(report, AFTER, m.id);
  post({ type: 'result', id: m.id, report: report });
}

// the engine's distilled results for the AI report writer (results_for_ai in the packed
// adapter): the page asks after the report is drawn; the reply is the JSON payload the page
// POSTs to the proxy's /report. Never rows: claims, analyses, story, forecast, health.
function onResults(m) {
  if (!booted || !nl) { post({ type: 'results_json', id: m.id, results: null }); return; }
  var results = null;
  try {
    if (nl.results_json) results = JSON.parse(nl.results_json(JSON.stringify(m.report || {})));
  } catch (e) { results = null; }
  post({ type: 'results_json', id: m.id, results: results });
}

var queue = Promise.resolve();
self.onmessage = function (e) {
  var m = e.data || {};
  queue = queue.then(function () {
    if (m.type === 'scan') return onScan(m);
    if (m.type === 'run') return onRun(m);
    if (m.type === 'results') return onResults(m);
    return null;
  }).catch(function (err) {
    if (err && err.code) fail(m.id, err.code, err.message, err.detail);
    else fail(m.id, 'engine', '', lastLine(err));
  });
};
