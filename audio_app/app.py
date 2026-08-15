"""Task 3 — Flask audio collection app.

Run from the repository root:
    python audio_app/app.py
"""

import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pipeline import norm_name, norm_phone  # noqa: E402
from project_paths import AUDIO_ROOT, DB_PATH  # noqa: E402

try:
    from .audio_analysis import AudioAnalysisError, analyze, require_tools
except ImportError:  # Supports: python audio_app/app.py
    from audio_analysis import AudioAnalysisError, analyze, require_tools

ORIG_DIR = AUDIO_ROOT / "originals"
WAV_DIR = AUDIO_ROOT / "wav"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


def db():
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def ensure_schema():
    """Create Task 3 storage idempotently inside the Task 1 database."""
    if not DB_PATH.exists():
        raise RuntimeError(
            f"{DB_PATH} does not exist — run 'python pipeline.py' first"
        )

    connection = db()
    try:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='persons'"
        ).fetchone():
            raise RuntimeError(
                "persons table is missing — run 'python pipeline.py' first"
            )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audio_submissions (
                submission_id     INTEGER PRIMARY KEY,
                person_id         INTEGER NOT NULL
                                  REFERENCES persons(person_id),
                matched_existing  INTEGER,
                submitted_name    TEXT,
                submitted_phone   TEXT,
                phone_normalized  TEXT,
                source_kind       TEXT,
                original_filename TEXT,
                original_path     TEXT,
                wav_path          TEXT,
                mime_type         TEXT,
                codec             TEXT,
                duration_sec      REAL,
                sample_rate_khz   REAL,
                bitrate_kbps      REAL,
                loudness_db       REAL,
                peak_db           REAL,
                noise_floor_db    REAL,
                snr_db            REAL,
                quality_label     TEXT,
                clipping          INTEGER,
                created_at        TEXT
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def initialize():
    """Validate dependencies and prepare runtime storage."""
    require_tools()
    for directory in (ORIG_DIR, WAV_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    ensure_schema()


def stored_audio_path(path):
    """Store a portable path relative to the configured audio directory."""
    return Path(path).resolve().relative_to(AUDIO_ROOT.resolve()).as_posix()


def resolve_audio_path(stored_path):
    """Resolve new relative paths while retaining legacy absolute records."""
    path = Path(stored_path)
    if path.is_absolute():
        return path
    resolved = (AUDIO_ROOT / path).resolve()
    try:
        resolved.relative_to(AUDIO_ROOT.resolve())
    except ValueError:
        return None
    return resolved


def find_or_create_person(connection, name, phone):
    """Resolve identity by normalized phone without mutating existing rows."""
    row = connection.execute(
        "SELECT person_id, full_name FROM persons WHERE phone = ?", (phone,)
    ).fetchone()
    if row:
        return row["person_id"], row["full_name"], True

    cursor = connection.execute(
        "INSERT INTO persons (full_name, phone, sources, match_notes) "
        "VALUES (?, ?, 'audio', 'created by audio app')",
        (name, phone),
    )
    return cursor.lastrowid, name, False


@app.errorhandler(RequestEntityTooLarge)
def file_too_large(_error):
    return jsonify(error="audio file exceeds the 25 MB limit"), 413


@app.before_request
def _ensure_schema_before_request():
    # pipeline.py rebuilds merged.db without Task 3 tables; recreate if needed.
    if request.endpoint in {
        "create_submission",
        "submissions_page",
        "submissions_json",
        "audio",
    }:
        ensure_schema()


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/submissions")
def create_submission():
    raw_name = request.form.get("name", "")
    raw_phone = request.form.get("phone", "")
    name = norm_name(raw_name)
    phone = norm_phone(raw_phone)
    source_kind = request.form.get("kind") or "uploaded"
    audio_file = request.files.get("audio")

    if not name:
        return jsonify(error="name is required"), 400
    if not phone:
        return (
            jsonify(
                error=(
                    "phone must be a valid 10-digit Indian mobile "
                    "(+91 / 0 prefixes are fine)"
                )
            ),
            400,
        )
    if source_kind not in {"recorded", "uploaded"}:
        return jsonify(error="kind must be recorded or uploaded"), 400
    if audio_file is None or audio_file.filename == "":
        return jsonify(error="no audio attached"), 400

    uid = uuid.uuid4().hex
    suffix = Path(audio_file.filename).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", suffix or ""):
        suffix = ".bin"
    original_path = ORIG_DIR / f"{uid}{suffix}"
    wav_path = WAV_DIR / f"{uid}.wav"

    for directory in (ORIG_DIR, WAV_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    audio_file.save(original_path)

    try:
        properties = analyze(original_path, wav_path)
    except AudioAnalysisError as exc:
        original_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        return jsonify(error=f"could not process audio: {exc}"), 400
    except Exception:
        app.logger.exception("Unexpected audio processing failure")
        original_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        return jsonify(error="could not process audio: internal error"), 500

    connection = db()
    try:
        with connection:
            person_id, name_on_file, matched = find_or_create_person(
                connection, name, phone
            )
            cursor = connection.execute(
                """
                INSERT INTO audio_submissions
                    (person_id, matched_existing, submitted_name,
                     submitted_phone, phone_normalized, source_kind,
                     original_filename, original_path, wav_path, mime_type,
                     codec, duration_sec, sample_rate_khz, bitrate_kbps,
                     loudness_db, peak_db, noise_floor_db, snr_db,
                     quality_label, clipping, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    person_id,
                    int(matched),
                    raw_name,
                    raw_phone,
                    phone,
                    source_kind,
                    audio_file.filename,
                    stored_audio_path(original_path),
                    stored_audio_path(wav_path),
                    audio_file.mimetype,
                    properties["codec"],
                    properties["duration_sec"],
                    properties["sample_rate_khz"],
                    properties["bitrate_kbps"],
                    properties["loudness_db"],
                    properties["peak_db"],
                    properties["noise_floor_db"],
                    properties["snr_db"],
                    properties["quality_label"],
                    properties["clipping"],
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            submission_id = cursor.lastrowid
    except sqlite3.Error:
        app.logger.exception("Could not store audio submission")
        original_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        return jsonify(error="could not store submission"), 500
    finally:
        connection.close()

    return (
        jsonify(
            submission_id=submission_id,
            person={
                "person_id": person_id,
                "matched_existing": matched,
                "name_on_file": name_on_file,
            },
            properties=properties,
        ),
        201,
    )


@app.get("/submissions")
def submissions_page():
    connection = db()
    try:
        rows = connection.execute(
            """
            SELECT s.*, p.full_name AS person_name
            FROM audio_submissions s
            JOIN persons p USING (person_id)
            ORDER BY s.submission_id DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return render_template("submissions.html", rows=rows)


@app.get("/api/submissions")
def submissions_json():
    connection = db()
    try:
        rows = connection.execute(
            """
            SELECT s.submission_id, s.person_id,
                   p.full_name AS person_name, s.matched_existing,
                   s.source_kind, s.duration_sec, s.sample_rate_khz,
                   s.bitrate_kbps, s.loudness_db, s.snr_db,
                   s.quality_label, s.clipping, s.created_at
            FROM audio_submissions s
            JOIN persons p USING (person_id)
            ORDER BY s.submission_id DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return jsonify(
        count=len(rows), submissions=[dict(row) for row in rows]
    )


@app.get("/audio/<int:submission_id>")
def audio(submission_id):
    connection = db()
    try:
        row = connection.execute(
            "SELECT wav_path FROM audio_submissions "
            "WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
    finally:
        connection.close()

    wav_path = resolve_audio_path(row["wav_path"]) if row else None
    if wav_path is None or not wav_path.is_file():
        return jsonify(error="not found"), 404
    return send_file(wav_path, mimetype="audio/wav", conditional=True)


@app.template_filter("fmt")
def fmt(value, decimal_places=1):
    return (
        "—"
        if value is None
        else f"{value:.{int(decimal_places)}f}"
    )


if __name__ == "__main__":
    initialize()
    app.run(host="0.0.0.0", port=5002, debug=False)
