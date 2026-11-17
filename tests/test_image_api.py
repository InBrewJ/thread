import json
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
        self.orig_images_dir = getattr(main, "IMAGES_DIR", None)
        main.IMAGES_DIR = self.images_dir
        main.rooms.clear()
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()

        conn = db.get_connection(self.db_path)
        row = conn.execute(
            "SELECT s.id, t.id AS timeline_id, t.user_id, t.share_token "
            "FROM slides s JOIN timelines t ON s.timeline_id = t.id LIMIT 1"
        ).fetchone()
        conn.close()
        self.slide_id = row["id"]
        self.timeline_id = row["timeline_id"]
        self.owner_id = row["user_id"]
        self.share_token = row["share_token"]

    def tearDown(self):
        if self.orig_images_dir is not None:
            main.IMAGES_DIR = self.orig_images_dir
        main.rooms.clear()
        self.tmpdir.cleanup()

    def _login_as(self, user_id):
        db.upsert_user(user_id, email=user_id, username=user_id.split("@")[0], display_name=user_id)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["username"] = user_id.split("@")[0]
            sess["email"] = user_id

    def test_image_route_rejects_unauthenticated_and_wrong_token(self):
        res = self.client.get(f"/api/slides/{self.slide_id}/image")
        self.assertIn(res.status_code, (401, 403))
        res_bad = self.client.get(f"/api/slides/{self.slide_id}/image?token=wrong-token")
        self.assertIn(res_bad.status_code, (401, 403))
        # Non-existent slide id must also return 401/403 to unauthenticated callers (no ID enumeration)
        res_missing = self.client.get("/api/slides/999999/image")
        self.assertIn(res_missing.status_code, (401, 403))

    def test_image_route_allows_valid_share_token(self):
        res_404 = self.client.get(f"/api/slides/{self.slide_id}/image?token={self.share_token}")
        self.assertEqual(res_404.status_code, 404)

        img_file = os.path.join(self.images_dir, f"{self.slide_id}.png")
        with open(img_file, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        res_ok = self.client.get(f"/api/slides/{self.slide_id}/image?token={self.share_token}")
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.mimetype, "image/png")
        res_ok.close()

    def test_image_route_allows_session_owner_and_rejects_other_user(self):
        img_file = os.path.join(self.images_dir, f"{self.slide_id}.png")
        with open(img_file, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

        # Non-owner session must be rejected
        self._login_as("stranger@example.com")
        res_forbidden = self.client.get(f"/api/slides/{self.slide_id}/image")
        self.assertEqual(res_forbidden.status_code, 403)

        # Owner session must be allowed
        self._login_as(self.owner_id)
        res_owner = self.client.get(f"/api/slides/{self.slide_id}/image")
        self.assertEqual(res_owner.status_code, 200)
        self.assertEqual(res_owner.mimetype, "image/png")
        res_owner.close()

        # Also supports session["user"] = {"id": ...} dict shape
        with self.client.session_transaction() as sess:
            sess.clear()
            sess["user"] = {"id": self.owner_id}
        res_user_dict = self.client.get(f"/api/slides/{self.slide_id}/image")
        self.assertEqual(res_user_dict.status_code, 200)
        self.assertEqual(res_user_dict.mimetype, "image/png")
        res_user_dict.close()

    def test_slide_crud_enqueues_and_deletes_image_jobs(self):
        self._login_as(self.owner_id)

        # POST creates slide and enqueues pending job
        res_post = self.client.post(
            f"/api/timeline/{self.timeline_id}/slides",
            json={"age": 30, "mood": 8, "emoji": "🌟", "events": ["New chapter"], "notes": "Sunny"},
        )
        self.assertEqual(res_post.status_code, 200)
        created_slide = res_post.get_json()["slide"]
        sid = created_slide["id"]
        expected_hash = db.compute_slide_content_hash(8, "🌟", ["New chapter"], "Sunny")

        img_map = db.get_slide_image_map(self.timeline_id, self.db_path)
        self.assertIn(sid, img_map)
        self.assertEqual(img_map[sid]["status"], "pending")
        self.assertEqual(img_map[sid]["content_hash"], expected_hash)

        # Complete job and create PNG on disk
        db.complete_image_job(sid, "prompt", f"mood_images/{sid}.png", self.db_path)
        png_path = os.path.join(self.images_dir, f"{sid}.png")
        with open(png_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

        # PUT with unchanged image-relevant fields keeps ready status
        res_put_same = self.client.put(
            f"/api/timeline/{self.timeline_id}/slides/{sid}",
            json={"age": 31, "mood": 8, "emoji": "🌟", "events": ["New chapter"], "notes": "Sunny"},
        )
        self.assertEqual(res_put_same.status_code, 200)
        img_map = db.get_slide_image_map(self.timeline_id, self.db_path)
        self.assertEqual(img_map[sid]["status"], "ready")

        # PUT with changed notes re-queues to pending with updated hash
        res_put_changed = self.client.put(
            f"/api/timeline/{self.timeline_id}/slides/{sid}",
            json={"age": 31, "mood": 4, "emoji": "🌧️", "events": ["Rain"], "notes": "Stormy"},
        )
        self.assertEqual(res_put_changed.status_code, 200)
        new_hash = db.compute_slide_content_hash(4, "🌧️", ["Rain"], "Stormy")
        img_map = db.get_slide_image_map(self.timeline_id, self.db_path)
        self.assertEqual(img_map[sid]["status"], "pending")
        self.assertEqual(img_map[sid]["content_hash"], new_hash)

        # DELETE removes slide, job row, and PNG file
        res_del = self.client.delete(f"/api/timeline/{self.timeline_id}/slides/{sid}")
        self.assertEqual(res_del.status_code, 200)
        self.assertFalse(os.path.exists(png_path))
        img_map = db.get_slide_image_map(self.timeline_id, self.db_path)
        self.assertNotIn(sid, img_map)

    def test_slide_payloads_include_image_ready_and_image_hash(self):
        # Before job exists: image_ready is False, image_hash is None
        res_api = self.client.get(f"/api/timeline/{self.timeline_id}")
        self.assertEqual(res_api.status_code, 200)
        slides = res_api.get_json()["slides"]
        target = next(s for s in slides if s["id"] == self.slide_id)
        self.assertFalse(target["image_ready"])
        self.assertIsNone(target["image_hash"])

        # Enqueue and complete job, but no file on disk yet -> still image_ready=False
        content_hash = db.compute_slide_content_hash(
            target["mood"], target["emoji"], target["events"], target["notes"]
        )
        db.enqueue_slide_image_job(self.slide_id, content_hash, self.db_path)
        db.complete_image_job(self.slide_id, "p", f"mood_images/{self.slide_id}.png", self.db_path)

        res_api_no_file = self.client.get(f"/api/timeline/{self.timeline_id}")
        target_no_file = next(s for s in res_api_no_file.get_json()["slides"] if s["id"] == self.slide_id)
        self.assertFalse(target_no_file["image_ready"])
        self.assertEqual(target_no_file["image_hash"], content_hash)

        # Write PNG file -> image_ready=True in API, /lead, and /watch
        with open(os.path.join(self.images_dir, f"{self.slide_id}.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

        res_api_ready = self.client.get(f"/api/timeline/{self.timeline_id}")
        target_ready = next(s for s in res_api_ready.get_json()["slides"] if s["id"] == self.slide_id)
        self.assertTrue(target_ready["image_ready"])
        self.assertEqual(target_ready["image_hash"], content_hash)

        # Check /watch/<share_token> HTML embeds enriched slides JSON
        res_watch = self.client.get(f"/watch/{self.share_token}")
        self.assertEqual(res_watch.status_code, 200)
        watch_html = res_watch.get_data(as_text=True)
        self.assertIn('"image_ready": true', watch_html)
        self.assertIn(content_hash, watch_html)

        # Check /lead/<timeline_id> HTML embeds enriched slides JSON
        self._login_as(self.owner_id)
        res_lead = self.client.get(f"/lead/{self.timeline_id}")
        self.assertEqual(res_lead.status_code, 200)
        lead_html = res_lead.get_data(as_text=True)
        self.assertIn('"image_ready": true', lead_html)
        self.assertIn(content_hash, lead_html)

    def test_socketio_image_mode_sync(self):
        leader = main.socketio.test_client(main.app)
        follower = main.socketio.test_client(main.app)

        leader.emit("join_room", {"timeline_id": self.timeline_id, "role": "leader"})
        leader_msgs = leader.get_received()
        room_states = [m["args"][0] for m in leader_msgs if m["name"] == "room_state"]
        self.assertEqual(len(room_states), 1)
        self.assertEqual(room_states[0]["image_mode"], "full")

        follower.emit("join_room", {"timeline_id": self.timeline_id, "role": "follower"})
        follower_msgs = follower.get_received()
        f_room_states = [m["args"][0] for m in follower_msgs if m["name"] == "room_state"]
        self.assertEqual(f_room_states[0]["image_mode"], "full")

        # Invalid mode is ignored
        leader.emit("set_image_mode", {"timeline_id": self.timeline_id, "mode": "invalid"})
        self.assertEqual(
            [m for m in follower.get_received() if m["name"] == "update_image_mode"],
            [],
        )

        # Follower cannot override leader's image mode
        follower.emit("set_image_mode", {"timeline_id": self.timeline_id, "mode": "generative"})
        self.assertEqual(
            [m for m in leader.get_received() if m["name"] == "update_image_mode"],
            [],
        )

        # Leader switches mode -> broadcast update_image_mode
        leader.emit("set_image_mode", {"timeline_id": self.timeline_id, "mode": "generative"})
        follower_updates = [m["args"][0] for m in follower.get_received() if m["name"] == "update_image_mode"]
        self.assertEqual(follower_updates, [{"mode": "generative"}])

        # Late joiner receives updated image_mode in room_state
        late = main.socketio.test_client(main.app)
        late.emit("join_room", {"timeline_id": self.timeline_id, "role": "follower"})
        late_states = [m["args"][0] for m in late.get_received() if m["name"] == "room_state"]
        self.assertEqual(late_states[0]["image_mode"], "generative")

        leader.disconnect()
        follower.disconnect()
        late.disconnect()

    def test_leader_and_watch_templates_render_artwork_ui(self):
        res_watch = self.client.get(f"/watch/{self.share_token}")
        self.assertEqual(res_watch.status_code, 200)
        watch_html = res_watch.get_data(as_text=True)
        self.assertIn("/static/mood-art.js", watch_html)
        self.assertIn("mood-artwork-frame", watch_html)
        self.assertIn("update_image_mode", watch_html)
        self.assertIn("shareToken", watch_html)

        self._login_as(self.owner_id)
        res_lead = self.client.get(f"/lead/{self.timeline_id}")
        self.assertEqual(res_lead.status_code, 200)
        lead_html = res_lead.get_data(as_text=True)
        self.assertIn("/static/mood-art.js", lead_html)
        self.assertIn("mood-artwork-frame", lead_html)
        self.assertIn("Full Scene", lead_html)
        self.assertIn("Generative Art", lead_html)
        self.assertIn("set_image_mode", lead_html)
        self.assertIn("update_image_mode", lead_html)
