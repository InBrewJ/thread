# Mood Point Image Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate local 400×400 mood-point artwork on `greenstreet` (Pi 5, 4 GB RAM) with a resource-capped `mood-gen` Docker worker (`stable-diffusion.cpp`) and a zero-RAM browser `<canvas>` Generative Art mode, toggled live between Leader and Follower views.

**Architecture:** `server/db.py` enables SQLite WAL mode and manages a `mood_image_jobs` table keyed by `slide_id` and `content_hash` so edits and backfills queue automatically. A separate `mood-gen` Docker service (`mem_limit: 1800m`, `cpus: "2.0"`) auto-downloads quantized GGUF weights into `./data/models/`, synthesizes "Van Gogh meets Munch meets Cézanne" prompts, and renders 400×400 PNGs into `./data/mood_images/<slide_id>.png`. Thread (`server/main.py`, `leader.html`, `watch.html`) serves images with owner-session or `share_token` authorization, syncs `full` vs `generative` mode over Socket.IO, and falls back to `mood-art.js` `<canvas>` rendering whenever an AI image is pending.

**Tech Stack:** Python 3.11, Flask, Flask-SocketIO, SQLite 3 (WAL), Docker Compose, `stable-diffusion.cpp` (ARM64 NEON), Pillow, Vanilla JS + HTML5 Canvas.

**Spec:** `docs/superpowers/specs/2026-11-17-mood-point-images-design.md`

## Global Constraints

- Hardware target: Raspberry Pi 5, **4 GB RAM**, ARM64 Linux (`greenstreet`).
- Docker resource caps on `mood-gen`: `mem_limit: 1800m`, `cpus: "2.0"`, polite inter-job sleep and `nice -n 15`.
- Output image size: **400×400 px** PNG, saved atomically under `./data/mood_images/<slide_id>.png`.
- Model weights stored under `./data/models/` and auto-downloaded on first startup if missing.
- Image endpoint `GET /api/slides/<int:slide_id>/image` must authorize via authenticated owner session OR valid `?token=<share_token>`.
- All data/models/images live under `./data` so `ship.sh` preserves them across deployments.

## Review Focus

- **Sequential ID enumeration on `/api/slides/<id>/image`:** Unauthenticated requests or requests with a `share_token` belonging to a *different* timeline must return `401`/`403`, never leak another user's image. (Pinned in Task 3.)
- **Slide edit invalidation & atomic PNG replacement:** Editing `mood`, `emoji`, `events`, or `notes` must change `content_hash`, re-queue the job as `pending`, and atomically replace `<slide_id>.png` without serving a half-written file. (Pinned in Task 1 & Task 2.)
- **SQLite concurrent access (`timeline` + `mood-gen`):** WAL mode + `busy_timeout` must allow simultaneous reads/writes without `database is locked`, and `claim_next_image_job` must not return the same job twice. (Pinned in Task 1.)
- **Corrupted or partial model download on first boot:** Downloading weights to a `.tmp` file and verifying non-empty size before `os.replace` prevents a crashed boot from leaving unusable weights. (Pinned in Task 2.)
- **Missing/empty `events`, `notes`, or `image_ready=false` in UI:** Empty text must not crash `build_mood_prompt` or `mood-art.js`, and `Full Scene` mode must fall back cleanly to `<canvas>` when `image_ready` is false or `<img>` fails to load. (Pinned in Task 2, Task 4, Task 5.)

---

### Task 1: SQLite WAL Mode, `mood_image_jobs` Schema & Queue Helpers

**Files:**
- Modify: `server/db.py:77-134` (WAL mode + `mood_image_jobs` table + helper functions)
- Create: `tests/test_mood_jobs_db.py`

**Interfaces:**
- Produces in `server/db.py`:
  - `compute_slide_content_hash(mood: float, emoji: str, events: list, notes: str) -> str`
  - `enqueue_slide_image_job(slide_id: int, content_hash: str, db_path: str | None = None) -> bool`
  - `backfill_missing_image_jobs(db_path: str | None = None) -> int`
  - `claim_next_image_job(db_path: str | None = None) -> dict | None`
  - `complete_image_job(slide_id: int, prompt: str, image_path: str, db_path: str | None = None) -> None`
  - `fail_image_job(slide_id: int, error: str, db_path: str | None = None) -> None`
  - `get_slide_image_map(timeline_id: str, db_path: str | None = None) -> dict[int, dict]`
  - `delete_slide_image_file(slide_id: int, images_dir: str | None = None) -> None`

