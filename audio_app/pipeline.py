"""
ConsultBae Task 1 — merge 3 messy CSVs into one clean SQLite database.

Design decisions (defend these):
  * SQLite: zero setup, single file ships with the repo.
  * One `persons` table holds canonical identity; each source keeps its own
    table (applications / gig_profiles / nexus_contacts) FK'd to persons.
    This preserves source-specific fields and conflicting values instead of
    destroying them in a single wide row.
  * Matching priority (strongest evidence first):
        1. normalized email   (exact)
        2. normalized phone   (exact)
        3. fuzzy name + same city  -> flagged low-confidence, never silent
    Name alone is NEVER enough: the data has many shared names (3x "Isha",
    2x "Nikhil Chopra") that are different people.
  * Every anomaly found is written to a `data_issues` table -> Task 4 report
    is generated from real pipeline output, not hand-waving.

Stdlib only (csv, sqlite3, re, difflib) so it runs anywhere.
"""

import csv
import re
import sqlite3
import sys
from datetime import datetime
from difflib import SequenceMatcher

DB_PATH = "merged.db"
ISSUES = []  # collected as we go, written to data_issues at the end


def log_issue(source, row_ref, issue, action):
    ISSUES.append((source, str(row_ref), issue, action))


# ---------------------------------------------------------------- normalizers

def norm_phone(raw):
    """Strip to a bare 10-digit Indian mobile. Handles +91 / 91 / 0 prefixes
    and separators. Returns None if what's left isn't 10 digits."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def norm_email(raw):
    e = (raw or "").strip().lower()
    return e if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e) else None


def norm_name(raw):
    return re.sub(r"\s+", " ", (raw or "").strip()).title() or None


# City alias map: collapse spelling/casing/synonym variants to one canonical
# form. "Delhi NCR" is a region, not a city — mapped to Delhi and logged.
CITY_ALIASES = {
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
    "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "new delhi": "Delhi", "delhi": "Delhi", "delhi ncr": "Delhi",
    "noida": "Noida", "pune": "Pune",
}


def norm_city(raw):
    key = re.sub(r"\s+", " ", (raw or "").strip()).lower()
    return CITY_ALIASES.get(key)


def parse_date(raw):
    """s1 mixes 5 formats. Slash-dates are treated as MM/DD/YYYY because the
    file contains 07/13/2026 and 08/19/2026, which are impossible as DD/MM.
    Ambiguous cases like 07/03/2026 are logged so the assumption is visible."""
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            d = datetime.strptime(raw, fmt)
            if fmt == "%m/%d/%Y" and d.day <= 12:
                log_issue("source1", raw, "ambiguous slash date (day<=12)",
                          "parsed as MM/DD/YYYY per file-level evidence")
            return d.date().isoformat()
        except ValueError:
            continue
    return None


def parse_ctc(raw):
    """s1 mixes absolute rupees (417964) and lakhs (4.2). Rule: values under
    100 are lakhs -> x100000. No plausible annual CTC is under Rs 100."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return round(v * 100000) if v < 100 else round(v)


def parse_rate(raw):
    """s2 mixes '1415/hr' and '28k/month'. Normalize everything to Rs/hour
    assuming 160 working hours/month for the monthly quotes."""
    raw = (raw or "").strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*/\s*hr$", raw)
    if m:
        return round(float(m.group(1)), 2)
    m = re.match(r"^(\d+(?:\.\d+)?)k\s*/\s*month$", raw)
    if m:
        return round(float(m.group(1)) * 1000 / 160, 2)
    return None


def parse_verified(raw):
    v = (raw or "").strip().lower()
    if v in ("y", "yes", "verified"):
        return 1
    if v in ("n", "no"):
        return 0
    return None


def norm_skills(raw):
    """Lowercase, trim, dedupe, stable order -> stored as comma-joined."""
    seen, out = set(), []
    for s in (raw or "").split(","):
        s = re.sub(r"\s+", " ", s.strip().lower())
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return ", ".join(out) or None


# ---------------------------------------------------------------- fuzzy names

