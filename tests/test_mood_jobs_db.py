import os
import sys
import tempfile
import unittest

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
        self.assertIn("age", job)
        self.assertIn("mood", job)
        self.assertIn("emoji", job)
        self.assertIsInstance(job["events"], list)
        self.assertIn("notes", job)

        db.complete_image_job(
            job["slide_id"],
            "test prompt",
            f"mood_images/{job['slide_id']}.png",
            self.db_path,
        )
        img_map = db.get_slide_image_map("timeline-default", self.db_path)
        self.assertEqual(img_map[job["slide_id"]]["status"], "ready")
        self.assertEqual(img_map[job["slide_id"]]["prompt"], "test prompt")

    def test_enqueue_requeues_when_hash_changes(self):
        db.backfill_missing_image_jobs(self.db_path)
        job = db.claim_next_image_job(self.db_path)
        db.complete_image_job(job["slide_id"], "p", "mood_images/x.png", self.db_path)
        # Same hash -> no-op
        self.assertFalse(
            db.enqueue_slide_image_job(job["slide_id"], job["content_hash"], self.db_path)
        )
        # New hash -> re-queued
        self.assertTrue(
            db.enqueue_slide_image_job(job["slide_id"], "new-hash-123", self.db_path)
        )
        next_job = db.claim_next_image_job(self.db_path)
        self.assertEqual(next_job["slide_id"], job["slide_id"])
        self.assertEqual(next_job["content_hash"], "new-hash-123")

    def test_fail_image_job_and_empty_claim(self):
        self.assertIsNone(db.claim_next_image_job(self.db_path))
        slide_id = db.add_slide("timeline-default", 30, 7, "✨", ["Milestone"], "Testing fail")
        content_hash = db.compute_slide_content_hash(7, "✨", ["Milestone"], "Testing fail")
        self.assertTrue(db.enqueue_slide_image_job(slide_id, content_hash, self.db_path))

        job = db.claim_next_image_job(self.db_path)
        self.assertEqual(job["slide_id"], slide_id)
        self.assertEqual(job["events"], ["Milestone"])

        db.fail_image_job(slide_id, "ComfyUI timeout", self.db_path)
        img_map = db.get_slide_image_map("timeline-default", self.db_path)
        self.assertEqual(img_map[slide_id]["status"], "failed")
        self.assertEqual(img_map[slide_id]["error"], "ComfyUI timeout")
        self.assertIsNone(db.claim_next_image_job(self.db_path))

    def test_delete_slide_image_file(self):
        images_dir = os.path.join(self.tmpdir.name, "mood_images")
        os.makedirs(images_dir, exist_ok=True)
        target = os.path.join(images_dir, "42.png")
        with open(target, "wb") as f:
            f.write(b"pngdata")

        db.delete_slide_image_file(42, images_dir)
        self.assertFalse(os.path.exists(target))
        # Calling again on missing file does not raise
        db.delete_slide_image_file(42, images_dir)

    def test_claim_does_not_double_claim_and_cascade_deletes(self):
        db.backfill_missing_image_jobs(self.db_path)
        job1 = db.claim_next_image_job(self.db_path)
        job2 = db.claim_next_image_job(self.db_path)
        self.assertIsNotNone(job1)
        self.assertIsNotNone(job2)
        self.assertNotEqual(job1["slide_id"], job2["slide_id"])

        db.delete_slide(job1["slide_id"])
        img_map = db.get_slide_image_map("timeline-default", self.db_path)
        self.assertNotIn(job1["slide_id"], img_map)
