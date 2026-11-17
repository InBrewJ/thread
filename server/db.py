import hashlib
import os
import json
import secrets
import sqlite3
import uuid
from datetime import datetime

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "timeline.db")
DATABASE_PATH = os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH)

DEFAULT_BIRTHDAY = "2000-01-01"

DEFAULT_SAMPLE_SLIDES = [
    {
        "age": 0,
        "mood": 5,
        "emoji": "👶",
        "events": ["Birth"],
        "notes": "Beginning of the timeline journey.",
    },
    {
        "age": 6,
        "mood": 7,
        "emoji": "🎒",
        "events": ["Started primary school", "Made first best friends"],
        "notes": "Energetic and curious early years.",
    },
    {
        "age": 12,
        "mood": 4,
        "emoji": "🎸",
        "events": ["Transition to secondary school", "Picked up guitar"],
        "notes": "Introverted period with lots of music exploration.",
    },
    {
        "age": 16,
        "mood": 6,
        "emoji": "📚",
        "events": ["School exams", "First part-time job"],
        "notes": "Gaining independence.",
    },
    {
        "age": 18,
        "mood": 8,
        "emoji": "🎓",
        "events": ["Graduation and university departure"],
        "notes": "Excitement and optimism about moving away from home.",
    },
    {
        "age": 22,
        "mood": 5,
        "emoji": "💼",
        "events": ["First professional career role", "Moved into first flat"],
        "notes": "Navigating adult responsibilities and finding work-life rhythm.",
    },
    {
        "age": 26,
        "mood": 8,
        "emoji": "🚀",
        "events": ["Launched new creative project", "Major milestone achievement"],
        "notes": "Feeling grounded and purpose-driven.",
    },
]

# If local private slides exist (gitignored), load them; otherwise use clean sample slides
PRIVATE_SLIDES_PATH = os.path.join(os.path.dirname(__file__), "private_slides.json")
if os.path.exists(PRIVATE_SLIDES_PATH):
    try:
        with open(PRIVATE_SLIDES_PATH, "r", encoding="utf-8") as f:
            SEED_SLIDES = json.load(f)
    except Exception:
        SEED_SLIDES = DEFAULT_SAMPLE_SLIDES
else:
    SEED_SLIDES = DEFAULT_SAMPLE_SLIDES


