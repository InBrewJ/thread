import base64
import json
import os
import secrets
import urllib.parse
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import requests

import db
import monmon

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "timeline-inbrewj-default-secret-key-2026")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# In production behind HTTPS reverse proxy, respect X-Forwarded-Proto
app.config["PREFERRED_URL_SCHEME"] = "https"

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
CORS(app)

# Initialize SQLite database
db_path = os.environ.get("DATABASE_PATH")
db.init_db(db_path)

IMAGES_DIR = (
    os.environ.get("IMAGES_DIR")
    or os.environ.get("MOOD_IMAGES_DIR")
    or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "mood_images")
)

# Rauthy / OIDC configuration
RAUTHY_PUBLIC_URL = os.environ.get("RAUTHY_URL", "https://identity.dryark.uk").rstrip("/")
RAUTHY_INTERNAL_URL = os.environ.get("RAUTHY_INTERNAL_URL", "http://127.0.0.1:8085").rstrip("/")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "thread-web")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "cb2d9d9b89e03f3c701582f2ed75493dbc1406e3432d6814aa1b090f67330fe7")
PUBLIC_APP_URL = os.environ.get("APP_URL", "https://thread.dryark.uk").rstrip("/")

# In-memory presentation room state:
# rooms[timeline_id] = { "current_slide": int, "leader_sid": str, "followers": set(sid), "image_mode": str }
rooms = {}
room_states = rooms


def get_base_url():
    """Detect external URL based on headers or environment."""
    forwarded_host = request.headers.get("X-Forwarded-Host")
    host = forwarded_host or request.headers.get("Host", "")
    proto = request.headers.get("X-Forwarded-Proto", "http")

    if "dryark.uk" in host:
        return "https://thread.dryark.uk"
    if host:
        return f"{proto}://{host}"
    return PUBLIC_APP_URL


def get_current_user():
    user_id = session.get("user_id")
    session_user = session.get("user")
    if not user_id and isinstance(session_user, dict):
        user_id = session_user.get("id")
    if not user_id:
        return None
    user = db.get_user(user_id)
    if not user:
        fallback = session_user if isinstance(session_user, dict) else {}
        return {
            "id": user_id,
            "username": session.get("username") or fallback.get("username") or user_id,
            "email": session.get("email") or fallback.get("email") or "",
        }
    return user


def _enrich_slide_dict(slide, image_map):
    if slide is None:
        return None
    job = image_map.get(slide["id"])
    image_path = os.path.join(IMAGES_DIR, f"{slide['id']}.png")
    if job:
        slide["image_hash"] = job.get("content_hash")
        slide["image_ready"] = bool(
            job.get("status") == "ready" and os.path.isfile(image_path)
        )
    else:
        slide["image_hash"] = None
        slide["image_ready"] = False
    return slide


def get_enriched_slides(timeline_id):
    slides = db.get_slides(timeline_id)
    image_map = db.get_slide_image_map(timeline_id)
    for slide in slides:
        _enrich_slide_dict(slide, image_map)
    return slides


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            session["return_to"] = request.url
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


def user_can_edit_timeline(user, timeline):
    if not user or not timeline:
        return False
    user_id = str(user.get("id", ""))
    user_email = str(user.get("email", ""))
    timeline_user_id = str(timeline.get("user_id", ""))

    if timeline_user_id and (timeline_user_id == user_id or timeline_user_id == user_email):
        return True

    is_jason_user = (
        user_id == "jason@urawizard.com"
        or user_email.lower() == "jason@urawizard.com"
        or "jason" in user_id.lower()
        or "urawizard" in user_email.lower()
    )
    if is_jason_user and (timeline.get("id") == "timeline-default" or timeline_user_id == "jason@urawizard.com"):
        return True

    return False


def decode_jwt_payload(token_str):
    """Safely decode base64url payload from JWT without external signature verification."""
    try:
        parts = token_str.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        return json.loads(decoded)
    except Exception as e:
        print(f"Error decoding JWT payload: {e}")
        return None


# --------------------------------------------------------------------------
# Auth routes (RAuthy OpenID Connect & Passkeys)
# --------------------------------------------------------------------------


