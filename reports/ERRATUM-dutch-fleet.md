# Erratum — Dutch fleet and electrification brief (session s5)

`20260922-121451-dutch-fleet-and-electrification-brief.pdf`, section 5, states:

> total any-electric 2,564,854 (23.7% of cars)

**The correct total is 2,565,854** (892,313 BEV + 1,672,577 PHEV + 964 electricity-plus-other-
fuel), from the same query the report was built on. The stated figure matches neither that total
nor BEV + PHEV alone (2,564,890). The percentage is unaffected at the precision printed.

The PDF is left unedited so the original output stays inspectable. The error was found by a
self-audit of all seven sessions, which also confirmed that all 92 recorded queries reproduce
exactly: the queries were right, and the model added the numbers up inside a sentence. See
`../AUDIT.md`. It is the reason the rebuilt engine does not let the model state any number.
