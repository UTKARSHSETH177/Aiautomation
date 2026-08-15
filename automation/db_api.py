"""
Task 2 connector: a thin HTTP layer over merged.db so n8n can read/write it.

Why this exists: n8n has no native SQLite node. The standard pattern for
connecting a no-code tool to a local datastore is a small API "connector".
ALL automation logic (trigger, batching, LLM calls, branching, alerting)
lives in the n8n flow — this file only moves rows in and out of SQLite.

Run from the repo root:  python automation/db_api.py
"""
import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, jsonify, request

DB_PATH = os.environ.get("MERGED_DB", "merged.db")
VALID_CATEGORIES = {"automation-heavy", "web-dev", "backend",
                    "data", "mixed", "unknown"}

app = Flask(__name__)


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def ensure_columns():
    """Idempotent migration. pipeline.py rebuilds merged.db from scratch on
    each run, so the enrichment columns are (re)added here at API startup
    instead of editing the Task 1 schema."""
    con = db()
    for col, typ in [("skill_category", "TEXT"),
                     ("tag_confidence", "REAL"),
                     ("tagged_at", "TEXT"),
                     ("tagged_by", "TEXT")]:
        try:
            con.execute(f"ALTER TABLE persons ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # column already exists — fine
    con.commit()
    con.close()


@app.get("/health")
def health():
    return jsonify(ok=True, db=DB_PATH)


@app.get("/people/untagged")
def untagged():
    """People with no skill_category yet, plus everything the LLM needs.
    Skills come from BOTH source tables (a person can have naukri
    applications and gig profiles)."""
    limit = min(int(request.args.get("limit", 100)), 500)
    con = db()
    rows = con.execute(
        """
        SELECT p.person_id, p.full_name, p.city,
               (SELECT group_concat(a.skills, ' | ') FROM applications a
                 WHERE a.person_id = p.person_id)      AS naukri_skills,
               (SELECT group_concat(g.skill_tags, ' | ') FROM gig_profiles g
                 WHERE g.person_id = p.person_id)      AS gig_skills,
               (SELECT max(a.experience_years) FROM applications a
                 WHERE a.person_id = p.person_id)      AS experience_years
        FROM persons p
        WHERE p.skill_category IS NULL
        ORDER BY p.person_id
        LIMIT ?
        """, (limit,)).fetchall()
    con.close()
    return jsonify(count=len(rows), people=[dict(r) for r in rows])


@app.post("/people/tags")
def write_tags():
    """Body: JSON array of {person_id, skill_category, confidence}.
    Whitelist enforced server-side too — never trust LLM output blindly."""
    updates = request.get_json(force=True, silent=True)
    if not isinstance(updates, list):
        return jsonify(error="body must be a JSON array"), 400
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated, skipped = 0, []
    con = db()
    for u in updates:
        pid = u.get("person_id")
        cat = (u.get("skill_category") or "").strip().lower()
        conf = u.get("confidence")
        if not isinstance(pid, int) or cat not in VALID_CATEGORIES:
            skipped.append(u)
            continue
        cur = con.execute(
            "UPDATE persons SET skill_category=?, tag_confidence=?, "
            "tagged_at=?, tagged_by=? WHERE person_id=?",
            (cat, conf if isinstance(conf, (int, float)) else None,
             now, "n8n+gemini", pid))
        updated += cur.rowcount
    con.commit()
    con.close()
    return jsonify(updated=updated, skipped=skipped)


@app.get("/people/stats")
def stats():
    con = db()
    total = con.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    left = con.execute("SELECT COUNT(*) FROM persons "
                       "WHERE skill_category IS NULL").fetchone()[0]
    by_cat = {r["skill_category"]: r["n"] for r in con.execute(
        "SELECT skill_category, COUNT(*) AS n FROM persons "
        "WHERE skill_category IS NOT NULL GROUP BY skill_category "
        "ORDER BY n DESC")}
    con.close()
    return jsonify(total=total, tagged=total - left,
                   untagged=left, by_category=by_cat)


if __name__ == "__main__":
    ensure_columns()
    app.run(host="0.0.0.0", port=5001, debug=False)
