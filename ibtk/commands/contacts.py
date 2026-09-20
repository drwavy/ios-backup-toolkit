"""Extract contacts from AddressBook.sqlitedb into CSV + vCard."""

import csv
import sqlite3
from pathlib import Path

PROPERTY_PHONE = 3
PROPERTY_EMAIL = 4


def run(backup, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    ab_path = backup.stage_known_db("addressbook.db")

    con = sqlite3.connect(str(ab_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT ROWID, First, Last, Organization FROM ABPerson")
    people = {row["ROWID"]: dict(row) for row in cur.fetchall()}

    cur.execute(
        "SELECT record_id, property, value FROM ABMultiValue WHERE value IS NOT NULL"
    )
    values = {}
    for row in cur.fetchall():
        values.setdefault(row["record_id"], {"phones": [], "emails": []})
        if row["property"] == PROPERTY_PHONE:
            values[row["record_id"]]["phones"].append(row["value"])
        elif row["property"] == PROPERTY_EMAIL:
            values[row["record_id"]]["emails"].append(row["value"])

    csv_path = out_dir / "contacts.csv"
    vcf_path = out_dir / "contacts.vcf"

    with open(csv_path, "w", newline="", encoding="utf-8") as cf, \
         open(vcf_path, "w", encoding="utf-8") as vf:
        writer = csv.writer(cf)
        writer.writerow(["First", "Last", "Organization", "Phones", "Emails"])

        for rowid, person in people.items():
            v = values.get(rowid, {"phones": [], "emails": []})
            name = " ".join(p for p in (person["First"], person["Last"]) if p) or person["Organization"] or "Unnamed"
            writer.writerow([person["First"] or "", person["Last"] or "",
                              person["Organization"] or "", "; ".join(v["phones"]), "; ".join(v["emails"])])

            vf.write("BEGIN:VCARD\nVERSION:3.0\n")
            vf.write(f"FN:{name}\n")
            if person["First"] or person["Last"]:
                vf.write(f"N:{person['Last'] or ''};{person['First'] or ''};;;\n")
            if person["Organization"]:
                vf.write(f"ORG:{person['Organization']}\n")
            for phone in v["phones"]:
                vf.write(f"TEL:{phone}\n")
            for email in v["emails"]:
                vf.write(f"EMAIL:{email}\n")
            vf.write("END:VCARD\n")

    con.close()
    print(f"Wrote {len(people)} contacts to {csv_path} and {vcf_path}")