def get_connection(db_path=None):
    path = db_path or DATABASE_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path=None):
    global DATABASE_PATH
    if db_path:
        DATABASE_PATH = db_path
    conn = get_connection(DATABASE_PATH)
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT,
                username TEXT,
                display_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS timelines (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                birthday TEXT NOT NULL,
                share_token TEXT UNIQUE NOT NULL,
                is_public INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS slides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timeline_id TEXT NOT NULL,
                age REAL NOT NULL,
                mood REAL NOT NULL,
                emoji TEXT NOT NULL,
                events TEXT NOT NULL,
                notes TEXT,
                order_index INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (timeline_id) REFERENCES timelines(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS mood_image_jobs (
                slide_id INTEGER PRIMARY KEY,
                content_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                prompt TEXT,
                image_path TEXT,
                error TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (slide_id) REFERENCES slides(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_timelines_user ON timelines(user_id);
            CREATE INDEX IF NOT EXISTS idx_timelines_share ON timelines(share_token);
            CREATE INDEX IF NOT EXISTS idx_slides_timeline ON slides(timeline_id);
            CREATE INDEX IF NOT EXISTS idx_slides_age ON slides(age);
            CREATE INDEX IF NOT EXISTS idx_mood_jobs_status ON mood_image_jobs(status);
            """
        )

        # Check if default timeline exists, seed it if not
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, share_token FROM timelines WHERE id = 'timeline-default'")
        existing = cursor.fetchone()
        if not existing:
            # Seed default owner
            default_user_id = "jason@urawizard.com"
            cursor.execute(
                """
                INSERT OR IGNORE INTO users (id, email, username, display_name)
                VALUES (?, ?, ?, ?)
                """,
                (default_user_id, "jason@urawizard.com", "jason", "Jason Brewer"),
            )

            # Seed default timeline with an unguessable secure share token
            default_timeline_id = "timeline-default"
            secure_share_token = secrets.token_urlsafe(12)
            cursor.execute(
                """
                INSERT INTO timelines (id, user_id, title, birthday, share_token)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    default_timeline_id,
                    default_user_id,
                    "Mental Health & Mood Timeline",
                    DEFAULT_BIRTHDAY,
                    secure_share_token,
                ),
            )

            # Insert slides
            for idx, slide in enumerate(SEED_SLIDES):
                cursor.execute(
                    """
                    INSERT INTO slides (timeline_id, age, mood, emoji, events, notes, order_index)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        default_timeline_id,
                        float(slide["age"]),
                        float(slide["mood"]),
                        str(slide.get("emoji", "")),
                        json.dumps(slide.get("events", [])),
                        str(slide.get("notes", "")),
                        idx,
                    ),
                )
        else:
            # Ensure jason@urawizard.com user exists
            cursor.execute(
                """
                INSERT OR IGNORE INTO users (id, email, username, display_name)
                VALUES (?, ?, ?, ?)
                """,
                ("jason@urawizard.com", "jason@urawizard.com", "jason", "Jason Brewer"),
            )
            # Ensure timeline-default is explicitly owned by jason@urawizard.com
            cursor.execute(
                "UPDATE timelines SET user_id = 'jason@urawizard.com' WHERE id = 'timeline-default'"
            )

        # Rotate any easily guessable 'default' share tokens to unguessable random tokens
        cursor.execute("SELECT id FROM timelines WHERE share_token = 'default'")
        for row in cursor.fetchall():
            new_token = secrets.token_urlsafe(12)
            cursor.execute("UPDATE timelines SET share_token = ? WHERE id = ?", (new_token, row["id"]))
    conn.close()


def upsert_user(user_id, email="", username="", display_name=""):
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO users (id, email, username, display_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                email = coalesce(nullif(excluded.email, ''), users.email),
                username = coalesce(nullif(excluded.username, ''), users.username),
                display_name = coalesce(nullif(excluded.display_name, ''), users.display_name)
            """,
            (user_id, email, username, display_name or username or email),
        )
    conn.close()


def get_user(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_timelines_by_user(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT email, username FROM users WHERE id = ?", (user_id,))
    u_row = cursor.fetchone()
    email = u_row["email"] if u_row else ""
    is_jason = (
        user_id == "jason@urawizard.com"
        or (email and email.lower() == "jason@urawizard.com")
        or "jason" in str(user_id).lower()
        or (email and "urawizard" in str(email).lower())
    )

    if is_jason:
        cursor.execute(
            """
            SELECT t.*, count(s.id) as slide_count
            FROM timelines t
            LEFT JOIN slides s ON s.timeline_id = t.id
            WHERE t.user_id = ? OR t.user_id = 'jason@urawizard.com' OR t.id = 'timeline-default'
            GROUP BY t.id
            ORDER BY t.created_at ASC
            """,
            (user_id,),
        )
    else:
        cursor.execute(
            """
            SELECT t.*, count(s.id) as slide_count
            FROM timelines t
            LEFT JOIN slides s ON s.timeline_id = t.id
            WHERE t.user_id = ?
            GROUP BY t.id
            ORDER BY t.created_at ASC
            """,
            (user_id,),
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_timeline(timeline_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM timelines WHERE id = ?", (timeline_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_timeline_by_share_token(share_token):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM timelines WHERE share_token = ?", (share_token,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def create_timeline(user_id, title="My Mood Timeline", birthday=DEFAULT_BIRTHDAY):
    conn = get_connection()
    timeline_id = str(uuid.uuid4())
    share_token = secrets.token_urlsafe(12)
    with conn:
        conn.execute(
            """
            INSERT INTO timelines (id, user_id, title, birthday, share_token)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timeline_id, user_id, title, birthday, share_token),
        )
    conn.close()
    return timeline_id


def update_timeline(timeline_id, title, birthday, is_public=1):
    conn = get_connection()
    with conn:
        conn.execute(
            """
            UPDATE timelines
            SET title = ?, birthday = ?, is_public = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, birthday, is_public, timeline_id),
        )
    conn.close()


def delete_timeline(timeline_id, user_id):
    conn = get_connection()
    with conn:
        conn.execute(
            "DELETE FROM timelines WHERE id = ? AND user_id = ?",
            (timeline_id, user_id),
        )
    conn.close()


def _parse_events(raw_events) -> list:
    if isinstance(raw_events, list):
        return raw_events
    if not raw_events:
        return []
    try:
        parsed = json.loads(raw_events)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except Exception:
        return [e.strip() for e in str(raw_events).split(",") if e.strip()]


def get_slides(timeline_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM slides
        WHERE timeline_id = ?
        ORDER BY age ASC, order_index ASC, id ASC
        """,
        (timeline_id,),
    )
    rows = cursor.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["events"] = _parse_events(d["events"])
        result.append(d)
    conn.close()
    return result


def get_slide(slide_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM slides WHERE id = ?", (slide_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["events"] = _parse_events(d["events"])
    return d


def add_slide(timeline_id, age, mood, emoji="", events=None, notes="", order_index=0):
    if events is None:
        events = []
    events_json = json.dumps(events) if isinstance(events, list) else json.dumps([str(events)])
    conn = get_connection()
    with conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO slides (timeline_id, age, mood, emoji, events, notes, order_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (timeline_id, float(age), float(mood), str(emoji), events_json, str(notes), int(order_index)),
        )
        slide_id = cursor.lastrowid
        conn.execute("UPDATE timelines SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (timeline_id,))
    conn.close()
    return slide_id


def update_slide(slide_id, age, mood, emoji="", events=None, notes="", order_index=0):
    if events is None:
        events = []
    events_json = json.dumps(events) if isinstance(events, list) else json.dumps([str(events)])
    conn = get_connection()
    with conn:
        conn.execute(
            """
            UPDATE slides
            SET age = ?, mood = ?, emoji = ?, events = ?, notes = ?, order_index = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (float(age), float(mood), str(emoji), events_json, str(notes), int(order_index), slide_id),
        )
        # Touch timeline updated_at
        conn.execute(
            """
            UPDATE timelines SET updated_at = CURRENT_TIMESTAMP
            WHERE id = (SELECT timeline_id FROM slides WHERE id = ?)
            """,
            (slide_id,),
        )
    conn.close()


def delete_slide(slide_id):
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM slides WHERE id = ?", (slide_id,))
    conn.close()


def replace_slides(timeline_id, slides_list):
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM slides WHERE timeline_id = ?", (timeline_id,))
        for idx, slide in enumerate(slides_list):
            age = float(slide.get("age", 0))
            mood = float(slide.get("mood", 5))
            emoji = str(slide.get("emoji", ""))
            events = slide.get("events", [])
            events_json = json.dumps(events) if isinstance(events, list) else json.dumps([str(events)])
            notes = str(slide.get("notes", ""))
            conn.execute(
                """
                INSERT INTO slides (timeline_id, age, mood, emoji, events, notes, order_index)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (timeline_id, age, mood, emoji, events_json, notes, idx),
            )
        conn.execute("UPDATE timelines SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (timeline_id,))
    conn.close()


_UPSERT_PENDING_JOB_SQL = """
    INSERT INTO mood_image_jobs (slide_id, content_hash, status, error, updated_at)
    VALUES (?, ?, 'pending', NULL, CURRENT_TIMESTAMP)
    ON CONFLICT(slide_id) DO UPDATE SET
        content_hash = excluded.content_hash,
        status = 'pending',
        error = NULL,
        updated_at = CURRENT_TIMESTAMP
"""


def compute_slide_content_hash(mood: float, emoji: str, events: list, notes: str) -> str:
    """Compute a deterministic SHA-256 hash of a slide's image-relevant fields."""
    norm_events = [str(e) for e in _parse_events(events)]
    payload = json.dumps(
        {
            "mood": float(mood),
            "emoji": str(emoji or ""),
            "events": norm_events,
            "notes": str(notes or ""),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enqueue_slide_image_job(slide_id: int, content_hash: str, db_path: str | None = None) -> bool:
    """Enqueue or re-queue a slide image job when missing, failed, or content_hash changed."""
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute(
            "SELECT content_hash, status FROM mood_image_jobs WHERE slide_id = ?",
            (slide_id,),
        ).fetchone()
        if row is not None and row["content_hash"] == content_hash and row["status"] != "failed":
            conn.rollback()
            return False
        conn.execute(_UPSERT_PENDING_JOB_SQL, (slide_id, content_hash))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def backfill_missing_image_jobs(db_path: str | None = None) -> int:
    """Queue image jobs for slides missing a job or whose content_hash is stale."""
    conn = get_connection(db_path)
    queued = 0
    try:
        conn.execute("BEGIN IMMEDIATE;")
        rows = conn.execute(
            """
            SELECT s.id, s.mood, s.emoji, s.events, s.notes, j.content_hash AS job_hash
            FROM slides s
            LEFT JOIN mood_image_jobs j ON j.slide_id = s.id
            ORDER BY s.id ASC
            """
        ).fetchall()
        for row in rows:
            content_hash = compute_slide_content_hash(
                row["mood"],
                row["emoji"],
                _parse_events(row["events"]),
                row["notes"] or "",
            )
            if row["job_hash"] == content_hash:
                continue
            conn.execute(_UPSERT_PENDING_JOB_SQL, (row["id"], content_hash))
            queued += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return queued


def requeue_stale_processing_jobs(db_path: str | None = None) -> int:
    """Reset any interrupted 'processing' jobs back to 'pending' on worker startup."""
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        cur = conn.execute(
            """
            UPDATE mood_image_jobs
            SET status = 'pending',
                error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'processing'
            """
        )
        conn.commit()
        return cur.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_next_image_job(db_path: str | None = None) -> dict | None:
    """Atomically claim the next pending image job and return it with joined slide fields."""
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        job_row = conn.execute(
            """
            UPDATE mood_image_jobs
            SET status = 'processing',
                error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE slide_id = (
                SELECT j.slide_id
                FROM mood_image_jobs j
                JOIN slides s ON s.id = j.slide_id
                WHERE j.status = 'pending'
                ORDER BY j.updated_at DESC, j.slide_id ASC
                LIMIT 1
            )
            RETURNING slide_id, content_hash, status, prompt, image_path, error, updated_at
            """
        ).fetchone()
        if job_row is None:
            conn.rollback()
            return None
        slide_row = conn.execute(
            """
            SELECT timeline_id, age, mood, emoji, events, notes
            FROM slides
            WHERE id = ?
            """,
            (job_row["slide_id"],),
        ).fetchone()
        conn.commit()
        result = dict(job_row)
        if slide_row is not None:
            result.update(
                {
                    "timeline_id": slide_row["timeline_id"],
                    "age": slide_row["age"],
                    "mood": slide_row["mood"],
                    "emoji": slide_row["emoji"],
                    "events": _parse_events(slide_row["events"]),
                    "notes": slide_row["notes"] or "",
                }
            )
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_image_job(
    slide_id: int,
    prompt: str,
    image_path: str,
    db_path: str | None = None,
    expected_hash: str | None = None,
) -> bool:
    """Mark a claimed image job as ready with its generated prompt and image path.

    When expected_hash is provided, only updates if status is 'processing' and
    content_hash still matches expected_hash. Returns True if a row was updated.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            if expected_hash is not None:
                cur = conn.execute(
                    """
                    UPDATE mood_image_jobs
                    SET status = 'ready',
                        prompt = ?,
                        image_path = ?,
                        error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE slide_id = ?
                      AND status = 'processing'
                      AND content_hash = ?
                    """,
                    (prompt, image_path, slide_id, expected_hash),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE mood_image_jobs
                    SET status = 'ready',
                        prompt = ?,
                        image_path = ?,
                        error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE slide_id = ?
                    """,
                    (prompt, image_path, slide_id),
                )
            return cur.rowcount > 0
    finally:
        conn.close()


def fail_image_job(
    slide_id: int,
    error: str,
    db_path: str | None = None,
    expected_hash: str | None = None,
) -> bool:
    """Mark an image job as failed with an error message.

    When expected_hash is provided, only updates if status is 'processing' and
    content_hash still matches expected_hash. Returns True if a row was updated.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            if expected_hash is not None:
                cur = conn.execute(
                    """
                    UPDATE mood_image_jobs
                    SET status = 'failed',
                        error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE slide_id = ?
                      AND status = 'processing'
                      AND content_hash = ?
                    """,
                    (str(error), slide_id, expected_hash),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE mood_image_jobs
                    SET status = 'failed',
                        error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE slide_id = ?
                    """,
                    (str(error), slide_id),
                )
            return cur.rowcount > 0
    finally:
        conn.close()


def get_slide_image_map(timeline_id: str, db_path: str | None = None) -> dict[int, dict]:
    """Return a mapping of slide_id to mood_image_jobs row dict for a timeline."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT j.slide_id, j.content_hash, j.status, j.prompt, j.image_path, j.error, j.updated_at
            FROM mood_image_jobs j
            JOIN slides s ON s.id = j.slide_id
            WHERE s.timeline_id = ?
            """,
            (timeline_id,),
        ).fetchall()
        return {int(row["slide_id"]): dict(row) for row in rows}
    finally:
        conn.close()


def delete_slide_image_file(slide_id: int, images_dir: str | None = None) -> None:
    """Delete a slide's generated PNG image from disk if it exists."""
    target_dir = (
        images_dir
        or os.environ.get("MOOD_IMAGES_DIR")
        or os.path.join(os.path.dirname(os.path.abspath(DATABASE_PATH)), "mood_images")
    )
    path = os.path.join(target_dir, f"{int(slide_id)}.png")
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
