"""
Task 4 — generate the data-issues report FROM the pipeline's own log.

Reads merged.db's data_issues table, groups entries into human categories,
writes DATA_ISSUES.md, and splices the same block into README.md between
<!-- DATA_ISSUES:START --> and <!-- DATA_ISSUES:END --> markers.
Rerun after every pipeline change:  python generate_issues_report.py
"""
import re
import sqlite3
from pathlib import Path

from project_paths import DB_PATH, PROJECT_ROOT

DB = DB_PATH
OUT = PROJECT_ROOT / "docs" / "DATA_ISSUES.md"
README = PROJECT_ROOT / "README.md"
MS, ME = "<!-- DATA_ISSUES:START -->", "<!-- DATA_ISSUES:END -->"

# (regex on issue text, category title, one-line handling summary)
# Order matters: first match wins, so specific patterns sit above generic ones.
CATEGORIES = [
    (r"appears to be in lakhs",
     "Unit ambiguity — CTC in lakhs vs absolute rupees",
     "Rule: value < 100 ⇒ lakhs × 100,000. No plausible annual CTC is under ₹100."),
    (r"monthly rate",
     "Unit ambiguity — gig rate '/hr' vs 'k/month'",
     "Normalized to ₹/hr assuming 160 working hours/month; the assumption is explicit and reversible."),
    (r"ambiguous slash date",
     "Date chaos — ambiguous MM/DD vs DD/MM",
     "Slash dates parsed MM/DD/YYYY because the file contains 07/13/2026 and 08/19/2026, impossible as DD/MM; every day≤12 case flagged."),
    (r"in the future",
     "Plausibility — applied_date in the future",
     "Stored as-is and flagged; the pipeline never invents data."),
    (r"exact duplicate gig profile",
     "Duplicates — disguised duplicate hidden behind a structural repair",
     "The column-shifted row, once repaired, exactly duplicated an earlier profile — dropped."),
    (r"exact duplicate row|duplicate person within",
     "Duplicates — repeated rows / repeat applications",
     "Exact dupes dropped; same-person re-applications attach to ONE person."),
    (r"second email|second phone",
     "Identity — multiple identifiers for one person",
     "First identifier kept canonical; alternates preserved in match_notes."),
    (r"names disagree",
     "Identity — name disagreement on a hard match",
     "Merged on the identifier (email/phone beats spelling); disagreement flagged."),
    (r"not merged",
     "Identity — same name+city but conflicting hard identifiers",
     "Name+city NEVER overrides an email/phone conflict; kept separate, flagged."),
    (r"fuzzy name\+city",
     "Identity — low-confidence fuzzy merges",
     "Merged only with city agreement and zero identifier conflicts; every one flagged for review."),
    (r"city conflict",
     "Identity — city disagreement between sources",
     "First-seen city kept; conflict recorded on the person."),
    (r"delhi ncr|region",
     "Geography — region recorded as a city",
     "'Delhi NCR' is a region, not a city; mapped to Delhi with a per-row log."),
    (r"empty row",
     "Structural — empty rows", "Dropped."),
    (r"column-shifted",
     "Structural — column-shifted row",
     "Detected because the email sat in the wrong column; repaired by rotation."),
    (r"repeated header",
     "Structural — header row repeated mid-file", "Dropped."),
    (r"no email anywhere",
     "Structural — unrepairable rows", "Quarantined; never guessed into the DB."),
    (r"unparseable|unmapped",
     "Parse failures — invalid values",
     "Stored NULL and logged; originals recoverable from the source files."),
]


def bucket(issue):
    low = issue.lower()
    for pat, title, blurb in CATEGORIES:
        if re.search(pat, low):
            return title, blurb
    return "Other", ""


def main():
    if not DB.is_file():
        raise RuntimeError(f"{DB} does not exist — run 'python pipeline.py' first")
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT source, row_ref, issue, action "
                       "FROM data_issues ORDER BY issue_id").fetchall()
    con.close()

    groups, order = {}, []
    for src, ref, issue, action in rows:
        title, blurb = bucket(issue)
        if title not in groups:
            groups[title] = {"blurb": blurb, "rows": []}
            order.append(title)
        groups[title]["rows"].append((src, ref, issue, action))

    ln = []
    ln.append("### Data issues — generated from the pipeline's own "
              "`data_issues` log")
    ln.append("")
    ln.append(f"**{len(rows)} logged issues across {len(order)} categories.** "
              "Produced by `generate_issues_report.py` from `merged.db`, so "
              "this section always reflects what the pipeline actually did — "
              "not hand-waving.")
    ln.append("")
    ln.append("| # | Category | Count | Handling |")
    ln.append("|---|----------|-------|----------|")
    for n, t in enumerate(order, 1):
        ln.append(f"| {n} | {t} | {len(groups[t]['rows'])} | "
                  f"{groups[t]['blurb']} |")
    ln.append("")
    for t in order:
        g = groups[t]
        ln.append(f"#### {t} ({len(g['rows'])})")
        ln.append("")
        for src, ref, issue, action in g["rows"][:8]:
            ln.append(f"- `{src}` {ref}: {issue} → *{action}*")
        if len(g["rows"]) > 8:
            ln.append(f"- …and {len(g['rows']) - 8} more of the same pattern")
        ln.append("")
    block = "\n".join(ln).rstrip() + "\n"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(block, encoding="utf-8")
    print(f"wrote {OUT}: {len(rows)} issues, {len(order)} categories")

    rp = Path(README)
    if rp.exists():
        txt = rp.read_text(encoding="utf-8")
        if MS in txt and ME in txt:
            pre, rest = txt.split(MS, 1)
            _, post = rest.split(ME, 1)
            rp.write_text(pre + MS + "\n" + block + ME + post,
                          encoding="utf-8")
            print(f"spliced report into {README}")
        else:
            print(f"add {MS} and {ME} to {README} where the report "
                  "should live, then rerun")


if __name__ == "__main__":
    main()
