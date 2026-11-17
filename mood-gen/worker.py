import logging
import os
import signal
import subprocess
import sys
import tempfile
import time

# Allow importing server/db.py when running from repo root or container (/app/db.py)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_SERVER_DIR = os.path.abspath(os.path.join(_THIS_DIR, "..", "server"))
_APP_DIR = os.path.abspath(os.path.join(_THIS_DIR, ".."))
for _candidate in (_THIS_DIR, _APP_DIR, _REPO_SERVER_DIR):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

import db  # noqa: E402
from prompt_builder import build_mood_prompt  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [mood-gen] %(levelname)s %(message)s",
)
logger = logging.getLogger("mood-gen")

DEFAULT_MODEL_URL = (
    "https://huggingface.co/gpustack/stable-diffusion-v1-5-GGUF/resolve/main/"
    "stable-diffusion-v1-5-Q4_0.gguf"
)


def _default_download(url: str, tmp_path: str) -> None:
    import requests

    logger.info("Downloading model weights from %s to %s", url, tmp_path)
    with requests.get(url, stream=True, timeout=(15, 300)) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", "0") or 0)
        downloaded = 0
        last_log = time.monotonic()
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last_log >= 15.0:
                    if total > 0:
                        pct = (downloaded / total) * 100.0
                        logger.info(
                            "Download progress: %.1f%% (%d / %d MiB)",
                            pct,
                            downloaded // (1024 * 1024),
                            total // (1024 * 1024),
                        )
                    else:
                        logger.info("Download progress: %d MiB", downloaded // (1024 * 1024))
                    last_log = now


def ensure_model_weights(model_path: str, model_url: str, downloader=None) -> str:
    """Ensure model weights exist on disk; atomically download if missing or empty."""
    if os.path.exists(model_path) and os.path.getsize(model_path) > 0:
        return model_path

    if not model_url:
        raise ValueError("MODEL_URL is required when model weights are missing")

    parent_dir = os.path.dirname(os.path.abspath(model_path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    tmp_path = f"{model_path}.tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    download_fn = downloader or _default_download
    try:
        download_fn(model_url, tmp_path)
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) <= 0:
            raise ValueError(f"Downloaded model weights at {tmp_path} are empty or missing")
        os.replace(tmp_path, model_path)
        logger.info("Model weights ready at %s (%d bytes)", model_path, os.path.getsize(model_path))
        return model_path
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def render_and_resize(
    sd_cli_bin: str,
    model_path: str,
    prompt: str,
    output_path: str,
    gen_size: int = 256,
    out_size: int = 400,
    steps: int = 4,
    runner=None,
) -> None:
    """Invoke sd-cli to render a raw PNG, Lanczos-resize to out_size, and write atomically."""
    from PIL import Image

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    run_fn = runner or subprocess.run
    tmp_out_path = f"{output_path}.tmp.png"

    try:
        with tempfile.TemporaryDirectory() as raw_dir:
            raw_path = os.path.join(raw_dir, "raw.png")
            cmd = [
                sd_cli_bin,
                "-m",
                model_path,
                "-p",
                prompt,
                "-W",
                str(gen_size),
                "-H",
                str(gen_size),
                "--steps",
                str(steps),
                "-o",
                raw_path,
            ]
            sd_threads = os.environ.get("SD_THREADS", "").strip()
            if sd_threads:
                cmd.extend(["-t", sd_threads])
            cfg_scale = os.environ.get("SD_CFG_SCALE", "").strip()
            if cfg_scale:
                cmd.extend(["--cfg-scale", cfg_scale])

            res = run_fn(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                stderr = (getattr(res, "stderr", "") or "").strip()
                raise RuntimeError(f"sd-cli failed with exit code {res.returncode}: {stderr}")

            if not os.path.exists(raw_path) or os.path.getsize(raw_path) <= 0:
                raise RuntimeError("sd-cli completed without producing a non-empty output image")

            resampling = getattr(Image, "Resampling", Image).LANCZOS
            with Image.open(raw_path) as img:
                rgb_img = img.convert("RGB")
                resized = rgb_img.resize((out_size, out_size), resampling)
                resized.save(tmp_out_path, format="PNG")

        os.replace(tmp_out_path, output_path)
    finally:
        if os.path.exists(tmp_out_path):
            try:
                os.remove(tmp_out_path)
            except OSError:
                pass


def process_one_job(
    db_path: str,
    images_dir: str,
    sd_cli_bin: str,
    model_path: str,
    runner=None,
) -> bool:
    """Claim and process a single pending mood image job. Returns True if a job was claimed."""
    job = db.claim_next_image_job(db_path)
    if job is None:
        return False

    slide_id = int(job["slide_id"])
    gen_size = int(os.environ.get("GEN_SIZE", "256"))
    out_size = int(os.environ.get("OUT_SIZE", "400"))
    steps = int(os.environ.get("SD_STEPS", "4"))

    try:
        prompt = build_mood_prompt(
            job.get("mood", 5.0),
            job.get("emoji", ""),
            job.get("events", []),
            job.get("notes", ""),
        )
        output_path = os.path.join(images_dir, f"{slide_id}.png")
        logger.info("Processing slide_id=%d (gen=%dx%d -> out=%dx%d, steps=%d)", slide_id, gen_size, gen_size, out_size, out_size, steps)
        render_and_resize(
            sd_cli_bin=sd_cli_bin,
            model_path=model_path,
            prompt=prompt,
            output_path=output_path,
            gen_size=gen_size,
            out_size=out_size,
            steps=steps,
            runner=runner,
        )
        rel_path = f"mood_images/{slide_id}.png"
        db.complete_image_job(slide_id, prompt, rel_path, db_path)
        logger.info("Completed slide_id=%d -> %s", slide_id, output_path)
    except Exception as exc:
        logger.exception("Failed processing slide_id=%d: %s", slide_id, exc)
        db.fail_image_job(slide_id, str(exc)[:500], db_path)

    return True


def main() -> None:
    try:
        os.nice(15)
    except OSError as exc:
        logger.warning("Could not set nice(15): %s", exc)

    db_path = os.environ.get("DATABASE_PATH", "/app/data/timeline.db")
    images_dir = os.environ.get("IMAGES_DIR", "/app/data/mood_images")
    model_path = os.environ.get("MODEL_PATH", "/app/data/models/sd-turbo-q4_0.gguf")
    model_url = os.environ.get("MODEL_URL", DEFAULT_MODEL_URL)
    sd_cli_bin = os.environ.get("SD_CLI_BIN", "sd-cli")
    inter_job_sleep = float(os.environ.get("INTER_JOB_SLEEP_SECONDS", "8"))
    idle_sleep = float(os.environ.get("IDLE_SLEEP_SECONDS", "10"))
    backfill_interval = float(os.environ.get("BACKFILL_INTERVAL_SECONDS", "300"))

    stop_requested = False

    def _handle_signal(signum, _frame):
        nonlocal stop_requested
        logger.info("Received signal %d; shutting down after current step...", signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    os.makedirs(images_dir, exist_ok=True)
    db.init_db(db_path)
    ensure_model_weights(model_path, model_url)

    last_backfill = 0.0
    while not stop_requested:
        now = time.monotonic()
        if now - last_backfill >= backfill_interval:
            queued = db.backfill_missing_image_jobs(db_path)
            if queued:
                logger.info("Backfill queued %d slide image job(s)", queued)
            last_backfill = now

        did_work = process_one_job(db_path, images_dir, sd_cli_bin, model_path)
        if stop_requested:
            break
        time.sleep(inter_job_sleep if did_work else idle_sleep)

    logger.info("mood-gen worker exited cleanly.")


if __name__ == "__main__":
    main()
