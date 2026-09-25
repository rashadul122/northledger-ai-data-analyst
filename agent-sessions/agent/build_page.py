#!/usr/bin/env python3
"""Assemble the single-file agent demo page: inlines style.css + content.html +
sessions.js (real transcripts, base64 artifacts) -> agent-demo.html"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")

css = open(os.path.join(SRC, "style.css")).read()
content = open(os.path.join(SRC, "content.html")).read()
app = open(os.path.join(SRC, "app.js")).read()
sessions = open(os.path.join(HERE, "sessions.js")).read()

html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NorthLedger Agent - AI data analyst in operation</title>
<meta name="description" content="An AI data analyst with live database access: profiles messy data, runs SQL/Python, forecasts, and writes PDF/Excel/Word reports. Real sessions, unedited.">
<style>
{css}
</style>
</head>
<body>
{content}
<script>
{sessions}
</script>
<script>
{app}
</script>
</body>
</html>
"""
out = os.path.join(HERE, "agent-demo.html")
open(out, "w").write(html)
print(f"built {out} ({len(html)/1e6:.2f} MB)")