# The Data Health Audit: the entry offer

**For:** business owners, including rental-building operators, sitting on messy exports and spreadsheets.
**Scope, timing and price:** as listed in the Services section of the site (the owner sets them in
`site.config.json`); fixed scope, a scored report and a short walkthrough call.

## What you get

1. **A profile of your data:** every table and column, with row counts, blanks, duplicates, format
   problems and inconsistent spellings, as counts rather than adjectives.
2. **The defect list:** what is broken, where it came from, and what it does to your numbers (for
   example duplicates inflating a total, or a join that silently drops rows).
3. **The cleaning plan:** ranked by payback: what can be automated, what needs a human decision, and
   the rough effort.
4. **A written verdict and a walkthrough call:** you keep the report whether or not we continue.

## How it is run

- The audit is run by the NorthLedger engine on your data. It calls no AI model: the audit runs
  recorded on the site made no AI model calls. The AI-analyst replays on the site are a separate,
  recorded demo, and they do send query results to a remote model.
- Rows that fail a rule are quarantined with their reason and counted, never silently dropped, so the
  rows in always equal the rows kept plus the rows set aside.
- Each finding is stored as a fact the engine can re-run, and it is gated: RECOMMEND, WATCH or
  INSUFFICIENT evidence. See the RentSafeTO run on the site for a full example, including what the
  engine cannot do yet.

## Where it can lead

| Step | Offer |
|---|---|
| 1 | Data Health Audit (this) |
| 2 | Automated Insights Build: sources connected, cleaned and modelled, then a report, a written brief and a backtested forecast |
| 3 | Insights Retainer: monitoring, monthly summaries, forecast refreshes and one deep-dive a month |

Prices for each are on the site, in the Services section.

## Booking

Book a short call first. Please do not send data in a first message: after the call you get a
private upload link.

---
*Work sample: the [recorded AI-analyst replays](agent-demo.html) and the RentSafeTO report on the
[main page](index.html), each with its receipts.*
