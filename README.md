# ConsultBae — AI Automation Assignment

End-to-end submission: messy CSV merge → n8n LLM tagging → audio collection →
audited data-issues report → weekend scaling plan. Everything below is what I
actually built and ran on Windows, including the failures.

---

## Quick start (fresh clone)

Work from this folder (`Utkarsh/`). Paths are relative to here.

```powershell
# 1) Task 1 — build the identity DB
Remove-Item merged.db -ErrorAction SilentlyContinue
python pipeline.py

# 2) Task 4 — regenerate the issues section in this README
python generate_issues_report.py

# 3) Task 2 — DB connector + n8n (separate terminals)
pip install -r automation/requirements.txt
python automation/db_api.py          # :5001
npx n8n                              # :5678 → import automation/n8n_skill_tagger.json, activate
curl.exe -X POST http://localhost:5678/webhook/tag-people

# 4) Task 3 — audio app (ffmpeg must be on PATH — open a NEW terminal after winget install)
winget install Gyan.FFmpeg
pip install -r audio_app/requirements.txt
python audio_app/app.py              # http://localhost:5002
```

| Artifact | Role |
|---|---|
| `pipeline.py` | Task 1 merge + identity resolution + `data_issues` log |
| `merged.db` | Shared SQLite DB for Tasks 1–4 |
| `automation/db_api.py` | Thin HTTP bridge so n8n can talk to SQLite |
| `automation/n8n_skill_tagger.json` | Task 2 workflow (Gemini skill tagger) |
| `audio_app/` | Task 3 record/upload + ffmpeg analysis |
| `generate_issues_report.py` | Task 4 report from `data_issues` → README |
| `SCALING.md` | Task 5 one-page scaling writeup |

---

## Task 1 — Data merge pipeline (how I did it)

### Goal
Merge three messy recruitment CSVs into one identity graph without inventing
data or silently collapsing different people.

### What I built
- One `persons` table for canonical identity.
- Source-specific tables: `applications` (Naukri), `gig_profiles`, `nexus_contacts`.
- Matching order: **normalized email → normalized phone → fuzzy name + same city**.
- Name+city never overrides a conflicting email/phone (the data has two real
  Arjun Mehtas in Noida).
- Every anomaly goes into `data_issues` for Task 4.

### Step by step
1. Inspected the three CSVs for unit chaos (CTC in lakhs vs rupees, `/hr` vs
   `k/month`), date formats, empty/shifted rows, repeated headers.
2. Wrote normalizers (`norm_phone`, `norm_email`, `norm_city`, `parse_ctc`,
   `parse_rate`, `parse_date`) with explicit rules I can defend.
3. Built `PersonIndex` for in-memory resolve + merge logging.
4. Ingested source1 first (has both email and phone), then source2 (email),
   then source3 (phone).
5. Ran `python pipeline.py` and checked counts.

### Final counts (after Task 4 audit patches)
| Table | Count |
|---|---|
| persons | 55 |
| applications | 42 |
| gig_profiles | 30 |
| nexus_contacts | 30 |
| data_issues | 63 |

### Problems I hit (Task 1)
| Problem | What happened | Fix |
|---|---|---|
| Wrong working directory | Ran `python pipeline.py` from `C:\AIautomation` → file not found | Always `cd` into `Utkarsh/` first |
| `table persons already exists` | Re-ran pipeline without deleting `merged.db` | `Remove-Item merged.db` then rerun |
| Shared names look like duplicates | Three "Arjun Mehta" / two "Deepak Nair" | Email/phone conflict → keep separate; fuzzy only with city + no ID conflict |
| CTC / rate units mixed | Values like `4.2` and `28k/month` | Rule-based conversion + per-row log |

---

## Task 2 — n8n LLM Skill Auto-Tagger (how I did it)

### Goal
Tag every person into one of six skill categories with Gemini, write results
back to `merged.db`, and make re-runs safe (idempotent).

### What I built
- `automation/db_api.py` — Flask connector on `:5001` (`GET` untagged people,
  `POST` tag write-back). No automation logic here; n8n owns the flow.
- `automation/n8n_skill_tagger.json` — webhook → fetch batch of 15 → Gemini
  (temperature 0) → validate → write back → alert path.
- Categories: `automation-heavy`, `web-dev`, `backend`, `data`, `mixed`, `unknown`.