def names_match(a, b):
    """True for near-identical names OR initial-style abbreviations
    ('R. Verma' vs 'Rohit Verma'). Used only WITH a city agreement check."""
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    if SequenceMatcher(None, a, b).ratio() >= 0.90:
        return True
    ta, tb = a.replace(".", "").split(), b.replace(".", "").split()
    if len(ta) == len(tb) and ta[-1] == tb[-1]:
        return all(x == y or (len(x) == 1 and y.startswith(x))
                   or (len(y) == 1 and x.startswith(y))
                   for x, y in zip(ta, tb))
    return False


# ---------------------------------------------------------------- load/repair

def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def load_source2(path):
    """Repairs: drop empty rows, fix the left-rotated row (detected when the
    email sits in the wrong column)."""
    header, *rows = read_csv(path)
    fixed = []
    for i, r in enumerate(rows, start=2):  # 1-based incl. header
        if not any(c.strip() for c in r):
            log_issue("source2", f"line {i}", "completely empty row", "dropped")
            continue
        if "@" not in r[0]:
            email_pos = next((j for j, c in enumerate(r) if "@" in c), None)
            if email_pos is not None:
                r = r[email_pos:] + r[:email_pos]  # undo the rotation
                log_issue("source2", f"line {i}",
                          "column-shifted row (email found in wrong column)",
                          "repaired by rotating columns back into place")
            else:
                log_issue("source2", f"line {i}", "no email anywhere in row",
                          "quarantined (not ingested)")
                continue
        fixed.append(dict(zip(header, r)))
    return fixed


def load_source3(path):
    header, *rows = read_csv(path)
    out = []
    for i, r in enumerate(rows, start=2):
        if r == header:
            log_issue("source3", f"line {i}", "repeated header row inside data",
                      "dropped")
            continue
        out.append(dict(zip(header, r)))
    return out


def load_source1(path):
    header, *rows = read_csv(path)
    return [dict(zip(header, r)) for i, r in enumerate(rows, start=2)]


# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE persons (
    person_id     INTEGER PRIMARY KEY,
    full_name     TEXT NOT NULL,
    email         TEXT UNIQUE,
    phone         TEXT UNIQUE,
    city          TEXT,
    sources       TEXT NOT NULL,          -- e.g. 'naukri,gig,cbnexus'
    match_notes   TEXT                    -- how cross-file merges happened
);
CREATE TABLE applications (               -- source1: naukri
    application_id INTEGER PRIMARY KEY,
    person_id      INTEGER NOT NULL REFERENCES persons(person_id),
    experience_years REAL,
    current_ctc_inr  INTEGER,
    applied_date     TEXT,                -- ISO
    skills           TEXT
);
CREATE TABLE gig_profiles (               -- source2
    gig_id        INTEGER PRIMARY KEY,
    person_id     INTEGER NOT NULL REFERENCES persons(person_id),
    rate_inr_per_hour REAL,
    status        TEXT,                   -- normalized lowercase
    skill_tags    TEXT
);
CREATE TABLE nexus_contacts (             -- source3
    contact_id    INTEGER PRIMARY KEY,
    person_id     INTEGER NOT NULL REFERENCES persons(person_id),
    verified      INTEGER,                -- 1/0/NULL
    projects_completed INTEGER
);
CREATE TABLE data_issues (
    issue_id  INTEGER PRIMARY KEY,
    source    TEXT, row_ref TEXT, issue TEXT, action TEXT
);
"""


class PersonIndex:
    """In-memory identity resolution. Wraps person upserts + match logging."""

    def __init__(self, con):
        self.con = con
        self.by_email = {}
        self.by_phone = {}
        self.people = {}  # person_id -> dict

    def _new(self, name, email, phone, city, source):
        cur = self.con.execute(
            "INSERT INTO persons (full_name,email,phone,city,sources) "
            "VALUES (?,?,?,?,?)", (name, email, phone, city, source))
        pid = cur.lastrowid
        self.people[pid] = dict(name=name, email=email, phone=phone,
                                city=city, sources={source}, notes=[])
        if email:
            self.by_email[email] = pid
        if phone:
            self.by_phone[phone] = pid
        return pid

    def _merge_into(self, pid, name, email, phone, city, source, how):
        p = self.people[pid]
        p["sources"].add(source)
        p["notes"].append(how)
        # fill gaps, never overwrite non-null canonical values; conflicts logged
        if email and not p["email"]:
            p["email"] = email
            self.by_email[email] = pid
        elif email and p["email"] and email != p["email"]:
            self.by_email[email] = pid  # both emails resolve to this person
            log_issue(source, name or "?",
                      f"second email '{email}' for person with '{p['email']}'",
                      "kept first as canonical, alt noted on person")
            p["notes"].append(f"alt email {email}")
        if phone and not p["phone"]:
            p["phone"] = phone
            self.by_phone[phone] = pid
        elif phone and p["phone"] and phone != p["phone"]:
            self.by_phone[phone] = pid
            log_issue(source, name or "?",
                      f"second phone '{phone}' for person with '{p['phone']}'",
                      "kept first as canonical, alt noted on person")
            p["notes"].append(f"alt phone {phone}")
        if city and not p["city"]:
            p["city"] = city
        elif city and p["city"] and city != p["city"]:
            log_issue(source, name or "?",
                      f"city conflict: '{city}' vs existing '{p['city']}'",
                      "kept first-seen city, conflict noted on person")
            p["notes"].append(f"city conflict {p['city']}|{city}")
        # prefer the longer (unabbreviated) name form
        if name and len(name) > len(p["name"]):
            p["name"] = name
        return pid

    def resolve(self, name, email, phone, city, source):
        """Email -> phone -> fuzzy(name)+city. Returns (person_id, matched_by)."""
        if email and email in self.by_email:
            return self._merge_into(self.by_email[email], name, email, phone,
                                    city, source, f"email match ({source})"), "email"
        if phone and phone in self.by_phone:
            return self._merge_into(self.by_phone[phone], name, email, phone,
                                    city, source, f"phone match ({source})"), "phone"
        for pid, p in self.people.items():
            if names_match(name, p["name"]) and city and p["city"] == city:
                # Hard-identifier conflict => provably risky. The data contains
                # two distinct 'Arjun Mehta's in Noida with different phones,
                # so name+city must NEVER override conflicting email/phone.
                if (email and p["email"] and email != p["email"]) or \
                   (phone and p["phone"] and phone != p["phone"]):
                    log_issue(source, name,
                              f"same name+city as person {pid} but conflicting "
                              f"email/phone", "NOT merged — kept as separate "
                              "person, flagged for review")
                    continue
                log_issue(source, name,
                          f"fuzzy name+city match to person {pid} ('{p['name']}', {city})",
                          "merged LOW CONFIDENCE — review")
                return self._merge_into(pid, name, email, phone, city, source,
                                        f"FUZZY name+city ({source})"), "fuzzy"
        return self._new(name, email, phone, city, source), "new"

    def flush(self):
        for pid, p in self.people.items():
            self.con.execute(
                "UPDATE persons SET full_name=?, email=?, phone=?, city=?, "
                "sources=?, match_notes=? WHERE person_id=?",
                (p["name"], p["email"], p["phone"], p["city"],
                 ",".join(sorted(p["sources"])),
                 "; ".join(p["notes"]) or None, pid))


# ---------------------------------------------------------------- ingest

def ingest(s1_path, s2_path, s3_path):
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    idx = PersonIndex(con)

    # ---- source1 first: it's the only file with BOTH email and phone,
    # so it builds the richest identity index for the other two to hit.
    seen_rows = set()
    for i, r in enumerate(load_source1(s1_path), start=2):
        key = tuple(v.strip().lower() for v in r.values())
        if key in seen_rows:
            log_issue("source1", f"line {i} ({r.get('Full Name')})",
                      "exact duplicate row", "dropped")
            continue
        seen_rows.add(key)

        name = norm_name(r.get("Full Name"))
        email = norm_email(r.get("Email"))
        phone = norm_phone(r.get("Phone"))
        city = norm_city(r.get("City"))
        for field, val, parsed in (("Email", r.get("Email"), email),
                                   ("Phone", r.get("Phone"), phone),
                                   ("City", r.get("City"), city)):
            if (val or "").strip() and parsed is None:
                log_issue("source1", f"line {i} ({name})",
                          f"unparseable {field}: '{val}'", "stored as NULL")

        raw_ctc = r.get("Current CTC", "")
        ctc = parse_ctc(raw_ctc)
        try:
            if float(raw_ctc) < 100:
                log_issue("source1", f"line {i} ({name})",
                          f"CTC '{raw_ctc}' appears to be in lakhs",
                          f"converted to Rs {ctc}")
        except (TypeError, ValueError):
            pass

        date = parse_date(r.get("Applied Date"))
        if date is None and (r.get("Applied Date") or "").strip():
            log_issue("source1", f"line {i} ({name})",
                      f"unparseable date '{r.get('Applied Date')}'", "stored NULL")

        pid, how = idx.resolve(name, email, phone, city, "naukri")
        if how != "new":
            log_issue("source1", f"line {i} ({name})",
                      f"duplicate person within source1 (matched by {how})",
                      "second application attached to same person")
        con.execute(
            "INSERT INTO applications (person_id, experience_years, "
            "current_ctc_inr, applied_date, skills) VALUES (?,?,?,?,?)",
            (pid, float(r["Experience (Years)"]) if r.get("Experience (Years)") else None,
             ctc, date, norm_skills(r.get("Skills"))))

    # ---- source2: email is the join key to source1.
    for i, r in enumerate(load_source2(s2_path), start=2):
        name = norm_name(r.get("worker_name"))
        email = norm_email(r.get("email_id"))
        city = norm_city(r.get("location"))
        rate = parse_rate(r.get("rate"))
        if rate is None and (r.get("rate") or "").strip():
            log_issue("source2", f"line {i} ({name})",
                      f"unparseable rate '{r.get('rate')}'", "stored NULL")
        elif "month" in (r.get("rate") or ""):
            log_issue("source2", f"line {i} ({name})",
                      f"monthly rate '{r.get('rate')}'",
                      f"converted to Rs {rate}/hr assuming 160 h/month")
        pid, how = idx.resolve(name, email, None, city, "gig")
        con.execute(
            "INSERT INTO gig_profiles (person_id, rate_inr_per_hour, status, "
            "skill_tags) VALUES (?,?,?,?)",
            (pid, rate, (r.get("status") or "").strip().lower() or None,
             norm_skills(r.get("skill_tags"))))

    # ---- source3: phone is the join key to source1.
    for i, r in enumerate(load_source3(s3_path), start=2):
        name = norm_name(r.get("Name"))
        phone = norm_phone(r.get("Phone Number"))
        city = norm_city(r.get("City"))
        verified = parse_verified(r.get("Verified"))
        if verified is None and (r.get("Verified") or "").strip():
            log_issue("source3", f"line {i} ({name})",
                      f"unmapped Verified value '{r.get('Verified')}'", "stored NULL")
        try:
            projects = int(r.get("Projects Completed"))
        except (TypeError, ValueError):
            projects = None
        pid, how = idx.resolve(name, None, phone, city, "cbnexus")
        con.execute(
            "INSERT INTO nexus_contacts (person_id, verified, "
            "projects_completed) VALUES (?,?,?)", (pid, verified, projects))

    idx.flush()
    con.executemany(
        "INSERT INTO data_issues (source,row_ref,issue,action) VALUES (?,?,?,?)",
        ISSUES)
    con.commit()

    # ---- summary
    q = lambda sql: con.execute(sql).fetchone()[0]
    print(f"persons:          {q('SELECT COUNT(*) FROM persons')}")
    all3 = q("SELECT COUNT(*) FROM persons WHERE sources='cbnexus,gig,naukri'")
    print(f"  in all 3 files: {all3}")
    print(f"applications:     {q('SELECT COUNT(*) FROM applications')}")
    print(f"gig_profiles:     {q('SELECT COUNT(*) FROM gig_profiles')}")
    print(f"nexus_contacts:   {q('SELECT COUNT(*) FROM nexus_contacts')}")
    print(f"data issues:      {q('SELECT COUNT(*) FROM data_issues')}")
    con.close()

    import urllib.request
    try:  # fire-and-forget: pipeline must not fail if n8n is down
        urllib.request.urlopen(urllib.request.Request(
            "http://localhost:5678/webhook/tag-people", method="POST"),
            timeout=5)
        print("triggered n8n skill-tagger webhook")
    except Exception as e:
        print(f"n8n webhook not reachable ({e}) — trigger manually")


if __name__ == "__main__":
    args = sys.argv[1:] or [
        "source1_naukri_applicants.csv",
        "source2_gig_workers.csv",
        "source3_cbnexus_contacts.csv",
    ]
    ingest(*args)
