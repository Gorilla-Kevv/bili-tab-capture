"""BiliTabCapture MCP Server（stdio）。

官方 MCP Python SDK（`mcp>=2.0`）入口。它是本地共享服务的**客户端**：
- 需要服务时自动复用/拉起常驻服务（同一个 data_dir）；
- 导入、截图、小节线、自动裁剪、导出都走现有 HTTP API；
- 默认不打开浏览器（`import_bvid(open_browser=True)` 除外）。

安装：pip install -r requirements-mcp.txt
启动：python mcp_server.py（通常由 MCP 客户端以 stdio 启动）
"""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mcp.server.mcpserver import MCPServer
except ImportError as exc:  # pragma: no cover - 运行环境缺依赖时给出明确提示
    raise SystemExit("缺少官方 MCP SDK：请先 pip install -r requirements-mcp.txt") from exc

import launcher
from runtime_paths import data_dir

APP_ENTRY = Path(__file__).resolve().with_name("app.py")
DEFAULT_PORT = launcher.DEFAULT_PORT
CACHE_DIR = data_dir() / "BiliTabCapture_cache"


def _read_version() -> str:
    try:
        return (Path(__file__).resolve().with_name("VERSION")).read_text(
            encoding="utf-8"
        ).strip() or "0.0"
    except OSError:
        return "0.0"


mcp = MCPServer(
    "BiliTabCapture",
    version=_read_version(),
    instructions=(
        "BiliTabCapture 是本地动态谱截图/排版工具。工具会复用同一个本地服务，"
        "先 import_bvid 导入，再 capture 生成截图；detect_measures/auto_crop 可批量处理，"
        "export_score 导出 PDF/psz/长图。需要人工调整时用 open_tab 打开网页。"
    ),
)


# --------------------------------------------------------------------------
# HTTP / source helpers
# --------------------------------------------------------------------------

def _error_text(status: int, data: Any) -> str:
    if isinstance(data, dict):
        return str(data.get("error") or data)[:800]
    if isinstance(data, bytes):
        return data[:800].decode("utf-8", "replace")
    return f"HTTP {status}: {str(data)[:800]}"


def _ensure_port() -> int:
    port, _reused, _info = launcher.ensure_server(APP_ENTRY, DEFAULT_PORT, False)
    return int(port)


def _existing_port() -> int:
    info = launcher.existing_server(DEFAULT_PORT)
    if info is None:
        raise RuntimeError("BiliTabCapture 服务未运行；请先调用 import_bvid 或启动 app.py")
    return int(info["port"])


def _call(port: int, method: str, path: str, **kwargs) -> Any:
    status, data = launcher.api_request(port, method, path, **kwargs)
    if status >= 400 or (isinstance(data, dict) and data.get("error")):
        raise RuntimeError(_error_text(status, data))
    return data


def _try_source(port: int, source_id: str) -> Optional[Dict[str, Any]]:
    status, data = launcher.api_request(
        port, "GET", f"/api/source/{urllib.parse.quote(str(source_id), safe='')}",
        timeout=30,
    )
    if status == 404:
        return None
    if status >= 400 or not isinstance(data, dict):
        raise RuntimeError(_error_text(status, data))
    return data


def _get_job(port: int, job_id: str) -> Dict[str, Any]:
    return _call(port, "GET", f"/api/jobs/{urllib.parse.quote(str(job_id), safe='')}", timeout=30)


def _wait_job(port: int, job_id: str, timeout: float) -> Dict[str, Any]:
    deadline = time.time() + max(0.0, float(timeout))
    while True:
        job = _get_job(port, job_id)
        if job.get("status") in ("done", "error"):
            return job
        if time.time() >= deadline:
            job = dict(job)
            job["timed_out"] = True
            return job
        time.sleep(1.0)


def _wait_download(port: int, source_id: str, timeout: float) -> Dict[str, Any]:
    deadline = time.time() + max(0.0, float(timeout))
    path = f"/api/source/{urllib.parse.quote(str(source_id), safe='')}/download"
    last: Dict[str, Any] = {"status": "unknown"}
    while True:
        data = _call(port, "GET", path, timeout=10)
        if isinstance(data, dict):
            last = data
            if data.get("status") in ("done", "error", "idle"):
                return data
        if time.time() >= deadline:
            result = dict(last)
            result["timed_out"] = True
            return result
        time.sleep(1.0)


