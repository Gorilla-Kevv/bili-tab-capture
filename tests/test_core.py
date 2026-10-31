"""核心状态/恢复/缓存行为的无头回归测试。

运行：python -m unittest discover -s tests -v
测试全部使用临时缓存目录，不会碰真实的 BiliTabCapture_cache/。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


META = {
    "raw_title": "测试视频",
    "display_title": "测试视频",
    "channel": "测试作者",
    "source_url": None,
}


def write_png(path: Path, size=(120, 60)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path)


def fake_extract(*, video_path, crops_dir, progress_callback=None, **_kwargs):
    crops_dir = Path(crops_dir)
    crops_dir.mkdir(parents=True, exist_ok=True)
    write_png(crops_dir / "c0.png", (120, 60))
    (crops_dir / "times.json").write_text(
        json.dumps({"c0.png": 1.0}), encoding="utf-8"
    )
    return app.ExtractionStats(captures_kept=1)


class CoreStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="btab_test_"))
        self.orig = {
            name: getattr(app, name)
            for name in ("CACHE_DIR", "UPLOADS_DIR", "PREVIEWS_DIR", "RUNS_DIR")
        }
        app.CACHE_DIR = self.tmp / "cache"
        app.UPLOADS_DIR = app.CACHE_DIR / "uploads"
        app.PREVIEWS_DIR = app.CACHE_DIR / "previews"
        app.RUNS_DIR = app.CACHE_DIR / "runs"
        app.ensure_cache_dirs()
        with app.STORE_LOCK:
            app.SOURCES.clear()
            app.JOBS.clear()
        app.DOWNLOAD_LOCKS.clear()
        self.client = app.app.test_client()
        self._orig_extract = app.extract_unique_crops
        self._orig_download = app.download_bilibili_video

    def tearDown(self) -> None:
        app.extract_unique_crops = self._orig_extract
        app.download_bilibili_video = self._orig_download
        with app.STORE_LOCK:
            app.SOURCES.clear()
            app.JOBS.clear()
        app.DOWNLOAD_LOCKS.clear()
        for name, value in self.orig.items():
            setattr(app, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_source(self, source_id: str, source_type: str, path: Path, cache_dir: Path):
        with app.STORE_LOCK:
            app.SOURCES[source_id] = {
                "id": source_id,
                "type": source_type,
                "path": str(path),
                "cache_dir": str(cache_dir),
                "metadata": META,
                "download": {"status": "done", "message": None},
            }

    def make_local_cache(self, name="h1"):
        cache = app.CACHE_DIR / "local" / name
        (cache / "crops").mkdir(parents=True, exist_ok=True)
        (cache / "video.mp4").write_bytes(b"fake-video")
        return cache

    # ------------------------------------------------------------------
    # 1. 截图完成不覆盖 params2/layout
    # ------------------------------------------------------------------
    def test_capture_preserves_params2_layout(self):
        cache = self.make_local_cache()
        app.save_state_json(
            cache,
            {
                "captures": [],
                "images": [],
                "capture_params": {},
                "params2": {"startSeconds": 12.5, "sample_every": 1.5},
                "layout": {"margin": 99, "title": "KEEP"},
                "maxStage": 4,
                "metadata": META,
            },
        )
        self.add_source("s1", "local", cache / "video.mp4", cache)
        with app.STORE_LOCK:
            app.JOBS["j1"] = {
                "id": "j1",
                "source_id": "s1",
                "status": "queued",
                "phase": "queued",
                "logs": [],
                "stats": None,
                "captures": [],
                "created_at": 0,
                "updated_at": 0,
            }
        app.extract_unique_crops = fake_extract

        app.run_capture_job("j1", {"source_id": "s1", "start": 0, "end": 2})

        state = json.loads((cache / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["params2"], {"startSeconds": 12.5, "sample_every": 1.5})
        self.assertEqual(state["layout"], {"margin": 99, "title": "KEEP"})
        self.assertEqual(state["maxStage"], 4)
        self.assertEqual(len(state["captures"]), 1)
        self.assertEqual(len(state["images"]), 1)

    # ------------------------------------------------------------------
    # 2. ?source= 刷新恢复 + cached job video_path
    # ------------------------------------------------------------------
    def test_source_instance_restores_cached_job(self):
        cache = self.make_local_cache()
        write_png(cache / "crops" / "c0.png")
        app.save_state_json(
            cache,
            {
                "captures": [{"file": "c0.png", "t": 1.0, "w": 120, "h": 60}],
                "images": [
                    {
                        "file": "c0.png",
                        "t": 1.0,
                        "w": 120,
                        "h": 60,
                        "hidden": False,
                        "crop": {"l": 0, "r": 1, "t": 0, "b": 1},
                        "measures": [],
                    }
                ],
                "capture_params": {},
                "params2": {"startSeconds": 3},
                "layout": {"margin": 50},
                "maxStage": 3,
                "metadata": META,
            },
        )
        self.add_source("s1", "local", cache / "video.mp4", cache)

        first = self.client.get("/api/source/s1").get_json()
        second = self.client.get("/api/source/s1").get_json()
        self.assertTrue(first.get("has_cache"))
        self.assertIn("restore", first)
        self.assertEqual(len(first["restore"]["images"]), 1)
        self.assertEqual(first["params2"]["startSeconds"], 3)
        self.assertEqual(first["restore"]["job_id"], second["restore"]["job_id"])

        job = app.JOBS[first["restore"]["job_id"]]
        self.assertTrue(job.get("video_path", "").endswith("video.mp4"))

    # ------------------------------------------------------------------
    # 3. 恢复后可以补插截图
    # ------------------------------------------------------------------
    def test_insert_captures_after_cached_restore(self):
        cache = self.make_local_cache("h2")
        write_png(cache / "crops" / "c0.png")
        app.save_state_json(
            cache,
            {
                "captures": [{"file": "c0.png", "t": 0, "w": 120, "h": 60}],
                "images": [],
                "capture_params": {},
                "metadata": META,
            },
        )
        self.add_source("s2", "local", cache / "video.mp4", cache)
        job_id = app.make_cached_job("s2", cache, app.load_state_json(cache))
        app.extract_unique_crops = fake_extract

        resp = self.client.post(
            "/api/insert_captures",
            json={"job_id": job_id, "t_start": 1, "t_end": 3, "sample_every": 0.5},
        )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(len(resp.get_json()["captures"]), 1)
        self.assertEqual(len(app.JOBS[job_id]["captures"]), 2)

    # ------------------------------------------------------------------
    # 4. 重置进度保留原文件与标题；删除缓存才移除
    # ------------------------------------------------------------------
    def test_reset_progress_keeps_master_and_metadata(self):
        key = "imgkey"
        cache = app.CACHE_DIR / "img" / key
        write_png(cache / "master.png", (200, 100))
        write_png(cache / "crops" / "old.png")
        app.save_state_json(
            cache,
            {
                "captures": [{"file": "old.png", "t": 0, "w": 200, "h": 100}],
                "images": [],
                "params2": {"startSeconds": 9},
                "metadata": META,
                "layout": {"margin": 30},
            },
        )

        resp = self.client.post(
            "/api/cache_clear", json={"type": "image", "key": key, "full": False}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue((cache / "master.png").exists())
        self.assertFalse((cache / "crops" / "old.png").exists())
        state = json.loads((cache / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["metadata"]["display_title"], "测试视频")
        self.assertEqual(state["captures"], [])

        restored = self.client.post(
            "/api/history/restore", json={"kind": "image", "key": key}
        ).get_json()
        self.assertEqual(restored["type"], "image")
        self.assertTrue(restored["path"].endswith("master.png"))
        self.assertEqual(restored["metadata"]["display_title"], "测试视频")
        self.assertNotIn("restore", restored)

    def test_full_clear_removes_master(self):
        key = "imgfull"
        cache = app.CACHE_DIR / "img" / key
        write_png(cache / "master.png", (200, 100))
        resp = self.client.post(
            "/api/cache_clear", json={"type": "image", "key": key, "full": True}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse((cache / "master.png").exists())

    def test_pdf_reset_restores_page_count(self):
        key = "pdfkey"
        cache = app.CACHE_DIR / "pdf" / key
        cache.mkdir(parents=True, exist_ok=True)
        pages = [Image.new("RGB", (200, 300), "white") for _ in range(3)]
        pages[0].save(
            cache / "master.pdf", "PDF", save_all=True, append_images=pages[1:]
        )
        app.save_state_json(cache, {"captures": [], "images": [], "metadata": META})

        self.client.post(
            "/api/cache_clear", json={"type": "pdf", "key": key, "full": False}
        )
        restored = self.client.post(
            "/api/history/restore", json={"kind": "pdf", "key": key}
        ).get_json()
        self.assertEqual(restored["type"], "pdf")
        self.assertEqual(restored["page_count"], 3)

    # ------------------------------------------------------------------
    # 5. 运行中的任务可以被刷新恢复
    # ------------------------------------------------------------------
    def test_source_instance_exposes_active_job(self):
        cache = self.make_local_cache("h3")
        self.add_source("s3", "local", cache / "video.mp4", cache)
        with app.STORE_LOCK:
            app.JOBS["running"] = {
                "id": "running",
                "source_id": "s3",
                "status": "running",
                "phase": "extracting",
                "stats": {"frames_checked": 4},
                "captures": [],
                "created_at": 1,
                "updated_at": 2,
            }
            app.JOBS["done"] = {
                "id": "done",
                "source_id": "s3",
                "status": "done",
                "phase": "done",
                "captures": [{"file": "x.png"}],
                "created_at": 0,
                "updated_at": 0,
            }
        data = self.client.get("/api/source/s3").get_json()
        self.assertEqual(data["active_job"]["job_id"], "running")
        self.assertEqual(data["active_job"]["phase"], "extracting")

        with app.STORE_LOCK:
            app.JOBS["running"]["status"] = "done"
        data = self.client.get("/api/source/s3").get_json()
        self.assertNotIn("active_job", data)

    # ------------------------------------------------------------------
    # 6. 同一个 BV 并发下载只调用一次下载器
    # ------------------------------------------------------------------
    def test_same_cache_download_is_serialized(self):
        cache = app.CACHE_DIR / "bv" / "BVtest"
        cache.mkdir(parents=True, exist_ok=True)
        calls = []

        def fake_download(url, out_dir, quality=None, with_audio=False):
            calls.append(url)
            time.sleep(0.3)
            out = Path(out_dir) / "video.m4s"
            out.write_bytes(b"fake")
            return out

        app.download_bilibili_video = fake_download
        try:
            with app.STORE_LOCK:
                app.SOURCES["a"] = {"id": "a", "download": {"status": "downloading"}}
                app.SOURCES["b"] = {"id": "b", "download": {"status": "downloading"}}
            threads = [
                threading.Thread(
                    target=app.run_bilibili_download,
                    args=(sid, "https://example/1", cache, "BVtest"),
                )
                for sid in ("a", "b")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertEqual(len(calls), 1)
            with app.STORE_LOCK:
                self.assertEqual(app.SOURCES["a"]["download"]["status"], "done")
                self.assertEqual(app.SOURCES["b"]["download"]["status"], "done")
            self.assertTrue((cache / "BVtest.m4s").exists())
            self.assertFalse((cache / "video.m4s").exists())
        finally:
            app.download_bilibili_video = self._orig_download


if __name__ == "__main__":
    unittest.main()
