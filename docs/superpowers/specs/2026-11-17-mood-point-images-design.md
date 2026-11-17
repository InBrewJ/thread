# Mood Point Image Generation Design

## Intent & Why

Thread presents a personal mental health and mood timeline across live **Leader** (`/lead/<timeline_id>`) and **Follower** (`/watch/<share_token>`) views. Right now, each life stage is represented only by numbers, an emoji, event bullets, and notes. Adding a dedicated 400×400 visual artwork for every mood point gives each stage an immediate emotional presence that updates as the presenter steps through the timeline.

All generation and storage must happen **locally on `greenstreet`** (Raspberry Pi 5, 4 GB RAM) so private mental health notes never leave the user's hardware, while keeping resource usage strictly bounded so existing services (`timeline`, `RAuthy`, `MonMon`, `nginx`) stay responsive.

---

## Goals, Non-Goals & Anti-Goals

### Goals
- Generate a local **400×400 px** image for every mood point (`slides` row), capturing its mood score (0–10), emoji, events, and notes in an abstract expressionist / post-impressionist aesthetic (**"Van Gogh meets Munch meets Cézanne"**), incorporating humanoid figures, animals, and robots where fitting.
- Support **two presentation modes** toggled from Leader mode and synced live to Followers:
  1. **Full Scene**: Local AI-generated PNG saved on disk.
  2. **Generative Art**: Zero-RAM procedural 400×400 `<canvas>` artwork rendered in the browser.
- Automatically **backfill** all existing mood points in SQLite and **queue new/edited** mood points without manual intervention.
- Strictly cap CPU and RAM of the AI generator via Docker so `greenstreet` never stalls or OOMs.

### Non-Goals
- Sub-second or real-time AI image generation on the Pi (a multi-hour or multi-day backfill is explicitly acceptable).
- Cloud APIs, remote GPU offloading, or sending slide text off-device.
- Per-user custom style pickers or interactive prompt editing UI (can be added later if desired).

### Anti-Goals
- Running diffusion inside the `timeline` Flask/Eventlet container (any memory spike risks OOM-killing Thread or stalling WebSockets).
- Unbounded Docker memory/CPU usage that starves `RAuthy`, `MonMon`, or `nginx`.

---

## Constraints

- **Hardware:** Raspberry Pi 5, **4 GB RAM**, ARM64 Linux (`greenstreet`).
- **Memory Budget:** Because `timeline`, `RAuthy`, `MonMon`, and `nginx` share the 4 GB host, the AI worker container must be capped around **`mem_limit: 1800m`** (with `cpus: "2.0"` out of 4 cores and polite inter-job pacing).
- **Privacy & Deployment:** Database (`./data/timeline.db`) and generated assets live under `./data` (preserved by `ship.sh` across deployments and excluded from git).

---

## Approaches Considered

1. **Approach 1 (Selected): Resident `mood-gen` Docker worker (`stable-diffusion.cpp`) + Browser `<canvas>` Generative Art**
   - A separate `mood-gen` container in `docker-compose.yaml` runs ARM64-optimized `stable-diffusion.cpp` (C++, no PyTorch) with a quantized model, polling a SQLite job table to backfill and process new/edited slides into `./data/mood_images/<slide_id>.png`.
   - Generative Art runs client-side on an HTML5 `<canvas>` with zero server RAM and acts as an instant fallback while a slide's Full Scene PNG is pending.
   - *Trade-off:* Keeps ~1.8 GB RAM reserved while `mood-gen` is active (though it can be stopped/started independently of `timeline`), with clean container isolation and automatic generation on slide edits.

2. **Approach 2: On-demand / Cron `mood-gen` batch container + Server-side Pillow Generative Art**
   - Same `stable-diffusion.cpp` worker, but run as a one-shot batch job that exits when idle, paired with pre-rendered Pillow PNGs for Generative Art.
   - *Trade-off:* Zero idle RAM, but newly created/edited slides wait for the next batch run instead of generating automatically, and static PNGs cannot subtly animate like browser canvas.

3. **Approach 3 (Rejected): In-process PyTorch/ONNX diffusion inside `timeline`**
   - *Why rejected:* Shares memory with the live Flask/Socket.IO server, risking OOM kills and WebSocket stalls on a 4 GB Pi 5.

---

## Architecture & Components

### 1. Data Model & Job Queue (`server/db.py`)
- Ensure SQLite runs in `WAL` mode (`PRAGMA journal_mode=WAL;`) with a reasonable `busy_timeout` so `timeline` and `mood-gen` can safely share `/app/data/timeline.db`.
- Add a `mood_image_jobs` table (or columns on `slides` + job table) keyed by `slide_id`:
  - `slide_id` (PK, FK → `slides.id` ON DELETE CASCADE)
  - `content_hash` (SHA-256 of `mood`, `emoji`, `events`, `notes`)
  - `status` (`pending` | `processing` | `ready` | `failed`)
  - `prompt` (TEXT — the synthesized prompt used)
  - `image_path` (TEXT — relative path, e.g. `mood_images/<slide_id>.png`)
  - `error` (TEXT, nullable)
  - `updated_at` (TIMESTAMP)
- **Triggering & Backfill:**
  - When `timeline` creates or updates a slide (`POST /api/timeline/<id>/slides`, `PUT /api/timeline/<id>/slides/<slide_id>`), it computes `content_hash`. If changed or missing, it upserts `mood_image_jobs` with `status = 'pending'`.
  - On startup (and periodically), `mood-gen` scans `slides` for any slide without a `mood_image_jobs` row (or with a mismatched `content_hash`) and enqueues it as `pending`. This handles the full backfill automatically.
  - When a slide is deleted, `ON DELETE CASCADE` removes the job row and `timeline` (or `mood-gen` cleanup) removes `./data/mood_images/<slide_id>.png`.

