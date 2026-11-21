import atexit
import os
import re
import shutil
import sys
import tempfile
import unittest

_MODULE_TMPDIR = tempfile.mkdtemp(prefix="thread-edit-test-")
atexit.register(shutil.rmtree, _MODULE_TMPDIR, ignore_errors=True)
os.environ.setdefault("DATABASE_PATH", os.path.join(_MODULE_TMPDIR, "init.db"))

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "server"))
import db
import main


class TestEditPageDateAndOrder(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        db.init_db(self.db_path)
        main.rooms.clear()
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()

        conn = db.get_connection(self.db_path)
        row = conn.execute(
            "SELECT id, user_id FROM timelines LIMIT 1"
        ).fetchone()
        conn.close()
        self.timeline_id = row["id"]
        self.owner_id = row["user_id"]

    def tearDown(self):
        main.rooms.clear()
        self.tmpdir.cleanup()

    def _login_as(self, user_id):
        db.upsert_user(user_id, email=user_id, username=user_id.split("@")[0], display_name=user_id)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["username"] = user_id.split("@")[0]
            sess["email"] = user_id

    def test_edit_table_lists_moods_in_rough_date_desc_order(self):
        self._login_as(self.owner_id)
        res = self.client.get(f"/timeline/{self.timeline_id}/edit")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Extract rendered ages from table rows in order
        ages = [float(a) for a in re.findall(r'class="slide-date" data-age="([^"]+)"', html)]
        self.assertGreaterEqual(len(ages), 2)
        self.assertEqual(ages, sorted(ages, reverse=True))

        # Live preview slidesData should remain chronological ascending
        slides_match = re.search(r"let slidesData = (\[.*?\]);", html)
        self.assertIsNotNone(slides_match)
        import json
        preview_ages = [float(s["age"]) for s in json.loads(slides_match.group(1))]
        self.assertEqual(preview_ages, sorted(preview_ages))

    def test_edit_date_selector_uses_fine_precision_and_live_input(self):
        self._login_as(self.owner_id)
        res = self.client.get(f"/timeline/{self.timeline_id}/edit")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Must not lock age to 0.1-year (~36.5-day) steps
        self.assertNotIn("Math.round(age * 10) / 10", html)
        self.assertIn("Math.round(age * 1000) / 1000", html)
        self.assertIn('step="any" id="slideAge"', html)
        self.assertIn('oninput="onDateChanged()"', html)
        self.assertIn('onchange="onDateChanged()"', html)
        self.assertIn("Date.UTC(", html)

    def test_graph_add_years_to_date_rounds_days_in_utc(self):
        js_path = os.path.join(REPO_ROOT, "server", "static", "graph.js")
        with open(js_path, "r", encoding="utf-8") as f:
            js = f.read()

        self.assertIn("Math.round(years * 365.2425)", js)
        self.assertIn('timeZone: "UTC"', js)


if __name__ == "__main__":
    unittest.main()