def _source_summary(source: Dict[str, Any], include_images: bool = False) -> Dict[str, Any]:
    restore = source.get("restore") or {}
    meta = source.get("metadata") or {}
    out: Dict[str, Any] = {
        "source_id": source.get("id"),
        "type": source.get("type"),
        "title": meta.get("display_title") or meta.get("raw_title"),
        "channel": meta.get("channel"),
        "cache_dir": source.get("cache_dir"),
        "download": source.get("download"),
        "has_cache": bool(source.get("has_cache")),
        "params2": source.get("params2"),
        "active_job": source.get("active_job"),
        "page_count": source.get("page_count"),
        "pdf_dpi": source.get("pdf_dpi") or source.get("dpi"),
    }
    if restore:
        images = restore.get("images") or []
        out["restore"] = {
            "job_id": restore.get("job_id"),
            "image_count": len(images),
            "max_stage": restore.get("maxStage"),
            "layout": restore.get("layout"),
        }
        if include_images:
            out["restore"]["images"] = images
    return out


def _job_summary(job: Dict[str, Any], include_captures: bool = False) -> Dict[str, Any]:
    captures = job.get("captures") or []
    out: Dict[str, Any] = {
        "job_id": job.get("id"),
        "source_id": job.get("source_id"),
        "status": job.get("status"),
        "phase": job.get("phase"),
        "stats": job.get("stats"),
        "error": job.get("error"),
        "captures_count": len(captures),
        "timed_out": bool(job.get("timed_out")),
    }
    if include_captures:
        out["captures"] = captures
    return out


def _default_images(captures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "file": item.get("file"),
            "t": item.get("t", 0),
            "w": item.get("w"),
            "h": item.get("h"),
            "hidden": False,
            "crop": {"l": 0.0, "r": 1.0, "t": 0.0, "b": 1.0},
            "measures": [],
        }
        for item in captures
    ]


def _images_for_job(
    port: int, source_id: Optional[str], job_id: str
) -> tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """返回 (images, layout, metadata)；优先用 state 里的裁剪/小节线。"""
    source: Optional[Dict[str, Any]] = None
    if source_id:
        source = _try_source(port, source_id)
    if source is None:
        job = _get_job(port, job_id)
        source_id = job.get("source_id") or source_id
        if source_id:
            source = _try_source(port, source_id)
    if source is not None:
        restore = source.get("restore") or {}
        images = restore.get("images")
        if images:
            return images, restore.get("layout") or {}, source.get("metadata") or {}
    job = _get_job(port, job_id)
    if job.get("status") != "done":
        raise RuntimeError("任务尚未完成，无法读取截图。")
    images = _default_images(job.get("captures") or [])
    if not images:
        raise RuntimeError("该任务没有截图。")
    meta = (source or {}).get("metadata") or job.get("metadata") or {}
    return images, {}, meta