### 2. Local AI Worker (`mood-gen/` service + `docker-compose.yaml`)
- **Container & Resource Caps:**
  - New `mood-gen` service in `docker-compose.yaml` with:
    - `mem_limit: 1800m`
    - `cpus: "2.0"`
    - `restart: unless-stopped`
    - Volumes: `./data:/app/data` (stores `timeline.db`, `mood_images/`, and `models/` so downloaded weights persist across `ship.sh` deploys).
- **Inference Engine & Model:**
  - Built on **`stable-diffusion.cpp`** compiled for ARM64 with NEON enabled.
  - Uses a small quantized model (e.g., `SD-Turbo` or `BK-SDM-Tiny` / `SD 1.5` in `Q4_0`/`Q5_0` GGUF) sized to stay comfortably under the 1.8 GB container cap.
  - **Auto-provisioning:** On startup, if the configured model file is not present in `/app/data/models/`, `mood-gen` automatically downloads it (e.g., from Hugging Face) to a temporary file and atomically moves it into place, logging progress before starting the job loop.
  - Generates at native fast resolution (e.g., 256×256 or 384×384) and Lanczos-resizes to **400×400** PNG saved atomically (write temp file, then `os.replace`) to `/app/data/mood_images/<slide_id>.png`.
- **Prompt Builder:**
  - Deterministically maps each slide into an expressionist prompt:
    - Base style: *"oil painting, Van Gogh swirling impasto brushstrokes, Edvard Munch expressionist emotion, Paul Cézanne structural color facets, abstract figurative"*
    - Mood band (0–10): maps `0–3` (stormy indigo, heavy shadows, turbulent sky), `4–6` (earthy ochre, muted sage, balanced horizon), `7–10` (luminous gold, vibrant cobalt, radiant motion).
    - Subject cues: extracts keywords/themes from `events` and `notes` (plus `emoji` meaning), weaving in humanoid figures, animals, or robots when relevant or as symbolic anchors.
- **Polite Scheduling:**
  - Processes one job at a time, sleeps briefly between jobs (e.g., 5–10 seconds), and uses `nice -n 15` so `greenstreet` stays responsive throughout a multi-day backfill.

### 3. Generative Art Engine (`server/static/mood-art.js`)
- Pure client-side HTML5 `<canvas>` renderer (400×400 internal resolution, scaled responsively with CSS) with zero server RAM cost.
- Deterministically seeded from `slide.id` + `content_hash` so a given mood point always produces the same composition.
- Visual grammar matching the requested aesthetic:
  - **Cézanne layer:** faceted background color blocks driven by `mood` band palette.
  - **Munch layer:** undulating horizon/sky bands whose amplitude scales with mood volatility (delta from neighboring slides).
  - **Van Gogh layer:** directional flow-field impasto brushstrokes swirling around focal anchors.
  - **Motif layer:** subtle abstract geometric/silhouette figures (humanoid, animal, or robot forms) seeded from text keywords/hash.

### 4. Thread Backend & Presentation UI (`server/main.py`, `leader.html`, `watch.html`, `app.css`)
- **Backend (`server/main.py`):**
  - Serve generated PNGs via `GET /api/slides/<int:slide_id>/image` (returns `./data/mood_images/<slide_id>.png` with `Cache-Control` + `v=<content_hash>` cache busting; returns `404` if not yet ready).
  - **Authorization:** Enforce strict privacy on `/api/slides/<slide_id>/image`: allow access only if the caller has an authenticated session owning the slide's timeline **or** passes `?token=<share_token>` matching the timeline's `share_token` (used by `watch.html`). Return `401`/`403` otherwise so sequential `slide_id` values cannot be enumerated.
  - Include `image_ready` (bool) and `image_hash` (str) in slide JSON payloads for Leader and Watch views.
  - Add room state field `image_mode` (`"full"` | `"generative"`, default `"full"`), persisted in `room_states` in memory alongside `current_slide`, and broadcast via Socket.IO:
    - Leader emits `set_image_mode` (`{ timeline_id, mode }`).
    - Server broadcasts `update_image_mode` (`{ mode }`) to the room and sends current `image_mode` in `room_state` on `join_room`.
- **UI Placement (`leader.html` & `watch.html`):**
  - Inside `.slide-card` (Current Life Stage / Life Stage Details), place the 400×400 artwork container **directly below the `Age` / `Mood` header row** and above `Notable Events`, styled responsively (`width: 100%; max-width: 400px; aspect-ratio: 1 / 1; margin: 0 auto 1.25rem;`) so it sits cleanly under the Age box on desktop and scales naturally on mobile.
  - In `leader.html`, add a compact **`Full Scene` / `Generative Art`** segmented toggle in the presentation controls bar.
  - When `image_mode === "full"` and `slide.image_ready` is true, display the `<img>` from `/api/slides/<id>/image?v=<hash>`; otherwise (in `"generative"` mode or while `"full"` is still pending backfill), display the `<canvas>` rendered by `mood-art.js`.

---

## Left to the Builder

- Benchmarking and selecting the exact quantized GGUF model (`SD-Turbo` vs `BK-SDM-Tiny` vs `SD 1.5` Q4_0) and step count on Pi 5 to stay safely under 1.8 GB peak RSS.
- Exact `stable-diffusion.cpp` CMake/Dockerfile build flags for ARM64 NEON.
- Exact prompt template wording, keyword-to-motif mapping, and `<canvas>` brush/palette tuning.
- Exact CSS spacing/breakpoints for the `.slide-card` image container on mobile and desktop.