@app.route("/login")
def login():
    return_to = request.args.get("return_to") or session.get("return_to") or url_for("dashboard")
    session["return_to"] = return_to

    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state

    base_url = get_base_url()
    redirect_uri = f"{base_url}/callback"

    auth_url = (
        f"{RAUTHY_PUBLIC_URL}/auth/v1/oidc/authorize"
        f"?client_id={urllib.parse.quote(OIDC_CLIENT_ID)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_type=code"
        f"&scope=openid+profile+email"
        f"&state={urllib.parse.quote(state)}"
    )
    return redirect(auth_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.get("oauth_state")

    if not code:
        flash("Login cancelled or failed - no authorization code received.", "error")
        return redirect(url_for("home"))

    base_url = get_base_url()
    redirect_uri = f"{base_url}/callback"

    token_hosts = [
        RAUTHY_INTERNAL_URL,
        RAUTHY_PUBLIC_URL,
        "http://127.0.0.1:8085",
        "http://localhost:8085",
    ]

    token_data = None
    for th in token_hosts:
        if not th:
            continue
        token_endpoint = f"{th}/auth/v1/oidc/token"
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": OIDC_CLIENT_ID,
            "client_secret": OIDC_CLIENT_SECRET,
        }
        try:
            resp = requests.post(token_endpoint, data=payload, timeout=8)
            if resp.status_code == 200:
                token_data = resp.json()
                break
            else:
                print(f"Token exchange failed at {token_endpoint}: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"Token connection error to {token_endpoint}: {e}")

    if not token_data or "id_token" not in token_data:
        flash("Authentication token exchange failed with RAuthy.", "error")
        return redirect(url_for("home"))

    claims = decode_jwt_payload(token_data["id_token"])
    if not claims:
        flash("Failed to decode user claims from ID token.", "error")
        return redirect(url_for("home"))

    user_id = claims.get("sub") or claims.get("preferred_username") or claims.get("email")
    email = claims.get("email", "")
    username = claims.get("preferred_username") or email.split("@")[0] if email else user_id
    display_name = claims.get("name") or username

    # Ensure user exists in SQLite
    db.upsert_user(user_id, email, username, display_name)

    # If the user is Jason or matches seeded user, ensure default timeline ownership is linked to jason@urawizard.com
    if (
        user_id == "jason@urawizard.com"
        or email.lower() == "jason@urawizard.com"
        or "jason" in user_id.lower()
        or "jason" in email.lower()
        or "jason" in username.lower()
        or "urawizard" in email.lower()
    ):
        conn = db.get_connection()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, email, username, display_name) VALUES (?, ?, ?, ?)",
                ("jason@urawizard.com", "jason@urawizard.com", "jason", "Jason Brewer"),
            )
            conn.execute("UPDATE timelines SET user_id = 'jason@urawizard.com' WHERE id = 'timeline-default'")
        conn.close()

    session["user_id"] = user_id
    session["username"] = username
    session["email"] = email
    if "id_token" in token_data:
        session["id_token"] = token_data["id_token"]
    if "access_token" in token_data:
        session["access_token"] = token_data["access_token"]

    # Check if user has at least one timeline; if not, create an initial one
    user_timelines = db.get_timelines_by_user(user_id)
    if not user_timelines:
        db.create_timeline(user_id, title=f"{display_name}'s Mood Thread")

    return_to = session.pop("return_to", url_for("dashboard"))
    resp = redirect(return_to)
    if "id_token" in token_data:
        resp.set_cookie("id_token", token_data["id_token"], httponly=True, samesite="Lax")
    return resp


@app.route("/logout")
@app.route("/centralLogout")
def logout():
    id_token = session.get("id_token") or request.cookies.get("id_token")
    session.clear()

    logout_url = f"{RAUTHY_PUBLIC_URL}/auth/v1/oidc/logout"
    base_url = get_base_url()
    if not base_url.endswith("/"):
        base_url += "/"

    params = {
        "post_logout_redirect_uri": base_url,
    }
    if id_token:
        params["id_token_hint"] = id_token

    target = f"{logout_url}?{urllib.parse.urlencode(params)}"
    resp = redirect(target)
    resp.delete_cookie("id_token")
    return resp


