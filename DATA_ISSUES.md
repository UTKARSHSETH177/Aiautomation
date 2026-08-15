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
