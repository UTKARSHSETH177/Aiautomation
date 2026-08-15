# Task 5 — Launching the audio app to 5,000 gig workers in one weekend

## Load model (assumptions stated so you can attack them)

Voice-collection gigs pay per clip, so workers submit many: I assume ~20
clips of ~30 s each → **~100,000 submissions over ~40 active weekend hours
(~0.7/s average)**. Gig traffic is never uniform — a WhatsApp-blast launch
spike and a Sunday-deadline spike mean planning for **~10× average, call it
5–7 submissions/s sustained at peak**. A 30 s clip is ~0.1–0.5 MB as
uploaded (opus/aac), but ~3 MB as the canonical WAV my demo app also writes.
If you dispute the 20-clips assumption, the failure *ordering* below doesn't
change at 1 clip per worker — only the clock does.

## What breaks first (in order)

**1. The synchronous request path — within the first hour.** Today one Flask
dev-server process holds each connection through the full mobile upload plus
two ffmpeg passes (~2–4 s of CPU per clip). At even 2/s the box saturates,
requests time out, and users do what users do: retry — doubling load and
inserting duplicate rows, since nothing makes retries idempotent.

**2. SQLite.** WAL helps readers, but there is exactly one writer; concurrent
inserts start throwing "database is locked" right when the launch spike hits.
One file on one disk is also the backup story, i.e. there isn't one.

**3. Storage.** Original + WAV ≈ 3 MB per clip → **~300 GB by Sunday**. On a
free-tier ephemeral disk that's dead Saturday afternoon, and a redeploy
deletes every recording collected so far.

**4. Identity and duplicates.** Phone-as-identity with no verification means
typo-phones spawn ghost workers, and — because clips are money — one person
submitting under many numbers, or recycling the same clip, is not an edge
case; it's the economics. Nothing today hashes content or rate-limits.

And silently underneath all four: no metrics, so I'd learn about each failure
from angry WhatsApp messages instead of a dashboard.

## What I'd change before launch (in order)

1. **Split accept from analyze.** Store the file, return 202 + a job id, run
   ffmpeg in a small worker pool off a queue; the list view shows
   "processing". Extraction becomes eventually-consistent, uploads stop
   competing with CPU.
2. **Direct-to-object-storage uploads** (presigned URLs to S3/R2). The server
   never proxies bytes; storage becomes durable and effectively unlimited.
3. **Reverse my own WAV decision: store originals only.** Modern browsers
   play opus/mp4 natively; transcode on demand for the rare exception. Right
   call for a 55-row demo, a 10× storage mistake at scale.
4. **Postgres (managed) instead of SQLite.** Many writers, real backups — and
   my Task 2 API seam means nothing upstream changes.
5. **Idempotency + dedupe.** Client-generated submission UUID so retries
   can't double-insert; sha256 of the audio, with (phone, hash) flagged —
   recycled clips surface instead of getting paid.
6. **Phone OTP before the first submission**, plus per-phone/IP rate limits
   and a client-side duration cap. Identity here is payout money.
7. **Gunicorn workers behind a real proxy**, ffmpeg with timeouts and a
   concurrency cap; Sentry plus a five-number dashboard (submissions/min,
   p95 latency, failure %, queue depth, storage) with alerts.
8. **A load test replaying the modeled peak hour** — because the honest
   answer to "what breaks first" is whatever the load test finds that this
   page didn't.

## Cost (the surprise: it's small)

~15–20 GB of originals ≈ **under $1/month** in R2/S3; smallest managed
Postgres ~$0–15; two small VMs for app + workers ~$20; the real line item is
**OTP SMS: 5,000 × ~₹0.20 ≈ ₹1,000**. Whole weekend well under $50. At this
scale cost is not the constraint — reliability and fraud are. The one
genuinely expensive mistake would be keeping the WAV copies; change #3
deletes it.

## What I would deliberately NOT do yet

Microservices, Kubernetes, a CDN, custom audio infra. 5,000 workers is one
boring box, object storage, a queue, and Postgres. Boring survives weekends.

*Everything above is a consequence of decisions already visible in this repo
— the WAV tradeoff, the API seam, idempotent schemas. This page is those
decisions meeting 5,000 people.*