### Step by step
1. Realized n8n has no SQLite node → built the HTTP DB API.
2. Added nullable `skill_category` / `tag_confidence` / `tagged_at` columns
   (created idempotently by the API at startup).
3. Imported the workflow JSON into n8n, attached Google Gemini credentials.
4. Activated the workflow (production webhook only works when active).
5. Triggered: `curl.exe -X POST http://localhost:5678/webhook/tag-people`
6. Re-triggered until response said no untagged people left.

### Problems I hit (Task 2)
| Problem | What happened | Fix |
|---|---|---|
| Webhook 404 | Workflow imported but not activated | Toggle Active in n8n editor |
| `"Error in workflow"` | Bad/missing Gemini credential or validation branch | Fixed credential on Tag with Gemini node; re-ran |
| Connector not running | n8n called `:5001` while `db_api.py` was down | Keep `python automation/db_api.py` up in its own terminal |
| Rebuilding `merged.db` wipes tags | Task 4 / Task 1 rebuild drops tag columns | Restart `db_api.py` (recreates schema) and re-trigger webhook once |

---

## Task 3 — Mini audio collection app (how I did it)

### Goal
Collect name + phone + audio (browser record **or** file upload), store into
the same `merged.db`, extract duration / sample rate / bitrate / loudness, plus
a rough quality label.

### What I built
- Flask app on `:5002` (`audio_app/app.py`).
- Browser `MediaRecorder` + upload fallback (`static/recorder.js`).
- ffmpeg/ffprobe analysis (`audio_analysis.py`): original kept, canonical
  16-bit PCM WAV for playback + metrics.
- Identity via `norm_phone` imported from `pipeline.py` — known phone links to
  existing person; unknown creates `sources='audio'`.
- `/submissions` list with play buttons.

### Step by step
1. Chose ffmpeg over Python audio libs so webm/opus from Chrome works.
2. Implemented analyze → insert → list flow against `merged.db` (WAL mode so
   Task 2 connector can share the file).
3. Installed ffmpeg with `winget install Gyan.FFmpeg`.
4. Started app and tested record + upload locally.

### Problems I hit (Task 3)
| Problem | What happened | Fix |
|---|---|---|
| `ffmpeg/ffprobe not found on PATH` | winget installed binaries but current shell still had old PATH | Opened a **new** terminal, verified `ffmpeg -version` |
| Wrong path / wrong pipeline copy | Ran `audio_app/pipeline.py` from parent folder → CSV not found | Use root `pipeline.py` from `Utkarsh/`; audio app only imports normalizers |
| Mic blocked / insecure context | Browser needs localhost or HTTPS | Demo on `http://localhost:5002` (or ngrok HTTPS for phone) |
| webm often has no duration header | Properties missing if you trust the container | Always decode with ffmpeg; serve WAV for cross-browser play |

---

## Task 4 — Data issues report (how I did it)

### Method
Every anomaly is written to `data_issues` at ingest. The report below is
*generated* (`python generate_issues_report.py`) so it cannot drift from what
the code did. After v1 I audited the pipeline against the raw files and found
four gaps my tooling had missed; those are patched and logged too.

**Matching philosophy:** email > phone > fuzzy name + same city; name+city
never overrides a conflicting hard identifier.

<!-- DATA_ISSUES:START -->
### Data issues — generated from the pipeline's own `data_issues` log

**63 logged issues across 13 categories.** Produced by `generate_issues_report.py` from `merged.db`, so this section always reflects what the pipeline actually did — not hand-waving.

