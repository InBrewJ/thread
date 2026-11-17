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

    def test_prompt_builder_sanitizes_and_detects_motifs(self):
        prompt = prompt_builder.build_mood_prompt(
            2.0,
            "🐺",
            ["Adopted a dog\nnew chapter", "Met a friend"],
            "  Quiet   walk\tthrough trees  ",
        )
        self.assertNotIn("\n", prompt)
        self.assertNotIn("\t", prompt)
        self.assertIn("animal", prompt.lower())
        self.assertIn("humanoid", prompt.lower())

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

    def test_ensure_model_weights_skips_existing_and_rejects_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = os.path.join(tmp, "existing.gguf")
            with open(existing, "wb") as f:
                f.write(b"already-here")

            called = []

            def fail_if_called(url, tmp_path):
                called.append(url)

            self.assertEqual(
                worker.ensure_model_weights(existing, "https://example.com/m.gguf", downloader=fail_if_called),
                existing,
            )
            self.assertEqual(called, [])

            empty_target = os.path.join(tmp, "empty.gguf")

            def empty_download(url, tmp_path):
                with open(tmp_path, "wb") as f:
                    pass

            with self.assertRaises(ValueError):
                worker.ensure_model_weights(empty_target, "https://example.com/m.gguf", downloader=empty_download)
            self.assertFalse(os.path.exists(empty_target))
            self.assertFalse(os.path.exists(empty_target + ".tmp"))

    def test_default_model_config_is_consistent(self):
        self.assertTrue(worker.DEFAULT_MODEL_PATH.endswith("sd-v1-5-q4_0.gguf"))
        self.assertIn("stable-diffusion-v1-5-Q4_0.gguf", worker.DEFAULT_MODEL_URL)
        self.assertEqual(worker.DEFAULT_SD_STEPS, 12)
        self.assertEqual(worker.DEFAULT_SD_CFG_SCALE, "7.0")

    def test_process_one_job_marks_ready_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            images_dir = os.path.join(tmp, "mood_images")
            db.init_db(db_path)
            db.backfill_missing_image_jobs(db_path)

            captured_cmds = []

            def fake_runner(cmd, **kwargs):
                captured_cmds.append(cmd)
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
            self.assertEqual(len(captured_cmds), 1)
            cmd = captured_cmds[0]
            self.assertEqual(cmd[cmd.index("--steps") + 1], "12")
            self.assertEqual(cmd[cmd.index("--cfg-scale") + 1], "7.0")

            img_map = db.get_slide_image_map("timeline-default", db_path)
            ready_jobs = [j for j in img_map.values() if j["status"] == "ready"]
            self.assertEqual(len(ready_jobs), 1)
            job = ready_jobs[0]
            out_file = os.path.join(images_dir, f"{job['slide_id']}.png")
            self.assertTrue(os.path.exists(out_file))
            self.assertEqual(job["image_path"], f"mood_images/{job['slide_id']}.png")
            self.assertTrue(job["prompt"])

            from PIL import Image
            with Image.open(out_file) as im:
                self.assertEqual(im.size, (400, 400))

            leftover_tmps = [f for f in os.listdir(images_dir) if ".tmp" in f]
            self.assertEqual(leftover_tmps, [])

    def test_process_one_job_marks_failed_and_empty_queue_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            images_dir = os.path.join(tmp, "mood_images")
            db.init_db(db_path)

            self.assertFalse(worker.process_one_job(db_path, images_dir, "sd-cli", "/fake/model.gguf"))

            slide_id = db.add_slide("timeline-default", 33, 2.5, "🌧️", ["Stormy day"], "Hard week")
            ch = db.compute_slide_content_hash(2.5, "🌧️", ["Stormy day"], "Hard week")
            db.enqueue_slide_image_job(slide_id, ch, db_path)

            def failing_runner(cmd, **kwargs):
                class Res:
                    returncode = 1
                    stderr = "sd-cli out of memory"

                return Res()

            processed = worker.process_one_job(db_path, images_dir, "sd-cli", "/fake/model.gguf", runner=failing_runner)
            self.assertTrue(processed)
            img_map = db.get_slide_image_map("timeline-default", db_path)
            self.assertEqual(img_map[slide_id]["status"], "failed")
            self.assertIn("out of memory", img_map[slide_id]["error"])