- [ ] **Step 1: Write the failing tests in `tests/test_mood_jobs_db.py`**

```python
import os
import tempfile
import unittest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import db


class TestMoodJobsDB(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_wal_mode_enabled(self):
        conn = db.get_connection(self.db_path)
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        conn.close()
        self.assertEqual(mode.lower(), "wal")

    def test_content_hash_changes_on_edit(self):
        h1 = db.compute_slide_content_hash(5, "😊", ["Job"], "Good day")
        h2 = db.compute_slide_content_hash(6, "😊", ["Job"], "Good day")
        h3 = db.compute_slide_content_hash(5, "😊", ["Job"], "Good day")
        self.assertEqual(h1, h3)
        self.assertNotEqual(h1, h2)

    def test_backfill_and_claim_lifecycle(self):
        queued = db.backfill_missing_image_jobs(self.db_path)
        self.assertGreater(queued, 0)
        # Re-running backfill without changes queues 0
        self.assertEqual(db.backfill_missing_image_jobs(self.db_path), 0)

        job = db.claim_next_image_job(self.db_path)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "processing")

        db.complete_image_job(job["slide_id"], "test prompt", f"mood_images/{job['slide_id']}.png", self.db_path)
        img_map = db.get_slide_image_map("timeline-default", self.db_path)
        self.assertEqual(img_map[job["slide_id"]]["status"], "ready")

    def test_enqueue_requeues_when_hash_changes(self):
        db.backfill_missing_image_jobs(self.db_path)
        job = db.claim_next_image_job(self.db_path)
        db.complete_image_job(job["slide_id"], "p", "mood_images/x.png", self.db_path)
        # Same hash -> no-op
        self.assertFalse(db.enqueue_slide_image_job(job["slide_id"], job["content_hash"], self.db_path))
        # New hash -> re-queued
        self.assertTrue(db.enqueue_slide_image_job(job["slide_id"], "new-hash-123", self.db_path))
        next_job = db.claim_next_image_job(self.db_path)
        self.assertEqual(next_job["slide_id"], job["slide_id"])
        self.assertEqual(next_job["content_hash"], "new-hash-123")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests/test_mood_jobs_db.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'compute_slide_content_hash'`

- [ ] **Step 3: Update `server/db.py` with WAL mode, `mood_image_jobs` table, and queue helpers**

In `get_connection`, enable `PRAGMA journal_mode = WAL;` and `sqlite3.connect(path, timeout=10.0)` + `PRAGMA busy_timeout = 5000;`. In `init_db`, create `mood_image_jobs` (`slide_id INTEGER PRIMARY KEY`, `content_hash TEXT NOT NULL`, `status TEXT NOT NULL DEFAULT 'pending'`, `prompt TEXT`, `image_path TEXT`, `error TEXT`, `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`, `FOREIGN KEY (slide_id) REFERENCES slides(id) ON DELETE CASCADE`) and `idx_mood_jobs_status`. Implement all 8 exported functions listed in Interfaces.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_mood_jobs_db.py -v`
Expected: PASS (all 4 tests OK)

- [ ] **Step 5: Commit**

```bash
git add server/db.py tests/test_mood_jobs_db.py
git commit -m "feat(db): add WAL mode and mood_image_jobs queue helpers"
```

---

### Task 2: Prompt Builder & `mood-gen` Worker Service

**Files:**
- Create: `mood-gen/prompt_builder.py`
- Create: `mood-gen/worker.py`
- Create: `mood-gen/Dockerfile`
- Create: `mood-gen/requirements.txt`
- Modify: `docker-compose.yaml` (add `mood-gen` service with `mem_limit: 1800m`, `cpus: "2.0"`, `./data:/app/data`)
- Create: `tests/test_mood_gen.py`

**Interfaces:**
- Consumes from `server/db.py`: `init_db`, `backfill_missing_image_jobs`, `claim_next_image_job`, `complete_image_job`, `fail_image_job`
- Produces in `mood-gen/prompt_builder.py`:
  - `build_mood_prompt(mood: float, emoji: str, events: list, notes: str) -> str`
- Produces in `mood-gen/worker.py`:
  - `ensure_model_weights(model_path: str, model_url: str, downloader=None) -> str`
  - `render_and_resize(sd_cli_bin: str, model_path: str, prompt: str, output_path: str, gen_size: int = 256, out_size: int = 400, steps: int = 4, runner=None) -> None`
  - `process_one_job(db_path: str, images_dir: str, sd_cli_bin: str, model_path: str, runner=None) -> bool`

- [ ] **Step 1: Write the failing tests in `tests/test_mood_gen.py`**

```python
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mood-gen"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import db
import prompt_builder
import worker