def _num(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _save_images(port: int, job_id: str, images: List[Dict[str, Any]]) -> None:
    _call(
        port,
        "POST",
        "/api/save_state",
        json_body={"job_id": job_id, "images": images},
        timeout=30,
    )


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

@mcp.tool()
def status() -> Dict[str, Any]:
    """查看 BiliTabCapture 共享服务是否运行（不启动服务）。"""
    return launcher.status_payload(DEFAULT_PORT)


@mcp.tool()
def stop_server(stop_all: bool = False) -> Dict[str, Any]:
    """停止共享服务。stop_all=True 时停止本副本所有实例（含随机端口）。"""
    return launcher.stop_payload(DEFAULT_PORT, stop_all=stop_all)


@mcp.tool()
def import_bvid(
    bvid: str,
    open_browser: bool = False,
    wait_download: bool = False,
    timeout: float = 120,
) -> Dict[str, Any]:
    """导入一个 BV/av/视频链接；按需复用或拉起共享服务。"""
    port = _ensure_port()
    source = _call(
        port, "POST", "/api/import", form={"bvid": bvid}, timeout=60
    )
    source_id = source.get("id")
    if not source_id:
        raise RuntimeError("导入接口没有返回 source id。")
    url = (
        f"http://127.0.0.1:{port}/?source="
        f"{urllib.parse.quote(str(source_id), safe='')}"
    )
    opened = launcher.open_browser(url) if open_browser else False
    download = source.get("download") or {}
    if wait_download:
        download = _wait_download(port, str(source_id), timeout)
    result = _source_summary(source)
    result.update(
        {
            "url": url,
            "opened": opened,
            "download": download,
        }
    )
    return result


@mcp.tool()
def open_tab(source_id: str) -> Dict[str, Any]:
    """在默认浏览器打开某个已导入 source 的页面（人工接手调整用）。"""
    port = _existing_port()
    source = _try_source(port, source_id)
    if source is None:
        raise RuntimeError("未知的 source_id。")
    url = (
        f"http://127.0.0.1:{port}/?source="
        f"{urllib.parse.quote(str(source_id), safe='')}"
    )
    return {"url": url, "opened": launcher.open_browser(url)}


@mcp.tool()
def get_source(source_id: str, include_images: bool = False) -> Dict[str, Any]:
    """读取 source 的下载/截图/运行中任务状态；include_images=True 时带完整裁剪状态。"""
    port = _existing_port()
    source = _try_source(port, source_id)
    if source is None:
        raise RuntimeError("未知的 source_id。")
    return _source_summary(source, include_images=include_images)


@mcp.tool()
def capture(
    source_id: str,
    wait: bool = True,
    timeout: float = 120,
    include_captures: bool = False,
    start: Optional[float] = None,
    end: Optional[float] = None,
    sample_every: Optional[float] = None,
    crop_y_start: Optional[float] = None,
    crop_y_end: Optional[float] = None,
    crop_x_start: Optional[float] = None,
    crop_x_end: Optional[float] = None,
    diff_threshold: Optional[float] = None,
    compare_window: Optional[int] = None,
    band_half_width: Optional[int] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
    pdf_dpi: Optional[int] = None,
) -> Dict[str, Any]:
    """生成截图。视频用 start/end/sample_every；PDF 用 start_page/end_page/pdf_dpi。"""
    port = _existing_port()
    payload = {
        key: value
        for key, value in {
            "start": start,
            "end": end,
            "sample_every": sample_every,
            "crop_y_start": crop_y_start,
            "crop_y_end": crop_y_end,
            "crop_x_start": crop_x_start,
            "crop_x_end": crop_x_end,
            "diff_threshold": diff_threshold,
            "compare_window": compare_window,
            "band_half_width": band_half_width,
            "start_page": start_page,
            "end_page": end_page,
            "pdf_dpi": pdf_dpi,
        }.items()
        if value is not None
    }
    payload["source_id"] = source_id
    created = _call(port, "POST", "/api/captures", json_body=payload, timeout=30)
    job_id = created.get("job_id")
    if not job_id:
        raise RuntimeError("截图接口没有返回 job_id。")
    if not wait:
        return {"job_id": job_id, "status": "queued"}
    return _job_summary(_wait_job(port, str(job_id), timeout), include_captures)


@mcp.tool()
def get_job(job_id: str, include_captures: bool = False) -> Dict[str, Any]:
    """读取截图任务状态；完成后可拿到 captures 文件列表。"""
    port = _existing_port()
    return _job_summary(_get_job(port, job_id), include_captures)


@mcp.tool()
def detect_measures(
    source_id: Optional[str] = None,
    job_id: Optional[str] = None,
    files: Optional[List[str]] = None,
    apply: bool = True,
    coefficient_horizontal: float = 0.7,
    coefficient_vertical: float = 0.8,
    note_color: Optional[str] = None,
    tolerance: float = 300.0,
    softness: float = 90.0,
) -> Dict[str, Any]:
    """识别小节线；apply=True 时写回 state.json，后续导出会按小节切分。"""
    port = _existing_port()
    resolved_job = job_id
    if not resolved_job:
        if not source_id:
            raise RuntimeError("需要 source_id 或 job_id。")
        source = _try_source(port, source_id)
        if source is None:
            raise RuntimeError("未知的 source_id。")
        restore = source.get("restore") or {}
        resolved_job = restore.get("job_id")
        if not resolved_job:
            raise RuntimeError("该 source 还没有完成的截图；先调用 capture。")
    job = _get_job(port, str(resolved_job))
    capture_files = files or [item.get("file") for item in (job.get("captures") or [])]
    payload: Dict[str, Any] = {
        "job_id": resolved_job,
        "files": capture_files,
        "coefficient_horizontal": coefficient_horizontal,
        "coefficient_vertical": coefficient_vertical,
        "tolerance": tolerance,
        "softness": softness,
    }
    if note_color:
        payload["note_color"] = note_color
    data = _call(port, "POST", "/api/detect_measures", json_body=payload, timeout=120)
    measures = data.get("measures") or {}
    applied = 0
    if apply:
        images, _layout, _meta = _images_for_job(
            port, source_id or job.get("source_id"), str(resolved_job)
        )
        for image in images:
            if image.get("file") in measures:
                image["measures"] = measures[image["file"]]
                applied += 1
        _save_images(port, str(resolved_job), images)
    return {
        "job_id": resolved_job,
        "measures": measures,
        "files": len(capture_files),
        "applied": applied,
    }


def _apply_stitch_to_images(
    images: List[Dict[str, Any]], details: Dict[str, Any], hide_duplicates: bool
) -> int:
    """按 /api/stitch 的 details 更新裁剪并隐藏重复/被完全覆盖的截图。"""
    seams = details.get("seams") or []
    valid = details.get("valid") or []
    duplicate = details.get("duplicate") or []
    count = len(images)

    for image in images:
        if image.get("autoHidden"):
            image["hidden"] = False
            image["autoHidden"] = False
        crop = dict(image.get("crop") or {})
        crop["l"], crop["r"] = 0.0, 1.0
        image["crop"] = crop

    if hide_duplicates:
        for index in range(count - 1):
            if index >= len(duplicate) or not duplicate[index]:
                continue
            image = images[index + 1]
            if not image.get("hidden"):
                image["hidden"] = True
                image["autoHidden"] = True
            crop = dict(image.get("crop") or {})
            crop["l"], crop["r"] = 0.0, 1.0
            image["crop"] = crop

    for index in range(count - 1):
        if index >= len(valid) or not valid[index]:
            continue
        if index < len(duplicate) and duplicate[index]:
            continue
        seam = seams[index] if index < len(seams) else None
        if not seam:
            continue
        left_img = images[index]
        right_img = images[index + 1]
        lc = dict(left_img.get("crop") or {})
        rc = dict(right_img.get("crop") or {})
        lc["r"] = max(0.0, min(1.0, _num(seam[0], 1.0)))
        rc["l"] = max(0.0, min(1.0, _num(seam[1], 0.0)))
        left_img["crop"], right_img["crop"] = lc, rc

    if hide_duplicates:
        for index in range(1, count - 1):
            image = images[index]
            if image.get("hidden") or image.get("autoHidden"):
                continue
            prev_valid = (
                index - 1 < len(valid)
                and valid[index - 1]
                and not (index - 1 < len(duplicate) and duplicate[index - 1])
            )
            next_valid = (
                index < len(valid)
                and valid[index]
                and not (index < len(duplicate) and duplicate[index])
            )
            crop = image.get("crop") or {}
            if prev_valid and next_valid and _num(crop.get("r"), 1.0) - _num(crop.get("l"), 0.0) < 0.03:
                image["hidden"] = True
                image["autoHidden"] = True
                crop = dict(crop)
                crop["l"], crop["r"] = 0.0, 1.0
                image["crop"] = crop

    for image in images:
        if image.get("autoHidden") or image.get("hidden"):
            continue
        crop = image.get("crop") or {}
        if _num(crop.get("r"), 1.0) - _num(crop.get("l"), 0.0) < 0.02:
            crop = dict(crop)
            crop["l"], crop["r"] = 0.0, 1.0
            image["crop"] = crop

    return sum(1 for image in images if image.get("autoHidden"))


@mcp.tool()
def auto_crop(
    source_id: Optional[str] = None,
    job_id: Optional[str] = None,
    apply: bool = True,
    hide_duplicates: bool = True,
    max_width: int = 600,
    coefficient_horizontal: float = 0.7,
    coefficient_vertical: float = 0.8,
    note_color: Optional[str] = None,
    tolerance: float = 300.0,
    softness: float = 90.0,
) -> Dict[str, Any]:
    """自动裁剪去重：用拼接缝调整左右裁剪框，可选隐藏重复/被覆盖的截图。"""
    port = _existing_port()
    resolved_job = job_id
    if not resolved_job:
        if not source_id:
            raise RuntimeError("需要 source_id 或 job_id。")
        source = _try_source(port, source_id)
        if source is None:
            raise RuntimeError("未知的 source_id。")
        resolved_job = (source.get("restore") or {}).get("job_id")
        if not resolved_job:
            raise RuntimeError("该 source 还没有完成的截图；先调用 capture。")
    job = _get_job(port, str(resolved_job))
    capture_files = [item.get("file") for item in (job.get("captures") or [])]
    payload: Dict[str, Any] = {
        "job_id": resolved_job,
        "files": capture_files,
        "max_width": max_width,
        "coefficient_horizontal": coefficient_horizontal,
        "coefficient_vertical": coefficient_vertical,
        "tolerance": tolerance,
        "softness": softness,
    }
    if note_color:
        payload["note_color"] = note_color
    data = _call(port, "POST", "/api/stitch", json_body=payload, timeout=120)
    seams = data.get("seams") or []
    hidden = 0
    applied = 0
    if apply:
        images, _layout, _meta = _images_for_job(
            port, source_id or job.get("source_id"), str(resolved_job)
        )
        hidden = _apply_stitch_to_images(images, data, hide_duplicates)
        _save_images(port, str(resolved_job), images)
        applied = len(images)
    return {
        "job_id": resolved_job,
        "seams": seams,
        "valid": data.get("valid") or [],
        "duplicate": data.get("duplicate") or [],
        "confidence": data.get("confidence") or [],
        "applied": applied,
        "hidden": hidden,
    }


@mcp.tool()
def export_score(
    source_id: Optional[str] = None,
    job_id: Optional[str] = None,
    format: str = "pdf",
    output: Optional[str] = None,
    title: Optional[str] = None,
    scale: Optional[float] = None,
    margin: Optional[float] = None,
    spacing: Optional[float] = None,
    title_spacing: Optional[float] = None,
    orientation: Optional[str] = None,
    align: Optional[str] = None,
    valign: Optional[str] = None,
    bg_color: Optional[str] = None,
    text_color: Optional[str] = None,
    invert: Optional[bool] = None,
    binarize: Optional[bool] = None,
    note_color: Optional[str] = None,
    tolerance: Optional[float] = None,
    softness: Optional[float] = None,
) -> Dict[str, Any]:
    """导出 PDF / psz / 长图。默认使用 state 里恢复的裁剪、小节线与排版参数；output 建议绝对路径。"""
    fmt = (format or "pdf").lower()
    aliases = {"long": "long_image", "png": "long_image", "psz": "psz", "pdf": "pdf"}
    fmt = aliases.get(fmt, fmt)
    if fmt not in ("pdf", "psz", "long_image"):
        raise RuntimeError("format 只能是 pdf / psz / long。")

    port = _existing_port()
    source: Optional[Dict[str, Any]] = None
    if source_id:
        source = _try_source(port, source_id)
    resolved_job = job_id
    if not resolved_job and source:
        resolved_job = (source.get("restore") or {}).get("job_id")
    if not resolved_job:
        raise RuntimeError("需要已完成的截图（job_id 或带 restore 的 source_id）。")

    if source is None:
        job = _get_job(port, str(resolved_job))
        sid = job.get("source_id")
        source = _try_source(port, sid) if sid else None

    images, layout, meta = _images_for_job(
        port, source_id or (source or {}).get("id"), str(resolved_job)
    )
    visible_images = [
        {
            "file": item.get("file"),
            "crop": item.get("crop") or {"l": 0, "r": 1, "t": 0, "b": 1},
            "measures": item.get("measures") or [],
        }
        for item in images
        if not item.get("hidden")
    ]
    if not visible_images:
        raise RuntimeError("没有可导出的截图（都隐藏了？）。")

    title_text = (
        title
        if title is not None
        else layout.get("title")
        or (meta or {}).get("display_title")
        or (meta or {}).get("raw_title")
        or ""
    )
    first_line = next((line.strip() for line in title_text.splitlines() if line.strip()), "")
    output_name = Path(output).name if output else (first_line or "tablatura")
    payload: Dict[str, Any] = {
        "job_id": resolved_job,
        "images": visible_images,
        "title": title_text,
        "output": output_name,
        "scale": scale if scale is not None else _num(layout.get("scale"), 1.0),
        "margin": margin if margin is not None else _num(layout.get("margin"), 40),
        "spacing": spacing if spacing is not None else _num(layout.get("spacing"), 25),
        "title_spacing": (
            title_spacing
            if title_spacing is not None
            else _num(layout.get("titleSpacing"), 130)
        ),
        "orientation": orientation or layout.get("orientation") or "portrait",
        "align": align or layout.get("align") or "left",
        "valign": valign or layout.get("valign") or "top",
        "bg_color": bg_color or layout.get("bgColor") or "white",
        "text_color": text_color or layout.get("textColor") or "#181818",
        "invert": bool(layout.get("invert")) if invert is None else bool(invert),
        "binarize": (
            bool(layout.get("binarize")) if binarize is None else bool(binarize)
        ),
        "note_color": note_color or layout.get("noteColor") or "#000000",
        "tolerance": (
            tolerance if tolerance is not None else _num(layout.get("tolerance"), 300)
        ),
        "softness": (
            softness if softness is not None else _num(layout.get("softness"), 90)
        ),
    }

    endpoint = {
        "pdf": "/api/pdf",
        "psz": "/api/psz",
        "long_image": "/api/long_image",
    }[fmt]
    result = _call(
        port, "POST", endpoint, json_body=payload, timeout=180
    )
    url_key = {"pdf": "pdf_url", "psz": "psz_url", "long_image": "long_url"}[fmt]
    name_key = {"pdf": "pdf_name", "psz": "psz_name", "long_image": "long_name"}[fmt]
    url = result.get(url_key)
    name = result.get(name_key)
    if not url or not name:
        raise RuntimeError("导出接口没有返回文件地址。")

    status, blob = launcher.api_request(port, "GET", url, timeout=180)
    if status >= 400 or not isinstance(blob, (bytes, bytearray)):
        raise RuntimeError(_error_text(status, blob))
    server_path = CACHE_DIR / "runs" / str(resolved_job) / str(name)
    saved_path = None
    if output:
        saved_path = Path(output).expanduser().resolve()
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        saved_path.write_bytes(bytes(blob))
    out: Dict[str, Any] = {
        "format": fmt,
        "name": name,
        "server_path": str(server_path),
        "output": str(saved_path) if saved_path else None,
        "url": url,
        "job_id": resolved_job,
        "images": len(visible_images),
    }
    for key in (
        "page_count",
        "measure_count",
        "has_meta",
        "has_audio",
        "has_cover",
    ):
        if key in result:
            out[key] = result[key]
    return out


@mcp.tool()
def history(limit: int = 50) -> Dict[str, Any]:
    """列出本地持久缓存（bv/local/img/pdf）的历史记录。"""
    port = _existing_port()
    data = _call(port, "GET", "/api/history", timeout=30)
    entries = data.get("entries") or []
    return {"entries": entries[: max(0, int(limit))], "total": len(entries)}


if __name__ == "__main__":
    mcp.run()
