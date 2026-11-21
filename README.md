# Thread | Mood & Mental Health Timeline Platform

Thread is a private mental health and mood tracking platform designed to visualize life journeys, detect turbulent or chaotic phases, and present live synchronized slide experiences to audiences in real time.

Live Instance: **[thread.dryark.uk](https://thread.dryark.uk)**

---

## Features

### 1. Private Mood Mapping & Volatility Analysis
- **Milestone Points**: Record life stages with Age, Mood score (0–10 scale), Emoji, Notable Life Events, and freeform Notes.
- **Continuous Mood Curve**: Dynamic cardinal splines render smooth, interpolated mood curves across life stages.
- **Visual Mood Bands**: Color-coded mood bands indicating Depression ($0\text{–}3$), Euthymia/Stability ($4\text{–}6$), and Hypomania/Mania ($7\text{–}10$).
- **Turbulence Indicators**: Visualizes relative rate-of-change and volatility zones across key life transitions.

### 2. Live Synchronized Presentation System
- **Presenter / Leader Mode (`/lead/<timeline_id>`)**:
  - Full presentation navigation (`<< Back`, `Forward >>`, slide counter, jump-to-point selector).
  - Designed for mobile phones with fast multi-tap responsiveness and zero double-tap zoom jitter.
  - Live follower counter and one-click share link / QR code generator.
- **Audience / Follower View (`/watch/<share_token>`)**:
  - Publicly accessible via unguessable share tokens (no account needed for audience members).
  - Dual synchronized canvases:
    - **Current Life Stage**: Animated spline curve up to the presenter's active slide.
    - **Overview Thread**: Complete lifetime journey overview.
  - Zero-latency slide transitions powered by WebSockets (Socket.IO).

### 3. MonMon Financial & Mood Volatility Correlation
- **Rest API Integration**: Seamlessly communicates with MonMon (`http://127.0.0.1:9000`) over internal loopback using shared RAuthy OIDC Bearer tokens.
- **6-Month Rolling Spend Baseline**: Calculates month-over-month expenditure windows and flags spending surges ($\ge +30\%$ over trailing 6-month average).
- **Mood & Impulse Correlator**: Automatically aligns calendar transaction dates with timeline life stages via birthdate, projecting financial surges directly onto the mood curve to identify manic/chaotic spending patterns.
- **Dynamic Spend Canvas Overlay**: Ambient amber spending envelope, vertical surge guides, and live spend badges on both Leader and Follower slide decks.

### 4. Local AI Mood Artwork Generation (`mood-gen`)
- **100% Local Diffusion Worker**: Runs as an isolated, resource-capped Docker sidecar powered by `stable-diffusion.cpp` and quantized Stable Diffusion v1.5 (`Q4_0` GGUF) weights, ensuring private mood notes and events never leave your hardware.
- **Expressionist Prompt Synthesis**: Deterministically translates each mood point's score, emoji, events, and notes into an abstract expressionist prompt (*Van Gogh impasto brushwork, Munch emotional atmosphere, and Cézanne color facets*), adapting palettes across low ($0\text{–}3$), stable ($4\text{–}6$), and elevated ($7\text{–}10$) mood bands.
- **Dual Presentation Modes & Live Sync**: Presenters can switch live between **Full Scene** (locally generated $400\times400$ PNGs served via authenticated/token-gated `/api/slides/<id>/image`) and **Generative Art** (zero-RAM procedural HTML5 `<canvas>` artwork in `mood-art.js`), broadcast in real time to all followers via Socket.IO with Generative Art serving as an instant fallback while images are generating.
- **Automatic Backfill & Polite Scheduling**: Polls a shared SQLite WAL job queue (`mood_image_jobs`) to backfill existing milestones and regenerate artwork whenever slide content changes, running at low priority (`nice -n 15`) with bounded container CPU/RAM limits.

### 5. Authentication & Data Privacy
- **RAuthy OIDC & Passkeys**: Secure SSO supporting Passkeys (WebAuthn) and OAuth2/OIDC.
- **Strict Data Isolation**: Multi-tenant architecture where each user's timelines and mood points are strictly partitioned by `user_id` in SQLite.
- **Privacy-First Design**: Databases and private user records are excluded from source control (`.gitignore`). New instances start with clean demo placeholder data.

---

## Tech Stack

- **Backend**: Python 3.11+, Flask, Flask-SocketIO, Eventlet
- **Database**: SQLite 3 with foreign key enforcement and WAL mode
- **Frontend**: Vanilla HTML5, CSS3, JavaScript (no bulky frontend framework dependencies)
- **Visuals**: Custom HTML5 Canvas renderer with Cardinal Spline interpolation
- **Real-Time Communication**: Socket.IO 4.x
- **Containerization & Deployment**: Docker, Docker Compose, systemd, Nginx

---

## Quickstart (Local Development)

### 1. Prerequisites
- Python 3.10+
- SQLite 3

### 2. Setup Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
```

### 3. Run the Application
```bash
cd server
export PORT=3001
export DATABASE_PATH=../data/timeline.db
export SECRET_KEY=development-secret-key
python main.py
```
Open [http://localhost:3001](http://localhost:3001) in your browser.

---

## Docker Deployment

To run Thread with Docker Compose:

```bash
docker compose build
docker compose up -d
```

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `PORT` | Listening port for the application | `3001` |
| `DATABASE_PATH` | Path to the SQLite database file | `/app/data/timeline.db` |
| `APP_URL` | Canonical public URL of the application | `https://thread.dryark.uk` |
| `SECRET_KEY` | Flask session signing secret | auto-generated if unset |
| `RAUTHY_URL` | Public URL for RAuthy OIDC SSO | `https://identity.dryark.uk` |
| `RAUTHY_INTERNAL_URL` | Internal URL for direct backend token verification | `http://127.0.0.1:8085` |
| `OIDC_CLIENT_ID` | OAuth2 Client ID registered with RAuthy | `thread-web` |
| `OIDC_CLIENT_SECRET` | OAuth2 Client Secret registered with RAuthy | `""` |

---

## Production Deployment (`ship.sh`)

Automated one-command production synchronization and container redeployment:

```bash
./ship.sh
# Or target a specific remote host:
./ship.sh greenstreet@greenstreet.local
```

### What `ship.sh` does:
1. Syncs application code, templates, static assets, and Docker configurations to the target host.
2. Preserves the host SQLite database volume (`/home/<user>/timeline/data/timeline.db`).
3. Rebuilds the Docker image natively for ARM64/Linux.
4. Starts the service stack with `docker compose up -d --force-recreate`.
5. Probes the `/health` endpoint until HTTP 200 is verified.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
