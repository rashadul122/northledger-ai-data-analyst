"""The records share_leak.mjs reads (the chart review of 30 Sep 2026): results_for_ai's charts for the emoji themes case
and the transactions case, written to REVIEW_VIZ_OUT (or a temp folder); prints the folder.
    python tools/fixtures/review-viz/py/share_leak_data.py      # then: node .../share_leak.mjs <folder>
"""
import io, contextlib
from common import *
with contextlib.redirect_stdout(io.StringIO()):
    exec(open(os.path.join(HERE, "emoji_theme.py")).read().split("rep = run(")[0])
    emoji = run(data, "emoji.csv", PLAN)
    exec(open(os.path.join(HERE, "correct_tx.py")).read().split("data = make()")[0])
    tx = run(make(), "tx.csv", dict(PLAN, charts=CH), as_of="2026-03-15")
for name, rep in (("emoji_rfa_charts.json", emoji), ("tx_rfa_charts.json", tx)):
    json.dump(NB.results_for_ai(rep)["charts"], open(os.path.join(OUT, name), "w"), indent=1)
print(OUT)
