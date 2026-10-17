#!/usr/bin/env python3
"""Write the tracker demo's two files: SYNTHETIC messy task lists for "Build a tracker" (Track G,
plan/DASHBOARD-DESIGN.md; engine/nl_tracker.py, CONTRACT-v2 section 5.18).

    python tools/make_sample_tracker.py           # (re)write both files
    python tools/make_sample_tracker.py --check   # exit 1 if a file on disk differs

SYNTHETIC: every task, team and date below is invented and written by hand in this script. No company, person or
building is in it. The file names say so, the page's button says so, and so does this docstring.

The two files are what a small property-operations team might track tasks in today:
  * engine/sample-tracker-tasks-a-synthetic.csv: the old spreadsheet. Task ID, Task, Status, Type, Team,
    Date logged, Due, Completed, Est hours; dates day first (31/01/2026); status written five ways (done, Done ,
    DONE, complete ...); one exact duplicate row; the header row repeated in the middle (two sheets pasted together);
    an unreadable due date ("next tue"), an unreadable number ("2 hrs"), a blank status, a "done" task with no
    completed date, a task completed before it was logged.
  * engine/sample-tracker-tasks-b-synthetic.csv: the newer inbox export. ID, Title, State, Category, Team, Opened,
    Due date, Closed, Notes; dates ISO (one written 2026/03/31); states New / WIP / Waiting / Resolved / Closed;
    categories in lower case; one exact duplicate row; a note line that is not a task; a blank state and a blank
    category.

The planted facts tools/test_nl_tracker.py checks (with pandas, independently of the engine):
  * the files overlap on T-115 to T-125: T-115, T-116, T-118, T-122, T-123 and T-124 are the same task in both files
    once cleaned (status words mapped, categories in one spelling) and are merged into file A's copy;
    T-117, T-119 and T-125 changed between the exports (status, closed date or a note) and are NOT merged: they are
    flagged as the same ID in both files;
  * one exact duplicate inside each file (T-110 in A, T-129 in B), merged by the cleaner;
  * two rows that are not tasks (A's repeated header, B's note line) go to the Unplaced sheet;
  * nothing is filled: T-126 is "done" with no completed date and stays blank, flagged; T-121 and T-133 have no
    status and stay blank, flagged.

Deterministic: no clock, no randomness. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_A = os.path.join(HERE, "..", "engine", "sample-tracker-tasks-a-synthetic.csv")
OUT_B = os.path.join(HERE, "..", "engine", "sample-tracker-tasks-b-synthetic.csv")
AS_OF = "2026-04-15"          # the demo's analysis date (build.py TRY_SAMPLE_TRACKER_AS_OF)

HEAD_A = ["Task ID", "Task", "Status", "Type", "Team", "Date logged", "Due", "Completed", "Est hours"]
ROWS_A = [
    ["T-101", "Replace lobby light fittings", "done", "Maintenance", "Facilities", "05/01/2026", "12/01/2026", "10/01/2026", "3"],
    ["T-102", "Renew fire extinguisher service", "Done ", "Safety", "Facilities", "06/01/2026", "31/01/2026", "28/01/2026", "2"],
    ["T-103", "Update tenant handbook", "in progress", "Admin", "Front desk", "08/01/2026", "15/02/2026", "", "6"],
    ["T-104", "Fix leaking tap unit 4B", "complete", "Maintenance", "Facilities", "13/01/2026", "16/01/2026", "15/01/2026", "1.5"],
    ["T-105", "Order replacement keys", "open", "Admin", "Front desk", "14/01/2026", "21/01/2026", "", "0.5"],
    ["T-106", "Repaint stairwell B", "On Hold", "Maintenance", "Contractors", "20/01/2026", "28/02/2026", "", "16"],
    ["T-107", "Quarterly boiler inspection", "DONE", "Safety", "Contractors", "22/01/2026", "05/02/2026", "04/02/2026", "4"],
    ["T-108", "Clear gutters", "open", "Maintenance", "Facilities", "27/01/2026", "N/A", "", "2"],
    ["T-109", "Reconcile petty cash", "In Progress", "Finance", "Front desk", "02/02/2026", "10/02/2026", "", "1"],
    ["T-110", "Install bike rack", "done", "Maintenance", "Contractors", "03/02/2026", "20/02/2026", "18/02/2026", "5"],
    ["T-110", "Install bike rack", "done", "Maintenance", "Contractors", "03/02/2026", "20/02/2026", "18/02/2026", "5"],
    ["T-111", "Test smoke alarms", "Done", "Safety", "Facilities", "09/02/2026", "13/02/2026", "12/02/2026", "3"],
    ["T-112", "Chase overdue invoices", "open", "Finance", "Front desk", "10/02/2026", "17/02/2026", "", "2 hrs"],
    ["T-113", "Replace broken window unit 2A", "in progress", "Maintenance", "Contractors", "16/02/2026", "23/02/2026", "", "4"],
    ["T-114", "Book elevator service", "open", "Safety", "Contractors", "18/02/2026", "next tue", "", "1"],
    list(HEAD_A),                                     # the header again: a second sheet pasted under the first
    ["T-115", "Order cleaning supplies", "done", "Admin", "Front desk", "23/02/2026", "25/02/2026", "24/02/2026", "0.5"],
    ["T-116", "Fix parking gate sensor", "open", "Maintenance", "Contractors", "25/02/2026", "06/03/2026", "", "3"],
    ["T-117", "Annual insurance review", "In progress", "Finance", "Front desk", "02/03/2026", "31/03/2026", "", "8"],
    ["T-118", "Snow clearing contract renewal", "done", "Admin", "Front desk", "03/03/2026", "10/03/2026", "02/03/2026", "1"],
    ["T-119", "Inspect roof membrane", "open", "Maintenance", "Contractors", "05/03/2026", "20/03/2026", "", "6"],
    ["T-120", "Replace hallway carpet", "cancelled", "Maintenance", "Contractors", "09/03/2026", "30/04/2026", "", "20"],
    ["T-121", "Update emergency contact list", "", "Admin", "Front desk", "11/03/2026", "18/03/2026", "", "1"],
    ["T-122", "  Service HVAC filters ", "Done", "Maintenance", "Facilities", "12/03/2026", "19/03/2026", "17/03/2026", "2"],
    ["T-123", "Review cleaning rota", "open", "Admin", "Facilities", "16/03/2026", "23/03/2026", "", "1"],
    ["T-124", "Fix door closer unit 7C", "done", "Maintenance", "Facilities", "17/03/2026", "20/03/2026", "22/03/2026", "1"],
    ["T-125", "Audit fire exits", "open", "Safety", "Facilities", "19/03/2026", "26/03/2026", "", "2"],
    ["T-126", "Order new mailbox locks", "done", "Admin", "Front desk", "24/03/2026", "31/03/2026", "", "1"],
]

HEAD_B = ["ID", "Title", "State", "Category", "Team", "Opened", "Due date", "Closed", "Notes"]
ROWS_B = [
    ["T-115", "Order cleaning supplies", "Closed", "admin", "Front desk", "2026-02-23", "2026-02-25", "2026-02-24", ""],
    ["T-116", "Fix parking gate sensor", "New", "maintenance", "Contractors", "2026-02-25", "2026-03-06", "", ""],
    ["T-117", "Annual insurance review", "Closed", "finance", "Front desk", "2026-03-02", "2026-03-31", "2026-04-02", "Signed renewal"],
    ["T-118", "Snow clearing contract renewal", "Resolved", "admin", "Front desk", "2026-03-03", "2026-03-10", "2026-03-02", ""],
    ["T-119", "Inspect roof membrane", "WIP", "maintenance", "Contractors", "2026-03-05", "2026-03-20", "", "Awaiting quote"],
    ["T-122", "Service HVAC filters", "Closed", "maintenance", "Facilities", "2026-03-12", "2026-03-19", "2026-03-17", ""],
    ["T-123", "Review cleaning rota", "New", "admin", "Facilities", "2026-03-16", "2026-03-23", "", ""],
    ["T-124", "Fix door closer unit 7C", "Closed", "maintenance", "Facilities", "2026-03-17", "2026-03-20", "2026-03-22", ""],
    ["T-125", "Audit fire exits", "Waiting", "safety", "Facilities", "2026-03-19", "2026-03-26", "", ""],
    ["T-127", "Replace entry intercom", "New", "maintenance", "Contractors", "2026-03-25", "2026-04-08", "", ""],
    ["T-128", "Prepare AGM pack", "WIP", "admin", "Front desk", "2026-03-26", "2026-04-10", "", ""],
    ["T-129", "Pest control visit", "Closed", "safety", "Contractors", "2026-03-27", "2026-04-03", "2026-04-01", ""],
    ["T-129", "Pest control visit", "Closed", "safety", "Contractors", "2026-03-27", "2026-04-03", "2026-04-01", ""],
    ["T-130", "Reconcile March utilities", "New", "finance", "Front desk", "2026/03/31", "2026-04-07", "", ""],
    ["", "Rows below added by the night shift", "", "", "", "", "", "", ""],
    ["T-131", "Patch drywall unit 3D", "Resolved", "maintenance", "Facilities", "2026-04-01", "2026-04-06", "2026-04-03", ""],
    ["T-132", "Renew lift certificate", "Waiting", "safety", "Contractors", "2026-04-02", "2026-04-30", "", ""],
    ["T-133", "Fix lobby door buzzer", "", "maintenance", "Facilities", "2026-04-06", "2026-04-09", "", ""],
    ["T-134", "Order replacement bulbs", "New", "", "Front desk", "2026-04-08", "2026-04-15", "", ""],
]


def text(head, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(head)
    w.writerows(rows)
    return buf.getvalue()


FILES = [(OUT_A, HEAD_A, ROWS_A), (OUT_B, HEAD_B, ROWS_B)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if a file on disk differs")
    a = ap.parse_args()
    bad = 0
    for path, head, rows in FILES:
        t = text(head, rows)
        name = os.path.basename(path)
        if a.check:
            try:
                with open(path, encoding="utf-8") as f:
                    same = f.read() == t
            except OSError:
                same = False
            print("%s: %s" % (name, "up to date" if same else "DIFFERS (run tools/make_sample_tracker.py)"))
            bad += 0 if same else 1
            continue
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(t)
        print("wrote %s: %d rows, %d bytes (SYNTHETIC)" % (os.path.relpath(path), len(rows), len(t.encode("utf-8"))))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