@app.route("/dev-login")
def dev_login():
    """Local development login fallback when working offline."""
    user_id = request.args.get("user", "jason@urawizard.com")
    db.upsert_user(user_id, email=user_id, username=user_id.split("@")[0], display_name="Local User")
    session["user_id"] = user_id
    session["username"] = user_id.split("@")[0]
    session["email"] = user_id
    return redirect(url_for("dashboard"))


# --------------------------------------------------------------------------
# Main Web Views
# --------------------------------------------------------------------------


@app.route("/")
def home():
    user = get_current_user()
    if user:
        return redirect(url_for("dashboard"))
    return render_template("landing.html", user=None)


@app.route("/dashboard")
def dashboard():
    user = get_current_user()
    if not user:
        return redirect(url_for("home"))

    timelines = db.get_timelines_by_user(user["id"])
    base_url = get_base_url()
    return render_template("dashboard.html", user=user, timelines=timelines, base_url=base_url)


# Isolated Leader interface
@app.route("/lead/<timeline_id>")
@app.route("/leader/<timeline_id>")
@login_required
def lead_timeline(timeline_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        flash("Timeline not found.", "error")
        return redirect(url_for("dashboard"))

    user = get_current_user()
    if not user_can_edit_timeline(user, timeline):
        flash("You do not have permission to lead this timeline.", "error")
        return redirect(url_for("dashboard"))

    slides = get_enriched_slides(timeline_id)
    base_url = get_base_url()
    follower_url = f"{base_url}/watch/{timeline['share_token']}"

    return render_template(
        "leader.html",
        timeline=timeline,
        slides=slides,
        follower_url=follower_url,
        base_url=base_url,
        user=user,
    )


# Isolated Follower interface (publicly shareable via unique share_token)
@app.route("/watch/<share_token>")
@app.route("/t/<share_token>")
def watch_timeline(share_token):
    if share_token == "default":
        flash("Timeline not found or link has expired.", "error")
        return redirect(url_for("home"))

    timeline = db.get_timeline_by_share_token(share_token)
    if not timeline:
        flash("Timeline not found or link has expired.", "error")
        return redirect(url_for("home"))

    slides = get_enriched_slides(timeline["id"])
    base_url = get_base_url()

    return render_template(
        "watch.html",
        timeline=timeline,
        slides=slides,
        base_url=base_url,
    )


# Mood Editing interface
@app.route("/timeline/<timeline_id>/edit")
@login_required
def edit_timeline(timeline_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        flash("Timeline not found.", "error")
        return redirect(url_for("dashboard"))

    user = get_current_user()
    if not user_can_edit_timeline(user, timeline):
        flash("You do not have permission to edit this timeline.", "error")
        return redirect(url_for("dashboard"))

    slides = get_enriched_slides(timeline_id)
    base_url = get_base_url()
    follower_url = f"{base_url}/watch/{timeline['share_token']}"
    leader_url = f"{base_url}/lead/{timeline['id']}"

    return render_template(
        "edit.html",
        timeline=timeline,
        slides=slides,
        base_url=base_url,
        follower_url=follower_url,
        leader_url=leader_url,
        user=user,
    )


# Legacy URL compatibility
@app.route("/leader")
def legacy_leader():
    user = get_current_user()
    if user:
        return redirect(url_for("dashboard"))
    return redirect(url_for("home"))


@app.route("/watch")
def legacy_watch():
    user = get_current_user()
    if user:
        return redirect(url_for("dashboard"))
    return redirect(url_for("home"))


# --------------------------------------------------------------------------
# REST API (Timeline & Mood Point Editing)
# --------------------------------------------------------------------------


@app.route("/api/timeline/<timeline_id>", methods=["GET"])
def api_get_timeline(timeline_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        return jsonify({"error": "Timeline not found"}), 404
    slides = get_enriched_slides(timeline_id)
    return jsonify({"timeline": dict(timeline), "slides": slides})


@app.route("/api/slides/<int:slide_id>/image", methods=["GET"])
def api_get_slide_image(slide_id):
    conn = db.get_connection()
    try:
        row = conn.execute(
            """
            SELECT s.id AS slide_id, s.timeline_id, t.user_id, t.share_token
            FROM slides s
            JOIN timelines t ON s.timeline_id = t.id
            WHERE s.id = ?
            """,
            (slide_id,),
        ).fetchone()
    finally:
        conn.close()

    user = get_current_user()
    token = request.args.get("token")

    if not row:
        if not user:
            return jsonify({"error": "Forbidden"}), 403
        return jsonify({"error": "Slide not found"}), 404

    timeline_ref = {
        "id": row["timeline_id"],
        "user_id": row["user_id"],
        "share_token": row["share_token"],
    }
    token_authorized = bool(
        token and row["share_token"] and secrets.compare_digest(str(token), str(row["share_token"]))
    )
    session_authorized = bool(user and user_can_edit_timeline(user, timeline_ref))

    if not (session_authorized or token_authorized):
        return jsonify({"error": "Forbidden"}), 403

    image_path = os.path.abspath(os.path.join(IMAGES_DIR, f"{slide_id}.png"))
    if not os.path.isfile(image_path):
        return jsonify({"error": "Image not found"}), 404

    resp = send_file(image_path, mimetype="image/png")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.route("/api/timeline/new", methods=["POST"])
@login_required
def api_create_timeline():
    user = get_current_user()
    data = request.get_json() or {}
    title = data.get("title", f"{user.get('display_name', 'My')} Mood Thread").strip()
    birthday = data.get("birthday", db.DEFAULT_BIRTHDAY).strip()

    timeline_id = db.create_timeline(user["id"], title=title, birthday=birthday)
    return jsonify({"success": True, "timeline_id": timeline_id})


@app.route("/api/timeline/<timeline_id>/settings", methods=["PUT"])
@login_required
def api_update_timeline_settings(timeline_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        return jsonify({"error": "Timeline not found"}), 404

    user = get_current_user()
    if not user_can_edit_timeline(user, timeline):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    title = data.get("title", timeline["title"]).strip()
    birthday = data.get("birthday", timeline["birthday"]).strip()
    is_public = int(data.get("is_public", timeline["is_public"]))

    db.update_timeline(timeline_id, title=title, birthday=birthday, is_public=is_public)
    return jsonify({"success": True})


@app.route("/api/timeline/<timeline_id>/slides", methods=["POST"])
@login_required
def api_add_slide(timeline_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        return jsonify({"error": "Timeline not found"}), 404

    user = get_current_user()
    if not user_can_edit_timeline(user, timeline):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    try:
        age = float(data.get("age", 0))
        mood = float(data.get("mood", 5))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid age or mood values"}), 400

    emoji = str(data.get("emoji", ""))
    events = data.get("events", [])
    if isinstance(events, str):
        events = [e.strip() for e in events.split("\n") if e.strip()]

    notes = str(data.get("notes", ""))
    order_index = int(data.get("order_index", 0))

    slide_id = db.add_slide(timeline_id, age, mood, emoji, events, notes, order_index)
    content_hash = db.compute_slide_content_hash(mood, emoji, events, notes)
    db.enqueue_slide_image_job(slide_id, content_hash)
    new_slide = _enrich_slide_dict(db.get_slide(slide_id), db.get_slide_image_map(timeline_id))

    # Notify any open presentation rooms of updated data
    socketio.emit("timeline_data_updated", {"timeline_id": timeline_id}, room=f"room_{timeline_id}")

    return jsonify({"success": True, "slide": new_slide})


@app.route("/api/timeline/<timeline_id>/slides/<int:slide_id>", methods=["PUT"])
@login_required
def api_update_slide(timeline_id, slide_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        return jsonify({"error": "Timeline not found"}), 404

    user = get_current_user()
    if not user_can_edit_timeline(user, timeline):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    try:
        age = float(data.get("age", 0))
        mood = float(data.get("mood", 5))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid age or mood values"}), 400

    emoji = str(data.get("emoji", ""))
    events = data.get("events", [])
    if isinstance(events, str):
        events = [e.strip() for e in events.split("\n") if e.strip()]

    notes = str(data.get("notes", ""))
    order_index = int(data.get("order_index", 0))

    db.update_slide(slide_id, age, mood, emoji, events, notes, order_index)
    updated_slide = db.get_slide(slide_id)
    if updated_slide:
        content_hash = db.compute_slide_content_hash(mood, emoji, events, notes)
        db.enqueue_slide_image_job(slide_id, content_hash)
        _enrich_slide_dict(updated_slide, db.get_slide_image_map(timeline_id))

    socketio.emit("timeline_data_updated", {"timeline_id": timeline_id}, room=f"room_{timeline_id}")
    return jsonify({"success": True, "slide": updated_slide})


@app.route("/api/timeline/<timeline_id>/slides/<int:slide_id>", methods=["DELETE"])
@login_required
def api_delete_slide(timeline_id, slide_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        return jsonify({"error": "Timeline not found"}), 404

    user = get_current_user()
    if not user_can_edit_timeline(user, timeline):
        return jsonify({"error": "Forbidden"}), 403

    db.delete_slide(slide_id)
    db.delete_slide_image_file(slide_id, IMAGES_DIR)
    socketio.emit("timeline_data_updated", {"timeline_id": timeline_id}, room=f"room_{timeline_id}")
    return jsonify({"success": True})


@app.route("/api/timeline/<timeline_id>/export", methods=["GET"])
@login_required
def api_export_slides(timeline_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        return jsonify({"error": "Timeline not found"}), 404

    user = get_current_user()
    if not user_can_edit_timeline(user, timeline):
        return jsonify({"error": "Forbidden"}), 403

    slides = db.get_slides(timeline_id)
    export_payload = {
        "title": timeline["title"],
        "birthday": timeline["birthday"],
        "slides": [
            {
                "age": s["age"],
                "mood": s["mood"],
                "emoji": s["emoji"],
                "events": s["events"],
                "notes": s["notes"],
            }
            for s in slides
        ],
    }
    return jsonify(export_payload)


@app.route("/api/timeline/<timeline_id>/import", methods=["POST"])
@login_required
def api_import_slides(timeline_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        return jsonify({"error": "Timeline not found"}), 404

    user = get_current_user()
    if not user_can_edit_timeline(user, timeline):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    slides_list = data.get("slides")
    if not isinstance(slides_list, list):
        return jsonify({"error": "Expected a JSON object with a 'slides' array"}), 400

    if data.get("birthday"):
        db.update_timeline(timeline_id, title=timeline["title"], birthday=data["birthday"])

    old_slides = db.get_slides(timeline_id)
    db.replace_slides(timeline_id, slides_list)
    for old_slide in old_slides:
        db.delete_slide_image_file(old_slide["id"], IMAGES_DIR)
    for new_slide in db.get_slides(timeline_id):
        content_hash = db.compute_slide_content_hash(
            new_slide["mood"], new_slide["emoji"], new_slide["events"], new_slide["notes"]
        )
        db.enqueue_slide_image_job(new_slide["id"], content_hash)
    socketio.emit("timeline_data_updated", {"timeline_id": timeline_id}, room=f"room_{timeline_id}")
    return jsonify({"success": True, "count": len(slides_list)})


@app.route("/api/timeline/<timeline_id>/spending-correlation", methods=["GET"])
def get_spending_correlation(timeline_id):
    timeline = db.get_timeline(timeline_id)
    if not timeline:
        timeline = db.get_timeline_by_share_token(timeline_id)
    if not timeline:
        return jsonify({"error": "Timeline not found"}), 404

    slides = db.get_slides(timeline["id"])
    birthday = timeline.get("birthday", "2000-01-01")

    # Determine user identity for MonMon query
    user = get_current_user()
    user_id = None
    if user:
        user_id = user.get("username") or user.get("email") or user.get("id")
    if not user_id:
        user_id = session.get("username") or session.get("email") or "inbrewj"

    token = session.get("id_token") or session.get("access_token")

    correlation = monmon.compute_spending_correlation(
        birthday, slides, user_id=user_id, token=token
    )
    return jsonify(correlation)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "app": "timeline", "version": "2.0"})


@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return send_from_directory(os.path.join(app.root_path, "static"), "apple-touch-icon.png")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(app.root_path, "static"), "apple-touch-icon.png")


# --------------------------------------------------------------------------
# WebSocket Presentation Engine (Isolated Rooms by timeline_id)
# --------------------------------------------------------------------------


def get_room(timeline_id):
    if timeline_id not in rooms:
        rooms[timeline_id] = {
            "current_slide": 0,
            "leader_sid": None,
            "followers": set(),
            "image_mode": "full",
        }
    return rooms[timeline_id]


@socketio.on("join_room")
def handle_join_room(data):
    timeline_id = data.get("timeline_id", "timeline-default")
    role = data.get("role", "follower")
    room_name = f"room_{timeline_id}"

    join_room(room_name)
    r = get_room(timeline_id)

    if role == "leader":
        r["leader_sid"] = request.sid
        print(f"[SocketIO] Leader joined room {room_name} (sid: {request.sid})")
        emit("leader_assigned", {"leader_id": request.sid}, room=room_name)
    else:
        r["followers"].add(request.sid)
        print(f"[SocketIO] Follower joined room {room_name} (sid: {request.sid}, count: {len(r['followers'])})")

    # Send current slide index, follower count, and image mode to caller immediately
    emit("room_state", {
        "current_slide": r["current_slide"],
        "follower_count": len(r["followers"]),
        "has_leader": r["leader_sid"] is not None,
        "image_mode": r["image_mode"],
    })

    # Broadcast updated follower count to room
    emit("follower_count", {"count": len(r["followers"])}, room=room_name)


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    for tid, r in list(rooms.items()):
        room_name = f"room_{tid}"
        if r["leader_sid"] == sid:
            r["leader_sid"] = None
            emit("leader_disconnected", {}, room=room_name)
        if sid in r["followers"]:
            r["followers"].remove(sid)
            emit("follower_count", {"count": len(r["followers"])}, room=room_name)


@socketio.on("change_slide_back")
def handle_change_slide_back(data):
    timeline_id = data.get("timeline_id", "timeline-default")
    r = get_room(timeline_id)

    # Only room leader can change slide
    if request.sid == r["leader_sid"] or not r["leader_sid"]:
        r["leader_sid"] = request.sid
        if r["current_slide"] > 0:
            r["current_slide"] -= 1
        room_name = f"room_{timeline_id}"
        print(f"[SocketIO] Room {room_name} back -> slide {r['current_slide']}")
        emit("update_slide", {"slide_index": r["current_slide"]}, room=room_name)


@socketio.on("change_slide_forward")
def handle_change_slide_forward(data):
    timeline_id = data.get("timeline_id", "timeline-default")
    r = get_room(timeline_id)

    if request.sid == r["leader_sid"] or not r["leader_sid"]:
        r["leader_sid"] = request.sid
        slides = db.get_slides(timeline_id)
        if r["current_slide"] < len(slides) - 1:
            r["current_slide"] += 1
        room_name = f"room_{timeline_id}"
        print(f"[SocketIO] Room {room_name} forward -> slide {r['current_slide']}")
        emit("update_slide", {"slide_index": r["current_slide"]}, room=room_name)


@socketio.on("set_slide")
def handle_set_slide(data):
    timeline_id = data.get("timeline_id", "timeline-default")
    slide_index = int(data.get("slide_index", 0))
    r = get_room(timeline_id)

    if request.sid == r["leader_sid"] or not r["leader_sid"]:
        r["leader_sid"] = request.sid
        slides = db.get_slides(timeline_id)
        if 0 <= slide_index < len(slides):
            r["current_slide"] = slide_index
        room_name = f"room_{timeline_id}"
        print(f"[SocketIO] Room {room_name} set -> slide {r['current_slide']}")
        emit("update_slide", {"slide_index": r["current_slide"]}, room=room_name)


@socketio.on("set_image_mode")
def handle_set_image_mode(data):
    if not isinstance(data, dict):
        return
    timeline_id = data.get("timeline_id", "timeline-default")
    mode = data.get("mode")
    if mode not in ("full", "generative"):
        return

    r = get_room(timeline_id)
    if request.sid == r["leader_sid"] or not r["leader_sid"]:
        r["leader_sid"] = request.sid
        r["image_mode"] = mode
        room_name = f"room_{timeline_id}"
        print(f"[SocketIO] Room {room_name} image_mode -> {mode}")
        emit("update_image_mode", {"mode": r["image_mode"]}, room=room_name)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3001))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1")
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)
