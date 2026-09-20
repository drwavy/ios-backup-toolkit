"""Extract calendar events from Calendar.sqlitedb into CSV + ICS."""

import csv
import sqlite3
import datetime
from pathlib import Path

from ibtk.timestamps import apple_epoch_to_datetime

ICS_DT_FMT = "%Y%m%dT%H%M%S"


def run(backup, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    cal_path = backup.stage_known_db("calendar.db")

    con = sqlite3.connect(str(cal_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        "SELECT summary, start_date, end_date, location, description, all_day "
        "FROM CalendarItem"
    )
    rows = cur.fetchall()

    csv_path = out_dir / "calendar.csv"
    ics_path = out_dir / "calendar.ics"

    with open(csv_path, "w", newline="", encoding="utf-8") as cf, \
         open(ics_path, "w", encoding="utf-8") as icf:
        writer = csv.writer(cf)
        writer.writerow(["Summary", "Start", "End", "Location", "Description", "All Day"])
        icf.write("BEGIN:VCALENDAR\nVERSION:2.0\n")

        count = 0
        for row in rows:
            start = apple_epoch_to_datetime(row["start_date"])
            end = apple_epoch_to_datetime(row["end_date"])
            writer.writerow([
                row["summary"] or "", start or "", end or "",
                row["location"] or "", row["description"] or "", bool(row["all_day"]),
            ])
            if start:
                icf.write("BEGIN:VEVENT\n")
                icf.write(f"SUMMARY:{row['summary'] or ''}\n")
                icf.write(f"DTSTART:{start.strftime(ICS_DT_FMT)}\n")
                if end:
                    icf.write(f"DTEND:{end.strftime(ICS_DT_FMT)}\n")
                if row["location"]:
                    icf.write(f"LOCATION:{row['location']}\n")
                if row["description"]:
                    icf.write(f"DESCRIPTION:{row['description']}\n")
                icf.write("END:VEVENT\n")
                count += 1

        icf.write("END:VCALENDAR\n")

    con.close()
    print(f"Wrote {count} calendar events to {csv_path} and {ics_path}")
