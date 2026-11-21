import atexit
import os
import shutil
import sys
import tempfile
import unittest

_MODULE_TMPDIR = tempfile.mkdtemp(prefix="thread-mobile-test-")
atexit.register(shutil.rmtree, _MODULE_TMPDIR, ignore_errors=True)
os.environ.setdefault("DATABASE_PATH", os.path.join(_MODULE_TMPDIR, "init.db"))

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "server"))
import db
import main


class TestMobileStageLayout(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        db.init_db(self.db_path)
        main.rooms.clear()
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()

        conn = db.get_connection(self.db_path)
        row = conn.execute(
            "SELECT id, user_id, share_token FROM timelines LIMIT 1"
        ).fetchone()
        conn.close()
        self.timeline_id = row["id"]
        self.owner_id = row["user_id"]
        self.share_token = row["share_token"]

    def tearDown(self):
        main.rooms.clear()
        self.tmpdir.cleanup()

    def _login_as(self, user_id):
        db.upsert_user(user_id, email=user_id, username=user_id.split("@")[0], display_name=user_id)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["username"] = user_id.split("@")[0]
            sess["email"] = user_id

    def test_leader_template_includes_mobile_stage_structure_and_controls(self):
        self._login_as(self.owner_id)
        res = self.client.get(f"/lead/{self.timeline_id}")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Viewport & stage classes
        self.assertIn('class="stage-html"', html)
        self.assertNotIn("viewport-fit=cover", html)
        self.assertIn('class="stage-page leader-page"', html)
        self.assertIn("syncStageViewportHeight", html)
        self.assertIn("--stage-vh", html)

        # Core portrait elements
        for token in (
            'class="stage-meta-row"',
            'id="age"',
            'id="rough-date"',
            'id="mood-badge"',
            'id="emoji"',
            'id="mood-artwork-frame"',
            'class="events-section"',
            'id="events-list"',
            'class="leader-graph-section"',
            'id="leader-graph"',
            'id="leader-graph-progress"',
            'id="control-back"',
            'id="control-forward"',
            'class="mobile-nav-tools"',
            'id="slide-select-mobile"',
            'highlightProgress: true',
            'eventsList.scrollTop = 0',
        ):
            self.assertIn(token, html)

    def test_watch_template_includes_mobile_stage_structure_and_separates_full_graph(self):
        res = self.client.get(f"/watch/{self.share_token}")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn('class="stage-html"', html)
        self.assertNotIn("viewport-fit=cover", html)
        self.assertIn('class="stage-page watch-page"', html)
        self.assertIn("syncStageViewportHeight", html)
        self.assertIn("--stage-vh", html)

        # Core portrait elements (progress graph separated from full mood thread)
        for token in (
            'class="stage-meta-row"',
            'id="age"',
            'id="rough-date"',
            'id="mood-badge"',
            'id="emoji"',
            'id="mood-artwork-frame"',
            'class="events-section"',
            'id="events-list"',
            'watch-progress-graph-wrapper',
            'id="graph-current-slide"',
            'watch-full-graph-wrapper',
            'id="graph-all"',
            'id="watch-graph-progress"',
            'eventsList.scrollTop = 0',
        ):
            self.assertIn(token, html)

        # Follower should not have leader navigation buttons
        self.assertNotIn('id="control-back"', html)
        self.assertNotIn('id="control-forward"', html)

    def test_css_locks_portrait_viewport_and_scrolls_only_notable_events(self):
        css_path = os.path.join(REPO_ROOT, "server", "static", "app.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        # Locked single-screen viewport on portrait mobile (overrides body min-height: 100vh)
        self.assertIn("height: 100dvh;", css)
        self.assertIn("height: var(--stage-vh, 100dvh);", css)
        self.assertIn("body.stage-page", css)
        self.assertIn("min-height: 0;", css)
        self.assertIn("position: fixed;", css)
        self.assertIn("overflow: hidden;", css)

        # Only events-list scrolls inside the stage
        self.assertIn(".stage-page .events-list", css)
        self.assertIn("overflow-y: auto;", css)
        self.assertIn("overscroll-behavior: contain;", css)

        # Follower hides full mood thread on mobile
        self.assertIn(".watch-page .watch-full-graph-wrapper", css)
        self.assertIn("display: none !important;", css)

        # Leader pins controls at bottom with safe-area padding
        self.assertIn(".leader-page .controls-row", css)
        self.assertIn("env(safe-area-inset-bottom", css)

    def test_graph_js_supports_compact_mobile_heights_and_progress_highlight(self):
        js_path = os.path.join(REPO_ROOT, "server", "static", "graph.js")
        with open(js_path, "r", encoding="utf-8") as f:
            js = f.read()

        self.assertIn("const isCompact = displayHeight < 110;", js)
        self.assertIn("opt.highlightProgress", js)
        self.assertNotIn("Math.max(rect.height || canvas.height || 200, 160)", js)


if __name__ == "__main__":
    unittest.main()