| # | Category | Count | Handling |
|---|----------|-------|----------|
| 1 | Unit ambiguity — CTC in lakhs vs absolute rupees | 21 | Rule: value < 100 ⇒ lakhs × 100,000. No plausible annual CTC is under ₹100. |
| 2 | Date chaos — ambiguous MM/DD vs DD/MM | 5 | Slash dates parsed MM/DD/YYYY because the file contains 07/13/2026 and 08/19/2026, impossible as DD/MM; every day≤12 case flagged. |
| 3 | Plausibility — applied_date in the future | 6 | Stored as-is and flagged; the pipeline never invents data. |
| 4 | Geography — region recorded as a city | 3 | 'Delhi NCR' is a region, not a city; mapped to Delhi with a per-row log. |
| 5 | Duplicates — repeated rows / repeat applications | 2 | Exact dupes dropped; same-person re-applications attach to ONE person. |
| 6 | Identity — multiple identifiers for one person | 1 | First identifier kept canonical; alternates preserved in match_notes. |
| 7 | Structural — empty rows | 1 | Dropped. |
| 8 | Structural — column-shifted row | 1 | Detected because the email sat in the wrong column; repaired by rotation. |
| 9 | Unit ambiguity — gig rate '/hr' vs 'k/month' | 14 | Normalized to ₹/hr assuming 160 working hours/month; the assumption is explicit and reversible. |
| 10 | Other | 2 |  |
| 11 | Duplicates — disguised duplicate hidden behind a structural repair | 1 | The column-shifted row, once repaired, exactly duplicated an earlier profile — dropped. |
| 12 | Structural — header row repeated mid-file | 1 | Dropped. |
| 13 | Identity — low-confidence fuzzy merges | 5 | Merged only with city agreement and zero identifier conflicts; every one flagged for review. |

#### Unit ambiguity — CTC in lakhs vs absolute rupees (21)

- `source1` line 6 (Amit Agarwal): CTC '4.2' appears to be in lakhs → *converted to Rs 420000*
- `source1` line 8 (Shreya Gupta): CTC '8.3' appears to be in lakhs → *converted to Rs 830000*
- `source1` line 14 (Nikhil Malhotra): CTC '5.1' appears to be in lakhs → *converted to Rs 510000*
- `source1` line 16 (Ritu Sharma): CTC '6.1' appears to be in lakhs → *converted to Rs 610000*
- `source1` line 17 (Arjun Mishra): CTC '5.8' appears to be in lakhs → *converted to Rs 580000*
- `source1` line 18 (Meera Bhatia): CTC '11.2' appears to be in lakhs → *converted to Rs 1120000*
- `source1` line 19 (Varun Jain): CTC '7.6' appears to be in lakhs → *converted to Rs 760000*
- `source1` line 21 (Kavya Mehta): CTC '2.4' appears to be in lakhs → *converted to Rs 240000*
- …and 13 more of the same pattern

#### Date chaos — ambiguous MM/DD vs DD/MM (5)

- `source1` 07/03/2026: ambiguous slash date (day<=12) → *parsed as MM/DD/YYYY per file-level evidence*
- `source1` 07/03/2026: ambiguous slash date (day<=12) → *parsed as MM/DD/YYYY per file-level evidence*
- `source1` 08/11/2026: ambiguous slash date (day<=12) → *parsed as MM/DD/YYYY per file-level evidence*
- `source1` 07/03/2026: ambiguous slash date (day<=12) → *parsed as MM/DD/YYYY per file-level evidence*
- `source1` 07/12/2026: ambiguous slash date (day<=12) → *parsed as MM/DD/YYYY per file-level evidence*

#### Plausibility — applied_date in the future (6)

- `source1` line 14 (Nikhil Malhotra): applied_date 2026-08-21 is in the future → *stored as-is, flagged for review*
- `source1` line 17 (Arjun Mishra): applied_date 2026-08-22 is in the future → *stored as-is, flagged for review*
- `source1` line 19 (Varun Jain): applied_date 2026-08-19 is in the future → *stored as-is, flagged for review*
- `source1` line 28 (Priya Saxena): applied_date 2026-08-16 is in the future → *stored as-is, flagged for review*
- `source1` line 32 (Rohit Nair): applied_date 2026-08-19 is in the future → *stored as-is, flagged for review*
- `source1` line 40 (Isha Kapoor): applied_date 2026-08-21 is in the future → *stored as-is, flagged for review*

#### Geography — region recorded as a city (3)

- `source1` line 18 (Meera Bhatia): region 'Delhi NCR' recorded as city → *mapped to canonical city 'Delhi'*
- `source1` line 36 (Amit Reddy): region 'Delhi NCR' recorded as city → *mapped to canonical city 'Delhi'*
- `source3` line 7 (Rahul Malhotra): region 'Delhi NCR' recorded as city → *mapped to canonical city 'Delhi'*

#### Duplicates — repeated rows / repeat applications (2)

- `source1` line 31 (Rohit Verma): duplicate person within source1 (matched by email) → *second application attached to same person*
- `source1` line 37 (Nikhil Chopra): duplicate person within source1 (matched by phone) → *second application attached to same person*

#### Identity — multiple identifiers for one person (1)

