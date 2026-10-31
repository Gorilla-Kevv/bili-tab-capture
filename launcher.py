"""快捷启动器：让 agent 用一条命令打开 GUI 并自动导入 B 站视频。

用法：
    python app.py --bvid BV1xx [--bvid BV1yy ...]
    BiliTabCapture.exe --bvid BV1xx

并发模型：
    - 多个 agent 同时调用时复用同一个常驻服务（通过 cache/server.json 发现），
      不会为每个标签页各起一个 Python 进程；
    - 服务由本进程 detached 拉起，本进程打印 JSON 后立即退出，不阻塞调用方；
    - 启动/发现过程用 cache/launcher.lock 串行化，避免并发时抢同一端口。

页面接手方式：
    导入仍然走现有 POST /api/import；成功后打开 `/?source=<source_id>`，
    前端读到该参数后自动 applySource()，不需要在浏览器里重复填 BV 号。
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import io
import json
import os
import subprocess
import sys
import time
import urllib.parse
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from runtime_paths import data_dir

APP_TAG = "BiliTabCapture"
DEFAULT_PORT = 5000
DISCOVERY_SPAN = 3       # canonical server.json 优先；没命中时最多再探 5000..5002
BIND_SPAN = 30           # serve 绑定时最多往后顺延多少个端口
LOCK_STALE_SEC = 30.0    # 仅用于“锁文件里没有 pid”的损坏场景
START_TIMEOUT_SEC = 60.0


def _cache_dir() -> Path:
    return data_dir() / "BiliTabCapture_cache"


def _info_path() -> Path:
    return _cache_dir() / "server.json"


def _lock_path() -> Path:
    return _cache_dir() / "launcher.lock"


def _log_path() -> Path:
    return _cache_dir() / "launcher-server.log"


# --------------------------------------------------------------------------
# 本地 HTTP（只用 http.client：绕过系统代理，避免 localhost 被代理劫持）
# --------------------------------------------------------------------------

def _request(
    port: int,
    method: str,
    path: str,
    form: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> Tuple[int, Any]:
    body = None
    headers: Dict[str, str] = {}
    if form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        ctype = resp.getheader("Content-Type") or ""
        if "application/json" in ctype:
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                pass
        return resp.status, raw
    finally:
        conn.close()


def _health(port: int, timeout: float = 0.8) -> Optional[Dict[str, Any]]:
    try:
        status, data = _request(port, "GET", "/api/version", timeout=timeout)
    except Exception:
        return None
    if status == 200 and isinstance(data, dict) and data.get("app") == APP_TAG:
        return data
    return None


# --------------------------------------------------------------------------
# 服务信息文件
# --------------------------------------------------------------------------

def _read_info(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = path or _info_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("app") != APP_TAG:
        return None
    try:
        int(data["port"])
    except (KeyError, TypeError, ValueError):
        return None
    return data


def _write_info(path: Path, info: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _remove_info(path: Path, port: int) -> None:
    info = _read_info(path)
    if not info:
        return
    if int(info.get("pid") or -1) != os.getpid():
        return
    if int(info.get("port") or -1) != int(port):
        return
    try:
        path.unlink()
    except OSError:
        pass


def _live_server(except_pid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    info = _read_info()
    if not info:
        return None
    if except_pid is not None and int(info.get("pid") or -1) == int(except_pid):
        return None
    if _health(int(info["port"])) is None:
        return None
    return info


def _find_server(preferred_port: int) -> Optional[Dict[str, Any]]:
    info = _live_server()
    if info:
        return info
    ports = []
    if preferred_port:
        ports.append(int(preferred_port))
    ports.extend(range(DEFAULT_PORT, DEFAULT_PORT + DISCOVERY_SPAN))
    seen = set()
    for port in ports:
        if port in seen:
            continue
        seen.add(port)
        if _health(port) is not None:
            return {"app": APP_TAG, "port": port, "url": f"http://127.0.0.1:{port}"}
    return None


# --------------------------------------------------------------------------
# 并发启动锁（O_EXCL 文件锁；进程被强杀时按 mtime 判过期）
# --------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class _LaunchLock:
    def __init__(self, timeout: float = LOCK_STALE_SEC + 60.0):
        self.path = _lock_path()
        self.timeout = timeout
        self.owned = False

    def _owner_pid(self) -> Optional[int]:
        try:
            parts = self.path.read_text(encoding="utf-8").split()
            return int(parts[0])
        except (OSError, ValueError, IndexError):
            return None

    def __enter__(self) -> "_LaunchLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(f"{os.getpid()} {time.time():.3f}")
                self.owned = True
                return self
            except FileExistsError:
                owner = self._owner_pid()
                if owner is not None:
                    if not _pid_alive(owner):
                        # 持有者已经不在了；再确认一次文件仍是同一个 owner，避免误删他人刚建的锁。
                        if self._owner_pid() == owner:
                            self.path.unlink(missing_ok=True)
                        continue
                    # 持有者还活着：即使 mtime 超过阈值也不能抢（可能在解包/启动较慢）。
                else:
                    try:
                        if time.time() - self.path.stat().st_mtime > LOCK_STALE_SEC:
                            self.path.unlink(missing_ok=True)
                            continue
                    except OSError:
                        pass
                if time.time() >= deadline:
                    raise TimeoutError("另一个启动器正在启动服务，请稍后重试。")
                time.sleep(0.1)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.owned:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


# --------------------------------------------------------------------------
# detached 拉服务
# --------------------------------------------------------------------------

def _tail(path: Path, lines: int = 20) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _spawn_server(entry: Optional[Path], port: int) -> Dict[str, Any]:
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    info_file = cache / f"spawn-{uuid.uuid4().hex}.json"
    log_path = _log_path()

    frozen = bool(getattr(sys, "frozen", False))
    cmd = [sys.executable]
    if not frozen:
        if entry is None:
            raise RuntimeError("缺少 app.py 路径，无法启动服务。")
        cmd.append(str(entry))
    cmd += [
        "--serve",
        "--no-browser",
        "--port",
        str(int(port)),
        "--info-file",
        str(info_file),
    ]

    cwd = entry.parent if (entry is not None and not frozen) else Path(sys.executable).parent
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        kwargs["creationflags"] = (
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True

    with open(log_path, "ab") as log:
        log.write(
            f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] spawn: {' '.join(cmd)}\n".encode(
                "utf-8"
            )
        )
        log.flush()
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            cwd=str(cwd),
            close_fds=True,
            env=env,
            **kwargs,
        )

    deadline = time.time() + START_TIMEOUT_SEC
    while time.time() < deadline:
        code = proc.poll()
        if code is not None:
            raise RuntimeError(
                f"服务启动失败（退出码 {code}）。日志：{log_path}\n{_tail(log_path)}"
            )
        info = _read_info(info_file)
        if info and _health(int(info["port"]), timeout=1.0) is not None:
            info_file.unlink(missing_ok=True)
            return info
        time.sleep(0.2)

    raise TimeoutError(
        f"等待服务启动超时（{START_TIMEOUT_SEC:g}s）。日志：{log_path}\n{_tail(log_path)}"
    )


def _ensure_server(
    entry: Optional[Path], preferred_port: int, force_new: bool = False
) -> Tuple[int, bool, Dict[str, Any]]:
    with _LaunchLock():
        if not force_new:
            info = _find_server(preferred_port)
            if info:
                return int(info["port"]), True, info
        info = _spawn_server(entry, preferred_port)
        return int(info["port"]), False, info


# --------------------------------------------------------------------------
# 服务模式（前台，阻塞）
# --------------------------------------------------------------------------

def serve(
    module,
    port: int,
    no_browser: bool,
    info_file: Optional[str] = None,
) -> int:
    from werkzeug.serving import ThreadedWSGIServer

    # 关键：Windows 下 SO_REUSEADDR 允许两个进程同时 bind 同一端口（实测 5000
    # 会被两个实例同时监听，请求随机落到其中一个）。关掉它，端口被占时 bind
    # 必须失败，才能按 5000→5001→… 顺延。
    class _ExclusiveThreadedWSGIServer(ThreadedWSGIServer):
        allow_reuse_address = os.name != "nt"

    module.ensure_cache_dirs()
    # 已有别的实例在跑时不要清理 runs/previews——那是它的活动目录。
    # 除了 canonical server.json，再探测默认端口段，避免 canonical 过期后误清。
    if _live_server(except_pid=os.getpid()) is None and _find_server(0) is None:
        try:
            module.cleanup_orphan_transient()
        except Exception:
            pass

    requested = int(port)
    candidates = [0] if requested == 0 else [requested + i for i in range(BIND_SPAN)]
    server = None
    last_exc: Optional[Exception] = None
    for candidate in candidates:
        # werkzeug 在 bind 失败时会直接 sys.exit(1)（不是抛 OSError），
        # 必须接住 SystemExit 才能继续试下一个端口。
        err_buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(err_buf):
                server = _ExclusiveThreadedWSGIServer(
                    "127.0.0.1", candidate, module.app
                )
            break
        except SystemExit:
            detail = err_buf.getvalue().strip().splitlines()
            last_exc = RuntimeError(detail[0] if detail else f"端口 {candidate} 不可用")
            continue
        except OSError as exc:
            last_exc = exc
            continue
    if server is None:
        raise RuntimeError(f"无法绑定端口：{last_exc}")

    actual = int(server.server_port)
    url = f"http://127.0.0.1:{actual}"
    info = {
        "app": APP_TAG,
        "pid": os.getpid(),
        "port": actual,
        "url": url,
        "version": getattr(module, "APP_VERSION", ""),
        "started_at": time.time(),
    }
    _write_info(_info_path(), info)
    if info_file:
        _write_info(Path(info_file), info)

    print(f"服务器已启动：{url}", flush=True)
    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _remove_info(_info_path(), actual)
        if info_file:
            Path(info_file).unlink(missing_ok=True)
    return 0


# --------------------------------------------------------------------------
# 启动模式：导入并打开标签页
# --------------------------------------------------------------------------

def _error_text(status: int, data: Any) -> str:
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace").strip()[:500] or f"HTTP {status}"
    return f"HTTP {status}"


def _open_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


def _wait_download(port: int, source_id: str, timeout: float) -> Dict[str, Any]:
    deadline = time.time() + max(0.0, timeout)
    last: Dict[str, Any] = {"status": "unknown"}
    path = f"/api/source/{urllib.parse.quote(source_id)}/download"
    while time.time() < deadline:
        try:
            status, data = _request(port, "GET", path, timeout=3)
            if status == 200 and isinstance(data, dict):
                last = data
                if data.get("status") in ("done", "error"):
                    return data
        except Exception as exc:
            last = {"status": "unknown", "message": str(exc)}
        time.sleep(1.0)
    return {"status": "timeout", "message": f"等待 {timeout:g}s 仍未完成", **last}


def _emit(payload: Dict[str, Any]) -> None:
    # ensure_ascii=True：Windows 控制台默认 GBK，直接输出中文会让 agent 侧解码成乱码。
    # JSON 里的 \uXXXX 转义对任何解析器都无损。
    print(json.dumps(payload, ensure_ascii=True, indent=2), flush=True)


def _fail(message: str) -> int:
    _emit({"ok": False, "app": APP_TAG, "error": message})
    return 1


def _run_launch(args: argparse.Namespace, entry: Optional[Path], no_browser: bool) -> int:
    try:
        port, reused, info = _ensure_server(entry, args.port, args.new_instance)
    except Exception as exc:
        return _fail(f"启动服务失败：{exc}")

    result: Dict[str, Any] = {
        "ok": True,
        "app": APP_TAG,
        "server": f"http://127.0.0.1:{port}",
        "reused": reused,
        "log": str(_log_path()),
        "tabs": [],
    }
    exit_code = 0

    for raw in args.bvid:
        bv = (raw or "").strip()
        if not bv:
            continue
        try:
            status, data = _request(
                port, "POST", "/api/import", form={"bvid": bv}, timeout=60
            )
            if status != 200 or not isinstance(data, dict):
                raise RuntimeError(_error_text(status, data))
            source_id = data.get("id")
            if not source_id:
                raise RuntimeError("导入接口没有返回 source id。")
            meta = data.get("metadata") or {}
            download = data.get("download") or {}
            tab: Dict[str, Any] = {
                "ok": True,
                "bvid": bv,
                "url": (
                    f"http://127.0.0.1:{port}/?source="
                    f"{urllib.parse.quote(str(source_id))}"
                ),
                "source_id": source_id,
                "type": data.get("type"),
                "title": meta.get("display_title") or meta.get("raw_title"),
                "download": download.get("status"),
                "has_cache": bool(data.get("has_cache")),
                "opened": False,
            }
            if not no_browser:
                tab["opened"] = _open_browser(tab["url"])
            result["tabs"].append(tab)
        except Exception as exc:
            exit_code = 1
            result["tabs"].append({"ok": False, "bvid": bv, "error": str(exc)})

    # 先把所有 BV 都导入（下载线程已经并发跑起来），再统一等待，避免 --wait 串行化。
    if args.wait and args.wait > 0:
        for tab in result["tabs"]:
            if not tab.get("ok") or not tab.get("source_id"):
                continue
            waited = _wait_download(port, str(tab["source_id"]), float(args.wait))
            tab["download"] = waited.get("status")
            if waited.get("message"):
                tab["download_message"] = waited["message"]
            if waited.get("status") == "error":
                tab["ok"] = False
                exit_code = 1

    result["ok"] = exit_code == 0
    _emit(result)
    return exit_code


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main(module, entry: Optional[Path] = None, argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="BiliTabCapture 启动器：打开 GUI 并自动导入 B 站视频。"
    )
    parser.add_argument(
        "--bvid",
        action="append",
        default=[],
        metavar="BV号",
        help="要导入的 BV 号/av 号/视频链接；可重复传递多个。",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", str(DEFAULT_PORT))),
        help=f"首选端口（默认 {DEFAULT_PORT}，被占用会自动顺延；0 = 系统分配）。",
    )
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器。")
    parser.add_argument(
        "--wait",
        type=float,
        default=0.0,
        metavar="秒",
        help="等待视频下载完成的最长秒数（0 = 导入后立即返回）。",
    )
    parser.add_argument(
        "--new-instance",
        action="store_true",
        help="不复用已有服务，强制新开一个实例。",
    )
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--info-file", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.bvid = [b.strip() for b in args.bvid if (b or "").strip()]

    no_browser = bool(args.no_browser or os.environ.get("BTAB_NO_BROWSER"))

    if args.serve or not args.bvid:
        try:
            return serve(module, args.port, no_browser, args.info_file)
        except KeyboardInterrupt:
            return 0

    return _run_launch(args, entry, no_browser)
