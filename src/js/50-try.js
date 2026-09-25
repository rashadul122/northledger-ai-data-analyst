/* "Try it on your own file": the visitor's CSV through the NorthLedger engine, in their browser.
   The engine runs in a Web Worker (engine/worker.js) that loads Pyodide only after the visitor
   picks a file or the sample. Every figure in the report is read from the engine's report (THE
   REPORT CONTRACT, checked by NLTry.validate before anything is drawn); nothing here computes a
   finding. The limits come from window.NL.try (build.py), never typed here.
   The optional AI summaries are offered only when the owner set site.config.json ai_proxy_url;
   the page sends NLTry.aiPayload (question, findings' id/claim/verdict/value, story) after the
   visitor ticks consent. The model writes an executive and a technical summary as words around
   slots ({F1.n1}, {F1.grade}); NLTry.checkSummaries runs the same template guard as the proxy
   (one shared block, tools/check_try_guard.js checks the copies match) on each part, and the page
   itself fills every figure and grade from the engine's findings. Each part is judged on its own:
   a part that passes is shown, labelled; a part that does not is named with the reason in plain
   words (NLTry.partNote). The engine's story stays on screen. */
(function () {
  'use strict';
  var U = window.NLU || {}, NL = window.NL || {};
  var CFG = NL.try || {};
  var T = {};
  window.NLTry = T;

  var esc = U.esc || function (s) { return String(s); };
  var VERDICTS = ['RECOMMEND', 'WATCH', 'INSUFFICIENT'];
  var STAGES = ['read', 'profile', 'decide', 'clean', 'analyze', 'forecast', 'story'];
  var FIRST = 8;                                    // findings shown before 'Show all'
  var GATE = 'The business analysis did not run';   // engine/nl_browser.py GATE_TRIPPED
  var CODED = 'coded as it arrived';                // engine/nl_browser.py CODED_ON_ARRIVAL
  var STORY_KEYS = [['what_happened', 'What happened'], ['why', 'Why'], ['what_to_do', 'What to do'],
    ['whats_next', "What's next"], ['cannot_answer', 'What we cannot answer']];

  /* ------------------------------------------------------------ limits (from build.py) */
  T.limits = function () {
    return { max_bytes: +CFG.max_bytes || 0, max_rows: +CFG.max_rows || 0,
      max_label: CFG.max_label || '', rows_label: CFG.rows_label || '' };
  };

  /* ------------------------------------------------------------ the AI summaries guard */
  /* ==== TEMPLATE GUARD: shared, byte for byte, by insight-proxy/src/guard.js and
     portfolio-website/src/js/50-try.js (tools/check_try_guard.js fails if the copies differ) ====
     The model never writes a figure, a grade or a confidence. It writes words around slots, and the
     engine's own text fills them:
       {F2.n1}   the 1st figure in finding 2's claim, exactly as the engine wrote it ("-3.2%", "$1,412.50")
       {F2.grade} finding 2's grade phrase for the register ("keep watching" / "WATCH")
       {F2.claim} finding 2's whole claim;  {S3.n1}, {S3.text} the same for story line 3
       {NONE_CONFIRMED} "Nothing is confirmed yet" (required when no finding is CONFIRMED, refused otherwise)
       [F2]      a citation: every sentence that uses a slot of F2 must cite [F2]
     Every sentence cites what it speaks of and holds one of its slots ({NONE_CONFIRMED} alone is the
     exception); each word the model writes comes from the text it cites or from a short list of
     linking words; a grade slot is never shared with a finding of another grade, and every finding
     the text states carries its grade. check() returns reason codes (empty: the text passes);
     fill() puts the engine's text in. */
  var TG = (function () {
    'use strict';
    var GRADES = { RECOMMEND: 'CONFIRMED', WATCH: 'WATCH', INSUFFICIENT: 'NOT_ENOUGH_DATA' };
    var REGISTERS = {
      executive: { title: 'Executive summary', name: 'executive summary', maxWords: 120, hardMaxWords: 150,
        grade: { CONFIRMED: 'confirmed', WATCH: 'keep watching', NOT_ENOUGH_DATA: 'not enough evidence yet' },
        none: 'Nothing is confirmed yet' },
      technical: { title: 'Technical summary', name: 'technical summary', maxWords: 180, hardMaxWords: 220,
        grade: { CONFIRMED: 'CONFIRMED', WATCH: 'WATCH', NOT_ENOUGH_DATA: 'NOT ENOUGH DATA' },
        none: 'No finding is graded CONFIRMED' }
    };
    var CODES = ['empty', 'length', 'format', 'unknown_marker', 'digit', 'percent', 'currency', 'number_word', 'grade_word',
      'confidence', 'cause', 'intensity', 'prediction', 'advice', 'register', 'echo', 'negation', 'vocabulary', 'citation',
      'binding', 'grade_swap', 'grade_missing', 'inferential', 'direction', 'unit', 'none_confirmed', 'no_figure'];
    var STORY_PARTS = ['headline', 'what_happened', 'why', 'what_to_do', 'whats_next', 'cannot_answer'];
    var MAX_CHARS = 6000;

    function setOf(list) { var o = Object.create(null); for (var i = 0; i < list.length; i++) o[list[i]] = true; return o; }
    function words(s) { return s.split(' '); }
    // refused wherever they appear
    var NUMBER_WORDS = setOf(words('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen ' +
      'fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds ' +
      'thousand thousands million millions billion billions trillion trillions dozen dozens half halves halved halving twice ' +
      'thrice double doubled doubles doubling triple tripled triples tripling treble trebled quadruple quadrupled quarter ' +
      'quarters third thirds both single pair pairs couple first second fourth fifth sixth seventh eighth ninth tenth ' +
      'eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth thirtieth ' +
      'fortieth fiftieth sixtieth seventieth eightieth ninetieth hundredth thousandth millionth billionth fifths sixths ' +
      'tenths decade decades fortnight fortnights century centuries annual annually annualised annualized biennial biannual ' +
      'majority minority grand lakh lakhs crore crores nil zillion umpteen'));
    var MAGNITUDES = setOf(words('k m mn bn b tn x grand thousand thousands million millions billion billions trillion trillions'));
    var PERCENT_WORDS = setOf(words('percent percentage percentages pct pp bps bp permille'));
    var PERCENT_PHRASES = ['per cent', 'basis point', 'percentage point'];
    var GRADE_WORDS = setOf(words('confirm confirms confirmed confirming confirmation unconfirmed recommend recommends ' +
      'recommended recommending recommendation recommendations watch watches watching watched insufficient insufficiently ' +
      'verdict verdicts actionable'));
    var GRADE_PHRASES = ['act on', 'acting on', 'enough evidence', 'enough data', 'not enough'];
    var CONFIDENCE_WORDS = setOf(words('confident confidently confidence certain certainly certainty uncertain uncertainty ' +
      'sure surely guarantee guaranteed guarantees definitely definite definitive definitively prove proves proved proven ' +
      'proof likely unlikely likelihood probability probabilities probable probably improbable odds chance chances ' +
      'undoubtedly doubtless conclusive conclusively inconclusive clearly obviously evidently reliable reliably trustworthy ' +
      'verified validated robust robustly firmly established settled solid solidly doubt doubts doubtful fluke flukes ' +
      'genuine genuinely conviction indisputable indisputably unquestionable unquestionably undeniable undeniably ' +
      'demonstrably demonstrate demonstrates demonstrated tentative tentatively preliminary possibly perhaps maybe plausible ' +
      'plausibly apparently seemingly assured evidence'));
    // refused unless the engine's own text for what the sentence cites uses the same word
    var SOFT_CONFIDENCE = setOf(words('noise noisy real really sound soundly clear firm risk risks risky trust trusted safe safely'));
    var CONFIDENCE_PHRASES = ['of the time', 'land in', 'lands in', 'landing in', 'room for'];
    var CAUSE_WORDS = setOf(words('because cause caused causes causing causal drove drive drives driven driving driver ' +
      'drivers therefore thus hence consequently attributable attributed owing fuel fuels fuelled fueled fuelling fueling ' +
      'reflect reflects reflected reflecting linked tied since amid amidst offset offsets offsetting explain explains ' +
      'explained explaining explanation why reason reasons triggered spurred prompted underlying factor factors behind ' +
      'helped hurt weighed lifted dragged'));
    var CAUSE_PHRASES = ['due to', 'led to', 'lead to', 'leads to', 'leading to', 'as a result', 'resulted in', 'results in',
      'result of', 'thanks to', 'stems from', 'stemmed from', 'on account of', 'responsible for', 'contributed to',
      'contributes to', 'effect of', 'impact of', 'on the back of', 'in line with', 'as a consequence', 'in response to',
      'linked to', 'tied to', 'because of', 'weighed on', 'associated with', 'comes from', 'come from', 'came from',
      'coming from', 'based on', 'owing to', 'on the basis of',
      'compared with', 'compared to', 'relative to', 'as opposed to', 'in contrast'];
    // a comparison or an order between what the text states ("refunds rose versus revenue", "fell after
    // refunds rose"): in a sentence that speaks of more than one finding or line, refused unless the
    // cited text uses the same word, like a cause ("rose against the prior year" of one finding is its own)
    var ORDER_WORDS = setOf(words('after before afterwards versus vs compared comparison relative against than while whereas ' +
      'whilst unlike when whenever following then until once as'));
    // "made revenue fall": a make word with a direction word close after it is a cause
    var MAKE_WORDS = setOf(words('make makes made making'));
    // "accounted for the fall": a cause, unless a figure follows ("duplicates account for {F3.n1} of rows")
    var ACCOUNT_WORDS = setOf(words('account accounts accounted accounting'));
    var INTENSITY_WORDS = setOf(words('surge surged surging plunge plunged plunging soar soared soaring sharp sharply dramatic ' +
      'dramatically significant significantly substantial substantially massive massively huge enormous tremendous steep ' +
      'steeply spike spiked spikes skyrocketed skyrocketing collapse collapsed crashed tumbled slumped remarkable remarkably ' +
      'considerable considerably markedly strong strongly sizeable sizable major slight slightly modest modestly marginal ' +
      'marginally notable notably severe severely rapid rapidly sudden suddenly meaningful meaningfully materially large ' +
      'largely larger largest big bigger biggest small smaller smallest tiny minor mere merely unprecedented alarming ' +
      'alarmingly worrying worryingly concerning striking strikingly noticeable noticeably pronounced heavy heavily deep ' +
      'deeply extreme extremely outsized historic sluggish weak weaker stronger strength'));
    var PREDICT_WORDS = setOf(words('will would should must expect expects expected expecting expectation predict predicts ' +
      'predicted prediction forecast forecasts forecasted projected projection anticipate anticipated advise advised suggest ' +
      'suggests suggested ought may might could can going poised continue continues continuing further outlook future soon ' +
      'upcoming keep keeps'));
    var PREDICT_PHRASES = ['set to', 'on track', 'on course', 'going to'];
    var ADVICE_WORDS = setOf(words('need needs needed consider considering warrant warrants warranted action actions urgent ' +
      'urgently immediate immediately advisable prioritise prioritize priority priorities plan plans planning budget budgets ' +
      'budgeting invest investing investment leadership management managers decide decision decisions monitor monitoring ' +
      'track tracking investigate investigation rely relied worth'));
    var CURRENCY_WORDS = setOf(words('dollar dollars cad usd eur euro euros pound pounds gbp cents yen yuan renminbi rmb rupee ' +
      'rupees inr franc francs peso pesos bucks buck quid'));
    var REGISTER_WORDS = setOf(words('exciting excited amazing incredible incredibly fantastic great excellent impressive ' +
      'awesome unlock unlocks unleash powerful revolutionary stunning outstanding superb wonderful thrilled delighted ' +
      'exceptional brilliant encouraging encouragingly healthy promising standout uplift blip stellar pleasing welcome ' +
      'disappointing disappointingly good bad nice terrific happy win wins winning boost boosted boosting headwind headwinds ' +
      'tailwind tailwinds worthless toy'));
    var REGISTER_PHRASES = ['game changer', 'game-changer', 'world class', 'world-class', 'best in class', 'best-in-class', 'good news'];
    var ECHO_WORDS = setOf(words('instruction instructions disregard jailbreak deepseek openai chatgpt assistant visit click ' +
      'contact website email subscribe'));
    var ECHO_PHRASES = ['system prompt', 'ignore previous', 'ignore the', 'ignore all', 'ignore any', 'as an ai', 'language model',
      'dot com', 'dot org', 'dot net', 'dot io', 'dot example'];
    // first and second person, read with their case so the region "US" is not "us"
    var PERSON = /(^|[^A-Za-z])(?:I|[Ww]e|[Oo]urs?|us|[Mm]e|[Mm]y|[Mm]ine|[Mm]yself|[Oo]urselves|[Yy]ou|[Yy]ours?|[Yy]ourself)(?=$|[^A-Za-z])/;
    var NEGATORS = setOf(words('not no never neither nor without cannot nothing none nobody nowhere false untrue hardly barely scarcely'));
    // directions: the neutral words may be written freely (the direction checks bind them to a
    // figure); the others also judge the change and must be the engine's own words
    var UP_NEUTRAL = words('rose rise rises rising risen up increase increased increases increasing grew grow grows growing ' +
      'grown growth higher climbed climb climbs climbing added plus expanded expand expands expanding expansion');
    var DOWN_NEUTRAL = words('fell fall falls falling fallen down decrease decreased decreases decreasing declined decline ' +
      'declines declining lower dropped drop drops dropping shrank shrink shrinks shrunk shrinking contracted contraction ' +
      'reduced reduce reduces reduction minus');
    var UP_WORDS = setOf(UP_NEUTRAL.concat(words('gain gained gains jumped jump jumps surged soared improved improve improves ' +
      'improving improvement rebound rebounded rebounds recovered recover recovery strengthened uptick upturn boosted positive ' +
      'rally rallied')));
    var DOWN_WORDS = setOf(DOWN_NEUTRAL.concat(words('lost loss losses slipped slid dip dipped dips worsened worsen worsening ' +
      'weakened softened eased downturn negative slump slumped deteriorated retreated plunged')));
    var NEUTRAL_MOVES = setOf(UP_NEUTRAL.concat(DOWN_NEUTRAL));
    var LOOSE_MOVES = setOf(['up', 'down', 'added', 'plus', 'minus']);   // read beside a figure only ("follow up")
    var PHRASAL = setOf(words('make makes made making sum sums summed add adds set sets end ends ended show shows showed turn ' +
      'turns turned pick picked follow followed take takes took break breaks broke broken write writes wrote written narrow ' +
      'narrowed shut settle back'));
    var UNIT_WORDS = setOf(words('day days week weeks month months year years hour hours minute minutes row rows record records ' +
      'point points time times period periods decade decades yr yrs hr hrs min mins mo mos fortnight fortnights century centuries'));
    var UNIT_ALIAS = { yr: 'year', hr: 'hour', min: 'minute', mo: 'month', centurie: 'century' };
    // the words any sentence may use besides the cited text: function words and neutral analytic nouns
    var FREE = setOf(words('a an the of in on at to by for from with and or but is are was were be been being has have had ' +
      'it its this that these those which there here their then than while whereas whilst although though also however ' +
      'meanwhile overall over under between against across within during after before per into via where when each same ' +
      'other another way latest prior previous preceding earlier recent current period periods year years month months week ' +
      'weeks day days average figure figures value values grade graded grades finding findings result results measure ' +
      'measured metric metrics row rows record records data file total totals level levels share rate rates count counts ' +
      'change changes changed move moved moves movement trend trends series reading reported report reports shows show ' +
      'showed shown stands stood stand remains remain remained reach reached reaching reaches held hold holds holding account ' +
      'accounts accounted make makes made rests rest based found check checks engine among amount amounts interval intervals ' +
      'range ranges compared comparison relative versus came comes come sits sat s'));
    var STOP = setOf(words('this that these those with from have were which their there over into than then they them what ' +
      'when where about after before between during each other same some such only also more most less least finding findings ' +
      'engine data value values total totals change changed changes level levels across within against still year years month ' +
      'months week weeks days grade graded while whereas although being been here just very figure figures reached reaching ' +
      'reach shows show showed story line file next last'));
    var ROMAN = /^M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$/;
    var NOT_ROMAN = setOf(['CI', 'CV', 'CD', 'DC', 'MD', 'MM', 'CC', 'CL', 'DL', 'ML', 'LI', 'MI', 'DI', 'IV', 'XL', 'MIX',
      'MIC', 'DIV', 'CIV', 'MIL', 'LCD']);
    var NUM_EXPR = /(?:[$€£¥]\s?)?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:[-/–]\d+(?:\.\d+)?)*(?:\s?%|\s(?:percentage points?|percent|per cent|pct|pp)\b|(?:k|m|b|mn|bn|tn)(?![A-Za-z])|\s(?:thousand|million|billion|trillion)\b)?/g;
    // a slot may hold up to two plain spaces inside each brace ("{ F1.claim }": the model wrote it so)
    var TOKEN = /\{ {0,2}([FS])(\d{1,3})\.([a-z]+\d{0,3}) {0,2}\}|\{ {0,2}NONE_CONFIRMED {0,2}\}|\[([FS])(\d{1,3})\]|\[(?:value [A-Z]{1,3}|your file)\]/g;
    // a name with an underscore (a column such as confirmed_units), read as one opaque word when the
    // engine's text holds it exactly; any other underscore is refused as formatting. Found as whole
    // runs of letters, digits and underscores (one linear pass, no lookbehind for older browsers).
    var RUN = /[A-Za-z0-9_]+/g, NAME = /^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+$/;
    function namesIn(t) { return (String(t).match(RUN) || []).filter(function (x) { return NAME.test(x); }); }
    var DASHES = /[+\-\u2010-\u2015\u2212\uFE63\uFF0D]\s?$/;   // any sign or dash, the em dash too
    var PUA = 0xE000;
    var PUA_ALL = new RegExp('[' + String.fromCharCode(0xE000) + '-' + String.fromCharCode(0xF8FF) + ']', 'g');

    function nfkc(s) { s = String(s === null || s === undefined ? '' : s); return s.normalize ? s.normalize('NFKC') : s; }
    function wordsOf(s) { return String(s).toLowerCase().match(/[a-z]+/g) || []; }
    // accents folded (confírmed reads as confirmed) and hyphens closed (con-firmed as confirmed)
    function fold(s) { return String(s).normalize('NFKD').replace(/\p{M}/gu, ''); }
    function modelWords(s) { var f = fold(s); return wordsOf(f).concat(wordsOf(f.replace(/([A-Za-z])[-‐‑]([A-Za-z])/g, '$1$2'))); }
    // a light stem, so "months" meets "monthly" and "duplicate" meets "duplicates"
    function stem(w) {
      if (w.length <= 3) return w;
      if (/ies$/.test(w) && w.length > 4) w = w.slice(0, -3) + 'y';
      var sufs = ['ingly', 'edly', 'ing', 'ed', 'ly', 'es', 's'];
      for (var i = 0; i < sufs.length; i++) {
        if (w.length - sufs[i].length >= 3 && w.slice(-sufs[i].length) === sufs[i]) { w = w.slice(0, -sufs[i].length); break; }
      }
      if (w.length > 3 && /e$/.test(w)) w = w.slice(0, -1);
      if (/([b-df-hj-np-tv-z])\1$/.test(w)) w = w.slice(0, -1);
      return w;
    }

    // How the site reads a number: digits with thousands commas only where the grouping is valid
    // (1,234; 3,4 is 3 and 4) or a bare .5, sign and unit left out, written back in its shortest
    // form (1234.0 -> 1234), after NFKC. The same reading as test/number-vectors.json.
    var NUMBER_TOKEN = /\d[\d,]*(?:\.\d+)?|\.\d+/g, GROUPED = /^\d{1,3}(?:,\d{3})+(?:\.\d+)?$/;
    function numbersIn(s) {
      var out = [], m, t = nfkc(s);
      NUMBER_TOKEN.lastIndex = 0;
      while ((m = NUMBER_TOKEN.exec(t)) !== null) {
        var tok = m[0].replace(/,+$/, ''), ps = tok.indexOf(',') < 0 ? [tok] : GROUPED.test(tok) ? [tok.replace(/,/g, '')] : tok.split(',');
        for (var i = 0; i < ps.length; i++) {
          var n = ps[i] ? Number(ps[i]) : NaN;
          if (isFinite(n)) out.push(String(Number(Math.abs(n).toPrecision(15))));
        }
      }
      return out;
    }
    function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
    function direction(ws) {
      var up = false, down = false;
      for (var i = 0; i < ws.length; i++) { if (UP_WORDS[ws[i]]) up = true; if (DOWN_WORDS[ws[i]]) down = true; }
      return up === down ? null : up ? 'up' : 'down';
    }
    // the direction the words right beside a figure give it: the last three words before it and
    // the first two after; "up" and "down" count only right next to it, and not in "make up" or "up to"
    function moveBeside(bw, aw) {
      var b = bw.slice(-3), a = aw.slice(0, 2), up = false, down = false, i, w;
      for (i = 0; i < b.length; i++) {
        w = b[i];
        if (LOOSE_MOVES[w] && (i !== b.length - 1 || PHRASAL[bw[bw.length - 2]])) continue;
        if (UP_WORDS[w]) up = true; if (DOWN_WORDS[w]) down = true;
      }
      for (i = 0; i < a.length; i++) {
        w = a[i];
        if (LOOSE_MOVES[w] && (i !== 0 || a[1] === 'to')) continue;
        if (UP_WORDS[w]) up = true; if (DOWN_WORDS[w]) down = true;
      }
      return up === down ? null : up ? 'up' : 'down';
    }
    function unitWord(w) {
      if (!w || !UNIT_WORDS[w]) return null;
      w = w.replace(/s$/, '');
      return has(UNIT_ALIAS, w) ? UNIT_ALIAS[w] : w;
    }
    function unitIn(ws) { for (var i = 0; i < ws.length && i < 3; i++) { var u = unitWord(ws[i]); if (u) return u; } return null; }
    // a figure the engine states as a p-value or a backtest coverage: the word right before it
    function inferKind(before) {
      var b = fold(before).toLowerCase();
      if (/(^|[^a-z])p(?:[- ]?values?)?(?:\s+(?:of|at|is|was))?\s*[=:<>≤]?\s*$/.test(b)) return 'p';
      if (/(^|[^a-z])coverage(?:\s+(?:of|at|is|was))?\s*[=:]?\s*$/.test(b)) return 'coverage';
      return null;
    }
    function stems(ws) {
      var o = Object.create(null);
      for (var i = 0; i < ws.length; i++) {
        var w = ws[i];
        if (w.length >= 4 && !STOP[w] && !UP_WORDS[w] && !DOWN_WORDS[w]) o[w.slice(0, 5)] = true;
      }
      return o;
    }
    function phraseIn(lower, ph) { return new RegExp('(^|[^a-z])' + ph.replace(/[-]/g, '\\-') + '($|[^a-z])').test(lower); }
    function countWords(s) { s = String(s).trim(); return s ? s.split(/\s+/).length : 0; }

    // One finding or story line: its figures as written, with the direction and unit word the
    // engine gave each, and the words the guard compares the model's words with.
    // a finding whose claim names its measure but holds no value of it ("Change in the average month's
    // row volume, latest 12 months against the 12 before"): its value is a number, no figure in the
    // claim is that value (at the claim's own precision, or as a percentage), and the claim's figures are
    // only whole numbers and dates (no percentage, currency, decimal or sign: those are measured values)
    function namesOnly(nums, value) {
      if (typeof value !== 'number' || !isFinite(value) || valueAt(nums, value) >= 0) return false;
      for (var i = 0; i < nums.length; i++) if (!/^\d+(?:[-\/–]\d+)*$/.test(nums[i].fill)) return false;
      return true;
    }
    // the first figure in a text that is the value, at the figure's own precision or as a percentage (-1: none)
    function valueAt(nums, value) {
      if (typeof value !== 'number' || !isFinite(value)) return -1;
      var a = Math.abs(value);
      for (var i = 0; i < nums.length; i++) {
        var d = nums[i].fill.replace(/[^\d.]/g, ''), x = Number(d), dec = (d.split('.')[1] || '').length;
        if (!d || !isFinite(x) || dec > 12) continue;
        if (Math.abs(x - Number(a.toFixed(dec))) < 1e-9 || Math.abs(x - Number((a * 100).toFixed(dec))) < 1e-9) return i;
      }
      return -1;
    }
    // for each place a placeholder ([value A], [your file]) stands in the engine's text, what stands
    // right before it: the word (lower case), the placeholder before it, '#' after a figure, '' at the start
    function phBefore(raw, ph) {
      var out = [], t = nfkc(raw), at = t.indexOf(ph);
      while (at >= 0) {
        var b = t.slice(0, at).replace(/[^A-Za-z0-9\]]+$/, ''), m = /\[(?:value [A-Z]{1,3}|your file)\]$/.exec(b);
        out.push(m ? m[0] : /[0-9]$/.test(b) ? '#' : (fold(b).toLowerCase().match(/[a-z]*$/) || [''])[0]);
        at = t.indexOf(ph, at + ph.length);
      }
      return out;
    }
    // a finding whose claim names its measure, and a story line that states its value
    function measurePair(a, b) {
      if (b.label) { var c = a; a = b; b = c; }
      return !!(a.label && b.kind === 'S' && valueAt(b.nums, a.value) >= 0);
    }
    function source(ref, raw, grade, value) {
      var t = nfkc(raw), nums = [], m, prevEnd = 0, list = [];
      NUM_EXPR.lastIndex = 0;
      while ((m = NUM_EXPR.exec(t)) !== null) list.push({ start: m.index, end: m.index + m[0].length, text: m[0] });
      for (var i = 0; i < list.length; i++) {
        var x = list[i], start = x.start, fillText = x.text, dir = null;
        var s1 = t.charAt(start - 1), s0 = t.charAt(start - 2);
        if ((s1 === '+' || s1 === '-' || s1 === '−') && !/[A-Za-z0-9]/.test(s0 || ' ')) {
          fillText = s1 + fillText; dir = s1 === '+' ? 'up' : 'down'; start -= 1;
        }
        var nextStart = i + 1 < list.length ? list[i + 1].start : t.length;
        var before = t.slice(prevEnd, start).split(/[.;:!?]/), after = t.slice(x.end, nextStart).split(/[.;:!?,]/)[0];
        var aw = wordsOf(after);
        if (!dir) dir = direction(wordsOf(before[before.length - 1]).slice(-3)) || direction(aw.slice(0, 2));
        nums.push({ fill: fillText, dir: dir, unit: unitIn(aw), infer: inferKind(t.slice(prevEnd, start)) });
        prevEnd = x.end;
      }
      var ws = wordsOf(fold(t)), dirs = { up: false, down: false }, st = Object.create(null);
      for (var j = 0; j < ws.length; j++) {
        st[stem(ws[j])] = true;
        // a direction the engine denies ("not dropped", "did not rise", "wasn't lower") is not one it gives
        var denied = j > 0 && (NEGATORS[ws[j - 1]] || (ws[j - 1] === 't' && j > 1 && /n$/.test(ws[j - 2])) ||
          (j > 1 && NEGATORS[ws[j - 2]] && /^(?:did|do|does|was|were|is|are|be|been|has|have|had)$/.test(ws[j - 1])));
        if (denied) continue;
        if (UP_WORDS[ws[j]]) dirs.up = true; if (DOWN_WORDS[ws[j]]) dirs.down = true;
      }
      for (var k = 0; k < nums.length; k++) if (nums[k].dir && /^[+\-−]/.test(nums[k].fill)) dirs[nums[k].dir] = true;
      return { ref: ref, kind: ref.charAt(0), grade: grade, raw: String(raw), lower: t.toLowerCase(), words: setOf(ws),
        vocab: st, stems: stems(ws), dirs: dirs, nums: nums, idents: setOf(namesIn(t)),
        label: ref.charAt(0) === 'F' && namesOnly(nums, value), value: ref.charAt(0) === 'F' ? value : null };
    }
    function sources(p) {
      var out = [], fs = (p && Array.isArray(p.findings)) ? p.findings : [], st = (p && p.story) || {}, k = 0;
      for (var i = 0; i < fs.length; i++) {
        var f = fs[i] || {};
        out.push(source('F' + (i + 1), typeof f.claim === 'string' ? f.claim : '', has(GRADES, f.verdict) ? GRADES[f.verdict] : null, f.value));
      }
      for (var j = 0; j < STORY_PARTS.length; j++) {
        var part = STORY_PARTS[j], v = part === 'headline' ? [st.headline] : (Array.isArray(st[part]) ? st[part] : []);
        for (var n = 0; n < v.length; n++) {
          if (typeof v[n] === 'string' && v[n].trim()) { var s = source('S' + (++k), v[n], null); s.part = part; out.push(s); }
        }
      }
      return out;
    }

    // One payload read once: its sources, by ref, the figures they hold and the engine's names. The
    // last payload read is kept (keyed by its JSON, so a changed payload is read again): the proxy
    // checks many short sentences against one payload while it builds the data message, and a
    // Cloudflare Worker has little CPU time per request. Nothing reads these objects to change them.
    var LAST = { key: null };
    function prepared(p) {
      var key = null;
      try { key = JSON.stringify(p === undefined ? null : p); } catch (e) { key = null; }
      if (key !== null && key === LAST.key) return LAST;
      var srcs = sources(p), byRef = Object.create(null), figures = Object.create(null), ids = Object.create(null), phs = placeholders(srcs);
      for (var i = 0; i < srcs.length; i++) {
        byRef[srcs[i].ref] = srcs[i];
        numbersIn(srcs[i].raw).forEach(function (n) { figures[n] = true; });
        for (var id in srcs[i].idents) ids[id] = true;
      }
      var out = { key: key, srcs: srcs, byRef: byRef, figures: figures, ids: ids, phs: phs };
      if (key !== null) LAST = out;
      return out;
    }

    // the placeholders ([value A], [your file]) the engine's text holds
    var PH = /\[(?:value [A-Z]{1,3}|your file)\]/g;
    function placeholders(srcs) {
      var o = Object.create(null);
      for (var i = 0; i < srcs.length; i++) (nfkc(srcs[i].raw).match(PH) || []).forEach(function (x) { o[x] = true; });
      return o;
    }
    // the tokens in normalised text, each resolved against the sources (bad: not a real slot, or a
    // placeholder the engine's text does not hold)
    function tokens(t, byRef, known, phs) {
      var out = [], m;
      if (!known) { known = Object.create(null); for (var r in byRef) for (var id in byRef[r].idents) known[id] = true; }
      if (!phs) { var all = []; for (var q in byRef) all.push(byRef[q]); phs = placeholders(all); }
      TOKEN.lastIndex = 0;
      while ((m = TOKEN.exec(t)) !== null) {
        var tok = { start: m.index, end: m.index + m[0].length, text: m[0], bad: false };
        if (m[1]) {
          var ref = m[1] + m[2], src = has(byRef, ref) ? byRef[ref] : null, field = m[3], nm = /^n([1-9]\d{0,2})$/.exec(field);
          tok.type = 'slot'; tok.ref = ref; tok.src = src; tok.field = nm ? 'n' : field;
          if (!src || /^0/.test(m[2])) tok.bad = true;
          else if (nm) { tok.num = src.nums[Number(nm[1]) - 1]; if (!tok.num) tok.bad = true; }
          else if (!((src.kind === 'F' && (field === 'grade' || field === 'claim')) || (src.kind === 'S' && field === 'text'))) tok.bad = true;
        } else if (m[4]) {
          tok.type = 'cite'; tok.ref = m[4] + m[5]; tok.src = has(byRef, tok.ref) ? byRef[tok.ref] : null;
          if (!tok.src || /^0/.test(m[5])) tok.bad = true;
        } else if (m[0].charAt(0) === '{') tok.type = 'none';
        else { tok.type = 'ph'; if (!phs[m[0]]) tok.bad = true; }
        out.push(tok);
      }
      // the engine's own names, outside the tokens above; any other name stays plain text
      var names = [], k = 0;
      RUN.lastIndex = 0;
      while ((m = RUN.exec(t)) !== null) {
        var a = m.index, b = a + m[0].length;
        while (k < out.length && out[k].end <= a) k++;
        if (!known[m[0]] || !NAME.test(m[0]) || (k < out.length && out[k].start < b)) continue;
        names.push({ start: a, end: b, text: m[0], bad: false, type: 'ident' });
      }
      return names.length ? out.concat(names).sort(function (x, y) { return x.start - y.start; }) : out;
    }

    // sentences of the masked text (each token is one private-use character), with a citation
    // written after the full stop moved back to the sentence it closes
    function sentences(masked, toks) {
      var out = [], cur = '';
      for (var i = 0; i < masked.length; i++) {
        var c = masked.charAt(i);
        if (c === '\n') { out.push(cur); cur = ''; continue; }
        cur += c;
        if ((c === '.' || c === '!' || c === '?') && (i + 1 >= masked.length || /\s/.test(masked.charAt(i + 1)))) { out.push(cur); cur = ''; }
      }
      out.push(cur);
      var res = [];
      for (var j = 0; j < out.length; j++) {
        var s = out[j];
        if (res.length) {
          var k = 0, lead = '';
          while (k < s.length) {
            var ch = s.charCodeAt(k);
            if (/\s/.test(s.charAt(k))) { k++; continue; }
            if (ch >= PUA && ch < PUA + toks.length && toks[ch - PUA].type === 'cite') { lead += s.charAt(k); k++; continue; }
            break;
          }
          if (lead) { res[res.length - 1] += lead; s = s.slice(k); }
        }
        if (s.trim()) res.push(s);
      }
      return res;
    }

    // notes (optional): an array that gets {code, detail} for each problem found (the proxy's guided retry)
    function check(text, payload, register, notes) { return checkP(prepared(payload), text, register, notes); }
    // many texts against one payload, read once (the proxy's data message probes each engine sentence)
    function checkMany(texts, payload, register) {
      var P = prepared(payload);
      return texts.map(function (x) { return checkP(P, x, register); });
    }
    function checkP(P, text, register, notes) {
      var R = has(REGISTERS, register) ? REGISTERS[register] : REGISTERS.executive;
      var raw = String(text === null || text === undefined ? '' : text), bad = Object.create(null);
      var add = function (c, d) { bad[c] = true; if (Array.isArray(notes) && notes.length < 80) notes.push({ code: c, detail: d === undefined ? null : d }); };
      var result = function () { return CODES.filter(function (c) { return bad[c]; }); };
      if (!raw.trim()) return ['empty'];
      if (raw.length > MAX_CHARS) { add('length'); return result(); }
      if (countWords(raw) > R.hardMaxWords) add('length', { words: countWords(raw), max: R.maxWords });
      var t = nfkc(raw), srcs = P.srcs, byRef = P.byRef, i;
      var findings = srcs.filter(function (s) { return s.kind === 'F'; });
      var toks = tokens(t, byRef, P.ids, P.phs), masked = '', last = 0;
      for (i = 0; i < toks.length; i++) {
        masked += t.slice(last, toks[i].start) + String.fromCharCode(PUA + i);
        last = toks[i].end;
        if (toks[i].bad) add('unknown_marker', { marker: toks[i].text });
      }
      masked += t.slice(last);
      var isTok = function (c) { return c >= PUA && c < PUA + toks.length; };
      var plain = masked.replace(PUA_ALL, ' '), lower = fold(plain).toLowerCase(), ws = modelWords(plain);

      // what the model may never write itself
      if (/[{}[\]]/.test(plain)) add('unknown_marker', { stray: (plain.match(/[{}[\]]/) || [''])[0] });
      if (/[*#`<>|~_!?]/.test(plain) || /^\s*[-•·]\s/m.test(plain) || /https?:|www\./i.test(plain) ||
        /(^|[^A-Za-z0-9])[A-Za-z0-9-]+\.(?:com|org|net|io|ai|co|uk|ca|example)(?![A-Za-z])/i.test(plain)) add('format', { chars: (plain.match(/[*#`<>|~_!?]/g) || []).slice(0, 4).join('') });
      // invisible characters, private-use characters, and letters outside plain Latin (a Cyrillic
      // "с" in "сonfirmed") would let a word slip past the lists below
      if (/[\p{Cf}\p{Co}]/u.test(t) || /(?![A-Za-z])\p{L}/u.test(fold(plain))) add('format');
      if (/\p{N}/u.test(plain)) add('digit', { figures: (plain.match(/\p{N}[\p{N},.:%-]*/gu) || []).slice(0, 6) });
      var caps = plain.match(/\b[IVXLCDM]{2,}\b/g) || [];
      for (i = 0; i < caps.length; i++) if (ROMAN.test(caps[i]) && !NOT_ROMAN[caps[i]]) add('digit', { figures: [caps[i]] });
      if (/(^|[^A-Za-z])(?=[lIO]*[lO])[lIO]{2,}(?![A-Za-z])/.test(plain)) add('digit', { figures: ['lO'] });   // "lO" for 10
      if (/[%‰]/.test(plain)) add('percent', { word: '%' });
      PERCENT_PHRASES.forEach(function (ph) { if (phraseIn(lower, ph)) add('percent', { word: ph }); });
      if (/\p{Sc}/u.test(plain)) add('currency', { word: (plain.match(/\p{Sc}/u) || [''])[0] });
      for (i = 0; i < ws.length; i++) {
        var w = ws[i];
        if (NUMBER_WORDS[w] || (w.length > 4 && /fold$/.test(w))) add('number_word', { word: w });
        if (PERCENT_WORDS[w]) add('percent', { word: w });
        if (GRADE_WORDS[w]) add('grade_word', { word: w });
        if (CONFIDENCE_WORDS[w]) add('confidence', { word: w });
        if (REGISTER_WORDS[w]) add('register', { word: w });
        if (ECHO_WORDS[w]) add('echo', { word: w });
        if (NEGATORS[w]) add('negation', { word: w });
      }
      if (/n['’]t(?![A-Za-z])/i.test(plain)) add('negation', { word: "n't" });
      GRADE_PHRASES.forEach(function (ph) { if (phraseIn(lower, ph)) add('grade_word', { word: ph }); });
      REGISTER_PHRASES.forEach(function (ph) { if (phraseIn(lower, ph)) add('register', { word: ph }); });
      ECHO_PHRASES.forEach(function (ph) { if (phraseIn(lower, ph)) add('echo', { word: ph }); });
      CONFIDENCE_PHRASES.forEach(function (ph) { if (phraseIn(lower, ph)) add('confidence', { word: ph }); });
      if (PERSON.test(fold(plain)) || /<<<|>>>/.test(t)) add('echo', { person: true });

      // sentence by sentence: citations and slots, the words used, grades, binding, direction, units
      var sents = sentences(masked, toks), anyCite = false, nones = 0, mentioned = Object.create(null), graded = Object.create(null);
      for (var si = 0; si < sents.length; si++) {
        var s = sents[si], cited = Object.create(null), nCited = 0, nSlots = 0, noneHere = false, items = [], seg = '';
        var flush = function () { wordsOf(fold(seg)).forEach(function (x) { items.push({ w: x }); }); seg = ''; };
        for (i = 0; i < s.length; i++) {
          var code = s.charCodeAt(i);
          if (!isTok(code)) { seg += s.charAt(i); continue; }
          flush();
          var tok = toks[code - PUA];
          items.push({ tok: tok, at: i });
          if (tok.type === 'cite' && !tok.bad && !cited[tok.ref]) { cited[tok.ref] = true; nCited++; anyCite = true; }
          if (tok.type === 'none') { nones++; noneHere = true; }
          if (tok.type === 'slot' && !tok.bad) nSlots++;
        }
        flush();
        // every sentence cites what it speaks of and holds one of its slots; "{NONE_CONFIRMED}." stands alone
        var bare = s.replace(PUA_ALL, function (c) { return toks[c.charCodeAt(0) - PUA].type === 'none' ? '' : c; }).replace(/[\s.,;:]/g, '');
        if (!(noneHere && !bare) && (!nCited || !nSlots)) add('citation', { sentence: si + 1, cites: nCited > 0, slots: nSlots > 0 });
        var scope = srcs.filter(function (x) { return cited[x.ref]; });
        var scopeWords = Object.create(null), scopeVocab = Object.create(null), scopeLower = '', scopeDirs = { up: false, down: false };
        scope.forEach(function (x) {
          var k; for (k in x.words) scopeWords[k] = true; for (k in x.vocab) scopeVocab[k] = true;
          scopeLower += ' ' + x.lower; if (x.dirs.up) scopeDirs.up = true; if (x.dirs.down) scopeDirs.down = true;
        });
        var sw = modelWords(s.replace(PUA_ALL, ' '));
        for (i = 0; i < sw.length; i++) {
          var v = sw[i];
          if (CAUSE_WORDS[v] && !scopeWords[v]) add('cause', { word: v });
          if (INTENSITY_WORDS[v] && !scopeWords[v]) add('intensity', { word: v });
          if (PREDICT_WORDS[v] && !scopeWords[v]) add('prediction', { word: v });
          if (ADVICE_WORDS[v] && !scopeWords[v]) add('advice', { word: v });
          if (SOFT_CONFIDENCE[v] && !scopeWords[v]) add('confidence', { word: v });
          if (CURRENCY_WORDS[v] && !scopeWords[v]) add('currency', { word: v });
          if (MAKE_WORDS[v] && !scopeWords[v]) {
            for (var mk = i + 1; mk < sw.length && mk <= i + 4; mk++) if (!LOOSE_MOVES[sw[mk]] && (UP_WORDS[sw[mk]] || DOWN_WORDS[sw[mk]])) { add('cause', { word: v + ' ' + sw[mk] }); break; }
          }
        }
        var slower = wordsOf(fold(s.replace(PUA_ALL, ' '))).join(' ');
        CAUSE_PHRASES.forEach(function (ph) { if (phraseIn(slower, ph) && !phraseIn(scopeLower, ph)) add('cause', { word: ph }); });
        PREDICT_PHRASES.forEach(function (ph) { if (phraseIn(slower, ph) && !phraseIn(scopeLower, ph)) add('prediction', { word: ph }); });
        var citedRefs = Object.keys(cited);
        // an engine name (confirmed_units) must be one the cited text holds
        for (i = 0; i < items.length; i++) {
          if (items[i].tok && items[i].tok.type === 'ident' && !scope.some(function (x) { return x.idents[items[i].tok.text]; })) add('vocabulary', { word: items[i].tok.text, cites: citedRefs });
        }
        // each word from the cited text (by its stem) or the short list of linking words
        if (nCited) {
          for (i = 0; i < items.length; i++) {
            var iw = items[i].w;
            if (iw && !FREE[iw] && !NEUTRAL_MOVES[iw] && !scopeWords[iw] && !scopeVocab[stem(iw)]) add('vocabulary', { word: iw, cites: citedRefs });
          }
        }

        // what the sentence speaks of: the sources it cites or holds a slot of
        var involved = scope.slice(), slotItems = [], gradeSlots = [];
        for (i = 0; i < items.length; i++) {
          var it = items[i].tok;
          if (!it || it.type !== 'slot' || it.bad) continue;
          slotItems.push(i);
          if (involved.indexOf(it.src) < 0) involved.push(it.src);
          if (it.field === 'grade') gradeSlots.push(it.src);
        }
        involved.forEach(function (x) {
          if (x.kind !== 'F') return;
          mentioned[x.ref] = true;
          if (gradeSlots.some(function (g) { return g.grade === x.grade; })) graded[x.ref] = true;
        });
        // a grade slot never serves a finding of another grade, unless that finding's own grade slot is here too
        gradeSlots.forEach(function (g) {
          involved.forEach(function (x) {
            if (x.kind === 'F' && x.grade !== g.grade && gradeSlots.indexOf(x) < 0) add('grade_swap', { grade_of: g.ref, finding: x.ref });
          });
        });
        // the source a slot speaks of: the nearest word, before it (else after it), that only one
        // of the sources in this sentence uses
        var ownerAt = function (idx) {
          var dirs = [-1, 1];
          for (var d = 0; d < 2; d++) {
            for (var j = idx + dirs[d]; j >= 0 && j < items.length; j += dirs[d]) {
              var x = items[j].w;
              if (!x || x.length < 4 || STOP[x] || UP_WORDS[x] || DOWN_WORDS[x]) continue;
              var key = x.slice(0, 5), owners = involved.filter(function (y) { return y.stems[key]; });
              if (owners.length === 1) return owners[0];
            }
          }
          return null;
        };
        for (i = 1; i + 1 < items.length; i++) {
          var nx = items[i + 1].tok;
          if (items[i].w === 'for' && ACCOUNT_WORDS[items[i - 1].w] && !phraseIn(scopeLower, items[i - 1].w + ' for') &&
            !(nx && !nx.bad && nx.type === 'slot' && nx.field === 'n')) add('cause', { word: items[i - 1].w + ' for' });
        }
        if (involved.length > 1) for (i = 0; i < sw.length; i++) if (ORDER_WORDS[sw[i]] && !scopeWords[sw[i]]) add('cause', { word: sw[i] });
        // a placeholder the model wrote stands where the cited text puts it: that text holds it, with
        // the same word (or placeholder) right before it, and its nearest words are that text's own
        for (i = 0; i < items.length; i++) {
          var pt = items[i].tok;
          if (!pt || pt.type !== 'ph' || pt.bad) continue;
          var holders = scope.filter(function (x) { return x.raw.indexOf(pt.text) >= 0; }), pv = i ? items[i - 1] : null;
          var want = !pv ? '' : pv.w ? pv.w : pv.tok.type === 'ph' ? pv.tok.text : pv.tok.type === 'ident' ? (pv.tok.text.toLowerCase().match(/[a-z]*$/) || [''])[0] : null;
          if (want === null || !holders.some(function (x) { return phBefore(x.raw, pt.text).indexOf(want) >= 0; })) add('binding', { ph: pt.text });
          else if (involved.length > 1) { var pown = ownerAt(i); if (pown && pown.raw.indexOf(pt.text) < 0) add('binding', { ph: pt.text }); }
        }
        // two figures (slots or placeholders) with no word between them run together or read as one
        // ("{F4.n2}{F4.n3}" -> 36, "{F1.n1} {F1.n2}", "{F5.n1}, {F5.n2}"); quotes, a semicolon or brackets part them
        var valueTok = function (x) { return x && !x.bad && ((x.type === 'slot' && x.field === 'n') || x.type === 'ph'); };
        for (i = 1; i < items.length; i++) {
          if (valueTok(items[i].tok) && valueTok(items[i - 1].tok) && /^[^A-Za-z'"‘’“”;:()]*$/.test(s.slice(items[i - 1].at + 1, items[i].at))) add('digit', { joined: true });
        }
        // a finding whose claim names its measure but not its value is stated with that value, from a
        // story line that gives it (its number slot, or the whole line), never by its name alone and
        // never with some other figure of a line
        involved.forEach(function (x) {
          if (x.label && !items.some(function (it) {
            var k = it.tok;
            return k && k.type === 'slot' && !k.bad && k.src.kind === 'S' &&
              ((k.field === 'n' && valueAt([k.num], x.value) === 0) || (k.field === 'text' && valueAt(k.src.nums, x.value) >= 0));
          })) add('no_figure', { finding: x.ref });
        });
        var nSlotsAt = [];
        for (var q = 0; q < slotItems.length; q++) {
          var qi = slotItems[q], sl = items[qi].tok, at = items[qi].at;
          if (!cited[sl.ref]) add('citation', { slot: sl.text, cite: sl.ref });
          if (sl.field !== 'n' && sl.field !== 'grade') continue;
          if (involved.length > 1) {
            var owner = ownerAt(qi);
            if (owner && owner !== sl.src) {
              if (sl.field === 'grade') { if (owner.kind === 'F' && owner.grade !== sl.src.grade) add('grade_swap', { grade_of: sl.ref, finding: owner.ref }); }
              // (a finding whose claim names its measure and the story line that states its value speak of
              // one measure: "Change in the monthly total of cost, latest {F2.n1} months ...: the monthly
              // total of cost moved from {S2.n1} ... a change of {S2.n3}")
              else if (!owner.nums.some(function (n) { return n.fill === sl.num.fill && n.unit === sl.num.unit; }) &&
                !measurePair(owner, sl.src)) add('binding', { slot: sl.text, words_of: owner.ref });
            }
          }
          if (sl.field !== 'n') continue;
          nSlotsAt.push(qi);
          // the words the model put next to the figure: since the previous token, and up to the next
          var prev = at - 1;
          while (prev >= 0 && !isTok(s.charCodeAt(prev))) prev--;
          var next = at + 1;
          while (next < s.length && !isTok(s.charCodeAt(next))) next++;
          var btext = s.slice(prev + 1, at), atext = s.slice(at + 1, next);
          var bseg = btext.split(/[.;:!?]/), aseg = atext.split(/[.;:!?,]/)[0], aw = wordsOf(aseg);
          if (DASHES.test(btext)) add('direction', { slot: sl.text, sign: true });   // a sign the model put on the engine's figure
          if (/[A-Za-z0-9]$/.test(btext)) add('digit', { glued: sl.text });   // letters or digits glued before the figure
          if (atext && !/^[\s.,;:)\]'"’”]/.test(atext)) add('digit', { glued: sl.text });   // "{F1.n1}k", "{F1.n1}OO", "{F1.n1}x"
          if (/^\s/.test(atext) && MAGNITUDES[wordsOf(atext)[0]] && /^\s+[A-Za-z]+(?![A-Za-z])/.test(atext)) add('number_word', { word: wordsOf(atext)[0] });
          var md = moveBeside(wordsOf(bseg[bseg.length - 1]), aw);
          if (md) {
            if (sl.num.dir ? sl.num.dir !== md : (!sl.src.dirs[md] || (sl.src.dirs.up && sl.src.dirs.down))) add('direction', { slot: sl.text });
          }
          var mu = unitIn(aw);
          if (mu && sl.num.unit && mu !== sl.num.unit) add('unit', { slot: sl.text });   // "3 of 6 years" where the engine wrote months
          if ((sl.num.infer || inferKind(btext)) && sl.num.infer !== inferKind(btext)) add('inferential', { slot: sl.text });
        }
        // each direction word belongs to the figure nearest it; with no figure, to the cited text
        for (i = 0; i < items.length; i++) {
          var dw = items[i].w;
          if (!dw || LOOSE_MOVES[dw] || !(UP_WORDS[dw] || DOWN_WORDS[dw])) continue;
          var want = UP_WORDS[dw] ? 'up' : 'down', best = -1;
          for (var r = 0; r < nSlotsAt.length; r++) {
            if (best < 0 || Math.abs(nSlotsAt[r] - i) < Math.abs(best - i) || (Math.abs(nSlotsAt[r] - i) === Math.abs(best - i) && nSlotsAt[r] > i)) best = nSlotsAt[r];
          }
          if (best >= 0) {
            var bn = items[best].tok;
            if (bn.num.dir ? bn.num.dir !== want : !bn.src.dirs[want]) add('direction', { word: dw, slot: bn.text });
          } else if (!scopeDirs[want]) add('direction', { word: dw });
        }
      }
      if (findings.length && !anyCite) add('citation', { none: true });
      // every finding the text states carries its grade (its own slot, or one shared with a finding of the same grade)
      for (var mref in mentioned) if (!graded[mref]) add('grade_missing', { finding: mref });
      // the filled text may hold no figure the engine did not write: two slots run together
      // ("{F2.n1}{F2.n2}" -> 73) or glued by a point ("48,200.7") make a new one
      if (!bad.unknown_marker) {
        var filledText = fillP(P, raw, register), filled = numbersIn(filledText);
        for (i = 0; i < filled.length; i++) if (!P.figures[filled[i]]) add('digit', { joined: true });
        // the word limit holds for the text the reader sees: each whole-text slot counts as all its words
        var fw = countWords(filledText);
        if (fw > R.maxWords) add('length', { words: fw, max: R.maxWords, filled: true });
      }
      var hasConfirmed = findings.some(function (f) { return f.grade === 'CONFIRMED'; });
      if (hasConfirmed ? nones > 0 : nones === 0) add('none_confirmed', { written: nones > 0 });
      return result();
    }

    // the text with each slot replaced by the engine's own words, as parts:
    // {text}, {text, ph: true} (a placeholder the model wrote, outside any slot) or {cite: 'F2', claim:
    // the cited text}. Citations are for the reader to trace.
    function parts(text, payload, register) { return partsP(prepared(payload), text, register); }
    function partsP(P, text, register) {
      var R = has(REGISTERS, register) ? REGISTERS[register] : REGISTERS.executive;
      var t = nfkc(text), byRef = P.byRef, out = [], last = 0;
      var toks = tokens(t, byRef, P.ids, P.phs);
      var push = function (x) { if (x) { if (out.length && out[out.length - 1].text !== undefined && !out[out.length - 1].ph) out[out.length - 1].text += x; else out.push({ text: x }); } };
      toks.forEach(function (k, i) {
        push(t.slice(last, k.start));
        last = k.end;
        if (k.bad) { push(k.text); return; }
        // the punctuation the template puts after a slot (past any citations)
        var after = t.slice(k.end).replace(/^(?:\s*\[[FS]\d{1,3}\])+/, ''), nx = toks[i + 1];
        if (k.type === 'cite') out.push({ cite: k.ref, claim: k.src.raw });
        else if (k.type === 'none') push(R.none + (/^\s*(?:$|[.!?])/.test(after) ? '' : '.'));   // always a sentence of its own
        else if (k.type === 'ph') out.push({ text: k.text, ph: true });   // a placeholder the model wrote itself
        else if (k.type === 'ident') push(k.text);
        else if (k.field === 'n') push(k.num.fill);
        else if (k.field === 'grade') push(R.grade[k.src.grade] || '');
        else {
          // the engine's whole sentence: its full stop gives way to the template's own punctuation, and
          // a grade slot right after it is set off with a semicolon
          var v = k.src.raw.replace(/\s+$/, '');
          if (/^\s*[.;:,!?]/.test(after)) v = v.replace(/\.+$/, '');
          else if (nx && !nx.bad && nx.type === 'slot' && nx.field === 'grade' && /^\s*$/.test(t.slice(k.end, nx.start))) v = v.replace(/\.+$/, '') + ';';
          push(v);
        }
      });
      push(t.slice(last));
      return out;
    }
    function fill(text, payload, register, opts) { return fillP(prepared(payload), text, register, opts); }
    function fillP(P, text, register, opts) {
      var keep = !!(opts && opts.cite === 'keep');
      return partsP(P, text, register).map(function (p) { return p.cite ? (keep ? '[' + p.cite + ']' : '') : p.text; }).join('')
        .replace(/[ \t]+([.,;:])/g, '$1').replace(/[ \t]{2,}/g, ' ').trim();
    }

    return { GRADES: GRADES, REGISTERS: REGISTERS, CODES: CODES, sources: sources, check: check, checkMany: checkMany, parts: parts, fill: fill,
      countWords: countWords, numbersIn: numbersIn, valueAt: valueAt };
  })();
  /* ==== END TEMPLATE GUARD ==== */
  T.numbersIn = TG.numbersIn;          // how the proxy reads numbers (the payload cut check uses it)
  T.checkTemplate = TG.check;          // reason codes for one summary; empty: it passes
  T.fillTemplate = TG.fill;            // the summary with the engine's figures, grades and text in
  T.reasonCodes = TG.CODES;            // every reason code checkTemplate can return
  T.SUMMARY_PARTS = ['executive', 'technical'];
  // the proxy's answer {executive, technical, model, rejected, unavailable}: each part is judged on
  // its own. A part is shown only when it came back as text and passes this page's guard too; any
  // other part is set aside with the proxy's reason codes (rejected), the guard's own, or the proxy's
  // error code (unavailable). Only known codes are kept: nothing the proxy sends is shown as text.
  // ok: at least one part is shown. (An older proxy sent both parts as text, or an error.)
  var UPSTREAM_CODES = ['upstream_timeout', 'upstream_busy', 'upstream_error', 'upstream_unreachable', 'upstream_bad_response', 'upstream_incomplete'];
  T.checkSummaries = function (ans, body) {
    var out = { ok: false, executive: null, technical: null, rejected: {}, unavailable: {} };
    if (!ans || typeof ans !== 'object') return out;
    var rej = (ans.rejected && typeof ans.rejected === 'object') ? ans.rejected : {}, una = (ans.unavailable && typeof ans.unavailable === 'object') ? ans.unavailable : {};
    var own = Object.prototype.hasOwnProperty;
    T.SUMMARY_PARTS.forEach(function (part) {
      var text = ans[part];
      if (typeof text === 'string' && text.trim()) {
        var reasons = TG.check(text, body, part);
        if (reasons.length) out.rejected[part] = reasons; else { out[part] = text.trim(); out.ok = true; }
      } else if (own.call(rej, part) && Array.isArray(rej[part])) {
        var known = rej[part].filter(function (c) { return TG.CODES.indexOf(c) >= 0; });
        out.rejected[part] = known.length ? known : ['empty'];
      } else if (own.call(una, part)) out.unavailable[part] = UPSTREAM_CODES.indexOf(una[part]) >= 0 ? una[part] : 'unknown';
      else out.rejected[part] = ['empty'];
    });
    return out;
  };
  // why a part could not be written, in plain words (the proxy's error codes)
  var UNAVAILABLE_TEXT = { upstream_timeout: 'the AI model did not answer in time', upstream_busy: 'the AI model was busy',
    upstream_error: 'the AI model answered with an error', upstream_unreachable: 'the AI model could not be reached',
    upstream_bad_response: 'the AI model gave no usable answer', upstream_incomplete: 'the AI model stopped before it finished', unknown: 'no usable answer came back' };
  // the plain-words note for one part that is not shown ('' when it is shown)
  T.partNote = function (chk, part) {
    var name = TG.REGISTERS[part].name;
    if (chk.rejected[part]) return 'The ' + name + ' was set aside: it ' + T.reasonText(chk.rejected[part]) + '.';
    if (chk.unavailable[part]) return 'The ' + name + ' could not be written: ' + (UNAVAILABLE_TEXT[chk.unavailable[part]] || UNAVAILABLE_TEXT.unknown) + '.';
    return '';
  };
  // every part not shown, one sentence each ('' when both are shown)
  T.partsNote = function (chk) {
    return T.SUMMARY_PARTS.map(function (part) { return T.partNote(chk, part); }).filter(Boolean).join(' ');
  };
  // one summary as HTML: the engine's words in the slots, each citation a superscript that carries
  // the claim it points to, quoted values put back (in this browser only), everything escaped.
  // A placeholder the model wrote in its own words (not inside a slot) is put back only when its value
  // holds no digit: the guard lets one stand only where its own finding puts it, and even if that
  // check failed, a model-placed value could never read as a figure here.
  T.summaryHtml = function (text, body, register, red) {
    var html = TG.parts(text, body, register).map(function (p) {
      if (p.cite) return '<sup class="tr-cite" title="' + esc(T.restore(p.claim, red)) + '">[' + esc(p.cite) + ']</sup>';
      if (p.ph) { var back = T.restore(p.text, red); return esc(/\p{N}/u.test(back) ? p.text : back); }
      return esc(T.restore(p.text, red));
    }).join('');
    return html.trim().split(/\n{2,}/).map(function (x) { return '<p>' + x.replace(/\s*\n\s*/g, ' ').trim() + '</p>'; }).join('');
  };
  // every string in a value, depth first (the redaction scan reads the story this way)
  function strings(x, out) {
    out = out || [];
    if (typeof x === 'string') out.push(x);
    else if (Array.isArray(x)) x.forEach(function (v) { strings(v, out); });
    else if (x && typeof x === 'object') Object.keys(x).forEach(function (k) { strings(x[k], out); });
    return out;
  }
  // what each reason code means, for the note shown when a summary is set aside
  var REASON_TEXT = { empty: 'came back empty', length: 'was over its word limit', format: 'used formatting or characters that plain prose does not need',
    unknown_marker: 'used a marker that is not in the findings', digit: 'wrote a number of its own', percent: 'wrote a percentage of its own',
    currency: 'wrote a currency of its own', number_word: 'wrote a number in words', grade_word: 'wrote a grade of its own',
    confidence: 'used confidence wording', cause: 'added a cause, comparison or order the engine did not state', intensity: 'added size wording the engine did not use',
    prediction: 'added a prediction the engine did not make', advice: 'added advice the engine did not give', register: 'used promotional wording',
    echo: 'echoed instructions, spoke as itself or addressed the reader', negation: 'turned a finding round with a negation',
    vocabulary: 'used words that are not in the finding it cites', citation: 'wrote a sentence that does not cite and use the finding it speaks of',
    binding: 'put a figure next to another finding\'s words', grade_swap: 'gave a finding another finding\'s grade',
    grade_missing: 'stated a finding without its grade', inferential: 'moved a p-value or a coverage figure away from what it measures',
    direction: 'changed a direction', unit: 'changed a unit', none_confirmed: 'misstated whether anything is confirmed',
    no_figure: 'named a measure without its figure' };
  T.reasonText = function (reasons) {
    var out = [];
    (reasons || []).forEach(function (r) { if (Object.prototype.hasOwnProperty.call(REASON_TEXT, r) && out.indexOf(REASON_TEXT[r]) < 0) out.push(REASON_TEXT[r]); });
    return out.length ? out.slice(0, 4).join('; ') : 'did not pass the check';
  };

  // What in the engine's text names the visitor's data: the file's name and every value the
  // engine quoted ('Sofia Lindqvist'). By default the AI payload carries placeholders instead,
  // and the AI text is shown with the values put back, in this browser only.
  var QUOTED = /(^|[\s(\[])(['"‘“])([^'"‘’“”\n]{1,120})(['"’”])(?=$|[\s.,;:)!?\]])/g;
  function letters(i) { var s = ''; i += 1; while (i > 0) { var r = (i - 1) % 26; s = String.fromCharCode(65 + r) + s; i = Math.floor((i - 1) / 26); } return s; }
  function aiTexts(rep) {
    var out = [];
    (rep.findings || []).forEach(function (f) { if (f && typeof f.claim === 'string') out.push(f.claim); });
    strings(rep.story || {}, out);
    return out;
  }
  T.aiRedactions = function (rep) {
    var out = [], seen = {}, n = 0, name = (rep.input && typeof rep.input.name === 'string') ? rep.input.name : '';
    var texts = aiTexts(rep);
    if (name && texts.some(function (t) { return t.indexOf(name) >= 0; })) { seen[name] = true; out.push({ label: 'your file\'s name', value: name, placeholder: '[your file]' }); }
    texts.forEach(function (t) {
      var m;
      QUOTED.lastIndex = 0;
      while ((m = QUOTED.exec(t)) !== null) {
        var v = m[3];
        if (!v.trim() || seen[v] || /^\[[a-z ]+\]$/.test(v)) continue;
        seen[v] = true;
        out.push({ label: 'a value quoted from your data', value: v, placeholder: '[value ' + letters(n++) + ']' });
      }
    });
    return out;
  };
  function redact(text, red) {
    var t = String(text === null || text === undefined ? '' : text);
    red.forEach(function (x) {
      if (x.placeholder === '[your file]') { t = t.split(x.value).join(x.placeholder); return; }
      [['\'', '\''], ['"', '"'], ['‘', '’'], ['“', '”']].forEach(function (q) { t = t.split(q[0] + x.value + q[1]).join(q[0] + x.placeholder + q[1]); });
    });
    return t;
  }
  T.restore = function (text, red) {
    var t = String(text);
    (red || []).forEach(function (x) { t = t.split(x.placeholder).join(x.value); });
    return t;
  };
  // exactly what the proxy receives: never rows, column values, the file or its fingerprint.
  // It is fitted to the proxy's limits (insight-proxy/src/guard.js LIMITS and MAX_BODY_BYTES), so a
  // long report is shortened here rather than refused there; text is cut only at a space, so a cut
  // never makes a new number out of part of one. keepValues: send quoted values and the file
  // name as they are (the visitor ticked it); otherwise they go as placeholders.
  var AI_LIMITS = { objective: 300, findings: 40, id: 64, claim: 300, line: 600, lines: 20, bytes: 16384 };
  function cut(s, max) {
    s = String(s === null || s === undefined ? '' : s);
    if (s.length <= max) return s;
    var head = s.slice(0, max - 1), sp = head.lastIndexOf(' ');
    return (sp > 0 ? head.slice(0, sp) : '').replace(/[\s,;:]+$/, '') + '…';
  }
  // The names a withheld column goes by in the report: the column itself and the cleaning rules
  // that act on it (a rule name such as salary_numeric names its column). A finding or a story
  // line that names one is never sent to an AI; the page itself still shows it, with no value.
  T.aiWithheldTerms = function (rep) {
    var cols = ((rep.privacy || {}).flagged || []).filter(function (f) { return f && f.decision === 'withhold' && typeof f.column === 'string' && f.column; })
      .map(function (f) { return f.column; });
    var terms = cols.slice(), add = function (k) { if (typeof k === 'string' && k && terms.indexOf(k) < 0) terms.push(k); };
    ((rep.health || {}).columns || []).forEach(function (c) {
      if (c && cols.indexOf(c.name) >= 0) [c.quarantined_by_rule, c.fixes_by_rule].forEach(function (o) { Object.keys(o || {}).forEach(add); });
    });
    ((rep.cleaning || {}).fixes || []).forEach(function (fx) { if (fx && cols.indexOf(fx.column) >= 0) add(fx.rule); });
    return terms;
  };
  // does the text name one of the terms as a whole name (a letter, digit or underscore on neither
  // side, except that a rule name may follow with an underscore: salary in salary_numeric)
  T.namesWithheld = function (text, terms) {
    var t = String(text === null || text === undefined ? '' : text);
    return (terms || []).some(function (w) {
      return new RegExp('(^|[^A-Za-z0-9_])' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?![A-Za-z0-9])', 'i').test(t);
    });
  };
  T.aiPayload = function (rep, objective, keepValues) {
    var red = keepValues ? [] : T.aiRedactions(rep), w = function (x) { return redact(x, red); };
    var terms = T.aiWithheldTerms(rep), named = function (x) { return T.namesWithheld(x, terms); };
    var L = AI_LIMITS, s = rep.story || {}, story = { headline: named(s.headline) ? '' : cut(w(s.headline), L.line) };
    STORY_KEYS.forEach(function (k) { story[k[0]] = (s[k[0]] || []).filter(function (x) { return !named(x); }).slice(0, L.lines).map(function (x) { return cut(w(x), L.line); }); });
    var body = {
      objective: cut(objective, L.objective),
      findings: (rep.findings || []).filter(function (f) { return f && typeof f.id === 'string' && f.id && f.id.length <= L.id && VERDICTS.indexOf(f.verdict) >= 0 && !named(f.id) && !named(f.claim); })
        .slice(0, L.findings).map(function (f) {
          return { id: f.id, claim: cut(w(f.claim), L.claim), verdict: f.verdict, value: (typeof f.value === 'number' && isFinite(f.value)) ? f.value : null };
        }),
      story: story
    };
    var size = function () { return new TextEncoder().encode(JSON.stringify(body)).length; };
    while (size() > L.bytes && body.findings.length) body.findings.pop();          // the least certain go first
    STORY_KEYS.slice().reverse().forEach(function (k) { while (size() > L.bytes && story[k[0]].length) story[k[0]].pop(); });
    return body;
  };

  /* ------------------------------------------------------------ reading the report */
  T.gateTripped = function (rep) { return !!(rep && rep.story && typeof rep.story.headline === 'string' && rep.story.headline.indexOf(GATE) === 0); };
  // story lines that share the engine's "Why: ... What would settle it: ..." text are shown once,
  // with each line's own claim listed under it; a lead they all share ("The data cannot support
  // this claim:") is said once too. The words and figures are the engine's.
  T.groupLines = function (lines) {
    var out = [], byKey = {};
    (lines || []).forEach(function (s) {
      var m = /^([\s\S]*?)\s*Why: ([\s\S]+?)(?:\s*What would settle it: ([\s\S]+))?$/.exec(s);
      if (!m || !m[1].trim()) { out.push({ text: s }); return; }
      var key = m[2].trim() + '\u0000' + (m[3] || '').trim();
      if (byKey[key]) { byKey[key].items.push(m[1].trim()); return; }
      byKey[key] = { items: [m[1].trim()], why: m[2].trim(), settle: (m[3] || '').trim(), src: s };
      out.push(byKey[key]);
    });
    return out.map(function (g) {
      if (g.text !== undefined) return g;
      if (g.items.length === 1) return { text: g.src };
      var lead = '', first = g.items[0], i = first.indexOf(': ');
      if (i > 0 && g.items.every(function (x) { return x.indexOf(first.slice(0, i + 2)) === 0 && x.length > i + 2; })) {
        lead = first.slice(0, i + 1);
        g.items = g.items.map(function (x) { return x.slice(i + 2); });
      }
      return { lead: lead, items: g.items, why: g.why, settle: g.settle };
    });
  };
  // the words a grouped entry shows, in order (the checks compare the page with this)
  T.groupText = function (g) {
    return g.text !== undefined ? g.text : [g.lead].concat(g.items, ['Why: ' + g.why], g.settle ? ['What would settle it: ' + g.settle] : []).filter(Boolean).join(' ');
  };

  /* ------------------------------------------------------------ reading the file in the page */
  // data rows (header excluded), counting a quoted field's line breaks as part of its row;
  // stops counting past `cap` so a huge file is refused quickly
  T.countRows = function (text, delim, cap) {
    var n = 0, inQ = false, content = false, len = text.length, c;
    cap = cap || Infinity;
    for (var i = 0; i < len; i++) {
      c = text.charCodeAt(i);
      if (inQ) {
        if (c === 34) { if (text.charCodeAt(i + 1) === 34) i++; else inQ = false; }
        continue;
      }
      if (c === 34) { inQ = true; content = true; continue; }
      if (c === 10 || c === 13) {
        if (c === 13 && text.charCodeAt(i + 1) === 10) i++;
        if (content) { n++; if (n > cap + 1) return n - 1; }
        content = false;
        continue;
      }
      if (c !== 32) content = true;
    }
    if (content) n++;
    return Math.max(0, n - 1);
  };
  T.sniff = function (bytes, name) {
    var nm = String(name || '').toLowerCase(), b = bytes || new Uint8Array(0);
    if (!b.length) return { ok: false, reason: 'empty' };
    var sig = function (arr) { for (var i = 0; i < arr.length; i++) if (b[i] !== arr[i]) return false; return b.length >= arr.length; };
    if (sig([0x50, 0x4b, 0x03, 0x04]) || sig([0x50, 0x4b, 0x05, 0x06])) return { ok: false, reason: /\.(xlsx|xlsm|xls|ods|numbers)$/.test(nm) || !/\.zip$/.test(nm) ? 'excel' : 'zip' };
    if (sig([0xd0, 0xcf, 0x11, 0xe0])) return { ok: false, reason: 'excel' };
    if (sig([0x25, 0x50, 0x44, 0x46])) return { ok: false, reason: 'pdf' };
    var enc = 'utf-8';
    if (sig([0xff, 0xfe])) enc = 'utf-16le';
    else if (sig([0xfe, 0xff])) enc = 'utf-16be';
    var head = b.subarray(0, Math.min(b.length, 65536));
    if (enc === 'utf-8') for (var j = 0; j < head.length; j++) if (head[j] === 0) return { ok: false, reason: 'binary' };
    var text;
    try { text = new TextDecoder(enc).decode(head); } catch (e) { return { ok: false, reason: 'binary' }; }
    var first = text.replace(/^\ufeff/, '').split(/\r?\n/)[0] || '';
    var tabs = (first.match(/\t/g) || []).length, commas = (first.match(/,/g) || []).length, semis = (first.match(/;/g) || []).length;
    var tsv = /\.tsv$/.test(nm) || (tabs > 0 && tabs >= commas && tabs >= semis);
    return { ok: true, kind: tsv ? 'tsv' : 'csv', delim: tsv ? '\t' : (semis > commas ? ';' : ','), encoding: enc };
  };

  /* ------------------------------------------------------------ THE REPORT CONTRACT */
  T.validate = function (r) {
    var bad = [];
    var is = {
      str: function (v) { return typeof v === 'string'; },
      num: function (v) { return typeof v === 'number' && isFinite(v); },
      int: function (v) { return typeof v === 'number' && Math.floor(v) === v; },
      bool: function (v) { return typeof v === 'boolean'; },
      arr: function (v) { return Array.isArray(v); },
      obj: function (v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
    };
    function need(ok, where, what) { if (!ok) bad.push(where + ' must be ' + what); return ok; }
    function orNull(f) { return function (v) { return v === null || f(v); }; }
    if (!need(is.obj(r), 'the report', 'an object')) return bad;
    need(is.bool(r.ok), 'ok', 'true or false');
    need(r.error === null || is.str(r.error), 'error', 'text or null');
    if (r.ok === false) { need(is.str(r.error) && r.error.length > 0, 'error', 'a reason when ok is false'); return bad; }
    if (need(is.obj(r.engine), 'engine', 'an object')) {
      need(is.str(r.engine.snapshot) && /^[0-9a-f]{12}$/.test(r.engine.snapshot), 'engine.snapshot', 'twelve hex characters');
      need(is.str(r.engine.version), 'engine.version', 'text');
    }
    if (need(is.obj(r.input), 'input', 'an object')) {
      need(is.str(r.input.name), 'input.name', 'text');
      ['bytes', 'rows', 'columns'].forEach(function (k) { need(is.int(r.input[k]), 'input.' + k, 'a whole number'); });
      need(is.str(r.input.sha256), 'input.sha256', 'text');
    }
    if (need(is.arr(r.timings), 'timings', 'a list')) r.timings.forEach(function (t, i) {
      need(is.obj(t) && STAGES.indexOf(t.stage) >= 0, 'timings[' + i + '].stage', 'one of ' + STAGES.join(', '));
      need(is.obj(t) && is.num(t.seconds), 'timings[' + i + '].seconds', 'a number');
    });
    if (need(is.obj(r.privacy) && is.arr(r.privacy.flagged), 'privacy.flagged', 'a list')) r.privacy.flagged.forEach(function (f, i) {
      need(is.obj(f) && is.str(f.column) && is.str(f.kind), 'privacy.flagged[' + i + ']', 'a column and a kind');
      need(is.obj(f) && ['withhold', 'code', 'keep'].indexOf(f.decision) >= 0, 'privacy.flagged[' + i + '].decision', 'withhold, code or keep');
    });
    if (need(is.obj(r.health), 'health', 'an object')) {
      need(orNull(is.num)(r.health.score), 'health.score', 'a number or null');
      need(is.arr(r.health.issues) && r.health.issues.every(is.str), 'health.issues', 'a list of text');
    }
    var c = r.cleaning;
    if (need(is.obj(c), 'cleaning', 'an object')) {
      ['rows_in', 'rows_clean', 'rows_quarantined'].forEach(function (k) { need(is.int(c[k]), 'cleaning.' + k, 'a whole number'); });
      if (need(is.arr(c.fixes), 'cleaning.fixes', 'a list')) c.fixes.forEach(function (f, i) {
        need(is.obj(f) && is.str(f.rule) && (f.column === null || is.str(f.column)) && is.int(f.count) && is.str(f.what),
          'cleaning.fixes[' + i + ']', 'a rule, a column (or null), a count and what it did');
      });
      if (need(is.arr(c.quarantine_reasons), 'cleaning.quarantine_reasons', 'a list')) c.quarantine_reasons.forEach(function (q, i) {
        need(is.obj(q) && is.str(q.reason) && is.int(q.count), 'cleaning.quarantine_reasons[' + i + ']', 'a reason and a count');
      });
    }
    var ro = r.roles;
    if (need(is.obj(ro), 'roles', 'an object')) {
      need(ro.date === null || is.str(ro.date), 'roles.date', 'a column name or null');
      need(is.arr(ro.measures) && ro.measures.every(is.str), 'roles.measures', 'a list of column names');
      need(is.arr(ro.dimensions) && ro.dimensions.every(is.str), 'roles.dimensions', 'a list of column names');
      need(is.obj(ro.excluded) && Object.keys(ro.excluded).every(function (k) { return is.str(ro.excluded[k]); }), 'roles.excluded', 'column: reason pairs');
    }
    if (need(is.arr(r.findings), 'findings', 'a list')) r.findings.forEach(function (f, i) {
      var w = 'findings[' + i + ']';
      if (!need(is.obj(f), w, 'an object')) return;
      ['id', 'claim', 'why', 'kind'].forEach(function (k) { need(is.str(f[k]), w + '.' + k, 'text'); });
      need(VERDICTS.indexOf(f.verdict) >= 0, w + '.verdict', 'RECOMMEND, WATCH or INSUFFICIENT');
      need(orNull(is.num)(f.value), w + '.value', 'a number or null');
    });
    var F = r.forecast;
    if (need(is.obj(F), 'forecast', 'an object')) {
      need(is.bool(F.available), 'forecast.available', 'true or false');
      need(is.str(F.reason), 'forecast.reason', 'text');
      need(orNull(is.str)(F.verdict), 'forecast.verdict', 'text or null');
      need(orNull(is.str)(F.champion), 'forecast.champion', 'text or null');
      need(orNull(is.bool)(F.baseline_won), 'forecast.baseline_won', 'true, false or null');
      var ym = function (v) { return is.str(v) && /^\d{4}-\d{2}$/.test(v); };
      if (need(is.arr(F.series), 'forecast.series', 'a list')) F.series.forEach(function (p, i) {
        need(is.obj(p) && ym(p.month) && is.num(p.actual), 'forecast.series[' + i + ']', 'a YYYY-MM month and an actual');
      });
      if (need(is.arr(F.forecast), 'forecast.forecast', 'a list')) F.forecast.forEach(function (p, i) {
        need(is.obj(p) && ym(p.month) && is.num(p.value) && orNull(is.num)(p.lo) && orNull(is.num)(p.hi), 'forecast.forecast[' + i + ']', 'a month, a value and its range (or null)');
      });
      if (need(is.obj(F.backtest), 'forecast.backtest', 'an object')) ['mape', 'mase', 'coverage'].forEach(function (k) {
        need(orNull(is.num)(F.backtest[k]), 'forecast.backtest.' + k, 'a number or null');
      });
    }
    if (need(is.obj(r.story), 'story', 'an object')) {
      need(is.str(r.story.headline), 'story.headline', 'text');
      STORY_KEYS.forEach(function (k) { need(is.arr(r.story[k[0]]) && r.story[k[0]].every(is.str), 'story.' + k[0], 'a list of text'); });
    }
    if (need(is.obj(r.downloads), 'downloads', 'an object')) ['clean_csv', 'quarantine_csv', 'ledger_json'].forEach(function (k) {
      need(is.str(r.downloads[k]), 'downloads.' + k, 'text');
    });
    return bad;
  };

  /* ------------------------------------------------------------ formatting */
  function num(v, dp) {
    if (v === null || v === undefined || !isFinite(v)) return 'n/a';
    return Number(v).toLocaleString('en-US', { maximumFractionDigits: dp === undefined ? 2 : dp });
  }
  function bytes(n) {
    if (n >= 1e6) return num(n / 1e6, 1) + ' MB';
    if (n >= 1e3) return num(n / 1e3, 0) + ' KB';
    return num(n, 0) + ' bytes';
  }
  function secs(s) { return (s === null || s === undefined || !isFinite(s)) ? '' : (s > 0 && s < 0.05 ? 'under 0.1 s' : Number(s).toFixed(1) + ' s'); }
  function pctOf(v) { return v === null || v === undefined ? 'n/a' : Number(v).toFixed(1) + '%'; }   // MAPE, in percent units, printed as the engine prints it
  // coverage arrives in percent (the adapter: 100 * hits / n) and prints the way the engine's own
  // sentences print it (forecast._pct0 rounds down), so the card and the verdict text agree
  function share(v) { return v === null || v === undefined ? 'n/a' : num(Math.floor(v + 1e-9), 0) + '%'; }
  function axis(v) {
    var a = Math.abs(v);
    if (a >= 1e9) return num(v / 1e9, 1) + 'B';
    if (a >= 1e6) return num(v / 1e6, 1) + 'M';
    if (a >= 1e4) return num(v / 1e3, 0) + 'k';
    return num(v, a < 10 ? 2 : 0);
  }
  function month(ym) { return U.month ? U.month(ym) : ym; }
  function badge(word, why) {
    var icon = (CFG.badges || {})[word] || '';
    return '<span class="badge b-' + esc(String(word).toLowerCase()) + '">' + icon + esc(word) + '</span>' + (why ? '<span class="why">' + esc(why) + '</span>' : '');
  }
  function list(items) { return items && items.length ? '<ul>' + items.map(function (s) { return '<li>' + esc(s) + '</li>'; }).join('') + '</ul>' : ''; }
  function tbl(label, cols, rows, cls) {
    return '<div class="tscroll" tabindex="0" role="region" aria-label="' + esc(label) + '"><table class="dt ' + (cls || '') + '"><thead><tr>' +
      cols.map(function (c) { return '<th scope="col"' + (c.num ? ' class="num"' : '') + '>' + esc(c.t) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r) { return '<tr>' + r.map(function (v, i) { return i === 0 ? '<th scope="row">' + v + '</th>' : '<td' + (cols[i].num ? ' class="num"' : '') + '>' + v + '</td>'; }).join('') + '</tr>'; }).join('') +
      '</tbody></table></div>';
  }
  function plural(n, one, many) { return num(n, 0) + ' ' + (n === 1 ? one : many); }
  function stem(name) { return String(name || 'file').replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'file'; }

  /* ------------------------------------------------------------ the forecast chart */
  function drawForecast(W, F) {
    var act = F.series, fc = F.forecast;
    var months = act.map(function (p) { return p.month; }).concat(fc.map(function (p) { return p.month; }));
    var vals = [];
    act.forEach(function (p) { vals.push(p.actual); });
    fc.forEach(function (p) { vals.push(p.value); if (isFinite(p.lo) && p.lo !== null) vals.push(p.lo); if (isFinite(p.hi) && p.hi !== null) vals.push(p.hi); });
    var ranged = fc.length && fc.every(function (p) { return typeof p.lo === 'number' && typeof p.hi === 'number'; });   // the engine may draw no range
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.1 || 1;
    var ymin = lo - pad, ymax = hi + pad;
    if (lo >= 0 && ymin < 0) ymin = 0;
    var twoRow = W < 520, T0 = twoRow ? 42 : 28, L = 54, R = W - 12, H = Math.max(240, Math.min(340, Math.round(W * 0.5))) + T0 - 12, Bt = H - 30;
    var x = U.scale(0, Math.max(1, months.length - 1), L, R), y = U.scale(ymin, ymax, Bt, T0), b = '';
    U.ticks(ymin, ymax, 5).forEach(function (t) {
      b += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + y(t).toFixed(1) + '" y2="' + y(t).toFixed(1) + '"/>' + U.txt(L - 6, y(t) + 4, axis(t), 'lab', 'end');
    });
    var jan = [];
    months.forEach(function (m, i) { if (m.slice(5) === '01') jan.push(i); });
    var every = Math.max(1, Math.ceil(jan.length / Math.max(1, Math.floor((R - L) / 56))));
    if (jan.length >= 2) jan.forEach(function (i, k) { if (k % every === 0) b += U.txt(x(i), H - 10, months[i].slice(0, 4), 'lab', 'middle'); });
    else { b += U.txt(L, H - 10, month(months[0]), 'lab', 'start') + U.txt(R, H - 10, month(months[months.length - 1]), 'lab', 'end'); }
    var off = act.length - 1, last = act[off];
    if (ranged) {
      var top = [[x(off), y(last.actual)]].concat(fc.map(function (p, i) { return [x(off + 1 + i), y(p.hi)]; }));
      var bot = fc.map(function (p, i) { return [x(off + 1 + i), y(p.lo)]; }).reverse();
      b += '<path class="fan" d="' + U.path(top) + 'L' + bot.map(function (p) { return p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join('L') + 'Z"/>';
    }
    b += '<path class="ln ln-s" d="' + U.path(act.map(function (p, i) { return [x(i), y(p.actual)]; })) + '"/>';
    if (fc.length) b += '<path class="ln ln-a ln-dash" d="' + U.path([[x(off), y(last.actual)]].concat(fc.map(function (p, i) { return [x(off + 1 + i), y(p.value)]; }))) + '"/>';
    var rows = act.map(function (p) { return [month(p.month), num(p.actual), '', '', '']; });
    var rad = Math.max(2.2, Math.min(4, (x(1) - x(0)) / 2.4));        // dots never touch on a narrow chart
    fc.forEach(function (p, i) {
      var t = month(p.month) + ': forecast ' + num(p.value) + (typeof p.lo === 'number' && typeof p.hi === 'number' ? ', 80% range ' + num(p.lo) + ' to ' + num(p.hi) : ', no range drawn');
      b += '<circle class="mk dot-a" cx="' + x(off + 1 + i).toFixed(1) + '" cy="' + y(p.value).toFixed(1) + '" r="' + rad.toFixed(1) + '" tabindex="0"' + U.tipAttr(t) + '/>';
      rows.push([month(p.month), '', num(p.value), num(p.lo), num(p.hi)]);
    });
    b += '<line x1="' + x(off).toFixed(1) + '" x2="' + x(off).toFixed(1) + '" y1="' + T0 + '" y2="' + Bt + '" stroke="var(--axis)" stroke-dasharray="2 3"/>' +
      (twoRow ? '' : U.txt(x(off) - 4, Bt - 6, 'last actual ' + month(last.month), 'lab', 'end'));   // on a phone the label would sit on the line
    b += '<path class="ln ln-s" d="M' + (L + 6) + ' 8h18"/>' + U.txt(L + 28, 12, 'actual', 'lab') +
      '<path class="ln ln-a ln-dash" d="M' + (L + 86) + ' 8h18"/>' + U.txt(L + 108, 12, 'forecast', 'lab') +
      (!ranged ? '' : twoRow ? '<rect class="fan" x="' + (L + 6) + '" y="19" width="18" height="10"/>' + U.txt(L + 28, 28, '80% range from past errors', 'lab')
        : '<rect class="fan" x="' + (L + 172) + '" y="3" width="18" height="10"/>' + U.txt(L + 194, 12, '80% range from past errors', 'lab'));
    return {
      svg: U.svg(W, H, 'Your monthly series: ' + plural(act.length, 'month', 'months') + ' of actuals from ' + month(act[0].month) + ' to ' + month(last.month) +
        ', then the engine\'s forecast for ' + plural(fc.length, 'month', 'months') + (ranged ? ' with its 80% range' : ''), b),
      table: { cols: ['Month', 'Actual', 'Forecast', '80% range low', '80% range high'], rows: rows }
    };
  }

  /* ------------------------------------------------------------ the page */
  U.onReady && U.onReady(function () {
    var root = document.getElementById('try');
    if (!root || !document.getElementById('try-report')) return;
    var $ = function (id) { return document.getElementById(id); };
    var el = { start: $('try-start'), drop: $('try-drop'), pick: $('try-pick'), file: $('try-file'), q: $('try-q'), sample: $('try-sample'),
      msg: $('try-msg'), pd: $('try-pd'), run: $('try-run'), runName: $('try-run-name'), cancel: $('try-cancel'), stages: $('try-stages'),
      note: $('try-run-note'), report: $('try-report') };
    var LIM = T.limits();
    var qWrap = $('try-q-wrap');
    if (qWrap && el.q) qWrap.hidden = !CFG.ai_proxy_url;             // the question is used only for the AI summaries
    Array.prototype.forEach.call(document.querySelectorAll('[data-needs-ai]'), function (n) { n.hidden = !CFG.ai_proxy_url; });   // AI wording notes follow the same switch
    var S = { worker: null, seq: 0, busy: false, name: '', objective: '', t0: {}, tick: null, report: null, ai: null, aiNote: '', aiRaw: false, aiRed: [] };
    var STAGE_LABEL = { load: 'Load the engine into this page', read: 'Read the file', profile: 'Profile the columns and look for personal data',
      decide: 'Apply your personal-data choices', clean: 'Clean with stated rules', analyze: 'Find and check findings',
      forecast: 'Backtest a forecast', story: 'Write the story' };
    var reduced = function () { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); };
    var goTo = function (node, focus) {
      if (!node) return;
      try { node.scrollIntoView({ behavior: reduced() ? 'auto' : 'smooth', block: 'start' }); } catch (e) { node.scrollIntoView(); }
      if (focus) { try { node.focus({ preventScroll: true }); } catch (e2) { node.focus(); } }
    };

    /* ---- messages in plain language ---- */
    function say(title, body, extra) {
      el.msg.innerHTML = '<h3>' + esc(title) + '</h3><p>' + esc(body) + '</p>' + (extra || '');
      el.msg.hidden = false;
    }
    function clearMsg() { el.msg.hidden = true; el.msg.innerHTML = ''; }
    function refuse(kind, info) {
      stopRun();
      var L = LIM;
      if (kind === 'big') say('This file is too big for the demo', 'It is ' + bytes(info.size) + '; the demo takes files up to ' + L.max_label + '. It did not open it, and nothing was sampled or uploaded. Split the file (for example one year at a time) or email me for the full audit.');
      else if (kind === 'rows') say('This file has too many rows for the demo', 'It has ' + num(info.rows, 0) + ' data rows; the demo takes up to ' + L.rows_label + '. It did not open it and has not sampled it: a sample would change every number. Split the file, or email me for the full audit.');
      else if (kind === 'excel') say('This looks like an Excel workbook, not a CSV', 'The demo reads CSV or TSV text. In Excel or Numbers, use File, Save as (or Export), choose CSV, then drop that file here.');
      else if (kind === 'pdf') say('This is a PDF, not a CSV', 'The demo reads tables saved as CSV or TSV text. Export the table from the program that made the PDF as CSV and drop that file here.');
      else if (kind === 'zip') say('This is a zip archive, not a CSV', 'Unzip it first, then drop the CSV file inside.');
      else if (kind === 'binary') say('This does not look like a CSV', 'The file holds binary data, not text. Save the table as CSV (comma-separated) or TSV (tab-separated) text and try again.');
      else if (kind === 'empty') say('This file is empty', 'There is nothing in it to read. Check you picked the right file.');
      else if (kind === 'norows') say('This file has a header but no data rows', 'The engine needs at least one row under the column names.');
      else if (kind === 'file') say('The demo needs this page opened from a web address', 'It starts a background engine, which browsers do not allow on a page opened from a file on disk. Open the site from its web address and try again.');
      else if (kind === 'sample') say('The sample file is not on this copy of the site', 'The page could not load the demo sample (' + (info && info.why || 'not found') + '). You can still drop your own CSV.');
      else say(info.title || 'The engine stopped', info.body || 'Something went wrong while it worked on the file.', info.extra);
      goTo(el.msg);
    }

    /* ---- the staged progress list ---- */
    function drawStages() {
      el.stages.innerHTML = ['load'].concat(STAGES).map(function (s) {
        return '<li data-stage="' + s + '" class="st-wait"><span class="st-dot" aria-hidden="true"></span><span class="st-name">' + esc(STAGE_LABEL[s]) +
          '<small class="st-state">waiting</small></span><span class="st-sec"></span></li>';
      }).join('');
    }
    function stageEl(s) { return el.stages.querySelector('li[data-stage="' + s + '"]'); }
    function setStage(s, state, seconds, note) {
      var li = stageEl(s);
      if (!li) return;
      var st = li.querySelector('.st-state'), sec = li.querySelector('.st-sec');
      if (state === 'start') {
        S.t0[s] = performance.now();
        li.className = 'st-run'; st.textContent = 'working';
        el.note.textContent = STAGE_LABEL[s] + '\u2026';
        if (!S.tick) S.tick = setInterval(tickStages, 100);
      } else if (state === 'done') {
        var t = (seconds !== null && seconds !== undefined && isFinite(seconds)) ? seconds : (S.t0[s] ? (performance.now() - S.t0[s]) / 1000 : null);
        li.className = 'st-done'; st.textContent = note || 'done'; sec.textContent = note === 'already loaded' ? '' : secs(t);
        delete S.t0[s];
      } else if (state === 'wait') {
        li.className = 'st-ask'; st.textContent = note || 'waiting for you'; sec.textContent = '';
        el.note.textContent = 'Waiting for your personal-data choices, above.';
        delete S.t0[s];
      } else if (state === 'skip') {
        li.className = 'st-done st-skip'; st.textContent = note || 'not needed'; sec.textContent = '';
        delete S.t0[s];
      }
    }
    function tickStages() {
      var any = false;
      Object.keys(S.t0).forEach(function (s) {
        var li = stageEl(s);
        if (li && li.className === 'st-run') { any = true; li.querySelector('.st-sec').textContent = secs((performance.now() - S.t0[s]) / 1000); }
      });
      if (!any && S.tick) { clearInterval(S.tick); S.tick = null; }
    }
    function stopRun() {
      if (S.tick) { clearInterval(S.tick); S.tick = null; }
      S.t0 = {}; S.busy = false;
      el.run.hidden = true; el.pd.hidden = true;
      el.sample.disabled = false; el.pick.disabled = false;
      if (el.q) el.q.disabled = false;
    }

    /* ---- the worker ---- */
    function worker() {
      if (S.worker) return S.worker;
      var w = new Worker(CFG.worker || 'engine/worker.js');
      w.onmessage = function (e) { onMsg(e.data || {}); };
      w.onerror = function (e) {
        if (e && e.preventDefault) e.preventDefault();
        killWorker();
        refuse('engine', { title: 'The engine could not start', body: 'The browser stopped the engine before it finished loading' + (e && e.message ? ' (' + e.message + ')' : '') + '. Reload the page and try again.' });
      };
      S.worker = w;
      return w;
    }
    function killWorker() { if (S.worker) { S.worker.terminate(); S.worker = null; } }
    var ERR = {
      runtime: ['The engine\'s Python runtime could not be downloaded', 'It comes from cdn.jsdelivr.net on the first run. Check the connection (or an ad or script blocker) and try again.'],
      engine_missing: ['The engine files are not on this copy of the site', 'The page could not load the packed engine next to it. Nothing was uploaded.'],
      engine: ['The engine stopped on this file', 'It raised an error while working on the file. Nothing was uploaded.']
    };
    function onMsg(m) {
      if (m.id !== undefined && m.id !== S.seq) return;          // a message from a run that was stopped
      if (m.type === 'stage') {
        setStage(m.stage, m.state, m.seconds, m.cached ? 'already loaded' : null);
      } else if (m.type === 'scanned') {
        var r = m.result || {};
        if (!r.ok) return refuse('engine', { title: 'The engine could not read this file', body: r.error || 'It gave no reason.' });
        var flagged = (r.flagged || []).filter(function (f) { return f && typeof f.column === 'string'; });
        if (flagged.length) askPersonal(flagged);
        else { setStage('decide', 'skip', null, 'no personal data flagged'); sendRun({}); }
      } else if (m.type === 'result') {
        onReport(m.report);
      } else if (m.type === 'error') {
        var e = ERR[m.code] || ERR.engine;
        refuse('engine', { title: e[0], body: (m.message ? m.message + ' ' : '') + e[1],
          extra: m.detail ? '<details class="more"><summary>Technical detail</summary><pre class="snip">' + esc(m.detail) + '</pre></details>' : '' });
        if (m.code === 'runtime' || m.code === 'engine_missing') killWorker();
      }
    }

    /* ---- the personal-data step ---- */
    var CHOICES = [['withhold', 'Withhold', 'its values are never shown, downloaded, used in the business analysis or sent to an AI, and nothing that names it is sent to an AI; the data-health findings on this page still name it and count its blanks and spellings'],
      ['code', 'Code', 'each value replaced with a code in the downloads; the analysis still leaves the column out'],
      ['keep', 'Keep', 'used as it is: it can appear in the findings, the story and the downloads']];
    function askPersonal(flagged) {
      setStage('decide', 'wait');
      el.pd.innerHTML = '<h3 id="try-pd-h">Personal data: you decide</h3>' +
        '<p>The engine flagged ' + plural(flagged.length, 'column', 'columns') + ' as possibly personal. Withhold is chosen for each; change it only if the column is safe to use. Whatever you pick, it stays in your browser.</p>' +
        '<p class="note">The scan reads column names and the shape of values. A column of names under a neutral heading (a technician or a vendor, say) can be missed, and is then used as it is.</p>' +
        flagged.map(function (f, i) {
          var coded = String(f.kind || '').indexOf(CODED) >= 0;
          return '<fieldset class="pd-col"><legend><code>' + esc(f.column) + '</code> <span class="pd-kind">flagged: ' + esc(f.kind || 'possibly personal') + '</span></legend><div class="pd-opts">' +
            CHOICES.map(function (c) {
              var off = coded && c[0] === 'keep';
              return '<label class="pd-opt"><input type="radio" name="pd-' + i + '" value="' + c[0] + '"' + (c[0] === 'withhold' ? ' checked' : '') + (off ? ' disabled' : '') + ' data-col="' + esc(f.column) + '"><span><b>' + c[1] + '</b><small>' + c[2] + '</small></span></label>';
            }).join('') + '</div>' + (coded ? '<p class="pd-note">Already coded as it arrived: an email address or phone number cannot be kept as it is, so Keep is not offered.</p>' : '') + '</fieldset>';
        }).join('') +
        '<div class="pd-go"><button type="button" class="btn btn-primary" id="try-pd-go">Continue with these choices</button></div>';
      el.pd.hidden = false;
      $('try-pd-go').addEventListener('click', function () {
        var d = {};
        Array.prototype.forEach.call(el.pd.querySelectorAll('input[type=radio]:checked'), function (x) { d[x.getAttribute('data-col')] = x.value; });
        el.pd.hidden = true;
        setStage('decide', 'start');
        sendRun(d);
      });
      goTo($('try-pd-h'));
      var first = el.pd.querySelector('input[type=radio]:checked');
      if (first) { try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); } }
    }
    function sendRun(decisions) {
      worker().postMessage({ type: 'run', id: S.seq, options: { name: S.name, objective: S.objective, decisions: decisions, as_of: S.asOf, max_bytes: LIM.max_bytes, max_rows: LIM.max_rows } });
    }

    /* ---- start: from the picker, a drop, or the sample ---- */
    function start(name, size, getBuffer, asOf) {
      if (S.busy) return;
      clearMsg();
      el.report.hidden = true;
      if (!LIM.max_bytes || !LIM.max_rows) return refuse('engine', { title: 'The demo is not set up on this page', body: 'Its limits are missing from the page data.' });
      if (size > LIM.max_bytes) return refuse('big', { size: size });
      if (location.protocol === 'file:') return refuse('file');
      S.busy = true; el.sample.disabled = true; el.pick.disabled = true;
      if (el.q) el.q.disabled = true;
      getBuffer().then(function (buf) {
        var u8 = new Uint8Array(buf), sn = T.sniff(u8, name);
        if (!sn.ok) return refuse(sn.reason);
        var text = new TextDecoder(sn.encoding).decode(u8);
        var rows = T.countRows(text.replace(/^\ufeff/, ''), sn.delim, LIM.max_rows);
        if (rows > LIM.max_rows) {
          // count on to the end, so the message states the file's real size
          return refuse('rows', { rows: T.countRows(text.replace(/^\ufeff/, ''), sn.delim) });
        }
        if (rows === 0) return refuse('norows');
        if (sn.encoding !== 'utf-8') buf = new TextEncoder().encode(text.replace(/^\ufeff/, '')).buffer;   // the engine reads UTF-8
        S.seq += 1; S.name = name; S.asOf = asOf || null; S.objective = CFG.ai_proxy_url && el.q ? (el.q.value || '').trim() : ''; S.report = null; S.ai = null; S.aiNote = ''; S.aiRaw = false; S.aiRed = [];
        el.runName.textContent = name;
        drawStages();
        el.run.hidden = false;
        goTo(el.run);
        var w;
        try { w = worker(); } catch (e) { return refuse('engine', { title: 'This browser cannot run the engine', body: 'It does not allow a background worker here (' + e.message + ').' }); }
        w.postMessage({ type: 'scan', id: S.seq, name: name, buffer: buf,
          options: { name: name, objective: S.objective, as_of: S.asOf, max_bytes: LIM.max_bytes, max_rows: LIM.max_rows } }, [buf]);
      }).catch(function (e) {
        refuse('engine', { title: 'The file could not be read', body: 'The browser could not open it (' + (e && e.message || e) + ').' });
      });
    }
    el.pick.addEventListener('click', function () { el.file.click(); });
    el.drop.addEventListener('click', function (e) { if (e.target === el.drop || (e.target.closest && !e.target.closest('button'))) { if (!S.busy) el.file.click(); } });
    el.file.addEventListener('change', function () {
      var f = el.file.files && el.file.files[0];
      if (f) start(f.name, f.size, function () { return f.arrayBuffer(); });
      el.file.value = '';
    });
    root.addEventListener('dragover', function (e) { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'; });
    root.addEventListener('drop', function (e) { e.preventDefault(); });
    el.drop.addEventListener('dragenter', function (e) { e.preventDefault(); el.drop.classList.add('over'); });
    el.drop.addEventListener('dragleave', function (e) { if (!el.drop.contains(e.relatedTarget)) el.drop.classList.remove('over'); });
    el.drop.addEventListener('drop', function (e) {
      e.preventDefault(); el.drop.classList.remove('over');
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) start(f.name, f.size, function () { return f.arrayBuffer(); });
    });
    el.sample.addEventListener('click', function () {
      var url = CFG.sample || 'engine/sample-messy.csv', name = url.split('/').pop();
      if (location.protocol === 'file:') return refuse('file');
      start(name, 0, function () {
        return fetch(url, { cache: 'no-cache' }).then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.arrayBuffer();
        }).then(function (b) {
          if (b.byteLength > LIM.max_bytes) throw new Error('larger than the limit');
          return b;
        });
      }, CFG.sample_as_of || null);   // the sample's fixed analysis date, so its report does not age
    });
    el.cancel.addEventListener('click', function () {
      killWorker();
      S.seq += 1;
      stopRun();
      say('Stopped', 'The engine was stopped and its copy of your file was dropped with it. Nothing was uploaded.');
      el.pick.focus();
    });

    /* ---- the report ---- */
    function onReport(rep) {
      if (S.tick) { clearInterval(S.tick); S.tick = null; }
      var bad = T.validate(rep);
      if (bad.length) {
        stopRun();
        return refuse('engine', { title: 'The engine\'s answer could not be shown', body: 'Its report did not have the shape this page reads, so nothing from it is shown.',
          extra: '<details class="more"><summary>What was wrong</summary>' + list(bad.slice(0, 12)) + '</details>' });
      }
      if (!rep.ok) { stopRun(); return refuse('engine', { title: 'The engine could not work on this file', body: rep.error }); }
      // stages the engine did not announce take their time from the report
      (rep.timings || []).forEach(function (t) { var li = stageEl(t.stage); if (li && li.className !== 'st-done') setStage(t.stage, 'done', t.seconds); });
      STAGES.forEach(function (s) { var li = stageEl(s); if (li && li.className !== 'st-done') setStage(s, 'skip'); });
      el.note.textContent = 'Done: the report is below.';
      S.report = rep; S.busy = false;
      el.sample.disabled = false; el.pick.disabled = false;
      if (el.q) el.q.disabled = false;
      drawReport();
      el.report.hidden = false;
      el.run.hidden = true;
      drawFc();
      goTo(el.report, true);
    }

    // what a visitor can do about the reasons rows were set aside (plain steps, no figures)
    function gateSteps(r) {
      var reasons = r.cleaning.quarantine_reasons.slice().sort(function (x, y) { return y.count - x.count; });
      var lines = /^source_line,/.test(r.downloads.quarantine_csv);
      var steps = [], add = function (t) { if (steps.indexOf(t) < 0) steps.push(t); };
      reasons.forEach(function (q) {
        var t = q.reason;
        if (/could be day\/month or month\/day|no known date format/.test(t)) add('Save the dates as YYYY-MM-DD (in Excel: select the column, Format Cells, Custom, then type yyyy-mm-dd). Written with slashes, many dates can be read as day/month or as month/day, and the engine will not guess.');
        else if (/is not a number/.test(t)) add('In a column of amounts, write plain numbers only: a note such as TBD or plus HST belongs in a column of its own. If the column is a label (a unit, suite or apartment) that is mostly digits, the engine reads it as a number: put the same letter in front of every value, or leave the column out.');
        else if (/duplicate/.test(t)) add('Remove the repeated rows at the source; the set-aside download lists each one' + (lines ? ' with its line in your file.' : '.'));
        else if (/in the future/.test(t)) add('Check dates later than today: the engine sets them aside as typing mistakes.');
        else if (/outside the possible range/.test(t)) add('Check values that cannot be real, such as a negative amount.');
      });
      add('Open the set-aside download: each row carries its reason' + (lines ? ' and its line in your file' : '') + '.');
      add('Save the file as CSV and run it here again.');
      return steps;
    }
    function gateCard(r) {
      var c = r.cleaning, top = c.quarantine_reasons.slice().sort(function (x, y) { return y.count - x.count; })[0];
      var one = top && top.count * 2 >= c.rows_quarantined;
      return '<article class="tr-card tr-gate" id="try-gate"><h3>' + (one ? 'Most of the set-aside rows share one cause' : 'Why the business analysis did not run') + '</h3>' +
        '<p>The engine set aside ' + num(c.rows_quarantined, 0) + ' of ' + plural(c.rows_in, 'row', 'rows') + ', too many to trust the cleaning, so it stopped before the business analysis, the forecast and the story. ' +
        (top ? 'The largest reason, for ' + plural(top.count, 'row', 'rows') + ': <b>' + esc(top.reason) + '</b>.' : '') + '</p>' +
        '<p><b>What you can do</b></p><ol>' + gateSteps(r).map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ol>' +
        '<p class="note">The cleaning, the data-health check and the data-quality findings below still ran.</p></article>';
    }
    function seriesName(r) {
      var f = r.findings.filter(function (x) { return /^forecast\.[^.]+\.(next|history_months)$/.test(x.id); })[0];
      return f ? f.id.split('.')[1].replace(/_/g, ' ') : '';
    }

    // pieces both report layouts show (v1 and the v2 analyst view)
    function cleaningHtml(r) {
      var c = r.cleaning, h = '';
      var dupFinding = r.findings.some(function (f) { return /duplicate/i.test(f.claim); }), dupReason = c.quarantine_reasons.some(function (q) { return /duplicate/i.test(q.reason); });
      h += '<div class="tr-two">';
      h += '<article class="tr-card tr-fixes"><h3>What the cleaning did</h3>' + (c.fixes.length
        ? '<p class="note">Each row is a stated rule and the cells it changed. A row that starts “Read as” counts every text value converted to a number or a date, not only the ones that needed a repair.</p>' + tbl('Fixes by rule', [{ t: 'Rule' }, { t: 'Column' }, { t: 'Cells or rows', num: true }, { t: 'What it did' }],
          c.fixes.map(function (f) { return ['<code>' + esc(f.rule) + '</code>', f.column === null ? '<span class="muted">across the file</span>' : '<code>' + esc(f.column) + '</code>', num(f.count, 0), esc(f.what)]; }), 'dt-fix')
        : '<p>No rule had anything to fix.</p>') + '</article>';
      h += '<article class="tr-card tr-quar"><h3>What was set aside, and why</h3>' + (c.rows_quarantined
        ? '<p>' + plural(c.rows_quarantined, 'row was', 'rows were') + ' set aside, out of ' + num(c.rows_in, 0) + '. They are not deleted: the set-aside download holds every one, so you can fix them at the source.</p>' +
          tbl('Set-aside rows by reason', [{ t: 'Reason' }, { t: 'Rows', num: true }], c.quarantine_reasons.map(function (q) { return [esc(q.reason), num(q.count, 0)]; })) +
          (dupFinding && dupReason ? '<p class="note">The duplicate count in the findings and the one here can differ: the finding counts rows repeated exactly as delivered; this table counts what the duplicate rule set aside after the other fixes, which can join rows that differed only in spaces or capitals, and skips rows already set aside for another reason.</p>' : '')
        : '<p>No row had to be set aside.</p>') + '</article></div>';
      return h;
    }
    function privRolesHtml(r, gated) {
      var h = '';
      var ro = r.roles, exKeys = Object.keys(ro.excluded);
      var priv = '<article class="tr-card tr-priv"><h3>Personal data</h3>' + (r.privacy.flagged.length
        ? tbl('Personal-data decisions', [{ t: 'Column' }, { t: 'Why it was flagged' }, { t: 'Decision' }], r.privacy.flagged.map(function (f) {
          return ['<code>' + esc(f.column) + '</code>', esc(f.kind), '<span class="pd-dec pd-' + esc(f.decision) + '">' + esc(f.decision) + '</span>'];
        })) + '<p class="note">Withhold: left out of the business analysis, the downloads and anything sent to an AI; the data-health findings still name the column and count its blanks and spellings, never showing a value. Code: each value replaced by a code in the downloads; still left out of the analysis. Keep: used as it is.</p>'
        : '<p>No column was flagged as personal data.</p>') +
        '<p class="note">The scan reads column names and the shape of values, so names under a neutral heading can be missed: look over the story and the downloads before you share them.</p></article>';
      if (gated) h += priv;
      else {
        h += '<div class="tr-two">' + priv;
        h += '<article class="tr-card tr-roles"><h3>How the engine read your columns</h3><dl class="tr-dl-roles">' +
          '<dt>Date</dt><dd>' + (ro.date ? '<code>' + esc(ro.date) + '</code>' : '<span class="muted">none found</span>') + '</dd>' +
          '<dt>Measures</dt><dd>' + (ro.measures.length ? ro.measures.map(function (m) { return '<code>' + esc(m) + '</code>'; }).join(' ') : '<span class="muted">none</span>') + '</dd>' +
          '<dt>Groupings</dt><dd>' + (ro.dimensions.length ? ro.dimensions.map(function (m) { return '<code>' + esc(m) + '</code>'; }).join(' ') : '<span class="muted">none</span>') + '</dd>' +
          (exKeys.length ? '<dt>Left out</dt><dd><ul>' + exKeys.map(function (k) { return '<li><code>' + esc(k) + '</code>: ' + esc(ro.excluded[k]) + '</li>'; }).join('') + '</ul></dd>' : '') +
          '</dl></article></div>';
      }
      return h;
    }
    function tailHtml(r) {
      var h = '';
      var hasLine = /^source_line,/.test(r.downloads.clean_csv) || /^source_line,/.test(r.downloads.quarantine_csv);
      var held = r.privacy.flagged.filter(function (f) { return f.decision === 'withhold'; }).map(function (f) { return f.column; });
      h += '<article class="tr-card tr-dl"><h3>Take it with you</h3><div class="tr-actions">' +
        '<button type="button" class="btn btn-ghost" data-dl="clean_csv">Cleaned CSV</button>' +
        '<button type="button" class="btn btn-ghost" data-dl="quarantine_csv">Set-aside rows CSV</button>' +
        '<button type="button" class="btn btn-ghost" data-dl="ledger_json">Evidence ledger (JSON)</button>' +
        '<button type="button" class="btn btn-ghost" data-act="print">Save as PDF</button>' +
        '<button type="button" class="btn btn-ghost" data-act="again">Try another file</button></div>' +
        '<p class="note">The files are made in your browser from the engine\'s output.' +
        (hasLine ? ' Each row starts with source_line, its line in your file, so you can find it there.' : '') +
        (held.length ? ' Withheld columns (' + held.map(esc).join(', ') + ') are left out of both CSV files.' : '') +
        ' The evidence ledger is the engine\'s own record of the facts behind the findings and how each was worked out.</p></article>';
      var email = CFG.contact_email, cta;
      if (email) {
        cta = '<a class="btn btn-primary" data-email="' + esc(email) + '" href="mailto:' + esc(email) + '?subject=' + encodeURIComponent('NorthLedger on my real data') + '&amp;body=' +
          encodeURIComponent('I tried the demo on the site. My data lives in:\nWhat I want to know:\nRough row count:\n') + '">Want this on your real data? Email me</a>';
      } else {
        cta = '<a class="btn btn-primary" href="#book">Want this on your real data? Get in touch</a>';
      }
      h += '<aside class="tr-card tr-cta"><div><p class="tr-cta-t">This ran on one file, in one browser tab.</p><p>The Data Health Audit runs the same engine on your real systems and I walk you through what it finds.</p></div>' +
        '<div class="tr-cta-go">' + cta + '<a class="tr-cta-more" href="#services">See the offers</a></div></aside>';
      return h;
    }
    // the file line, the engine line and the time by stage (both layouts); v1 leads with the headline
    function headHtml(r, withHeadline, compact) {
      var total = r.timings.reduce(function (a, t) { return a + t.seconds; }, 0);
      if (compact) {
        // report v2: one line for the file, the rest folded, so the bottom line is on a phone's first screen
        // (fixer round, 24 Sep 2026: at 390 px the first screen held only the file's metadata)
        return '<article class="tr-card tr-head tr-head-compact"><p class="tr-meta tr-meta-1"><span class="kicker">Your report</span><span><b>' + esc(r.input.name) + '</b></span><span>' + num(r.input.rows, 0) + ' rows × ' + num(r.input.columns, 0) + ' columns</span><span>Nothing was uploaded</span></p>' +
          (S.objective ? '<p class="tr-q"><span class="muted">Your question, used only if you choose AI summaries below:</span> ' + esc(S.objective) + '</p>' : '') +
          '<details class="more tr-times"><summary>File, engine and time by stage</summary>' +
          '<p class="tr-meta"><span>' + bytes(r.input.bytes) + '</span><span>sha256 <code title="' + esc(r.input.sha256) + '">' + esc(r.input.sha256.slice(0, 12)) + '</code></span></p>' +
          '<p class="tr-meta"><span>Engine <code>' + esc(r.engine.snapshot) + '</code> ' + esc(r.engine.version) + '</span><span>' + secs(total) + ' of engine time, in your browser</span></p>' +
          tbl('Engine time by stage', [{ t: 'Stage' }, { t: 'Seconds', num: true }],
            r.timings.map(function (t) { return [esc(STAGE_LABEL[t.stage] || t.stage), esc(Number(t.seconds).toFixed(2))]; })) + '</details></article>';
      }
      return '<article class="tr-card tr-head"><p class="kicker">Your report</p>' + (withHeadline ? '<h3 class="tr-headline">' + esc(r.story.headline) + '</h3>' : '') +
        (S.objective ? '<p class="tr-q"><span class="muted">Your question, used only if you choose AI summaries below:</span> ' + esc(S.objective) + '</p>' : '') +
        '<p class="tr-meta"><span><b>' + esc(r.input.name) + '</b></span><span>' + num(r.input.rows, 0) + ' rows × ' + num(r.input.columns, 0) + ' columns</span><span>' + bytes(r.input.bytes) + '</span>' +
        '<span>sha256 <code title="' + esc(r.input.sha256) + '">' + esc(r.input.sha256.slice(0, 12)) + '</code></span></p>' +
        '<p class="tr-meta"><span>Engine <code>' + esc(r.engine.snapshot) + '</code> ' + esc(r.engine.version) + '</span><span>' + secs(total) + ' of engine time, in your browser</span><span>Nothing was uploaded</span></p>' +
        '<details class="more tr-times"><summary>Time by stage</summary>' + tbl('Engine time by stage', [{ t: 'Stage' }, { t: 'Seconds', num: true }],
          r.timings.map(function (t) { return [esc(STAGE_LABEL[t.stage] || t.stage), esc(Number(t.seconds).toFixed(2))]; })) + '</details></article>';
    }
    // report contract v2 (engine/CONTRACT-v2.md): the manager and analyst views (src/js/52-nl2-report.js)
    function drawReport2(r) {
      var gated = T.gateTripped(r);
      el.report.innerHTML = headHtml(r, false, !gated) + (gated ? gateCard(r) : '') +
        window.NL2.html(r, { story: '<article class="tr-card tr-story" id="try-story"></article>', rolesPriv: privRolesHtml(r, gated), cleaning: cleaningHtml(r) }) + tailHtml(r);
      drawStory();
      window.NL2.mount(el.report.querySelector('.nl2'), r);
    }
    function drawReport() {
      var r = S.report, c = r.cleaning, F = r.forecast, gated = T.gateTripped(r);
      // a v2 report whose blocks the page can read gets the two views; otherwise the checked v1 report,
      // saying why the confidence details are not there
      var v2bad = window.NL2 && window.NL2.isV2(r) ? window.NL2.check(r) : null;
      if (v2bad && !v2bad.length) return drawReport2(r);
      var fallback = '';
      if (v2bad) fallback = 'The confidence details could not be shown: the report\'s v2 blocks did not have the shape this page reads (' + v2bad.slice(0, 4).join('; ') + '). Below is the checked v1 report.';
      else if (r.contract_version === 2) fallback = 'The confidence details are not in this report. ' + (r.limitations || []).map(function (l) { return l && l.text; }).filter(Boolean).join(' ');
      var counts = {}; VERDICTS.forEach(function (v) { counts[v] = 0; });
      r.findings.forEach(function (f) { counts[f.verdict] += 1; });
      var lost = c.rows_in - c.rows_clean - c.rows_quarantined;
      var h = fallback ? '<p class="tr-card nl2-fallback" role="status">' + esc(fallback) + '</p>' : '';
      h += headHtml(r, true);
      if (gated) h += gateCard(r);
      // key numbers; from contract v2 on, health.score is the weakest dimension, not the mean
      var sc = r.health.score, v2h = r.contract_version === 2;
      h += '<div class="tr-kpis">' +
        '<div class="tr-kpi tr-health"><span class="k-lab">' + (v2h ? 'Data health, weakest dimension' + (r.health.weakest ? ' (' + esc(r.health.weakest) + ')' : '') : 'Data health, as delivered') + '</span>' +
          (sc === null ? '<span class="k-val kv-sm">Not scored</span><span class="k-sub">the engine could not score this file</span>'
            : '<span class="k-val">' + num(sc, 1) + '<small>/100</small></span><span class="tr-meter" aria-hidden="true"><i style="width:' + Math.max(0, Math.min(100, sc)).toFixed(1) + '%"></i></span>') +
          (v2h && typeof r.health.score_mean === 'number' ? '<span class="k-sub">the engine\'s Data Health Score, the mean of the five: ' + num(r.health.score_mean, 1) + '</span>' : '') +
          (r.health.issues.length ? '<details class="tr-issues"><summary>' + plural(r.health.issues.length, 'issue', 'issues') + ' found</summary>' + list(r.health.issues) + '</details>' : '') + '</div>' +
        '<div class="tr-kpi"><span class="k-lab">Rows kept after cleaning</span><span class="k-val">' + num(c.rows_clean, 0) + '</span><span class="k-sub">of ' + num(c.rows_in, 0) + ' in; ' + num(c.rows_quarantined, 0) + ' set aside' +
          (lost === 0 ? ', none lost' : lost > 0 ? ', ' + num(lost, 0) + ' removed by the fixes' : '') + '</span></div>' +
        '<div class="tr-kpi"><span class="k-lab">Findings, each checked</span><span class="k-val">' + num(r.findings.length, 0) + '</span><span class="k-sub">' +
          VERDICTS.map(function (v) { return num(counts[v], 0) + ' ' + v; }).join(', ') + '</span></div>' +
        '<div class="tr-kpi"><span class="k-lab">Forecast</span>' + (F.available ? '<span class="k-val kv-sm">' + esc(F.verdict || 'made') + '</span><span class="k-sub">' + esc(F.champion ? 'won by ' + F.champion : F.reason) + '</span>'
          : '<span class="k-val kv-sm">None</span><span class="k-sub">why: see the forecast, below</span>') + '</div></div>';
      h += cleaningHtml(r);
      h += privRolesHtml(r, gated);
      // findings
      h += '<article class="tr-card tr-find"><div class="tr-find-head"><h3>Findings, each checked</h3>' +
        '<div class="seg" role="group" aria-label="Show findings"><button type="button" class="sg" data-verdict="" aria-pressed="true">All ' + num(r.findings.length, 0) + '</button>' +
        VERDICTS.filter(function (v) { return counts[v]; }).map(function (v) { return '<button type="button" class="sg" data-verdict="' + v + '" aria-pressed="false">' + v + ' ' + num(counts[v], 0) + '</button>'; }).join('') + '</div></div>' +
        '<p class="note">RECOMMEND: the evidence cleared the engine\'s gate, so it is a basis for action. WATCH: measured, but not yet strong enough to act on. INSUFFICIENT: too little data to judge. The engine\'s reason sits under each badge. Within each verdict, findings about the business come first, then the forecast, then data quality.</p>' +
        '<details class="more tr-howto"><summary>How to read the terms in a finding</summary><dl>' +
          '<dt>p</dt><dd>How often a gap this large would turn up by luck if nothing had really changed. The smaller it is, the less likely the finding is chance.</dd>' +
          '<dt>Effect size</dt><dd>How big the change is next to the usual ups and downs in the data. Near zero means small, even when it is real.</dd>' +
          '<dt>Basis</dt><dd>The count or amount the finding rests on.</dd>' +
          '<dt>Support</dt><dd>How many rows were read to reach it.</dd>' +
          '<dt>MASE</dt><dd>The forecast\'s typical miss divided by the typical miss of simply repeating the past. Below one beats that simple guess.</dd></dl></details>' +
        (r.findings.length ? '<ol class="tr-flist">' + r.findings.map(function (f, i) {
          return '<li data-verdict="' + esc(f.verdict) + '"' + (i >= FIRST ? ' hidden' : '') + '><p class="tr-claim">' + esc(f.claim) + '</p>' + badge(f.verdict, f.why) +
            '<span class="tr-kind">' + esc(String(f.kind).replace(/_/g, ' ')) + '</span></li>';
        }).join('') + '</ol>' + (r.findings.length > FIRST ? '<p class="tr-more"><button type="button" class="btn btn-ghost" data-act="more">Show all ' + num(r.findings.length, 0) + ' findings</button></p>' : '')
          : '<p>The engine found nothing it could check in this file.</p>') + '</article>';
      // forecast
      h += '<article class="tr-card tr-fc"><h3>Forecast, backtested</h3>';
      if (F.available && F.series.length) {
        var bt = F.backtest, sname = seriesName(r);
        h += '<p class="tr-fc-verdict">' + (VERDICTS.indexOf(F.verdict) >= 0 ? badge(F.verdict) : (F.verdict ? '<b>' + esc(F.verdict) + '</b>' : '')) + ' ' + esc(F.reason) +
          (F.baseline_won === true ? ' A simple baseline' + (F.champion ? ' (' + esc(F.champion) + ')' : '') + ' won the backtest, so the engine does not claim a cleverer model helps here.' : '') + '</p>' +
          '<figure class="visual" data-table="1"><figcaption>' + (sname ? 'The series: ' + esc(sname) + ', month by month (the left axis), then the engine\'s forecast' : 'Your monthly series and the engine\'s forecast') + '</figcaption><div class="viz" id="try-fc-viz"></div></figure>' +
          '<dl class="tr-bt"><div><dt>Average miss (MAPE)</dt><dd>' + pctOf(bt.mape) + '</dd></div><div><dt>Scaled error (MASE, lower is better)</dt><dd>' + num(bt.mase, 2) + '</dd></div>' +
          '<div><dt>Months inside the 80% range</dt><dd>' + share(bt.coverage) + '</dd></div></dl>' +
          '<p class="note">Backtest: the engine hid its latest months, forecast them from the months before, and compared. These are those misses, not a promise.</p>';
      } else {
        h += '<p class="tr-nofc"><b>No forecast for this file:</b> ' + esc(String(F.reason).replace(/[.\s]+$/, '')) + '. Here is what the engine can still tell you: what it fixed, what it set aside, the checked findings' + (gated ? '.' : ' and the story.') + '</p>';
      }
      h += '</article>';
      // story
      h += '<article class="tr-card tr-story" id="try-story"></article>';
      h += tailHtml(r);
      el.report.innerHTML = h;
      drawStory();
    }

    function drawFc() {
      var F = S.report && S.report.forecast;
      if (!F || !F.available || !F.series.length || !document.getElementById('try-fc-viz')) return;
      if (U.visual) U.visual('try-fc-viz', function (W) { return drawForecast(W, F); });
    }

    /* ---- the story (the engine's), and the optional AI summaries under it ---- */
    function storyItem(g, show) {
      if (g.text !== undefined) return '<li>' + esc(show(g.text)) + '</li>';
      return '<li class="tr-grp">' + (g.lead ? '<p class="tr-grp-lead">' + esc(show(g.lead)) + '</p>' : '') + '<ul>' + g.items.map(function (x) { return '<li>' + esc(show(x)) + '</li>'; }).join('') + '</ul>' +
        '<p class="tr-grp-why"><b>Why:</b> ' + esc(show(g.why)) + '</p>' + (g.settle ? '<p class="tr-grp-why"><b>What would settle it:</b> ' + esc(show(g.settle)) + '</p>' : '') + '</li>';
    }
    function storyBody(st, show) {
      show = show || function (x) { return x; };
      var parts = STORY_KEYS.filter(function (k) { return (st[k[0]] || []).length; }).map(function (k) {
        var items = st[k[0]], groups = T.groupLines(items);
        var wide = k[0] === 'cannot_answer' || groups.length > 3 || groups.some(function (g) { return g.items; });
        return '<div class="tr-sb' + (wide ? ' tr-sb-wide' : '') + '" data-key="' + k[0] + '"><h4>' + esc(k[1]) + '</h4><ul>' + groups.map(function (g) { return storyItem(g, show); }).join('') + '</ul></div>';
      });
      return parts.length ? parts.join('') : '<p class="muted">The engine wrote no story sections for this file.</p>';
    }
    function drawStory() {
      var box = document.getElementById('try-story');
      if (!box) return;
      var r = S.report, gated = T.gateTripped(r), h = '<div class="tr-story-head"><h3>The story</h3></div>';
      if (S.aiNote) h += '<p class="tr-ai-fallback" role="status">' + esc(S.aiNote) + '</p>';
      // the engine's story is the record and always stays; AI summaries are shown under it, labelled
      h += '<p class="tr-ai-label tr-engine-label">Written by the engine from its checked facts</p>' + (gated ? '' : '<p class="tr-lead">' + esc(r.story.headline) + '</p>') + '<div class="tr-sgrid">' + storyBody(r.story) + '</div>';
      if (S.ai) {
        h += '<div class="tr-ai-sum" role="region" aria-label="AI summaries">' +
          '<p class="tr-ai-label">AI-reworded summaries (DeepSeek); every figure and grade put in by the engine</p>' +
          '<p class="tr-ai-limit">The model wrote only the words. Each figure, grade and quoted value below was filled in by this page from the engine\'s findings, and a summary that wrote a number, a grade, a cause, size or confidence wording of its own, used words its finding does not use, or set a figure or a grade beside another finding\'s words, was refused. The check cannot prove that a sentence means what the finding means: the findings and the story above are the record. Grades use the evidence names: CONFIRMED is the engine\'s RECOMMEND, NOT ENOUGH DATA its INSUFFICIENT.</p>' +
          T.SUMMARY_PARTS.map(function (part) {
            // a part that did not pass is named, with the reason in plain words; its text is never shown
            if (S.ai.chk[part] === null) return '<h4>' + esc(TG.REGISTERS[part].title) + '</h4><p class="tr-ai-aside note" role="status" data-aside="' + part + '">' +
              esc(T.partNote(S.ai.chk, part)) + ' The engine\'s findings and story above are the record.</p>';
            return '<h4>' + esc(TG.REGISTERS[part].title) + '</h4><div class="tr-ai-text" data-part="' + part + '">' + T.summaryHtml(S.ai.chk[part], S.ai.body, part, S.aiRed) + '</div>';
          }).join('') + '</div>';
      }
      if (CFG.ai_proxy_url && !S.ai) {
        var red = T.aiRedactions(r), payload = JSON.stringify(T.aiPayload(r, S.objective, S.aiRaw), null, 2), MAXL = 12;
        h += '<div class="tr-ai"><h4>Optional: AI summaries</h4>' +
          '<p>An AI model (DeepSeek), through the site owner\'s proxy, can write a short executive summary and a technical summary of these findings. It receives exactly what “Exactly what is sent” shows, and nothing else: ' +
          (S.objective ? 'your question exactly as you typed it, ' : '') + 'each finding\'s id, claim, verdict and value, and the engine\'s story. Those name your column headings; your rows and your file are not sent. The proxy adds its instructions and a list of markers for the figures in that text.</p>' +
          (red.length ? '<div class="tr-ai-swap"><p>' + (S.aiRaw ? 'You chose to send these as they are:' : 'These are sent as placeholders; the summaries are shown here with them put back, in this browser only:') + '</p><ul>' +
            red.slice(0, MAXL).map(function (x) { return '<li>' + esc(x.label) + ' <code>' + esc(x.value) + '</code>' + (S.aiRaw ? '' : ' as <code>' + esc(x.placeholder) + '</code>') + '</li>'; }).join('') +
            (red.length > MAXL ? '<li>and ' + num(red.length - MAXL, 0) + ' more, listed in the preview</li>' : '') + '</ul>' +
            '<label class="tr-ai-ok"><input type="checkbox" id="try-ai-raw"' + (S.aiRaw ? ' checked' : '') + '> Send them as they are instead</label></div>' : '') +
          '<p class="note">The personal-data scan reads column names and the shape of values, so a name under a neutral heading can be missed: read the preview before you agree. DeepSeek is run from China and handles what it receives under its own privacy policy, published on deepseek.com.</p>' +
          '<details class="more"><summary>Exactly what is sent</summary><pre class="snip" id="try-ai-preview">' + esc(payload) + '</pre></details>' +
          '<label class="tr-ai-ok"><input type="checkbox" id="try-ai-ok"> I agree to send the data above to the AI model</label>' +
          '<div class="tr-ai-go"><button type="button" class="btn btn-ghost" id="try-ai-go" disabled>Write the AI summaries</button><span class="note" id="try-ai-status" role="status"></span></div>' +
          '<p class="note">The model writes only the words: every figure, grade and quoted value is put in by this page from the engine\'s findings. A summary that writes a number, a grade, a cause or confidence wording of its own, uses words its finding does not use, or sets a figure or a grade beside another finding\'s words, is set aside, and this page says which one and why. Each summary is judged on its own: one that passes is shown even when the other is set aside, and the engine\'s story always stays.</p></div>';
      }
      box.innerHTML = h;
      var ok = document.getElementById('try-ai-ok'), go = document.getElementById('try-ai-go'), raw = document.getElementById('try-ai-raw');
      if (ok && go) {
        ok.addEventListener('change', function () { go.disabled = !ok.checked; });
        go.addEventListener('click', function () { if (ok.checked) askAI(go); });
      }
      if (raw) raw.addEventListener('change', function () {
        S.aiRaw = raw.checked;
        drawStory();
        var again = document.getElementById('try-ai-raw');
        if (again) again.focus();
      });
    }
    function askAI(go) {
      var st = document.getElementById('try-ai-status');
      var body = T.aiPayload(S.report, S.objective, S.aiRaw), red = S.aiRaw ? [] : T.aiRedactions(S.report);
      var FALLBACK = ' Only the engine\'s story is shown.';
      go.disabled = true;
      if (st) st.textContent = 'Asking the AI model…';
      var ctrl = window.AbortController ? new AbortController() : null;
      var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, 45000);
      fetch(CFG.ai_proxy_url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        credentials: 'omit', referrerPolicy: 'no-referrer', cache: 'no-store', signal: ctrl ? ctrl.signal : undefined })
        .then(function (res) {
          return res.json().catch(function () { return null; }).then(function (ans) {
            if (res.ok) return ans;
            var err = new Error('the proxy answered ' + res.status);
            err.status = res.status; err.ans = ans && typeof ans === 'object' ? ans : {};
            throw err;
          });
        })
        .then(function (ans) {
          clearTimeout(timer);
          var chk = T.checkSummaries(ans, body);      // the same guard as the proxy, part by part, on what this page sent
          if (!chk.ok) {
            S.aiNote = 'The AI summaries were set aside. ' + (T.partsNote(chk) || 'Nothing usable came back.') + FALLBACK;
            S.ai = null;
          } else { S.ai = { chk: chk, body: body }; S.aiRed = red; S.aiNote = ''; }
          drawStory();
        })
        .catch(function (e) {
          clearTimeout(timer);
          var a = (e && e.ans) || {};
          if (e && e.status === 429) S.aiNote = 'The AI model is busy right now (the proxy answered 429), so no summaries were written. Try again in a minute.' + FALLBACK;
          else if (a.error === 'rejected_wording' && a.rejected && typeof a.rejected === 'object') {
            // the per-part proxy: every part refused (or not written), each named with its reasons
            var none = T.checkSummaries({ rejected: a.rejected, unavailable: a.unavailable }, body);
            S.aiNote = 'The AI summaries were set aside (the proxy answered ' + e.status + '). ' + T.partsNote(none) + FALLBACK;
          } else if (a.error === 'rejected_wording' && (a.part === 'executive' || a.part === 'technical')) {
            S.aiNote = 'The AI summaries were set aside (the proxy answered ' + e.status + '): the ' + a.part + ' summary ' + T.reasonText(Array.isArray(a.reasons) ? a.reasons : []) + '.' + FALLBACK;
          } else S.aiNote = 'The AI summaries are not available right now (' + (e && e.name === 'AbortError' ? 'no answer in time' : (e && e.message || 'no answer')) + ').' + FALLBACK;
          drawStory();
        });
    }

    // "Save as PDF": every finding and every folded detail open while the browser prints, then
    // the page goes back as it was
    function printReport() {
      var undo = [], v2 = el.report.querySelector('.nl2');
      if (v2 && window.NL2) undo.push(window.NL2.preparePrint(v2));
      Array.prototype.forEach.call(el.report.querySelectorAll('.tr-flist li[hidden]'), function (li) { li.hidden = false; undo.push(function () { li.hidden = true; }); });
      Array.prototype.forEach.call(el.report.querySelectorAll('details:not([open])'), function (d) { d.open = true; undo.push(function () { d.open = false; }); });
      var done = false, restore = function () {
        if (done) return;
        done = true;
        undo.forEach(function (f) { f(); });
        document.body.classList.remove('print-try');
        window.removeEventListener('afterprint', restore);
      };
      document.body.classList.add('print-try');
      window.addEventListener('afterprint', restore);
      try { window.print(); } finally { setTimeout(restore, 0); }
    }

    /* ---- report actions (one listener for the report) ---- */
    el.report.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('button');
      if (!b || !S.report) return;
      var dl = b.getAttribute('data-dl'), act = b.getAttribute('data-act'), v = b.getAttribute('data-verdict');
      if (dl) {
        var ext = dl === 'ledger_json' ? '.json' : '.csv', mime = dl === 'ledger_json' ? 'application/json' : 'text/csv';
        var name = stem(S.report.input.name) + (dl === 'clean_csv' ? '-clean' : dl === 'quarantine_csv' ? '-set-aside' : '-evidence-ledger') + ext;
        var url = URL.createObjectURL(new Blob([S.report.downloads[dl]], { type: mime + ';charset=utf-8' }));
        var a = document.createElement('a');
        a.href = url; a.download = name; a.hidden = true;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      } else if (act === 'print') {
        printReport();
      } else if (act === 'again') {
        el.report.hidden = true; clearMsg();
        goTo(el.start);
        el.pick.focus();
      } else if (act === 'more') {
        Array.prototype.forEach.call(el.report.querySelectorAll('.tr-flist li'), function (li) { li.hidden = false; });
        var firstNew = el.report.querySelectorAll('.tr-flist li')[FIRST];
        b.parentNode.remove();
        if (firstNew) { firstNew.setAttribute('tabindex', '-1'); firstNew.focus(); }
      } else if (v !== null && b.closest('.tr-find')) {
        Array.prototype.forEach.call(b.parentNode.querySelectorAll('.sg'), function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        // a verdict shows all its findings; "All" keeps the first few until "Show all" is pressed
        var more = el.report.querySelector('.tr-more');
        Array.prototype.forEach.call(el.report.querySelectorAll('.tr-flist li'), function (li, i) {
          li.hidden = v ? li.getAttribute('data-verdict') !== v : (!!more && i >= FIRST);
        });
        if (more) more.hidden = !!v;
      }
    });
  });
})();
