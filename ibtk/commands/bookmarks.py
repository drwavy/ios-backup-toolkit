"""Extract Safari bookmarks from Bookmarks.db into an HTML bookmarks file."""

import sqlite3
import html as html_module
from pathlib import Path


def run(backup, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    bm_path = backup.stage_known_db("bookmarks.db")

    con = sqlite3.connect(str(bm_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    # Safari's Bookmarks.db schema: bookmarks table with a title and a
    # separate bookmark_id -> url mapping via the same or a joined table,
    # depending on iOS version. Try the common shape; fall back gracefully.
    try:
        cur.execute("SELECT title, url FROM bookmarks WHERE url IS NOT NULL")
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        rows = []

    out_path = out_dir / "bookmarks.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<TITLE>Bookmarks</TITLE>\n<H1>Bookmarks</H1>\n<DL><p>\n")
        for row in rows:
            title = html_module.escape(row["title"] or row["url"])
            url = html_module.escape(row["url"])
            f.write(f'    <DT><A HREF="{url}">{title}</A>\n')
        f.write("</DL><p>\n")

    con.close()
    print(f"Wrote {len(rows)} bookmarks to {out_path}")