- `naukri` Nikhil Chopra: second email 'nikhil.chopra70@example.com' for person with 'alt.nikhil.chopra70@example.com' → *kept first as canonical, alt noted on person*

#### Structural — empty rows (1)

- `source2` line 12: completely empty row → *dropped*

#### Structural — column-shifted row (1)

- `source2` line 20: column-shifted row (email found in wrong column) → *repaired by rotating columns back into place*

#### Unit ambiguity — gig rate '/hr' vs 'k/month' (14)

- `source2` line 6 (Isha Kapoor): monthly rate '15k/month' → *converted to Rs 93.75/hr assuming 160 h/month*
- `source2` line 10 (Rahul Chopra): monthly rate '72k/month' → *converted to Rs 450.0/hr assuming 160 h/month*
- `source2` line 14 (Sneha Chopra): monthly rate '28k/month' → *converted to Rs 175.0/hr assuming 160 h/month*
- `source2` line 16 (Gaurav Mehta): monthly rate '56k/month' → *converted to Rs 350.0/hr assuming 160 h/month*
- `source2` line 17 (Neha Bhatia): monthly rate '79k/month' → *converted to Rs 493.75/hr assuming 160 h/month*
- `source2` line 18 (Arjun Mehta): monthly rate '42k/month' → *converted to Rs 262.5/hr assuming 160 h/month*
- `source2` line 19 (Manish Bhatia): monthly rate '73k/month' → *converted to Rs 456.25/hr assuming 160 h/month*
- `source2` line 21 (Divya Chopra): monthly rate '55k/month' → *converted to Rs 343.75/hr assuming 160 h/month*
- …and 6 more of the same pattern

#### Other (2)

- `gig` Arjun Mehta: same name+city as person 19 but conflicting email/phone → *NOT merged — kept as separate person, flagged for review*
- `cbnexus` Arjun Mehta: same name+city as person 19 but conflicting email/phone → *NOT merged — kept as separate person, flagged for review*

#### Duplicates — disguised duplicate hidden behind a structural repair (1)

- `source2` line 20 (Isha Chopra): exact duplicate gig profile (identical email/rate/skills) — the column-shifted row, once repaired, duplicated an earlier row → *dropped; one profile kept*

#### Structural — header row repeated mid-file (1)

- `source3` line 16: repeated header row inside data → *dropped*

#### Identity — low-confidence fuzzy merges (5)

- `cbnexus` Arjun Mehta: fuzzy name+city match to person 41 ('Arjun Mehta', Noida) → *merged LOW CONFIDENCE — review*
- `cbnexus` Manish Bhatia: fuzzy name+city match to person 42 ('Manish Bhatia', Noida) → *merged LOW CONFIDENCE — review*
- `cbnexus` Divya Chopra: fuzzy name+city match to person 43 ('Divya Chopra', Noida) → *merged LOW CONFIDENCE — review*
- `cbnexus` Karan Chopra: fuzzy name+city match to person 44 ('Karan Chopra', Pune) → *merged LOW CONFIDENCE — review*
- `cbnexus` Vikram Mehta: fuzzy name+city match to person 45 ('Vikram Mehta', Pune) → *merged LOW CONFIDENCE — review*
<!-- DATA_ISSUES:END -->

### Notable catches
1. **Disguised duplicate behind a repair.** source2 line 20 is column-shifted;
   after repair it is an exact copy of Isha Chopra's earlier profile. Repair ≠
   done — post-repair dedupe dropped it (`gig_profiles` 31 → 30).
2. **Three "Arjun Mehta" records → two people.** Conflicting identifiers stay
   separate; the third fuzzy-merges into the non-conflicting one.
3. **Bug in my own issue log.** After dropped rows, line refs drifted (Divya
   Chopra logged as line 20, actually CSV line 21). Loaders now return real
   CSV line numbers.

### Judgment calls

| Call | Basis |
|---|---|
| CTC < 100 ⇒ lakhs × 100,000 | No plausible annual CTC under ₹100 |
| Slash dates = MM/DD/YYYY | File has 07/13/2026 and 08/19/2026 — impossible as DD/MM |
| `k/month` → ₹/hr at 160 h/month | Stated, reversible, all conversions logged |
| Future applied_dates stored, not "fixed" | Ingest flags; never invents data |
| First-seen identifier wins | Deterministic; alternates in `match_notes` |