class TestMoodGen(unittest.TestCase):
    def test_prompt_builder_handles_mood_bands_and_empty_text(self):
        low = prompt_builder.build_mood_prompt(1.5, "🌧️", [], "")
        mid = prompt_builder.build_mood_prompt(5.0, "🎸", ["Picked up guitar"], None)
        high = prompt_builder.build_mood_prompt(9.0, "🚀", ["Robot project"], "Feeling electric")
        for p in (low, mid, high):
            self.assertIn("Van Gogh", p)
            self.assertIn("Munch", p)
            self.assertIn("Cezanne", p)
        self.assertNotEqual(low, high)
        self.assertIn("robot", high.lower())

    def test_ensure_model_weights_atomic_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "models", "model.gguf")
            def fake_download(url, tmp_path):
                with open(tmp_path, "wb") as f:
                    f.write(b"gguf-bytes")
            path = worker.ensure_model_weights(target, "https://example.com/m.gguf", downloader=fake_download)
            self.assertEqual(path, target)
            self.assertTrue(os.path.exists(target))
            self.assertFalse(os.path.exists(target + ".tmp"))

    def test_process_one_job_marks_ready_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            images_dir = os.path.join(tmp, "mood_images")
            db.init_db(db_path)
            db.backfill_missing_image_jobs(db_path)

            def fake_runner(cmd, **kwargs):
                # Find -o <raw_path> and write a valid tiny PNG
                out_idx = cmd.index("-o") + 1
                raw_path = cmd[out_idx]
                from PIL import Image
                Image.new("RGB", (64, 64), (100, 120, 200)).save(raw_path)
                class Res:
                    returncode = 0
                    stderr = ""
                return Res()

            processed = worker.process_one_job(db_path, images_dir, "sd-cli", "/fake/model.gguf", runner=fake_runner)
            self.assertTrue(processed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests/test_mood_gen.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prompt_builder'`

- [ ] **Step 3: Implement `mood-gen/prompt_builder.py`, `mood-gen/worker.py`, `mood-gen/requirements.txt`, `mood-gen/Dockerfile`, and `docker-compose.yaml` service**

- `prompt_builder.py`: Build prompts combining the base style (`"oil painting, Van Gogh swirling impasto brushstrokes, Edvard Munch expressionist emotion, Paul Cezanne structural color facets, abstract figurative"`), mood band atmosphere (`0-3` stormy/indigo, `4-6` earthy/ochre, `7-10` luminous/gold), keyword-extracted motifs (humanoid figures, animals, robots when present or as subtle symbolic anchors), and sanitized `events`/`notes`.
- `worker.py`: Atomic model download to `<model_path>.tmp` + `os.replace`, invoke `sd-cli` with `-m`, `-p`, `-W`, `-H`, `--steps`, `-o` to a temp raw PNG, resize with `Image.Resampling.LANCZOS` to `400x400`, save atomically to `<images_dir>/<slide_id>.png`, update job status (`complete_image_job` / `fail_image_job`), and run `main()` loop with `nice(15)` and `INTER_JOB_SLEEP_SECONDS` (default `8`).
- `mood-gen/Dockerfile`: Multi-stage build compiling `stable-diffusion.cpp` (`cmake -DSD_BUILD_EXAMPLES=ON -DCMAKE_BUILD_TYPE=Release`) on Debian/Ubuntu, copying `sd-cli` into `python:3.11-slim` with `Pillow` and `requests`, plus `server/db.py` (or mounting/copying `db.py`).
- `docker-compose.yaml`: Add `mood-gen` service with `mem_limit: 1800m`, `cpus: "2.0"`, `restart: unless-stopped`, `volumes: ["./data:/app/data"]`, env vars `DATABASE_PATH=/app/data/timeline.db`, `IMAGES_DIR=/app/data/mood_images`, `MODEL_PATH=/app/data/models/sd-turbo-q4_0.gguf`, `MODEL_URL=...`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_mood_gen.py -v`
Expected: PASS (if Pillow is missing on host Python, install in a local test venv or verify inside Docker; document exact command run).

- [ ] **Step 5: Commit**

```bash
git add mood-gen/ docker-compose.yaml tests/test_mood_gen.py
git commit -m "feat(mood-gen): add prompt builder, worker, Dockerfile, and compose service"
```

---

### Task 3: Thread Backend Image Route, Slide Payloads & Socket.IO Mode Sync

**Files:**
- Modify: `server/main.py` (slide CRUD hooks, `/api/slides/<int:slide_id>/image`, slide payload enrichment, Socket.IO `set_image_mode` / `update_image_mode` / `room_state`)
- Create: `tests/test_image_api.py`

**Interfaces:**
- Consumes from `server/db.py`: `compute_slide_content_hash`, `enqueue_slide_image_job`, `get_slide_image_map`, `delete_slide_image_file`
- Produces in `server/main.py`:
  - `GET /api/slides/<int:slide_id>/image` (accepts session owner OR `?token=<share_token>`, returns 401/403/404/200 PNG)
  - Slide dicts in `/lead/<id>`, `/watch/<token>`, `/api/timeline/<id>` include `image_ready` (`bool`) and `image_hash` (`str | None`)
  - Socket.IO events: `set_image_mode` (`{ timeline_id, mode }`), `update_image_mode` (`{ mode }`), and `image_mode` in `room_state`

- [ ] **Step 1: Write the failing tests in `tests/test_image_api.py`**

```python
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import db
import main


class TestImageAPI(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.images_dir = os.path.join(self.tmpdir.name, "mood_images")
        os.makedirs(self.images_dir, exist_ok=True)
        db.init_db(self.db_path)
        main.IMAGES_DIR = self.images_dir
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()

        conn = db.get_connection(self.db_path)
        row = conn.execute("SELECT s.id, t.share_token FROM slides s JOIN timelines t ON s.timeline_id = t.id LIMIT 1").fetchone()
        conn.close()
        self.slide_id = row["id"]
        self.share_token = row["share_token"]

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_image_route_rejects_unauthenticated_and_wrong_token(self):
        res = self.client.get(f"/api/slides/{self.slide_id}/image")
        self.assertIn(res.status_code, (401, 403))
        res_bad = self.client.get(f"/api/slides/{self.slide_id}/image?token=wrong-token")
        self.assertIn(res_bad.status_code, (401, 403))

    def test_image_route_allows_valid_share_token(self):
        # Missing file -> 404 when authorized
        res_404 = self.client.get(f"/api/slides/{self.slide_id}/image?token={self.share_token}")
        self.assertEqual(res_404.status_code, 404)

        img_file = os.path.join(self.images_dir, f"{self.slide_id}.png")
        with open(img_file, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        res_ok = self.client.get(f"/api/slides/{self.slide_id}/image?token={self.share_token}")
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.mimetype, "image/png")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests/test_image_api.py -v`
Expected: FAIL with 404 on unauthenticated request (route not defined yet)

- [ ] **Step 3: Implement image route, slide enrichment, CRUD hooks, and Socket.IO mode sync in `server/main.py`**

- Configure `IMAGES_DIR = os.environ.get("IMAGES_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "mood_images"))`.
- Add `GET /api/slides/<int:slide_id>/image`: look up slide's `timeline_id`, `user_id`, `share_token`; authorize if `session.get("user", {}).get("id") == user_id` or `request.args.get("token") == share_token`; return `403` otherwise; serve `<IMAGES_DIR>/<slide_id>.png` with `send_file` or `404` if missing.
- Enrich slide dicts with `image_ready` (job status == `'ready'` and PNG exists on disk) and `image_hash`.
- On slide `POST` / `PUT`, call `enqueue_slide_image_job`; on `DELETE`, call `delete_slide_image_file`.
- In `room_states[timeline_id]`, track `"image_mode": "full"`; emit `image_mode` in `room_state` on `join_room`; add `@socketio.on("set_image_mode")` validating `mode in ("full", "generative")` and broadcasting `update_image_mode`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests/test_image_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/main.py tests/test_image_api.py
git commit -m "feat(api): add authorized slide image route and Socket.IO image mode sync"
```

---

### Task 4: Client-Side Generative Art Engine (`server/static/mood-art.js`)

**Files:**
- Create: `server/static/mood-art.js`

**Interfaces:**
- Produces in `server/static/mood-art.js`:
  - `window.renderMoodArt(canvas: HTMLCanvasElement, slide: object, prevSlide?: object) -> void`

- [ ] **Step 1: Create `server/static/mood-art.js`**

Implement deterministic PRNG (`mulberry32` seeded from `slide.id`, `slide.mood`, `slide.age`, `slide.events`, `slide.notes`) and `window.renderMoodArt(canvas, slide, prevSlide)`:
1. Resize/ensure `canvas.width = 400`, `canvas.height = 400`.
2. Map `slide.mood` (`0–3` stormy indigo/slate, `4–6` earthy ochre/sage, `7–10` luminous gold/cobalt) to a 5-color palette.
3. Draw Cézanne geometric color-faceted background blocks.
4. Draw Munch-inspired undulating horizon/sky bands whose wave amplitude scales with `Math.abs(slide.mood - (prevSlide?.mood ?? slide.mood))`.
5. Draw Van Gogh directional flow-field impasto brushstrokes swirling across the canvas.
6. Draw subtle abstract silhouette motifs (humanoid figure, animal, or robot) chosen deterministically from text keywords (`events` + `notes`) or slide hash.

- [ ] **Step 2: Smoke-test `mood-art.js` syntax**

Run: `node --check server/static/mood-art.js` (if `node` is available; otherwise verify via browser/python check)
Expected: Exit code 0 with no syntax errors.

- [ ] **Step 3: Commit**

```bash
git add server/static/mood-art.js
git commit -m "feat(ui): add deterministic Van Gogh/Munch/Cezanne canvas generative art engine"
```

---

### Task 5: Leader & Follower Presentation UI + Responsive Layout

**Files:**
- Modify: `server/templates/leader.html` (controls toggle + `.mood-artwork-wrap` below Age row + Socket.IO `set_image_mode`/`update_image_mode` + `showSlide` rendering)
- Modify: `server/templates/watch.html` (`.mood-artwork-wrap` below Age row + Socket.IO `update_image_mode` + `showSlide` rendering with `share_token`)
- Modify: `server/static/app.css` (`.mood-artwork-wrap`, `.mood-artwork-frame`, `.mode-toggle` responsive styles)

**Interfaces:**
- Consumes: `window.renderMoodArt` from `/static/mood-art.js`, `GET /api/slides/<id>/image`, Socket.IO `set_image_mode` / `update_image_mode` / `room_state`

- [ ] **Step 1: Add responsive styles to `server/static/app.css`**

Add `.mood-artwork-wrap` (`margin: 0 0 1.25rem 0; display: flex; justify-content: center;`), `.mood-artwork-frame` (`width: 100%; max-width: 400px; aspect-ratio: 1 / 1; border-radius: 12px; overflow: hidden; background: rgba(15, 23, 42, 0.5); border: 1px solid rgba(255, 255, 255, 0.08);`), `.mood-artwork-frame img, .mood-artwork-frame canvas` (`width: 100%; height: 100%; display: block; object-fit: cover;`), and `.mode-toggle` segmented button styles.

- [ ] **Step 2: Update `server/templates/leader.html`**

- Include `<script src="/static/mood-art.js"></script>`.
- Add `Full Scene` / `Generative Art` toggle buttons in the controls bar emitting `socket.emit("set_image_mode", { timeline_id: timelineId, mode })`.
- Insert `<div class="mood-artwork-wrap"><div class="mood-artwork-frame">...</div></div>` inside `.slide-card` directly below the `Age` / `Mood` header row (`div` ending at line 159) and above `Notable Events`.
- Update `showSlide(index)` and `update_image_mode` / `room_state` handlers to display `<img>` when `imageMode === "full" && slide.image_ready`, or render `<canvas>` via `renderMoodArt` otherwise (with `img.onerror` falling back to `<canvas>`).

- [ ] **Step 3: Update `server/templates/watch.html`**

- Include `<script src="/static/mood-art.js"></script>` and `const shareToken = {{ timeline.share_token|tojson }};`.
- Insert the identical `.mood-artwork-wrap` block inside `.slide-card` directly below the `Age` / `Mood` header row and above `Notable Events`.
- Listen for `room_state.image_mode` and `update_image_mode`, and update `showSlide(index)` to use `/api/slides/${slide.id}/image?v=${slide.image_hash}&token=${encodeURIComponent(shareToken)}` when `imageMode === "full" && slide.image_ready`, falling back to `renderMoodArt` otherwise.

- [ ] **Step 4: Run full unit test suite & verify templates render**

Run: `python3 -m unittest discover -s tests -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server/static/app.css server/templates/leader.html server/templates/watch.html
git commit -m "feat(ui): display 400x400 mood artwork and Full Scene/Generative Art sync in leader and watch"
```
