# Case study: RentSafeTO Building Health

*Rashadul Islam Roman, NorthLedger Insights, Toronto. Sample work on public data. Every figure
below was filled in by build.py from the data files in data/; none was typed. City data to
{{f rentsafe_meta:meta.data_as_of}} (latest evaluation in the file); built {{d built_at_label}}.*

## The client question

A Toronto operator of older purpose-built rental buildings asks: how do our buildings compare on
the City's RentSafeTO evaluations, which fixes are worth the most points, and can we see a lost
green sign coming?

## What the data is

City of Toronto open data (Open Government Licence, Toronto): the apartment building
evaluations since 2023, the evaluations before 2023, and the building registration file.
{{d nl_rows_in}} rows in the three files. The latest evaluation of each building gives
{{f rentsafe_meta:kpis.buildings|int}} buildings and {{f rentsafe_meta:kpis.units|int}} units.

{{f rentsafe_meta:meta.licence.required_attribution_text}} This is independent analysis; it is
not produced by, affiliated with or endorsed by the City of Toronto or RentSafeTO.

## What the audit found in the data

- The engine audited and cleaned all three files: {{d nl_rows_in}} rows in =
  {{d nl_clean}} clean + {{d nl_quarantined}} quarantined, nothing dropped without a reason.
- The City's own score counts an area that could not be entered as zero points: counting a
  refused item as zero reproduces {{d a_zero_rows_share}} of the {{d a_zero_rows}} affected scores
  exactly; leaving it out reproduces {{d a_missing_rows_share}}.
- The published tier weights reproduce {{d a_exact}} scores exactly.
- One year label reads {{f rentsafe_meta:health.flags.malformed_year_label_values.0}}; the
  completion date sets the year instead, and the label is flagged, not dropped.

## What it means for an operator

- Citywide, {{f rentsafe_meta:kpis.pct_green|pct:1}} of buildings hold a green sign,
  {{f rentsafe_meta:kpis.pct_yellow|pct:1}} yellow and {{f rentsafe_meta:kpis.pct_red|pct:1}} red.
- Book access to every area before evaluation day: a refused area costs full marks.
- The single most valuable fix across the city is {{d a_top_item}}, worth {{d a_top_points}}
  points per building on average.
- Paperwork items score the lowest mark more often ({{d a_paperwork_scored1}}) than high-risk
  items ({{d a_highrisk_scored1}}).
- Same building, next evaluation: the proactive score changed by {{d panel_mean_change}} points on
  average over {{f engine_scorecard:rentsafe.panel_vs_window.pairs|int}} repeat evaluations (pairs of
  evaluations of the same building), and {{d panel_share_improved}} of them scored higher.
  {{r band_split}} Repairs and the second round of a new scoring tool are not yet separated, so this
  is a measurement to watch, not a result to act on.

## What we could not predict

A numpy logistic model of losing the green sign was tested against simple baselines on a later
season. {{r next_eval}}

## What the engine does today, and does not

{{f engine_scorecard:verdict.short}}

## What a client gets

- A scored Data Health Audit with every finding re-runnable.
- The interactive report and scorecard. A private version with the client's own buildings against
  anonymised peers can be built on request; it is not shown publicly, and the public page ranks no landlord.
- A Power BI project export (structure checked; not yet opened in Power BI Desktop).