### Checked for and NOT present
Invalid emails/phones (hardening logs fire 0 times) · negative experience/
projects · second-phone conflicts · post-normalization city conflicts on merges ·
unmapped `Verified` · unmapped source2 locations.

### Problems I hit (Task 4)
| Problem | What happened | Fix |
|---|---|---|
| Silent "Delhi NCR" mapping | Comment claimed it was logged; `norm_city` never called `log_issue` | Per-row logs in s1/s3 ingest |
| Future applied_dates | Stored with no comment | Flag as-is for review |
| Line-ref drift after drops | Filtered lists re-enumerated from 2 | Loaders return `(csv_line_no, row)` |
| Expectation miss if patch wrong | Counts must be 55 / 42 / 30 / 30 / 63 | Re-check patches; do not invent new rules |

---

## Task 5 — Stretch: scaling to 5,000 workers

One page, no code: what breaks first and what I would change before launch —
see **[SCALING.md](SCALING.md)**.

Short version: the **synchronous upload+ffmpeg path** dies first; then SQLite's
single writer; then disk from keeping WAV copies. Before launch I would split
accept from analyze, upload direct to object storage, and reverse my own
store-a-WAV decision (right at 55 rows, wrong at ~300 GB).

---

## Stuck log (real blockers, what I tried, what I rejected)

### 1. ffmpeg installed but app still crashed
- **Stuck at:** starting `audio_app/app.py` after `winget install Gyan.FFmpeg`.
- **Error:** `ffmpeg/ffprobe not found on PATH`.
- **Searched / tried:** reinstall; calling full path; asking the agent to
  "just use pydub/librosa instead".
- **Rejected:** swapping to Python audio libs — MediaRecorder webm/opus is
  exactly what those struggle with; ffmpeg was the right tool.
- **What worked:** new shell so PATH refreshed; verified with `ffmpeg -version`.

### 2. n8n webhook looked broken when the workflow was the problem
- **Stuck at:** `curl` to `/webhook/tag-people`.
- **Errors:** first 404 ("webhook not registered"), then `"Error in workflow"`.
- **Tried:** restarting n8n; hitting test vs production URL; blaming `db_api`.
- **Rejected:** rewriting the whole flow in Python — the assignment wants
  automation in n8n; the Flask API must stay thin.
- **What worked:** Activate the workflow; attach Gemini credential; keep
  `db_api.py` running; re-trigger until idempotent no-op.

### 3. "Repaired ≠ done" — disguised duplicate
- **Stuck at:** Task 4 audit — Isha Chopra had two identical gig profiles.
- **How found:** `JOIN` on `gig_profiles`/`persons`, not by staring at the CSV.
- **AI suggested:** more aggressive fuzzy dedupe across the whole file.
- **Rejected:** fuzzy profile dedupe — would risk dropping real same-rate
  workers; key is exact `(email, rate, skill_tags)` after repair only.
- **Lesson:** structural repair can create a semantic duplicate; log and drop.

### 4. Path / cwd confusion on Windows
- **Stuck at:** running scripts from `C:\AIautomation` instead of `Utkarsh\`.
- **Symptoms:** `pipeline.py` not found; CSVs not found; empty/wrong `merged.db`.
- **Rejected:** hardcoding absolute `C:\...` paths into the code (breaks clones).
- **What worked:** document "run from repo root"; relative paths only.

---

## Cross-task gotchas (read before re-demo)

1. Rebuilding `merged.db` wipes Task 2 tags and Task 3 `audio_submissions`.
   Restart `automation/db_api.py` and `audio_app/app.py`, then re-trigger the
   n8n tagger once.
2. Pipeline creates tables with `CREATE TABLE` (not `IF NOT EXISTS`) — always
   delete `merged.db` before a clean Task 1 run.
3. Do not commit `data/` audio blobs, API keys, or `n8n_install.log`.

---

## Video checklist (≈6 minutes)

| Segment | Beat |
|---|---|
| Intro | Who I am + what's in the repo |
| Task 1 | Delete DB → run pipeline → show counts + Arjun Mehta story |
| Task 2 | Trigger webhook → tags land → idempotent re-run |
| Task 3 | Live record → linked person → quality metrics on `/submissions` |
| Task 4 | Scroll generated report — "I audited the auditor" |
| Task 5 | Scroll `SCALING.md` — reverse the WAV decision |
| Close | Hardest decision + thanks |
