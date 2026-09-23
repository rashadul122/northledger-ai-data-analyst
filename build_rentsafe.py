#!/usr/bin/env python3
"""Build the RentSafeTO Building Health Scorecard data for the portfolio site (stage 1, preliminary).

Inputs  : the City of Toronto open-data files in ../data/rentsafe/ (see SOURCES.json there).
Outputs : portfolio-website/data/rentsafe_*.json (scorecard, cube, analyses, health, meta).

Everything is pandas/numpy only. Every number written to the JSON is computed here from the
downloaded files; nothing is typed by hand. Stage 2 will re-run the same code on frames that
NorthLedger has cleaned: pass --input-dir to point at a folder holding the same three CSV names.

Usage:
  build_rentsafe.py                 build all outputs
  build_rentsafe.py --check         build into a temp dir and run the invariant checks only
  build_rentsafe.py --input-dir D   read the three CSVs from D instead of ../data/rentsafe
  build_rentsafe.py --out-dir D     write JSON to D instead of ./data

PROVISIONAL DECISION (for the owner to confirm): an item score of '0' means "could not be evaluated
(obstruction or refusal)" per the dataset notes. In the pillar heatmap and in model features it is
treated as a separate REFUSED flag, not as a score of 0 (it is left out of numerator and denominator
and counted in the refusal columns). Analysis A tests how the City itself treats it; the cube also
carries a "city basis" variant (0 counted as 0) so the page can toggle between the two.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IN = os.path.normpath(os.path.join(HERE, "..", "data", "rentsafe"))
DEFAULT_OUT = os.path.join(HERE, "data")

F_POST = "apartment-building-evaluations-2023-current.csv"
F_PRE = "pre-2023-apartment-building-evaluations.csv"
F_REG = "apartment-building-registration-data.csv"
F_WARDS = "city-wards-data-4326.geojson"

SEED = 20260923

# ---------------------------------------------------------------------------------------------
# Reference tables. Each is copied from a named City page; the URL is carried into the output.
# ---------------------------------------------------------------------------------------------
TIER_SOURCE = ("https://www.toronto.ca/community-people/housing-shelter/rental-housing-rights-information/"
               "housing-property-standards/apartment-building-standards/audits-evaluations/")
# 'Building Evaluation Criteria' tables on TIER_SOURCE: every one of the 50 categories has a
# Prioritization (High Risk = 3%, Moderate Risk = 2%, Cosmetic = 0.5%). Column name -> (City name, tier).
ITEMS = {
    "NUMBERING OF PROPERTY": ("Numbering of Property", "High"),
    "EXTERIOR GROUNDS": ("Exterior Grounds", "Moderate"),
    "FENCING": ("Fencing", "Cosmetic"),
    "RETAINING WALLS": ("Retaining Walls", "High"),
    "CATCH BASINS / STORM DRAINAGE": ("Catch Basins and Storm Drainage", "High"),
    "BUILDING EXTERIOR": ("Building Exterior", "High"),
    "BALCONY GUARDS": ("Balcony Guards", "High"),
    "WINDOWS": ("Windows", "High"),
    "EXT. RECEPTACLE STORAGE AREA": ("Exterior Receptacle Storage Area", "Moderate"),
    "EXTERIOR WALKWAYS": ("Exterior Walkways", "High"),
    "CLOTHING DROP BOXES": ("Clothing Drop Boxes", "Moderate"),
    "ACCESSORY BUILDINGS": ("Accessory Buildings", "Moderate"),
    "INTERCOM": ("Intercom", "High"),
    "EMERGENCY CONTACT SIGN": ("Emergency Contact Sign", "High"),
    "LOBBY - WALLS AND CEILING": ("Lobby - Walls and Ceiling", "Moderate"),
    "LOBBY FLOORS": ("Lobby - Floors", "Moderate"),
    "LAUNDRY ROOM": ("Laundry Room", "Moderate"),
    "INT. RECEPTACLE STORAGE AREA": ("Interior Receptacle Storage Area", "Moderate"),
    "MAIL RECEPTACLES": ("Mail Receptacles", "Moderate"),
    "EXTERIOR DOORS": ("Exterior Doors", "High"),
    "STORAGE AREAS/LOCKERS MAINT.": ("Storage Areas/Lockers - Maintenance", "Moderate"),
    "POOLS": ("Pools", "High"),
    "OTHER AMENITIES": ("Other Amenities", "Moderate"),
    "PARKING AREAS": ("Parking Areas", "Moderate"),
    "ABANDONED EQUIP./DERELICT VEH.": ("Abandoned Equipment and Derelict Vehicles", "Moderate"),
    "GARBAGE/COMPACTOR ROOM": ("Garbage/Compactor Room", "Moderate"),
    "ELEVATOR MAINTENANCE": ("Elevator - Maintenance", "High"),
    "ELEVATOR COSMETICS": ("Elevator - Cosmetics", "Moderate"),
    "INT. HALLWAY - WALLS / CEILING": ("Interior Hallway - Walls and Ceiling", "Cosmetic"),
    "INTERIOR HALLWAY FLOORS": ("Interior Hallway Floors", "Moderate"),
    "INT. LOBBY / HALLWAY LIGHTING": ("Interior Lobby and Hallway Lighting Levels", "Moderate"),
    "COMMON AREA VENTILATION": ("Common Area Ventilation", "High"),
    "ELECTRICAL SERVICES / OUTLETS": ("Electrical Services and Outlets", "High"),
    "CHUTE ROOMS - MAINTENANCE": ("Chute Rooms - Maintenance", "Moderate"),
    "STAIRWELL - WALLS AND CEILING": ("Stairwell - Walls and Ceiling", "Moderate"),
    "STAIRWELL - LANDING AND STEPS": ("Stairwell - Landing and Steps", "High"),
    "STAIRWELL LIGHTING": ("Stairwell - Lighting", "Moderate"),
    "INT. HANDRAIL / GUARD - SAFETY": ("Interior Handrail and Guard - Safety", "High"),
    "INT. HANDRAIL / GUARD - MAINT.": ("Interior Handrail and Guard - Maintenance", "Cosmetic"),
    "GRAFFITI": ("Graffiti", "Cosmetic"),
    "BUILDING CLEANLINESS": ("Building Cleanliness", "Cosmetic"),
    "COMMON AREA PESTS": ("Common Area Pests", "High"),
    "TENANT NOTIFICATION BOARD": ("Tenant Notification Board", "Moderate"),
    "PEST CONTROL LOG": ("Pest Control Log", "Cosmetic"),
    "MAINTENANCE LOG": ("Maintenance Log", "Cosmetic"),
    "CLEANING LOG": ("Cleaning Log", "Cosmetic"),
    "VITAL SERVICE PLAN": ("Vital Service Plan", "Moderate"),
    "ELECTRICAL SAFETY PLAN": ("Electrical Safety Plan", "Moderate"),
    "STATE OF GOOD REPAIR PLAN": ("State of Good Repair Plan (Capital Plan)", "Cosmetic"),
    "TENANT SERVICE REQUEST LOG": ("Tenant Service Request Log", "Cosmetic"),
}
TIER_WEIGHT = {"High": 3.0, "Moderate": 2.0, "Cosmetic": 0.5}

# 8 pillars (the plan's grouping; this grouping is ours, not the City's).
PILLARS = [
    ("envelope", "Envelope", ["BUILDING EXTERIOR", "BALCONY GUARDS", "WINDOWS", "RETAINING WALLS",
                              "CATCH BASINS / STORM DRAINAGE"]),
    ("grounds", "Grounds & parking", ["NUMBERING OF PROPERTY", "EXTERIOR GROUNDS", "FENCING", "EXTERIOR WALKWAYS",
                                      "PARKING AREAS", "ABANDONED EQUIP./DERELICT VEH.", "ACCESSORY BUILDINGS",
                                      "CLOTHING DROP BOXES"]),
    ("entry", "Entry & security", ["INTERCOM", "EXTERIOR DOORS", "EMERGENCY CONTACT SIGN", "MAIL RECEPTACLES"]),
    ("systems", "Building systems", ["ELEVATOR MAINTENANCE", "ELEVATOR COSMETICS", "COMMON AREA VENTILATION",
                                     "ELECTRICAL SERVICES / OUTLETS", "INT. LOBBY / HALLWAY LIGHTING"]),
    ("stairs", "Stairs & life safety", ["STAIRWELL - LANDING AND STEPS", "STAIRWELL LIGHTING",
                                        "STAIRWELL - WALLS AND CEILING", "INT. HANDRAIL / GUARD - SAFETY",
                                        "INT. HANDRAIL / GUARD - MAINT."]),
    ("interiors", "Common interiors & amenities", ["LOBBY - WALLS AND CEILING", "LOBBY FLOORS",
                                                   "INT. HALLWAY - WALLS / CEILING", "INTERIOR HALLWAY FLOORS",
                                                   "LAUNDRY ROOM", "STORAGE AREAS/LOCKERS MAINT.", "POOLS",
                                                   "OTHER AMENITIES", "GRAFFITI"]),
    ("waste", "Waste, pests & cleanliness", ["GARBAGE/COMPACTOR ROOM", "CHUTE ROOMS - MAINTENANCE",
                                             "EXT. RECEPTACLE STORAGE AREA", "INT. RECEPTACLE STORAGE AREA",
                                             "BUILDING CLEANLINESS", "COMMON AREA PESTS"]),
    ("paperwork", "Paperwork & tenant communication", ["PEST CONTROL LOG", "MAINTENANCE LOG", "CLEANING LOG",
                                                       "VITAL SERVICE PLAN", "ELECTRICAL SAFETY PLAN",
                                                       "STATE OF GOOD REPAIR PLAN", "TENANT SERVICE REQUEST LOG",
                                                       "TENANT NOTIFICATION BOARD"]),
]
PILLAR_KEYS = [k for k, _, _ in PILLARS]

# Pre-2023 (20 categories, 1-5) -> pillar crosswalk. Approximate: the old categories are broader
# and there was no paperwork category. Used only for descriptive history, never mixed into cells.
PRE_CATS = {
    "ENTRANCE_LOBBY": "interiors", "ENTRANCE_DOORS_WINDOWS": "entry", "SECURITY": "entry",
    "STAIRWELLS": "stairs", "LAUNDRY_ROOMS": "interiors", "INTERNAL_GUARDS_HANDRAILS": "stairs",
    "GARBAGE_CHUTE_ROOMS": "waste", "GARBAGE_BIN_STORAGE_AREA": "waste", "ELEVATORS": "systems",
    "STORAGE_AREAS_LOCKERS": "interiors", "INTERIOR_WALL_CEILING_FLOOR": "interiors",
    "INTERIOR_LIGHTING_LEVELS": "systems", "GRAFFITI": "interiors", "EXTERIOR_CLADDING": "envelope",
    "EXTERIOR_GROUNDS": "grounds", "EXTERIOR_WALKWAYS": "grounds", "BALCONY_GUARDS": "envelope",
    "WATER_PEN_EXT_BLDG_ELEMENTS": "envelope", "PARKING_AREA": "grounds", "OTHER_FACILITIES": "interiors",
}

# Column crosswalk: canonical name <- (post-2023 column, pre-2023 column).
CROSSWALK = [
    ("rsn", "RSN", "RSN"),
    ("year_registered", "YEAR REGISTERED", "YEAR_REGISTERED"),
    ("year_evaluated_label", "YEAR EVALUATED", "YEAR_EVALUATED"),
    ("year_built", "YEAR BUILT", "YEAR_BUILT"),
    ("property_type", "PROPERTY TYPE", "PROPERTY_TYPE"),
    ("ward", "WARD", "WARD"),
    ("ward_name", "WARDNAME", "WARDNAME"),
    ("site_address", "SITE ADDRESS", "SITE_ADDRESS"),
    ("storeys", "CONFIRMED STOREYS", "CONFIRMED_STOREYS"),
    ("units", "CONFIRMED UNITS", "CONFIRMED_UNITS"),
    ("completed_on", "EVALUATION COMPLETED ON", "EVALUATION_COMPLETED_ON"),
    ("score_city", "PROACTIVE BUILDING SCORE", "SCORE"),
    ("areas_evaluated", "NO OF AREAS EVALUATED", "NO_OF_AREAS_EVALUATED"),
    ("grid", "GRID", "GRID"),
    ("latitude", "LATITUDE", "LATITUDE"),
    ("longitude", "LONGITUDE", "LONGITUDE"),
    ("x", "X", "X"),
    ("y", "Y", "Y"),
]
CROSSWALK_NOTES = {
    "score_city": ("post-2023 PROACTIVE BUILDING SCORE (50 weighted items, 1-3) vs pre-2023 SCORE (20 equal "
                   "categories, 1-5). Different instruments: compare only through within-year percentiles."),
    "post_only": ("CURRENT BUILDING EVAL SCORE and CURRENT REACTIVE SCORE exist only post-2023. Reactive "
                  "deductions are a snapshot at refresh time and are non-zero only on each building's latest row."),
    "pre_only": "RESULTS_OF_SCORE (old next-evaluation band) exists only pre-2023.",
}

# Ward -> Community Council (City page), and the GRID first letter observed for it.
COUNCIL_SOURCE = ("https://www.toronto.ca/city-government/data-research-maps/neighbourhoods-communities/"
                  "community-council-area-profiles/")
COUNCILS = {
    "Etobicoke York": ["01", "02", "03", "05", "07"],
    "North York": ["06", "08", "15", "16", "17", "18"],
    "Toronto and East York": ["04", "09", "10", "11", "12", "13", "14", "19"],
    "Scarborough": ["20", "21", "22", "23", "24", "25"],
}
DISTRICT_ORDER = ["Etobicoke York", "North York", "Toronto and East York", "Scarborough"]
WARD_TO_DISTRICT = {w: d for d, ws in COUNCILS.items() for w in ws}

BANDS = [("green", 85, 100), ("yellow", 70, 84), ("red", 0, 69)]
BAND_SOURCE = ("https://www.toronto.ca/community-people/housing-shelter/rental-housing-rights-information/"
               "housing-property-standards/apartment-building-standards/rentsafeto-colour-coded-signs/")


def band_of(score):
    s = np.asarray(score, dtype=float)
    return np.where(s >= 85, "green", np.where(s >= 70, "yellow", "red"))


def half_up(x):
    return np.floor(np.asarray(x, dtype=float) + 0.5)


# Values the page prints at 1 or 2 dp are stored at CELL_DP so they are rounded exactly once, when
# printed. Storing them at 2 dp and printing at 1 dp rounded twice (91.6486 -> 91.65 -> "91.7").
CELL_DP = 6


def r(x, nd=2):
    """Round for JSON; None for NaN."""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return x
    if not np.isfinite(xf):
        return None
    return round(xf, nd)


def fmt_half_up(x, nd=1):
    """Format a number for a sentence, rounded once and half-up (what the browser's toFixed does)."""
    from decimal import Decimal, ROUND_HALF_UP
    return str(Decimal(float(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------
# Load and harmonise
# ---------------------------------------------------------------------------------------------
def load(in_dir):
    post = pd.read_csv(os.path.join(in_dir, F_POST), dtype=str, keep_default_na=False)
    pre = pd.read_csv(os.path.join(in_dir, F_PRE), dtype=str, keep_default_na=False)
    reg = pd.read_csv(os.path.join(in_dir, F_REG), dtype=str, keep_default_na=False)
    return post, pre, reg


def parse_items(frame, cols):
    """Return (values float array with NaN for N/A, refused bool array, whitespace-variant count)."""
    raw = frame[cols]
    ws = int(sum((raw[c] != raw[c].str.strip()).sum() for c in cols))
    s = raw.apply(lambda c: c.str.strip())
    allowed = {"0", "1", "2", "3", "4", "5", "N/A", ""}
    bad = set(np.unique(s.values.ravel())) - allowed
    if bad:
        raise ValueError(f"unexpected item codes: {sorted(bad)[:10]}")
    na = s.isin(["N/A", ""]).values
    vals = s.where(~s.isin(["N/A", ""]), np.nan).astype(float).values
    refused = vals == 0
    return vals, refused, na, ws


def harmonise(post, pre, reg):
    """Build the long evaluation panel (both schemas) plus the post-2023 item matrix.

    Returns dict with: panel (DataFrame), items (post-2023 values), refused, na, health (dict).
    """
    health = {"rows_in": {"post2023": int(len(post)), "pre2023": int(len(pre)), "registration": int(len(reg))}}
    item_cols = list(ITEMS)
    missing = [c for c in item_cols if c not in post.columns]
    if missing:
        raise ValueError(f"post-2023 file lacks item columns: {missing}")
    pre_cols = list(PRE_CATS)

    frames = []
    for schema, df, idx in (("post2023", post, 1), ("pre2023", pre, 2)):
        out = pd.DataFrame({canon: df[cols[idx - 1]].str.strip() for canon, *cols in
                            [(c[0], c[1], c[2]) for c in CROSSWALK]})
        out["schema"] = schema
        out["_id"] = df["_id"].astype(int).values
        out["src_row"] = np.arange(len(df))
        frames.append(out)
    panel = pd.concat(frames, ignore_index=True)
    panel["completed_on"] = pd.to_datetime(panel["completed_on"], errors="coerce")
    for c in ("score_city", "areas_evaluated", "storeys", "units", "year_built", "latitude", "longitude", "x", "y"):
        panel[c] = pd.to_numeric(panel[c], errors="coerce")

    # --- quarantine / flag rules (stage-1 rules; NorthLedger replaces these in stage 2) ---
    q = pd.Series("", index=panel.index)
    created_err = panel["site_address"].str.contains(r"\*\* CREATED IN ERROR \*\*", regex=True)
    q[created_err] = "address marked '** CREATED IN ERROR **'"
    bad_date = panel["completed_on"].isna()
    q[bad_date & (q == "")] = "unparseable completion date"
    bad_score = panel["score_city"].isna()
    q[bad_score & (q == "")] = "missing score"
    dup = panel.duplicated(["schema", "rsn", "completed_on"], keep="first")
    q[dup & (q == "")] = "duplicate RSN + completion date within a file (later copy)"
    panel["quarantine_reason"] = q
    lab = panel["year_evaluated_label"]
    comp_year = panel["completed_on"].dt.year.astype("Int64").astype(str)
    panel["flag_year_label"] = np.where(lab == "", "blank", np.where(~lab.str.fullmatch(r"\d{4}"), "malformed",
                                        np.where(lab != comp_year, "differs from completion year", "")))
    panel["flag_no_latlon"] = panel["latitude"].isna()
    panel["flag_no_latlon_has_xy"] = panel["latitude"].isna() & panel["x"].notna()

    # GRID rule, asserted on every row: characters 2-3 of GRID equal WARD, and the first letter
    # is constant within each Community Council.
    grid_ok = panel["grid"].str.slice(1, 3) == panel["ward"]
    letter = panel["grid"].str.slice(0, 1)
    letters_by_district = {}
    for d in DISTRICT_ORDER:
        m = panel["ward"].isin(COUNCILS[d])
        letters_by_district[d] = sorted(letter[m].unique().tolist())
    health["grid_rule"] = {
        "rows_checked": int(len(panel)),
        "grid_chars_2_3_equal_ward": int(grid_ok.sum()),
        "violations": int((~grid_ok).sum()),
        "grid_first_letter_by_council": letters_by_district,
        "wards_outside_council_table": sorted(set(panel["ward"]) - set(WARD_TO_DISTRICT)),
        "note": ("District comes from the City's ward -> Community Council table, not from the letter. "
                 "The letter is a check: it is a compass code (S = Toronto and East York, E = Scarborough), "
                 "so reading S as Scarborough would be wrong."),
    }
    panel["district"] = panel["ward"].map(WARD_TO_DISTRICT)

    # item matrix, post-2023 rows only (aligned to post src_row)
    vals, refused, na, ws = parse_items(post, item_cols)
    pvals, prefused, pna, pws = parse_items(pre, pre_cols)
    health["whitespace_item_cells_repaired"] = {"post2023": ws, "pre2023": pws}
    health["item_code_counts_post2023"] = {
        "scored_1": int((vals == 1).sum()), "scored_2": int((vals == 2).sum()), "scored_3": int((vals == 3).sum()),
        "refused_or_obstructed_0": int(refused.sum()), "not_applicable": int(na.sum())}
    health["item_code_counts_pre2023"] = {str(k): int((pvals == k).sum()) for k in (1, 2, 3, 4, 5)}
    health["item_code_counts_pre2023"]["not_applicable_or_blank"] = int(pna.sum())
    health["item_code_counts_pre2023"]["zero"] = int(prefused.sum())

    # pre-2023 score reproduction check (sum / (5 x n evaluated), half-up)
    n_pre = np.sum(~np.isnan(pvals), 1)
    pre_rec = half_up(np.nansum(pvals, 1) / (5 * np.where(n_pre == 0, np.nan, n_pre)) * 100)
    pre_score = pd.to_numeric(pre["SCORE"], errors="coerce").values
    health["pre2023_score_rule"] = {
        "rule": "SCORE = round_half_up(sum of category scores / (5 x categories evaluated) x 100)",
        "rows": int(len(pre)), "exact_matches": int(np.sum(pre_rec == pre_score)),
        "mae": r(np.nanmean(np.abs(pre_rec - pre_score)), 3)}
    pre_pillar = {}
    for pk in PILLAR_KEYS:
        cols = [i for i, c in enumerate(pre_cols) if PRE_CATS[c] == pk]
        if not cols:
            continue
        sub = pvals[:, cols]
        cnt = np.sum(~np.isnan(sub), 1)
        pre_pillar[pk] = np.where(cnt > 0, np.nansum(sub, 1) / (5 * np.maximum(cnt, 1)) * 100, np.nan)

    # rows_in = clean + quarantined, per file
    for schema in ("post2023", "pre2023"):
        m = panel["schema"] == schema
        nq = int((panel.loc[m, "quarantine_reason"] != "").sum())
        nc = int(m.sum()) - nq
        assert nc + nq == health["rows_in"][schema]
        health.setdefault("reconciliation", {})[schema] = {"rows_in": int(m.sum()), "clean": nc, "quarantined": nq}
    health["quarantine_reasons"] = {k: int(v) for k, v in
                                    panel.loc[panel.quarantine_reason != "", "quarantine_reason"].value_counts().items()}
    health["flags"] = {
        "year_label_blank": _flagcount(panel, "flag_year_label", "blank"),
        "year_label_malformed": _flagcount(panel, "flag_year_label", "malformed"),
        "year_label_differs_from_completion_year": _flagcount(panel, "flag_year_label", "differs from completion year"),
        "malformed_year_label_values": sorted(panel.loc[panel.flag_year_label == "malformed",
                                                        "year_evaluated_label"].unique().tolist()),
        "no_lat_long": {s: int(panel.loc[panel.schema == s, "flag_no_latlon"].sum()) for s in ("post2023", "pre2023")},
        "no_lat_long_but_has_xy": {s: int(panel.loc[panel.schema == s, "flag_no_latlon_has_xy"].sum())
                                   for s in ("post2023", "pre2023")},
        "non_integer_published_scores": int((panel["score_city"].notna() & (panel["score_city"] != np.round(panel["score_city"]))).sum()),
        "year_label_note": ("Pre-2023 labels that differ from the completion year are mostly cycle labels (a 2017 or "
                            "2018 cycle finished the next calendar year); post-2023 has 29 rows labelled 2025 but "
                            "completed in 2026 and one malformed label. The completion date is used throughout."),
    }
    return {"panel": panel, "vals": vals, "refused": refused, "na": na, "pre_pillar": pre_pillar, "health": health}


def _flagcount(panel, col, val):
    return {s: int(((panel.schema == s) & (panel[col] == val)).sum()) for s in ("post2023", "pre2023")}


# ---------------------------------------------------------------------------------------------
# Building-level scores (post-2023)
# ---------------------------------------------------------------------------------------------
def weights_vector():
    return np.array([TIER_WEIGHT[ITEMS[c][1]] for c in ITEMS])


def city_formula(vals, refused, zero_as="zero", decimals=2):
    """Reconstruct the proactive score with the City's tier weights.

    zero_as='zero'    : a 0 counts as 0 points and its weight stays in the denominator.
    zero_as='missing' : a 0 is dropped from numerator and denominator (like N/A).
    """
    W = weights_vector()
    v = vals.copy()
    if zero_as == "missing":
        v[refused] = np.nan
    m = ~np.isnan(v)
    num = np.sum(np.where(m, W * np.nan_to_num(v) / 3.0, 0.0), 1)
    den = np.sum(np.where(m, W, 0.0), 1)
    raw = num / np.where(den == 0, np.nan, den) * 100
    return raw, half_up(np.round(raw, decimals)), den


def pillar_scores(vals, refused):
    """Per building x pillar: (flag basis %, city basis %, scored weight, refused weight, refused count)."""
    W = weights_vector()
    names = list(ITEMS)
    out = {}
    for pk, _, cols in PILLARS:
        ix = [names.index(c) for c in cols]
        v = vals[:, ix]
        w = W[ix]
        scored = (~np.isnan(v)) & (v > 0)
        ref = refused[:, ix]
        num = np.sum(np.where(scored, w * np.nan_to_num(v) / 3.0, 0.0), 1)
        den_s = np.sum(np.where(scored, w, 0.0), 1)
        den_r = np.sum(np.where(ref, w, 0.0), 1)
        flag = np.where(den_s > 0, num / np.where(den_s == 0, 1, den_s) * 100, np.nan)
        city = np.where(den_s + den_r > 0, num / np.where(den_s + den_r == 0, 1, den_s + den_r) * 100, np.nan)
        out[pk] = {"flag": flag, "city": city, "den_scored": den_s, "den_refused": den_r,
                   "n_refused": ref.sum(1)}
    return out


def latest_panel(H):
    panel = H["panel"]
    post = panel[(panel.schema == "post2023")].copy()
    post["n_refused_items"] = H["refused"][post["src_row"].values].sum(1)
    clean = post[post.quarantine_reason == ""]
    clean = clean.sort_values(["rsn", "completed_on", "_id"])
    latest = clean.groupby("rsn", as_index=False).tail(1).copy()
    H["health"]["latest_per_rsn"] = {
        "key": "RSN", "order": "EVALUATION COMPLETED ON, then _id",
        "rows_before": int(len(clean)), "buildings_after": int(len(latest)),
        "rows_collapsed": int(len(clean) - len(latest)),
        "evaluations_per_building": {str(k): int(v) for k, v in clean.groupby("rsn").size().value_counts().sort_index().items()}}
    return clean, latest


# ---------------------------------------------------------------------------------------------
# Scorecard (heatmap rows) and cube
# ---------------------------------------------------------------------------------------------
def building_table(H, post_raw, latest):
    rows = latest["src_row"].values
    vals, refused = H["vals"][rows], H["refused"][rows]
    ps = pillar_scores(vals, refused)
    cur = pd.to_numeric(post_raw["CURRENT BUILDING EVAL SCORE"].values[rows], errors="coerce")
    rea = pd.to_numeric(post_raw["CURRENT REACTIVE SCORE"].values[rows], errors="coerce")
    b = pd.DataFrame({
        "rsn": latest["rsn"].values, "ward": latest["ward"].values, "ward_name": latest["ward_name"].values,
        "district": latest["district"].values, "property_type": latest["property_type"].values,
        "year_built": latest["year_built"].values, "storeys": latest["storeys"].values,
        "units": latest["units"].values, "completed_on": latest["completed_on"].values,
        "score_proactive": latest["score_city"].values, "score_current": cur, "reactive": rea,
        "n_refused_items": refused.sum(1),
    })
    b["band"] = band_of(b["score_current"])
    for pk in PILLAR_KEYS:
        b[f"p_{pk}"] = ps[pk]["flag"]
        b[f"pc_{pk}"] = ps[pk]["city"]
    b["decade_built"] = np.where(b["year_built"].notna(), (b["year_built"] // 10 * 10).astype("Int64").astype(str) + "s",
                                 "unknown")
    b.loc[b["year_built"] < 1900, "decade_built"] = "pre-1900"
    b["storeys_band"] = pd.cut(b["storeys"], [0, 4, 9, 19, 1000], labels=["3-4", "5-9", "10-19", "20+"]).astype(str)
    b["eval_year"] = pd.to_datetime(b["completed_on"]).dt.year.astype(int)
    return b


def agg_rows(g):
    """Summary stats for one group of latest buildings."""
    units = g["units"].fillna(0).values
    out = {"n_buildings": int(len(g)), "units": int(units.sum())}
    sc = g["score_current"].values
    out["overall_mean"] = r(np.mean(sc), CELL_DP)
    out["overall_unit_weighted"] = r(np.sum(sc * units) / units.sum(), CELL_DP) if units.sum() > 0 else None
    for band in ("green", "yellow", "red"):
        out[f"pct_{band}"] = r(100 * np.mean(g["band"].values == band), CELL_DP)
    out["refusal_evals_per_100"] = r(100 * np.mean(g["n_refused_items"].values > 0), CELL_DP)
    out["refused_items_per_100_evals"] = r(100 * np.mean(g["n_refused_items"].values), CELL_DP)
    for pk in PILLAR_KEYS:
        v = g[f"p_{pk}"].values
        ok = ~np.isnan(v)
        out[f"{pk}_mean"] = r(np.mean(v[ok]), CELL_DP) if ok.any() else None
        out[f"{pk}_unit_weighted"] = (r(np.sum(v[ok] * units[ok]) / units[ok].sum(), CELL_DP)
                                      if ok.any() and units[ok].sum() > 0 else None)
        out[f"{pk}_n"] = int(ok.sum())
        vc = g[f"pc_{pk}"].values
        okc = ~np.isnan(vc)
        out[f"{pk}_mean_city_basis"] = r(np.mean(vc[okc]), CELL_DP) if okc.any() else None
    return out


def scorecard(b, meta):
    cols = ["overall"] + PILLAR_KEYS
    wards = []
    for w, g in b.groupby("ward"):
        row = {"ward": w, "ward_name": g["ward_name"].iloc[0], "district": g["district"].iloc[0]}
        row.update(agg_rows(g))
        row["low_n"] = row["n_buildings"] < 5
        wards.append(row)
    # rank by overall_mean (simple mean of buildings' current City score, stored at CELL_DP so two wards
    # that differ only past the second decimal are not tied), 1 = best, ties share the lower rank
    ov = np.array([w["overall_mean"] for w in wards])
    order = (-ov).argsort(kind="stable")
    for rank_pos, i in enumerate(order):
        wards[i]["rank"] = int(1 + np.sum(ov > ov[i]))
    for pk in PILLAR_KEYS:
        pv = np.array([w[f"{pk}_mean"] if w[f"{pk}_mean"] is not None else -1 for w in wards])
        for w_, v in zip(wards, pv):
            w_[f"{pk}_rank"] = int(1 + np.sum(pv > v))

    def group_row(label, level, members, g):
        row = {"label": label, "level": level}
        # default: simple average of member ward cells (TTDI-style regional average)
        for c in ["overall_mean", "overall_unit_weighted", "pct_green", "pct_yellow", "pct_red",
                  "refusal_evals_per_100", "refused_items_per_100_evals"] + \
                 [f"{pk}_mean" for pk in PILLAR_KEYS] + [f"{pk}_unit_weighted" for pk in PILLAR_KEYS] + \
                 [f"{pk}_mean_city_basis" for pk in PILLAR_KEYS]:
            vals = [m[c] for m in members if m[c] is not None]
            row[f"{c}__avg_of_wards"] = r(np.mean(vals), CELL_DP) if vals else None
        pooled = agg_rows(g)
        for k, v in pooled.items():
            row[f"{k}__pooled_buildings"] = v
        row["n_wards"] = len(members)
        row["n_buildings"] = pooled["n_buildings"]
        row["units"] = pooled["units"]
        return row

    districts = []
    for d in DISTRICT_ORDER:
        members = [w for w in wards if w["district"] == d]
        districts.append(group_row(d, "district", members, b[b.district == d]))
    city = group_row("City of Toronto", "city", wards, b)

    return {
        "title": "RentSafeTO Building Health Scorecard: 25 wards x 8 pillars",
        "design_credit": "Layout inspired by the WEF TTDI table; no TTDI scores are reproduced.",
        "as_of": meta["data_as_of"], "built_at": meta["built_at"],
        "columns": [{"key": "overall", "label": "Overall (City score)"}] +
                   [{"key": k, "label": lbl, "items": [ITEMS[c][0] for c in cols_], "item_columns": cols_,
                     "weight_total": float(sum(TIER_WEIGHT[ITEMS[c][1]] for c in cols_))}
                    for k, lbl, cols_ in PILLARS],
        "bands": [{"band": b_, "min": lo, "max": hi} for b_, lo, hi in BANDS],
        "bands_source": BAND_SOURCE,
        "wards": sorted(wards, key=lambda w: w["ward"]),
        "districts": districts,
        "city": city,
        "method": {
            "unit": ("Each building's LATEST post-2023 evaluation (the RSN panel is collapsed to the latest row "
                     "by completion date)."),
            "overall": ("Overall = the City's CURRENT BUILDING EVAL SCORE (proactive score plus current reactive "
                        "deductions), which is what sets the lobby sign colour. Not recomputed."),
            "pillar": ("Pillar % for one building = sum(w_i x s_i / 3) / sum(w_i) x 100 over the pillar's items "
                       "scored 1-3, with the City's published tier weights (High 3, Moderate 2, Cosmetic 0.5). "
                       "N/A items are left out. Provisional choice, to be confirmed: items scored 0 (an area "
                       "refused or blocked) are also left out and counted in the refusal columns instead; the "
                       "*_mean_city_basis fields count them as 0 points, which is what the City's own score does "
                       "(see analysis A)."),
            "ward_cell": ("Ward cell (default) = simple mean over the ward's buildings, each building counted "
                          "once (*_mean). The unit-weighted mean (*_unit_weighted, weights = CONFIRMED UNITS) "
                          "and the building count (*_n) are also given."),
            "district_and_city_rows": ("District and City rows: the default (*__avg_of_wards) is the SIMPLE "
                                       "AVERAGE OF THE MEMBER WARD CELLS, each ward counted once, like a "
                                       "regional-average row in the TTDI table. The pooled mean over all the "
                                       "buildings in the district or city (*__pooled_buildings) is also "
                                       "given; the two differ because wards hold different numbers of buildings."),
            "bands": "Colour bands are the City's lobby-sign bands: 85-100 green, 70-84 yellow, 0-69 red.",
            "pct_band": "pct_green/yellow/red = share of the ward's buildings whose latest City score is in the band.",
            "refusals": ("refusal_evals_per_100 = latest evaluations with at least one item scored 0 per 100 "
                         "evaluations; refused_items_per_100_evals = items scored 0 per 100 evaluations."),
            "rank": ("rank = 1 + number of wards with a higher overall_mean (ties share a rank); *_rank the same "
                     "per pillar on *_mean."),
            "low_n": "low_n is true when a ward has fewer than 5 buildings.",
        },
        "limits": [
            "The 8-pillar grouping is ours; the City publishes items and tiers, not pillars.",
            "Scores are the City's bylaw-evaluation scores, not a safety rating; a red sign does not mean unsafe.",
            "Latest evaluations span several years (2023-2026), so wards are compared across slightly different dates.",
            "Provisional choice, to be confirmed: a refused area is kept as a separate flag here; the City's own "
            "score counts it as zero points.",
            "Public view is ward/district level only; no landlord, firm or address is ranked.",
        ],
    }


def cube(b):
    dims = ["ward", "property_type", "decade_built", "storeys_band", "eval_year"]
    g = b.groupby(dims, dropna=False)
    recs = []
    for key, grp in g:
        units = grp["units"].fillna(0).values
        rec = dict(zip(dims, [str(k) for k in key]))
        rec.update({"n": int(len(grp)), "units": int(units.sum()),
                    "score_sum": r(grp["score_current"].sum(), 4),
                    "score_usum": r(float(np.sum(grp["score_current"].values * units)), 4),
                    "proactive_sum": r(grp["score_proactive"].sum(), 4),
                    "green": int((grp.band == "green").sum()), "yellow": int((grp.band == "yellow").sum()),
                    "red": int((grp.band == "red").sum()),
                    "refused_evals": int((grp.n_refused_items > 0).sum()),
                    "refused_items": int(grp.n_refused_items.sum())})
        for pk in PILLAR_KEYS:
            v = grp[f"p_{pk}"].values
            ok = ~np.isnan(v)
            vc = grp[f"pc_{pk}"].values
            okc = ~np.isnan(vc)
            rec[f"{pk}_sum"] = r(v[ok].sum(), 4)
            rec[f"{pk}_n"] = int(ok.sum())
            rec[f"{pk}_usum"] = r(float(np.sum(v[ok] * units[ok])), 4)
            rec[f"{pk}_units"] = int(units[ok].sum())
            rec[f"{pk}_csum"] = r(vc[okc].sum(), 4)
            rec[f"{pk}_cn"] = int(okc.sum())
        recs.append(rec)
    fields = list(recs[0].keys())
    return {
        "grain": "latest evaluation per building, grouped by " + " x ".join(dims),
        "fields": fields,
        "rows": [[rec[f] for f in fields] for rec in recs],
        "n_cells": len(recs),
        "how_to_use": ("Filter rows, then mean = sum / n, unit-weighted mean = usum / units, per pillar "
                       "{p}_sum/{p}_n (0 as refused flag) or {p}_csum/{p}_cn (City basis, 0 counted as 0). "
                       "score_* is the City's current score; % green = green / n."),
        "district_of_ward": WARD_TO_DISTRICT,
    }


# ---------------------------------------------------------------------------------------------
# Analysis A: points per fix / weight reconstruction
# ---------------------------------------------------------------------------------------------
def r2(y, p):
    ss_res = np.sum((y - p) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    return 1 - ss_res / ss_tot


def analysis_a(H, clean, b, latest):
    rows = clean["src_row"].values
    vals, refused = H["vals"][rows], H["refused"][rows]
    y = clean["score_city"].values.astype(float)
    has0 = refused.any(1)
    res = {"question": ("Can the City's published proactive score be rebuilt from the 50 item scores and the "
                        "published tier weights, and how does the City treat an item scored 0 (refused or "
                        "obstructed)? Which fixes are worth the most points?"),
           "data": f"{len(rows)} post-2023 evaluations (all clean rows, not only the latest)."}
    tests = []
    for zero_as in ("zero", "missing"):
        raw, rounded, den = city_formula(vals, refused, zero_as=zero_as)
        err = np.abs(rounded - y)
        tests.append({
            "hypothesis": "0 counts as 0 points (weight stays in denominator)" if zero_as == "zero"
            else "0 is dropped like N/A",
            "zero_as": zero_as,
            "rows": int(len(y)), "exact_matches": int(np.sum(err == 0)), "exact_share": r(np.mean(err == 0), 4),
            "mae_points": r(err.mean(), 4), "r2": r(r2(y, rounded), 4),
            "rows_with_a_0": int(has0.sum()),
            "rows_with_a_0_exact_share": r(np.mean(err[has0] == 0), 4) if has0.any() else None,
            "rows_with_a_0_mae_points": r(err[has0].mean(), 4) if has0.any() else None,
            "rows_without_0_exact_share": r(np.mean(err[~has0] == 0), 4),
        })
    res["constrained_tests"] = tests
    winner = min(tests, key=lambda t: t["mae_points"])
    res["constrained_winner"] = winner["zero_as"]
    raw_z, rounded_z, den_z = city_formula(vals, refused, zero_as="zero")
    res["rounding_rule"] = ("score = round_half_up(round(sum(w_i x s_i / 3) / sum(w_i) x 100, 2)), over items that "
                            "are not N/A; w = 3 / 2 / 0.5 by tier.")
    mism = np.abs(rounded_z - y) > 0
    res["unexplained_rows"] = {"count": int(mism.sum()),
                               "abs_diff_distribution": {str(k): int(v) for k, v in
                                                         pd.Series(np.abs(rounded_z - y)[mism]).round(1).value_counts().sort_index().items()}}
    res["areas_evaluated_counts_zeros"] = {
        "rows": int(len(rows)),
        "NO_OF_AREAS_EVALUATED_equals_items_not_NA": int(np.sum(clean["areas_evaluated"].values ==
                                                                np.sum(~np.isnan(vals), 1))),
        "NO_OF_AREAS_EVALUATED_equals_items_scored_1_to_3": int(np.sum(clean["areas_evaluated"].values ==
                                                                       np.sum(vals > 0, 1))),
    }

    # unconstrained: y = b0 - sum_i beta_i * deficit_i, deficit = (3 - s)/3 with 0 counted as full deficit,
    # N/A -> 0; temporal split (earliest 80% of completion dates train, latest 20% test).
    D = np.where(np.isnan(vals), 0.0, (3 - np.nan_to_num(vals)) / 3.0)
    order = np.argsort(clean["completed_on"].values, kind="stable")
    cut = int(0.8 * len(order))
    tr, te = order[:cut], order[cut:]
    X = np.column_stack([np.ones(len(D)), -D])
    beta, *_ = np.linalg.lstsq(X[tr], y[tr], rcond=None)
    p_te = X[te] @ beta
    p_tr = X[tr] @ beta
    W = weights_vector()
    names = list(ITEMS)
    coef = beta[1:]
    tier_arr = np.array([ITEMS[c][1] for c in names])
    by_tier_x = {t: float(np.mean(coef[tier_arr == t])) for t in ("High", "Moderate", "Cosmetic")}
    by_tier = {t: r(v, 3) for t, v in by_tier_x.items()}
    # the same fit on scores produced by the City's exact (ratio) formula: what a straight line gives
    # when the published weights are true by construction. Ratios come from the unrounded tier means
    # (rounded once, when stored), so the stored ratio can be checked against a refit.
    fin = np.isfinite(raw_z)
    tr_f = tr[fin[tr]]
    beta_f, *_ = np.linalg.lstsq(X[tr_f], raw_z[tr_f], rcond=None)
    by_tier_fx = {t: float(np.mean(beta_f[1:][tier_arr == t])) for t in ("High", "Moderate", "Cosmetic")}
    by_tier_f = {t: r(v, 3) for t, v in by_tier_fx.items()}
    ratio_rec = by_tier_x["High"] / by_tier_x["Cosmetic"] if by_tier_x["Cosmetic"] else None
    ratio_f = by_tier_fx["High"] / by_tier_fx["Cosmetic"] if by_tier_fx["Cosmetic"] else None
    test_from = str(pd.Timestamp(clean["completed_on"].values[te].min()).date())
    # constrained on same test rows for a like-for-like comparison
    res["unconstrained"] = {
        "model": "OLS: score = b0 - sum_i beta_i x (3 - s_i)/3, N/A -> 0 deficit, 0 -> full deficit",
        "split": {"train_rows": int(len(tr)), "test_rows": int(len(te)),
                  "test_from": test_from,
                  "rule": "temporal: earliest 80% of evaluations by completion date train, latest 20% test"},
        "train_r2": r(r2(y[tr], p_tr), 4), "test_r2": r(r2(y[te], p_te), 4),
        "test_mae_points": r(np.mean(np.abs(p_te - y[te])), 4),
        "constrained_same_test_rows_mae_points": r(np.mean(np.abs(rounded_z[te] - y[te])), 4),
        "constrained_same_test_rows_r2": r(r2(y[te], rounded_z[te]), 4),
        "intercept": r(beta[0], 3),
        "mean_points_per_full_deficit_by_tier": by_tier,
        "high_to_cosmetic_ratio_recovered": r(ratio_rec, 2),
        "high_to_cosmetic_ratio_recovered_exact": r(ratio_rec, CELL_DP),
        "high_to_cosmetic_ratio_published": 6.0,
        "mean_points_per_full_deficit_by_tier_on_formula_scores": by_tier_f,
        "ratio_on_formula_scores": r(ratio_f, 2),
        "ratio_on_formula_scores_exact": r(ratio_f, CELL_DP),
        "ratio_on_formula_scores_note": ("The same least-squares fit, same training rows, on the unrounded scores the "
                                         "City's formula gives with the published weights. If it matches the ratio "
                                         "recovered from the published scores, the gap from 6 is the straight-line "
                                         "approximation, not a disagreement with the published weights."),
        "note": ("The recovered coefficient is points lost when an item goes from 3 to 0, averaged over "
                 "denominators; it should be about 100 x w_i / sum(w) (about 3 points for High, 0.5 for Cosmetic)."),
    }

    # points per fix on the latest panel, City formula (0 counted as 0 and kept separate as 'access' points)
    lrows = latest["src_row"].values
    lv, lr = H["vals"][lrows], H["refused"][lrows]
    _, _, den = city_formula(lv, lr, zero_as="zero")
    scored = (~np.isnan(lv)) & (lv > 0)
    gain_fix = np.where(scored, W * (3 - np.nan_to_num(lv)) / 3.0, 0.0) / den[:, None] * 100
    gain_access = np.where(lr, W * 3 / 3.0, 0.0) / den[:, None] * 100
    med_den = float(np.median(den))
    items_out = []
    for i, c in enumerate(names):
        sc = lv[:, i][scored[:, i]]
        items_out.append({
            "item": ITEMS[c][0], "column": c, "tier": ITEMS[c][1], "weight_pct": TIER_WEIGHT[ITEMS[c][1]],
            "pillar": next(k for k, _, cs in PILLARS if c in cs),
            "buildings_scored": int(scored[:, i].sum()),
            "avg_score_1_to_3": r(sc.mean(), CELL_DP) if len(sc) else None,
            "share_below_3": r(np.mean(sc < 3), 4) if len(sc) else None,
            "share_scored_1": r(np.mean(sc == 1), 4) if len(sc) else None,
            "expected_points_per_building": r(gain_fix[:, i].mean(), 4),
            "access_points_per_building": r(gain_access[:, i].mean(), 4),
            "refused_buildings": int(lr[:, i].sum()),
            "points_1_to_3_at_median_denominator": r(TIER_WEIGHT[ITEMS[c][1]] * 2 / 3 / med_den * 100, 3),
        })
    items_out.sort(key=lambda d: -d["expected_points_per_building"])
    for k, d in enumerate(items_out):
        d["rank"] = k + 1
    # tier comparison: paperwork vs high-risk share of 1s
    paper_ix = [names.index(c) for c in dict((k, cs) for k, _, cs in PILLARS)["paperwork"]]
    high_ix = [i for i, c in enumerate(names) if ITEMS[c][1] == "High"]
    def share1(ix):
        v = lv[:, ix]
        s = v[(~np.isnan(v)) & (v > 0)]
        return r(np.mean(s == 1), 4), int(len(s))
    p1, pn = share1(paper_ix)
    h1, hn = share1(high_ix)
    # ward level expected points by pillar
    ward_pts = []
    for w in sorted(b["ward"].unique()):
        m = (latest["ward"].values == w)
        row = {"ward": w, "n": int(m.sum()),
               "total_fix_points_per_building": r(gain_fix[m].sum(1).mean(), 3),
               "total_access_points_per_building": r(gain_access[m].sum(1).mean(), 3)}
        for pk, _, cs in PILLARS:
            ix = [names.index(c) for c in cs]
            row[f"{pk}_fix_points"] = r(gain_fix[m][:, ix].sum(1).mean(), 3)
        ward_pts.append(row)
    res["points_per_fix"] = {
        "basis": ("Latest evaluation per building. Points from raising item i to 3 = w_i x (3 - s_i) / 3 / "
                  "sum(w over non-N/A items) x 100, the City's own formula. expected_points_per_building averages "
                  "this over ALL latest buildings (so it already includes the share of buildings affected). Items "
                  "scored 0 are kept separate as access_points_per_building: points the City formula withholds "
                  "when an area is refused or obstructed."),
        "buildings": int(len(lrows)), "median_denominator_weight": r(med_den, 2),
        "items": items_out,
        "by_ward": ward_pts,
        "city_total_fix_points_per_building": r(gain_fix.sum(1).mean(), 3),
        "city_total_access_points_per_building": r(gain_access.sum(1).mean(), 3),
        "paperwork_vs_high_risk": {"paperwork_share_scored_1": p1, "paperwork_scored_items": pn,
                                   "high_risk_share_scored_1": h1, "high_risk_scored_items": hn},
    }
    res["method"] = ("Constrained: the published tier weights with two treatments of 0, compared with the "
                     "published PROACTIVE BUILDING SCORE on every clean post-2023 row. Unconstrained: least squares "
                     "on the 50 item deficits with a temporal train/test split, to see whether the data recover the "
                     "tier ordering without being told it.")
    res["finding_facts"] = {
        "best_rule": winner["hypothesis"], "best_rule_exact_share": winner["exact_share"],
        "best_rule_mae": winner["mae_points"],
        "rows_with_0_exact_share_zero_rule": tests[0]["rows_with_a_0_exact_share"],
        "rows_with_0_exact_share_missing_rule": tests[1]["rows_with_a_0_exact_share"],
        "rows_with_0_mae_zero_rule": tests[0]["rows_with_a_0_mae_points"],
        "rows_with_0_mae_missing_rule": tests[1]["rows_with_a_0_mae_points"],
    }
    res["limits"] = [
        "The rebuild uses the tier weights published on the City's evaluations page; it cannot see officer notes "
        "or re-evaluations, which may explain the unexplained rows.",
        "Points per fix assumes the other items stay the same and that an item can be raised to 3; it is not a cost model.",
        ("The unconstrained fit uses item deficits only; the City score is a ratio, so a linear fit is an approximation. "
         "The same fit on scores produced by the City's exact formula also gives a high-to-cosmetic ratio of %s, so the "
         "gap from the published %s comes from that approximation; the data agree with the published weights. "
         if abs(ratio_f - ratio_rec) < 0.05 else
         "The unconstrained fit uses item deficits only; the City score is a ratio, so a linear fit is an approximation. "
         "The same fit on scores produced by the City's exact formula gives a high-to-cosmetic ratio of %s, not the "
         "ratio recovered from the published scores, so the gap from the published %s is not only that approximation. ")
        % (fmt_half_up(ratio_f, 2), fmt_half_up(res["unconstrained"]["high_to_cosmetic_ratio_published"], 0))
        + "The fit is tested on the most recent 20%% of evaluations (from %s), not on each building's latest evaluation." % test_from,
    ]
    return res


# ---------------------------------------------------------------------------------------------
# Analysis B: risk of losing green at the next evaluation
# ---------------------------------------------------------------------------------------------
def norm_firm(s):
    s = s.fillna("").str.upper().str.replace(r"[^A-Z0-9 ]", " ", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()
    s = s.str.replace(r"\b(INC|LTD|LIMITED|CORP|CORPORATION|CO|THE)\b", "", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()
    return s


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -35, 35)))


def fit_logit(X, y, lam, iters=100):
    """L2-regularised logistic regression by Newton-Raphson (intercept not penalised)."""
    n, k = X.shape
    w = np.zeros(k)
    P = np.eye(k) * lam
    P[0, 0] = 0
    for _ in range(iters):
        p = sigmoid(X @ w)
        g = X.T @ (p - y) + P @ w
        Hm = (X * (p * (1 - p))[:, None]).T @ X + P
        step = np.linalg.solve(Hm, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def fit_ridge(X, y, lam):
    k = X.shape[1]
    P = np.eye(k) * lam
    P[0, 0] = 0
    return np.linalg.solve(X.T @ X + P, X.T @ y)


def wilson(k, n, z=1.959964):
    if n == 0:
        return float("nan"), float("nan")
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return float(c - h), float(c + h)


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def pr_auc(p, y):
    """Average precision (step-wise area under the precision-recall curve)."""
    order = np.argsort(-p, kind="stable")
    ys = y[order]
    tp = np.cumsum(ys)
    prec = tp / np.arange(1, len(ys) + 1)
    return float(np.sum(prec * ys) / max(ys.sum(), 1))


def reliability(p, y, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & ((p < edges[i + 1]) if i < bins - 1 else (p <= edges[i + 1]))
        if m.sum():
            out.append({"bin": f"{edges[i]:.1f}-{edges[i + 1]:.1f}", "n": int(m.sum()),
                        "mean_predicted": r(p[m].mean(), 4), "observed_rate": r(y[m].mean(), 4)})
    return out


SCORE_BANDS_FOR_BASELINE = [0, 70, 80, 85, 88, 91, 94, 101]


def verdict_sentence(m_mae, p_mae, m_brier, p_brier):
    """The pre-set rule's verdict as one sentence, worded from the four test metrics."""
    mae = "repeating the last score on average miss (%s against %s points)" % (fmt_half_up(m_mae, 2), fmt_half_up(p_mae, 2))
    bri = "the band's past rate on Brier score (%s against %s)" % (fmt_half_up(m_brier, 3), fmt_half_up(p_brier, 3))
    beat = [(m_mae < p_mae, mae), (m_brier < p_brier, bri)]
    won = [t for ok, t in beat if ok]
    lost = [t for ok, t in beat if not ok]
    if not lost:
        return "The model was offered: it beat %s and %s, as the pre-set rule requires." % (won[0], won[1])
    if not won:
        return ("The model was not offered: it beat neither %s nor %s, and the pre-set rule needed both."
                % (lost[0], lost[1]))
    return ("The model was not offered: it beat %s, but not %s, and the pre-set rule needed both."
            % (won[0], lost[0]))


def analysis_b(H, clean, reg):
    panel = H["panel"]
    c = clean.sort_values(["rsn", "completed_on", "_id"]).copy()
    c["next_score"] = c.groupby("rsn")["score_city"].shift(-1)
    c["next_date"] = c.groupby("rsn")["completed_on"].shift(-1)
    pairs = c[c["next_score"].notna()].copy()
    rows = pairs["src_row"].values
    ps = pillar_scores(H["vals"][rows], H["refused"][rows])

    # pre-2023 history: within-year percentile of each pre-2023 score; keep the last one per RSN
    pre = panel[(panel.schema == "pre2023") & (panel.quarantine_reason == "")].copy()
    pre["year"] = pre["completed_on"].dt.year
    pre["pctile"] = pre.groupby("year")["score_city"].rank(pct=True) * 100
    last_pre = pre.sort_values(["rsn", "completed_on"]).groupby("rsn").tail(1).set_index("rsn")["pctile"]
    post_all = panel[(panel.schema == "post2023") & (panel.quarantine_reason == "")].copy()
    post_all["year"] = post_all["completed_on"].dt.year
    post_all["pctile"] = post_all.groupby("year")["score_city"].rank(pct=True) * 100
    pairs["pctile_now"] = post_all.set_index("src_row").loc[rows, "pctile"].values

    regi = reg.copy()
    regi["firm"] = norm_firm(regi["PROP_MANAGEMENT_COMPANY_NAME"])
    firm_size = regi[regi.firm != ""].groupby("firm")["RSN"].nunique()
    regi["firm_portfolio"] = regi["firm"].map(firm_size).fillna(1)
    regi = regi.drop_duplicates("RSN").set_index("RSN")

    f = pd.DataFrame(index=pairs.index)
    f["last_score"] = pairs["score_city"].values
    for pk in PILLAR_KEYS:
        v = ps[pk]["flag"]
        f[f"pillar_{pk}"] = v
    f["refused_items"] = H["refused"][rows].sum(1)
    f["year_built"] = pairs["year_built"].values
    f["log_storeys"] = np.log(pairs["storeys"].values)
    f["log_units"] = np.log(np.maximum(pairs["units"].values, 1))
    f["tchc"] = (pairs["property_type"].values == "TCHC").astype(float)
    f["social"] = (pairs["property_type"].values == "SOCIAL HOUSING").astype(float)
    rr = regi.reindex(pairs["rsn"].values)
    f["elevator_original"] = (rr["ELEVATOR_STATUS"].values == "ORIGINAL").astype(float)
    f["heating_original"] = (rr["HEATING_EQUIPMENT_STATUS"].values == "ORIGINAL").astype(float)
    f["single_pane"] = (rr["WINDOW_TYPE"].values == "SINGLE PANE").astype(float)
    f["log_firm_portfolio"] = np.log(rr["firm_portfolio"].fillna(1).values.astype(float))
    for d in DISTRICT_ORDER[1:]:
        f["district_" + d.split()[0].lower()] = (pairs["district"].values == d).astype(float)
    lp = last_pre.reindex(pairs["rsn"].values).values
    f["pre2023_last_pctile"] = lp
    f["pctile_change_vs_pre2023"] = pairs["pctile_now"].values - lp
    f["has_pre2023"] = (~np.isnan(lp)).astype(float)

    y_next = pairs["next_score"].values.astype(float)
    y_bin = (y_next < 85).astype(float)
    last = f["last_score"].values
    next_dates = pairs["next_date"].values
    # temporal split: next evaluation completed before 2026-01-01 = train, from 2026-01-01 = test
    split_date = np.datetime64("2026-01-01")
    tr = next_dates < split_date
    te = ~tr

    cols = list(f.columns)
    Xraw = f.values.astype(float)
    med = np.nanmedian(Xraw[tr], 0)
    miss_cols = [j for j in range(Xraw.shape[1]) if np.isnan(Xraw[:, j]).any() and cols[j] not in
                 ("pre2023_last_pctile", "pctile_change_vs_pre2023")]
    Ximp = np.where(np.isnan(Xraw), med, Xraw)
    # pre-2023 features: fill with train median and rely on has_pre2023
    mu, sd = Ximp[tr].mean(0), Ximp[tr].std(0)
    sd[sd == 0] = 1
    Z = np.column_stack([np.ones(len(Ximp)), (Ximp - mu) / sd])

    # choose lambda on an inner temporal split of the training pairs
    tr_idx = np.where(tr)[0]
    inner_cut = np.datetime64("2025-08-01")
    itr = tr_idx[next_dates[tr_idx] < inner_cut]
    iva = tr_idx[next_dates[tr_idx] >= inner_cut]
    lam_grid = [0.1, 1, 3, 10, 30, 100, 300]
    inner = []
    for lam in lam_grid:
        w = fit_logit(Z[itr], y_bin[itr], lam)
        inner.append({"lambda": lam, "val_brier": r(brier(sigmoid(Z[iva] @ w), y_bin[iva]), 5)})
    lam_best = min(inner, key=lambda d: d["val_brier"])["lambda"]
    w = fit_logit(Z[tr], y_bin[tr], lam_best)
    p_model = sigmoid(Z[te] @ w)

    # persistence baselines (fitted on train only)
    band_idx = np.digitize(last, SCORE_BANDS_FOR_BASELINE) - 1
    base_rate = {}
    for bi in range(len(SCORE_BANDS_FOR_BASELINE) - 1):
        m = tr & (band_idx == bi)
        base_rate[bi] = (y_bin[m].sum() + 0.5) / (m.sum() + 1.0)  # Jeffreys-style smoothing
    p_persist = np.array([base_rate[bi] for bi in band_idx[te]])
    p_naive = (last[te] < 85).astype(float)

    # score regression
    ridge_inner = []
    for lam in lam_grid:
        bw = fit_ridge(Z[itr], y_next[itr], lam)
        ridge_inner.append({"lambda": lam, "val_mae": r(np.mean(np.abs(Z[iva] @ bw - y_next[iva])), 4)})
    lam_r = min(ridge_inner, key=lambda d: d["val_mae"])["lambda"]
    bw = fit_ridge(Z[tr], y_next[tr], lam_r)
    s_model = Z[te] @ bw
    resid_tr = y_next[tr] - Z[tr] @ bw
    q10, q90 = np.quantile(resid_tr, [0.1, 0.9])
    cover = np.mean((y_next[te] >= s_model + q10) & (y_next[te] <= s_model + q90))
    pers_resid = y_next[tr] - last[tr]
    pq10, pq90 = np.quantile(pers_resid, [0.1, 0.9])
    pcover = np.mean((y_next[te] >= last[te] + pq10) & (y_next[te] <= last[te] + pq90))

    yt = y_bin[te]
    m_brier, p_brier, n_brier = brier(p_model, yt), brier(p_persist, yt), brier(p_naive, yt)
    m_mae, p_mae = float(np.mean(np.abs(s_model - y_next[te]))), float(np.mean(np.abs(last[te] - y_next[te])))
    # a stronger, still model-free baseline: last score + the training mean change for its score band
    band_change = {bi: float(np.mean(y_next[tr & (band_idx == bi)] - last[tr & (band_idx == bi)]))
                   if np.any(tr & (band_idx == bi)) else 0.0 for bi in base_rate}
    pd_mae = float(np.mean(np.abs(last[te] + np.array([band_change[bi] for bi in band_idx[te]]) - y_next[te])))
    champion = "model" if (m_brier < p_brier and m_mae < p_mae) else "persistence"
    if not all(np.isfinite(v) for v in (m_brier, p_brier, m_mae, p_mae, pd_mae)):
        raise ValueError("non-finite metric in analysis B")
    k_cov = int(round(cover * te.sum()))
    wl, wh = wilson(k_cov, int(te.sum()))

    # secondary target: bottom 2.5% of the next evaluation's completion year
    post_all["cut025"] = post_all.groupby("year")["score_city"].transform(lambda s: np.quantile(s, 0.025))
    cut_by_year = post_all.groupby("year")["cut025"].first()
    ny = pd.to_datetime(next_dates).year
    y_bot = (y_next <= cut_by_year.reindex(ny).values).astype(float)

    coefs = sorted([{"feature": cn, "coef_per_sd": r(cw, 4)} for cn, cw in zip(cols, w[1:])],
                   key=lambda d: -abs(d["coef_per_sd"]))
    # by ward: mean predicted risk on the test set vs observed
    ward_te = pairs["ward"].values[te]
    by_ward = []
    for wd in sorted(set(ward_te)):
        m = ward_te == wd
        by_ward.append({"ward": wd, "n_pairs_test": int(m.sum()), "mean_model_risk": r(p_model[m].mean(), 4),
                        "mean_persistence_risk": r(p_persist[m].mean(), 4), "observed_rate": r(yt[m].mean(), 4)})
    # transition table on all pairs
    sign_order = ["green", "yellow", "red"]
    trans = pd.crosstab(band_of(last), band_of(y_next)).reindex(index=sign_order, columns=sign_order, fill_value=0)
    return {
        "question": "Will a building's next biennial evaluation score fall below 85 (lose or miss the green sign)?",
        "target": ("next PROACTIVE BUILDING SCORE < 85. The proactive score is used because reactive deductions are "
                   "only recorded on each building's latest row (a refresh-time snapshot), so they are not "
                   "available at evaluation time for earlier rows."),
        "pairs": {"total": int(len(pairs)), "train": int(tr.sum()), "test": int(te.sum()),
                  "split": "temporal: pairs whose NEXT evaluation completed before 2026-01-01 train; on/after test",
                  "train_positive_rate": r(y_bin[tr].mean(), 4), "test_positive_rate": r(yt.mean(), 4),
                  "bottom_2_5pct_pairs_all": int(y_bot.sum())},
        "features": cols,
        "feature_notes": ("From the earlier evaluation of each pair plus registration attributes. Pillar scores keep "
                          "a refused area as a separate flag (a provisional choice, to be confirmed). Registration is a single 2026-07-05 snapshot, so "
                          "elevator/heating/window/firm fields may postdate the earlier evaluation (a leakage risk "
                          "stated as a limit). Firm portfolio size = buildings per normalised management-firm name "
                          "(names used only inside the model; never published)."),
        "model": {"logistic": f"numpy Newton-Raphson logistic regression, L2 lambda={lam_best} (chosen on an inner "
                              f"temporal split of the training pairs at 2025-08-01)",
                  "lambda_search": inner,
                  "score_model": f"numpy ridge regression, lambda={lam_r}", "ridge_lambda_search": ridge_inner,
                  "coefficients_per_sd": coefs},
        "baselines": {
            "persistence_band_rate": ("P(next < 85) = training-set rate of next < 85 among pairs whose last score "
                                      "is in the same band " + str(SCORE_BANDS_FOR_BASELINE)),
            "persistence_band_rates": {f"{SCORE_BANDS_FOR_BASELINE[i]}-{SCORE_BANDS_FOR_BASELINE[i + 1] - 1}":
                                       r(base_rate[i], 4) for i in base_rate},
            "naive_last_value": "predict next score = last score; P(next < 85) = 1 if last < 85 else 0",
        },
        "test_metrics": {
            "brier_model": r(m_brier, 5), "brier_persistence_band": r(p_brier, 5), "brier_naive_0_1": r(n_brier, 5),
            "pr_auc_model": r(pr_auc(p_model, yt), 4), "pr_auc_persistence_band": r(pr_auc(p_persist, yt), 4),
            "pr_auc_no_skill": r(yt.mean(), 4),
            "mae_score_model": r(m_mae, 4), "mae_score_persistence": r(p_mae, 4),
            "mae_score_persistence_plus_band_mean_change": r(pd_mae, 4),
            "mae_note": ("Plain persistence (next = last) ignores the typical rise between evaluations; the "
                         "band-mean-change baseline adds the training mean change for the last-score band. The gate "
                         "uses plain persistence as specified; the stronger baseline is shown for honesty."),
            "interval80_model": {"lo_offset": r(q10, 3), "hi_offset": r(q90, 3), "coverage_test": r(cover, 4),
                                 "k": k_cov, "n": int(te.sum()), "wilson95": [r(wl, 4), r(wh, 4)],
                                 "method": "offsets = 10th/90th percentiles of training residuals"},
            "interval80_persistence": {"lo_offset": r(pq10, 3), "hi_offset": r(pq90, 3), "coverage_test": r(pcover, 4)},
        },
        "reliability_model": reliability(p_model, yt),
        "reliability_persistence": reliability(p_persist, yt),
        "gate": {"rule": "The model is champion only if it beats persistence on BOTH Brier (P(next<85)) and MAE "
                         "(next score) out of sample.",
                 "champion": champion,
                 "verdict_text": verdict_sentence(m_mae, p_mae, m_brier, p_brier)},
        "by_ward_test": by_ward,
        "transition_counts_all_pairs": {"rows_last_band": trans.index.tolist(), "cols_next_band": trans.columns.tolist(),
                                        "counts": trans.values.tolist()},
        "limits": [
            "About one post-2023 pair per building; the test set is one season (2026) and small.",
            "Biennial evaluations: a building's condition between evaluations is not observed.",
            "Registration attributes are a 2026 snapshot (possible look-ahead).",
            "Reliability bins with few buildings are noisy; read them with their n.",
        ],
    }


# ---------------------------------------------------------------------------------------------
# Analysis D: archetypes (k-means) and control chart
# ---------------------------------------------------------------------------------------------
def kmeans(X, k, rng, n_init=10, iters=200):
    best = None
    for _ in range(n_init):
        # k-means++ init
        C = [X[rng.integers(len(X))]]
        for _ in range(1, k):
            d2 = np.min(((X[:, None, :] - np.array(C)[None]) ** 2).sum(2), 1)
            C.append(X[rng.choice(len(X), p=d2 / d2.sum())])
        C = np.array(C)
        for _ in range(iters):
            lab = np.argmin(((X[:, None, :] - C[None]) ** 2).sum(2), 1)
            newC = np.array([X[lab == j].mean(0) if np.any(lab == j) else C[j] for j in range(k)])
            if np.allclose(newC, C):
                break
            C = newC
        inertia = float(((X - C[lab]) ** 2).sum())
        if best is None or inertia < best[0]:
            best = (inertia, C, lab)
    return best


def silhouette(X, lab):
    D = np.sqrt(((X[:, None, :] - X[None]) ** 2).sum(2))
    ks = np.unique(lab)
    s = np.zeros(len(X))
    for i in range(len(X)):
        same = lab == lab[i]
        a = D[i, same].sum() / max(same.sum() - 1, 1)
        bmin = min(D[i, lab == k].mean() for k in ks if k != lab[i])
        s[i] = (bmin - a) / max(a, bmin) if max(a, bmin) > 0 else 0
    return float(s.mean())


def analysis_d(b, clean):
    rng = np.random.default_rng(SEED)
    P = b[[f"p_{k}" for k in PILLAR_KEYS]].values
    ok = ~np.isnan(P).any(1)
    X = P[ok]
    sub = rng.choice(len(X), size=min(1500, len(X)), replace=False)
    sil = []
    fits = {}
    for k in range(2, 7):
        inertia, C, lab = kmeans(X, k, rng)
        fits[k] = (C, lab)
        sil.append({"k": k, "silhouette_on_1500_sample": r(silhouette(X[sub], lab[sub]), 4), "inertia": r(inertia, 1)})
    kbest = max(sil, key=lambda d: d["silhouette_on_1500_sample"])["k"]
    C, lab = fits[kbest]
    city_mean = X.mean(0)
    arche = []
    bb = b[ok].reset_index(drop=True)
    for j in np.argsort(-np.bincount(lab)):
        m = lab == j
        prof = {k: r(v, CELL_DP) for k, v in zip(PILLAR_KEYS, C[j])}
        gap = {k: r(v - cm, CELL_DP) for k, v, cm in zip(PILLAR_KEYS, C[j], city_mean)}
        weakest = min(gap, key=lambda k: gap[k])
        arche.append({"cluster": int(j), "n_buildings": int(m.sum()), "share": r(m.mean(), 4),
                      "centroid": prof, "gap_vs_city_mean": gap, "weakest_pillar_vs_city": weakest,
                      "mean_city_score": r(bb.loc[m, "score_current"].mean(), CELL_DP),
                      "pct_green": r(100 * np.mean(bb.loc[m, "band"] == "green"), CELL_DP),
                      "by_district_share": {d: r(np.mean(bb.loc[m, "district"] == d), 4) for d in DISTRICT_ORDER}})
    corr = np.corrcoef(X.T)

    # shape variant: subtract each building's own mean across pillars, so clusters follow the PROFILE
    # (which pillar lags) rather than the overall level
    Xs = X - X.mean(1, keepdims=True)
    sil_s, fits_s = [], {}
    for k in range(2, 7):
        inertia, Cs, lab_s = kmeans(Xs, k, rng)
        fits_s[k] = (Cs, lab_s)
        sil_s.append({"k": k, "silhouette_on_1500_sample": r(silhouette(Xs[sub], lab_s[sub]), 4), "inertia": r(inertia, 1)})
    kbest_s = max(sil_s, key=lambda d: d["silhouette_on_1500_sample"])["k"]
    Cs, lab_s = fits_s[kbest_s]
    shape = []
    for j in np.argsort(-np.bincount(lab_s)):
        m = lab_s == j
        dev = {k: r(v, CELL_DP) for k, v in zip(PILLAR_KEYS, Cs[j])}
        shape.append({"cluster": int(j), "n_buildings": int(m.sum()), "share": r(m.mean(), 4),
                      "deviation_from_own_mean": dev, "lagging_pillar": min(dev, key=lambda k: dev[k]),
                      "mean_level": r(X[m].mean(), CELL_DP),
                      "mean_city_score": r(bb.loc[m, "score_current"].mean(), CELL_DP),
                      "pct_green": r(100 * np.mean(bb.loc[m, "band"] == "green"), CELL_DP)})

    # null reference: the same data with each pillar shuffled independently across buildings (no
    # profile structure left), same k, same 1,500-building sample, its own fixed seed so the fits
    # above are unchanged
    rng0 = np.random.default_rng(SEED + 1)
    Xn = np.column_stack([rng0.permutation(X[:, j]) for j in range(X.shape[1])])
    _, _, lab_n = kmeans(Xn, kbest, rng0)
    Xns = Xn - Xn.mean(1, keepdims=True)
    _, _, lab_ns = kmeans(Xns, kbest_s, rng0)
    null_sil = {"raw": r(silhouette(Xn[sub], lab_n[sub]), 4), "shape": r(silhouette(Xns[sub], lab_ns[sub]), 4),
                "k": int(kbest_s), "k_raw": int(kbest), "seed": SEED + 1, "sample": int(len(sub)),
                "method": ("Each pillar column shuffled independently across buildings (seed %d), then the same "
                           "k-means and silhouette on the same 1,500-building sample. It shows how high a "
                           "silhouette gets with no profile structure at all." % (SEED + 1))}
    # the deepest-lagging shape cluster against a one-line rule on that pillar's own gap
    lag_c = min(shape, key=lambda c_: min(c_["deviation_from_own_mean"].values()))
    lag_p = lag_c["lagging_pillar"]
    member = lab_s == lag_c["cluster"]
    gap_p = Xs[:, PILLAR_KEYS.index(lag_p)]
    grid = np.round(np.arange(-40.0, 0.0001, 0.5), 1)
    agree = np.array([np.mean((gap_p < t) == member) for t in grid])
    t_best = float(grid[int(np.argmax(agree))])
    one_rule = {"pillar": lag_p, "threshold": t_best, "agreement": r(float(agree.max()), 4),
                "cluster_buildings": int(member.sum()), "rule_buildings": int((gap_p < t_best).sum()),
                "rule": ("the %s pillar more than %s points below the building's own average across the 8 pillars"
                         % (lag_p, fmt_half_up(-t_best, 1))),
                "threshold_search": "best threshold on a 0.5-point grid from -40 to 0"}

    # control chart on citywide monthly mean proactive score (post-2023, all clean evaluations)
    c = clean.copy()
    c["month"] = c["completed_on"].dt.to_period("M").astype(str)
    mon = c.groupby("month")["score_city"].agg(["mean", "count"]).reset_index()
    MIN_N_MONTH = 20            # a month with fewer evaluations is drawn but not judged
    enough = mon["count"] >= MIN_N_MONTH
    med = float(np.median(mon.loc[enough, "mean"]))
    mad = float(np.median(np.abs(mon.loc[enough, "mean"] - med))) * 1.4826
    lo, hi = med - 3 * mad, med + 3 * mad
    pts = []
    for _, rw in mon.iterrows():
        flag = ""
        if rw["count"] < MIN_N_MONTH:
            flag = "low n"
        elif rw["mean"] > hi:
            flag = "above"
        elif rw["mean"] < lo:
            flag = "below"
        pts.append({"month": rw["month"], "mean_score": r(rw["mean"], 3), "n": int(rw["count"]), "flag": flag,
                    "_mean": float(rw["mean"])})
    # run rule: MIN_RUN or more eligible months in a row on the same side of the centre is a sustained
    # shift even when no single month crosses a limit (months with n < 20 are skipped; a month
    # exactly on the centre ends a run)
    MIN_RUN = 8
    runs, cur = [], []
    for i, p_ in enumerate(pts):
        if p_["n"] < MIN_N_MONTH:
            continue
        side = "above" if p_["_mean"] > med else ("below" if p_["_mean"] < med else "")
        if cur and side and side == cur[-1][1]:
            cur.append((i, side))
        else:
            if len(cur) >= MIN_RUN:
                runs.append(cur)
            cur = [(i, side)] if side else []
    if len(cur) >= MIN_RUN:
        runs.append(cur)
    for run in runs:
        for i, side in run:
            if not pts[i]["flag"]:
                pts[i]["flag"] = "run %s centre" % side
    run_out = [{"first_month": pts[run[0][0]]["month"], "last_month": pts[run[-1][0]]["month"], "side": run[0][1],
                "months": len(run)} for run in runs]
    for p_ in pts:
        del p_["_mean"]
    eligible = int(enough.sum())
    beyond = sum(1 for p_ in pts if p_["flag"] in ("above", "below"))
    yr = c.assign(year=c["completed_on"].dt.year).groupby("year")["score_city"].mean()
    steps = [(int(y0), int(y1), float(yr[y1] - yr[y0])) for y0, y1 in zip(yr.index[:-1], yr.index[1:])]
    y0, y1, _ = max(steps, key=lambda t: abs(t[2]))
    cc_limits = [
        "Evaluations are scheduled, not random: the mix of buildings evaluated changes by month, so a "
        "flag is a prompt to look, not evidence of a citywide change.",
        "Months with fewer than %d evaluations are shown but not flagged; %d of the %d months have at least %d."
        % (MIN_N_MONTH, eligible, len(pts), MIN_N_MONTH),
        "The limits come from month-to-month swings that already include the change in which buildings are due, so "
        "they are wide (plus or minus %s points)%s." % (fmt_half_up((hi - lo) / 2, 1),
                                                        " and no eligible month (%d or more evaluations) crosses them" % MIN_N_MONTH
                                                        if beyond == 0 else ""),
    ]
    if run_out:
        cc_limits.append("The run rule catches what the limits miss: %s. The yearly averages moved from %s in %d to %s in %d."
                         % ("; ".join("%s to %s (%d months) all sit %s the centre" % (u["first_month"], u["last_month"],
                                                                                     u["months"], u["side"]) for u in run_out),
                            fmt_half_up(yr[y0], 1), y0, fmt_half_up(yr[y1], 1), y1))
    return {
        "archetypes": {
            "question": "Do buildings fall into a few recurring maintenance profiles across the 8 pillars?",
            "method": ("k-means (numpy, k-means++ init, 10 restarts, seed %d) on the 8 pillar scores of each "
                       "building's latest evaluation (0-100, unscaled; a refused area is a separate flag, a provisional choice); k from 2-6 "
                       "chosen by mean silhouette on a fixed random sample of 1,500 buildings." % SEED),
            "buildings_used": int(ok.sum()), "buildings_skipped_missing_a_pillar": int((~ok).sum()),
            "k_search": sil, "k_chosen": kbest, "city_mean_profile": {k: r(v, CELL_DP) for k, v in zip(PILLAR_KEYS, city_mean)},
            "clusters": arche,
            "pillar_correlation": {"pillars": PILLAR_KEYS, "matrix": [[r(v, 3) for v in row] for row in corr]},
            "shape_variant": {
                "method": ("Same k-means and silhouette search on row-centred profiles (each pillar minus the "
                           "building's own mean across the 8 pillars), so clusters follow which pillar lags, not "
                           "the overall level."),
                "k_search": sil_s, "k_chosen": kbest_s, "clusters": shape,
                "null_silhouette": null_sil, "one_line_rule": one_rule},
            "limits": ["Cluster names are for the reader to assign; archetypes are descriptive, not causal.",
                       "Low silhouette values mean the clusters overlap; the score is reported so readers can judge.",
                       "Buildings missing any pillar (all items N/A or refused) are left out.",
                       "No distinct archetypes: the best split of the profiles (k = %d) has a silhouette of %s, against "
                       "%s for the same data with each pillar shuffled (no structure at all). The split is one "
                       "pillar: a single rule (%s) reproduces %s%% of it."
                       % (kbest_s, "%.2f" % sil_s[kbest_s - 2]["silhouette_on_1500_sample"], "%.2f" % null_sil["shape"],
                          one_rule["rule"], fmt_half_up(100 * one_rule["agreement"], 1))],
        },
        "control_chart": {
            "question": "Did the citywide average evaluation score shift in any month beyond normal variation?",
            "method": ("Monthly mean PROACTIVE BUILDING SCORE of evaluations completed that month (post-2023). "
                       "Centre = median of monthly means (months with n >= %d); limits = centre +/- 3 x MAD x 1.4826. "
                       "Run rule: %d or more months in a row (n >= %d) on the same side of the centre are flagged "
                       "as a sustained shift." % (MIN_N_MONTH, MIN_RUN, MIN_N_MONTH)),
            "centre": r(med, 3), "lower": r(lo, 3), "upper": r(hi, 3), "points": pts,
            "run_rule": {"min_run": MIN_RUN, "rule": ("%d or more eligible months in a row on the same side of the "
                                                      "centre (Western Electric run rule); months with n < %d are "
                                                      "skipped and a month on the centre ends a run" % (MIN_RUN, MIN_N_MONTH))},
            "runs": run_out, "min_n": MIN_N_MONTH, "eligible_months": eligible, "months_beyond_limits": beyond,
            "yearly_means": {str(int(y)): r(v, CELL_DP) for y, v in yr.items()},
            "limits": cc_limits,
        },
        "survival": {"status": "[unrun]", "note": "Kaplan-Meier green-to-first-yellow was not run in stage 1."},
    }


# ---------------------------------------------------------------------------------------------
# Trend (percentile-harmonised history)
# ---------------------------------------------------------------------------------------------
def trend(H):
    panel = H["panel"]
    c = panel[panel.quarantine_reason == ""].copy()
    c["year"] = c["completed_on"].dt.year
    c["pctile"] = c.groupby(["schema", "year"])["score_city"].rank(pct=True) * 100
    city = []
    for (schema, year), g in c.groupby(["schema", "year"]):
        city.append({"schema": schema, "year": int(year), "n": int(len(g)),
                     "mean_score": r(g.score_city.mean(), CELL_DP), "median_score": r(g.score_city.median(), CELL_DP)})
    ward = []
    for (w, year), g in c.groupby(["ward", "year"]):
        ward.append({"ward": w, "year": int(year), "n": int(len(g)), "mean_pctile": r(g.pctile.mean(), CELL_DP),
                     "schemas": sorted(g.schema.unique().tolist())})
    # which buildings each year holds: share of a year's buildings also evaluated 1 and 2 years before
    overlap = []
    for schema, gs in c.groupby("schema"):
        sets = {int(y): set(gy.rsn) for y, gy in gs.groupby("year")}
        for lag in (1, 2):
            for y in sorted(sets):
                if y - lag in sets:
                    both = len(sets[y] & sets[y - lag])
                    overlap.append({"schema": schema, "from_year": y - lag, "to_year": y, "lag_years": lag,
                                    "to_year_buildings": len(sets[y]), "also_in_from_year": both,
                                    "share_of_to_year_also_in_from_year": r(both / len(sets[y]), CELL_DP)})
    post1 = [o for o in overlap if o["schema"] == "post2023" and o["lag_years"] == 1]
    post2 = [o for o in overlap if o["schema"] == "post2023" and o["lag_years"] == 2]
    worst1 = max(o["share_of_to_year_also_in_from_year"] for o in post1) if post1 else None
    last_pre = str(c.loc[c.schema == "pre2023", "completed_on"].max().date())
    first_post = str(c.loc[c.schema == "post2023", "completed_on"].min().date())
    same_bldg = ""
    if post2:
        same_bldg = " (%s)" % ", and ".join(
            "%s%% of %d's buildings were also evaluated in %d" % (
                fmt_half_up(100 * o["share_of_to_year_also_in_from_year"], 1), o["to_year"], o["from_year"])
            for o in post2)
    return {
        "method": ("Within-year percentile harmonisation: each evaluation's score is ranked against all evaluations "
                   "completed in the same calendar year AND the same schema (pre-2023 20 categories x 1-5, or "
                   "post-2023 50 weighted items x 1-3), giving 0-100. A ward's yearly mean percentile says where its "
                   "buildings sat relative to that year's evaluations; it cannot show a citywide improvement."),
        "city_by_year": city, "ward_by_year": ward, "year_overlap": overlap,
        "method_break": "2023: the instrument changed (20 categories x 1-5 -> 50 weighted items x 1-3). Raw means are not comparable across it.",
        "limits": ["2023 holds both schemas (pre-2023 file to %s, new tool from %s); they are ranked separately."
                   % (last_pre, first_post),
                   "Buildings are evaluated every two years, so since 2023 each year's point for a ward is a different "
                   "set of buildings (at most %s%% of a year's buildings were also evaluated the year before). Compare "
                   "2023 with 2025, and 2024 with 2026, to follow the same buildings%s. Percentiles are relative to "
                   "that year's evaluations." % (fmt_half_up(100 * worst1, 1) if worst1 is not None else "n/a", same_bldg)],
    }


# ---------------------------------------------------------------------------------------------
# Ward map (optional): Douglas-Peucker simplification in numpy
# ---------------------------------------------------------------------------------------------
def dp(points, eps):
    if len(points) < 3:
        return points
    a, bpt = points[0], points[-1]
    ab = bpt - a
    L = np.hypot(*ab)
    rel = points - a
    d = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / L if L > 0 else np.hypot(rel[:, 0], rel[:, 1])
    i = int(np.argmax(d))
    if d[i] > eps:
        left = dp(points[: i + 1], eps)
        right = dp(points[i:], eps)
        return np.vstack([left[:-1], right])
    return np.vstack([a, bpt])


def wards_map(in_dir, eps=0.0006):
    path = os.path.join(in_dir, F_WARDS)
    if not os.path.exists(path):
        return None
    g = json.load(open(path))
    feats = []
    for ft in g["features"]:
        geom = ft["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        out = []
        for poly in polys:
            rings = []
            for ring in poly:
                arr = np.array(ring, dtype=float)[:, :2]
                s = dp(arr, eps)
                if len(s) >= 4:
                    rings.append([[round(x, 4), round(y, 4)] for x, y in s])
            if rings:
                out.append(rings)
        feats.append({"type": "Feature", "properties": {"ward": ft["properties"]["AREA_SHORT_CODE"],
                                                         "name": ft["properties"]["AREA_NAME"]},
                      "geometry": {"type": "MultiPolygon", "coordinates": out}})
    return {"type": "FeatureCollection", "features": feats,
            "note": f"Simplified from {F_WARDS} (Douglas-Peucker, tolerance {eps} degrees, 4 decimals). Display only."}


# ---------------------------------------------------------------------------------------------
# KPIs for page 1
# ---------------------------------------------------------------------------------------------
def kpis(b, clean, as_of):
    units = b["units"].fillna(0).values
    last_year = int(clean["completed_on"].dt.year.max())
    yr = clean[clean["completed_on"].dt.year == last_year]
    cut = float(np.quantile(yr["score_city"], 0.025))
    due = pd.to_datetime(b["completed_on"]) + pd.DateOffset(years=2)
    asof = pd.Timestamp(as_of)
    return {
        "buildings": int(len(b)), "units": int(units.sum()),
        "score_mean": r(b["score_current"].mean(), 2),
        "score_unit_weighted": r(np.sum(b["score_current"].values * units) / units.sum(), 2),
        "pct_green": r(100 * np.mean(b.band == "green"), 2), "pct_yellow": r(100 * np.mean(b.band == "yellow"), 2),
        "pct_red": r(100 * np.mean(b.band == "red"), 2),
        "refusal_evals_per_100": r(100 * np.mean(b.n_refused_items > 0), 2),
        "audit_zone": {"year": last_year, "evaluations_that_year": int(len(yr)),
                       "p2_5_cutoff_proactive": r(cut, 2),
                       "evaluations_at_or_below_cutoff": int((yr["score_city"] <= cut).sum()),
                       "note": ("The City prioritises the lowest 2.5% of evaluated buildings each year for audit and "
                                "now uses proactive AND reactive scores; this count uses the proactive score only.")},
        "due_next_90_days_from_as_of": {"as_of": str(asof.date()),
                                        "count": int(((due >= asof) & (due <= asof + pd.Timedelta(days=90))).sum()),
                                        "overdue_count": int((due < asof).sum()),
                                        "overdue_note": ("buildings whose latest evaluation in this file is more than 2 years "
                                                         "before the as-of date; the next evaluation may be scheduled or not yet published"),
                                        "rule": "due = latest completion date + 2 years (the City's biennial cycle)"},
    }


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------
def validate_reference_tables():
    """Fail fast if the hand-copied reference tables drift from what the City publishes."""
    allc = [c for _, _, cs in PILLARS for c in cs]
    if sorted(allc) != sorted(ITEMS) or len(allc) != 50:
        missing = sorted(set(ITEMS) - set(allc))
        extra = sorted(set(allc) - set(ITEMS))
        raise ValueError(f"pillar mapping must cover the 50 items exactly once (missing {missing}, extra {extra}, "
                         f"listed {len(allc)})")
    tiers = [t for _, t in ITEMS.values()]
    if (tiers.count("High"), tiers.count("Moderate"), tiers.count("Cosmetic")) != (17, 23, 10):
        raise ValueError("tier counts differ from the City's published 17 High / 23 Moderate / 10 Cosmetic")
    if sorted(WARD_TO_DISTRICT) != [f"{i:02d}" for i in range(1, 26)]:
        raise ValueError("ward -> council table must hold wards 01-25 exactly once")
    if sorted(set(PRE_CATS.values()) - set(PILLAR_KEYS)):
        raise ValueError("pre-2023 crosswalk points at an unknown pillar")


def build(in_dir, out_dir, sources_path=None):
    validate_reference_tables()
    t0 = dt.datetime.now(dt.timezone.utc)
    post, pre, reg = load(in_dir)
    H = harmonise(post, pre, reg)
    clean, latest = latest_panel(H)
    as_of = str(clean["completed_on"].max().date())
    meta = {"data_as_of": as_of, "built_at": t0.strftime("%Y-%m-%dT%H:%M:%SZ")}
    b = building_table(H, post, latest)

    # registration join + freshness
    H["health"]["registration"] = {
        "latest_buildings_found_in_registration": int(b["rsn"].isin(reg["RSN"]).sum()),
        "latest_buildings": int(len(b)),
        "empty_columns": [c for c in reg.columns if (reg[c].str.strip() == "").all()],
    }
    src = None
    if sources_path and os.path.exists(sources_path):
        src = json.load(open(sources_path))
        for f_ in src.get("files", []):
            if f_["file"] == F_REG:
                refreshed = f_.get("ckan_package_last_refreshed")
                H["health"]["registration"]["ckan_last_refreshed"] = (str(pd.Timestamp(refreshed).date())
                                                                      if refreshed else None)
                H["health"]["registration"]["ckan_last_refreshed_raw"] = refreshed
                rate = f_.get("ckan_refresh_rate")
                H["health"]["registration"]["stated_refresh_rate"] = rate.lower() if isinstance(rate, str) else rate
        # verify the inputs are the recorded downloads
        mism = []
        nl_path = os.path.join(in_dir, "NL_CLEAN.json")
        nl = json.load(open(nl_path)) if os.path.exists(nl_path) else None
        nl_src = {f_["source_file"]: f_["source_sha256"] for f_ in (nl or {}).get("files", [])}
        for f_ in src.get("files", []):
            p = os.path.join(in_dir, f_["file"])
            if nl is not None and f_["file"] in nl_src:
                # NorthLedger-cleaned input: the cleaned CSV differs by design; the raw file it
                # was cleaned from must be the recorded download.
                if nl_src[f_["file"]] != f_["sha256"]:
                    mism.append(f_["file"])
            elif os.path.exists(p) and sha256(p) != f_["sha256"]:
                mism.append(f_["file"])
        if nl is not None:
            meta["northledger_clean"] = {
                "engine": nl.get("engine"), "made_at": nl.get("made_at"), "as_of": nl.get("as_of"),
                "method": nl.get("method"),
                "files": [{k: f_.get(k) for k in ("source_file", "source_sha256", "rows_in", "clean", "quarantined",
                                                  "reconciled", "quarantine_reasons", "health_score",
                                                  "cells_changed_total", "latest_per_key")}
                          for f_ in nl.get("files", [])]}
        meta["inputs_match_sources_sha256"] = not mism
        meta["inputs_sha256_mismatch"] = mism
        meta["sources"] = [{k: f_.get(k) for k in ("file", "dataset", "resource_id", "url", "retrieved_at", "bytes",
                                                    "sha256")} for f_ in src.get("files", [])]
        meta["licence"] = src.get("licence")
    meta["input_dir"] = os.path.relpath(in_dir, HERE)
    meta["input_is_recorded_download"] = os.path.abspath(in_dir) == os.path.abspath(DEFAULT_IN)
    meta["input_is_northledger_clean"] = "northledger_clean" in meta
    meta["python"] = sys.version.split()[0]
    meta["pandas"] = pd.__version__
    meta["numpy"] = np.__version__

    sc = scorecard(b, meta)
    cb = cube(b)
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):  # spurious Accelerate matmul warnings
        A = analysis_a(H, clean, b, latest)
    # numpy 2.0 + macOS Accelerate emits spurious divide/overflow warnings from matmul; every metric is
    # checked for finiteness inside analysis_b instead.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        B = analysis_b(H, clean, reg)
    Dd = analysis_d(b, clean)
    TR = trend(H)
    K = kpis(b, clean, as_of)
    health = H["health"]
    health["decisions"] = [
        {"id": "zero_sentinel", "status": "Provisional choice, to be confirmed",
         "decision": ("An item scored 0 (an area refused or blocked) is kept as a separate flag here, not scored as 0, in "
                      "pillar cells and model features; the City's own score counts it as zero points."),
         "evidence": ("Dataset notes define 0 as 'cannot be evaluated due to an obstruction or refusal'. Analysis A "
                      "shows the City's own proactive score counts it as 0 points; the cube carries both bases."),
         "options": ["code-as-0 (City basis)", "withhold (drop the item)", "separate flag (chosen for now, to be confirmed)"]},
        {"id": "not_applicable", "status": "applied",
         "decision": "'N/A' (and blank) item cells are structural not-applicable: excluded from numerator and denominator and not counted as missing data."},
        {"id": "latest_per_rsn", "status": "applied",
         "decision": "Scorecard and cube use each RSN's latest post-2023 evaluation by completion date (ties by _id)."},
        {"id": "year_label", "status": "applied",
         "decision": "The completion date, not the YEAR EVALUATED label, sets the evaluation year; label anomalies are flagged, not dropped."},
        {"id": "district", "status": "applied",
         "decision": "District = the City's Community Council for the ward (ward table), checked against the GRID first letter on every row."},
    ]
    kp = {"meta": meta, "kpis": K, "health": health,
          "crosswalk": {"columns": [{"canonical": c, "post2023": a, "pre2023": p_} for c, a, p_ in CROSSWALK],
                        "notes": CROSSWALK_NOTES,
                        "pre2023_categories_to_pillars": PRE_CATS,
                        "items": [{"column": c, "city_name": n_, "tier": t, "weight_pct": TIER_WEIGHT[t],
                                   "pillar": next(k for k, _, cs in PILLARS if c in cs)} for c, (n_, t) in ITEMS.items()],
                        "tier_source": TIER_SOURCE, "council_source": COUNCIL_SOURCE}}

    outs = {
        "rentsafe_meta.json": kp,
        "rentsafe_scorecard.json": sc,
        "rentsafe_cube.json": cb,
        "rentsafe_analysis_a_points_per_fix.json": A,
        "rentsafe_analysis_b_risk.json": B,
        "rentsafe_analysis_d_archetypes.json": Dd,
        "rentsafe_trend.json": TR,
    }
    wm = wards_map(in_dir)
    if wm is not None:
        outs["rentsafe_wards_map.json"] = wm
    for v in outs.values():
        if isinstance(v, dict) and "meta" not in v:
            v.setdefault("stage", ("stage 2: NorthLedger-cleaned frames (engine %s), then this script's domain rules"
                                   % meta["northledger_clean"]["engine"]["id"][:12]) if "northledger_clean" in meta
                         else "stage 1 preliminary: pandas/numpy on the raw CKAN files; stage 2 re-runs on NorthLedger-cleaned data")
            v.setdefault("data_as_of", as_of)
            v.setdefault("built_at", meta["built_at"])
    os.makedirs(out_dir, exist_ok=True)
    for name, obj in outs.items():
        tmp = os.path.join(out_dir, name + ".tmp")
        with open(tmp, "w") as fh:
            json.dump(obj, fh, separators=(",", ":"), allow_nan=False)
        os.replace(tmp, os.path.join(out_dir, name))
    meta["seconds"] = round((dt.datetime.now(dt.timezone.utc) - t0).total_seconds(), 2)
    return outs, {"b": b, "H": H, "clean": clean, "meta": meta}


def check(outs, ctx):
    """Invariant checks. Returns a list of failure strings (empty = pass)."""
    fails = []
    H, b = ctx["H"], ctx["b"]
    h = H["health"]
    for s, rec in h["reconciliation"].items():
        if rec["clean"] + rec["quarantined"] != rec["rows_in"]:
            fails.append(f"reconciliation {s}")
    if h["grid_rule"]["violations"] != 0:
        fails.append(f"GRID rule violated on {h['grid_rule']['violations']} rows")
    for d, letters in h["grid_rule"]["grid_first_letter_by_council"].items():
        if len(letters) != 1:
            fails.append(f"council {d} has GRID letters {letters}")
    if h["grid_rule"]["wards_outside_council_table"]:
        fails.append("wards outside council table")
    sc = outs["rentsafe_scorecard.json"]
    if len(sc["wards"]) != 25:
        fails.append(f"expected 25 wards, got {len(sc['wards'])}")
    if sum(w["n_buildings"] for w in sc["wards"]) != len(b):
        fails.append("ward building counts do not sum to latest panel")
    # cube re-aggregation equals scorecard
    cb = outs["rentsafe_cube.json"]
    df = pd.DataFrame(cb["rows"], columns=cb["fields"])
    for w in sc["wards"]:
        g = df[df.ward == w["ward"]]
        if int(g.n.sum()) != w["n_buildings"]:
            fails.append(f"cube n != scorecard for ward {w['ward']}")
        if abs(g.score_sum.sum() / g.n.sum() - w["overall_mean"]) > 0.006:
            fails.append(f"cube overall mean != scorecard for ward {w['ward']}")
        for pk in PILLAR_KEYS:
            if g[f"{pk}_n"].sum() and w[f"{pk}_mean"] is not None:
                if abs(g[f"{pk}_sum"].sum() / g[f"{pk}_n"].sum() - w[f"{pk}_mean"]) > 0.006:
                    fails.append(f"cube {pk} mean != scorecard for ward {w['ward']}")
    # band shares add to 100
    for w in sc["wards"]:
        if abs(w["pct_green"] + w["pct_yellow"] + w["pct_red"] - 100) > 0.05:
            fails.append(f"band shares != 100 for ward {w['ward']}")
    # city row avg-of-wards is the simple average of ward cells
    ov = np.mean([w["overall_mean"] for w in sc["wards"]])
    if abs(ov - sc["city"]["overall_mean__avg_of_wards"]) > 0.006:
        fails.append("city avg_of_wards != mean of ward cells")
    # pillar items cover all 50 exactly once
    allc = [c for _, _, cs in PILLARS for c in cs]
    if sorted(allc) != sorted(ITEMS) or len(allc) != 50:
        fails.append("pillar mapping does not cover the 50 items exactly once")
    tiers = [t for _, t in ITEMS.values()]
    if (tiers.count("High"), tiers.count("Moderate"), tiers.count("Cosmetic")) != (17, 23, 10):
        fails.append("tier counts differ from the City's 17/23/10")
    # the raw inputs must be the recorded downloads when the default input dir is used
    meta = ctx["meta"]
    if meta.get("input_is_recorded_download") and (meta.get("inputs_sha256_mismatch") or
                                                   "inputs_match_sources_sha256" not in meta):
        fails.append(f"inputs differ from SOURCES.json: {meta.get('inputs_sha256_mismatch', 'SOURCES.json missing')}")
    nlc = meta.get("northledger_clean")
    if nlc is not None:
        if meta.get("inputs_sha256_mismatch"):
            fails.append(f"NorthLedger input was cleaned from files that differ from SOURCES.json: {meta['inputs_sha256_mismatch']}")
        for f_ in nlc["files"]:
            if not f_.get("reconciled") or f_["clean"] + f_["quarantined"] != f_["rows_in"]:
                fails.append(f"NorthLedger rows_in != clean + quarantined for {f_['source_file']}")
    # every heatmap cell is on the 0-100 scale
    for w in sc["wards"]:
        for pk in PILLAR_KEYS + ["overall"]:
            v = w["overall_mean"] if pk == "overall" else w[f"{pk}_mean"]
            if v is None or not (0 <= v <= 100):
                fails.append(f"ward {w['ward']} {pk} cell out of range: {v}")
    # analysis A: the City rule must reproduce the published score on at least 99% of rows
    A = outs["rentsafe_analysis_a_points_per_fix.json"]
    if A["constrained_tests"][0]["exact_share"] < 0.99:
        fails.append(f"City formula reproduces only {A['constrained_tests'][0]['exact_share']} of scores")
    # analysis B: the gate verdict must follow from the metrics
    B = outs["rentsafe_analysis_b_risk.json"]
    tm = B["test_metrics"]
    want = "model" if (tm["brier_model"] < tm["brier_persistence_band"] and
                       tm["mae_score_model"] < tm["mae_score_persistence"]) else "persistence"
    if B["gate"]["champion"] != want:
        fails.append("analysis B gate verdict does not follow from its metrics")
    if B["gate"]["verdict_text"].startswith("The model was offered") != (want == "model"):
        fails.append("analysis B verdict sentence does not follow from its metrics")
    # no NaN leaked (json.dump allow_nan=False already enforces it)
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default=DEFAULT_IN)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="build into a temp dir and run invariant checks only")
    a = ap.parse_args()
    sources = os.path.join(DEFAULT_IN, "SOURCES.json")
    out_dir = tempfile.mkdtemp(prefix="rentsafe_check_") if a.check else a.out_dir
    outs, ctx = build(a.input_dir, out_dir, sources)
    fails = check(outs, ctx)
    for f_ in fails:
        print("FAIL", f_)
    if not fails:
        print("PASS rentsafe invariants (%d check groups)" % 14)
    meta = outs["rentsafe_meta.json"]["meta"]
    print(f"data as of {meta['data_as_of']}, built {meta['built_at']}, wrote {len(outs)} files to {out_dir}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
