# AGENTS.md

本文件为在本仓库工作的 AI/工程师提供上下文。**先读本文件，再动手改代码。**

## 文档维护约定（重要）

- **新增功能后，必须先向用户确认，再把该功能的说明补进本文件**（模块职责、缓存结构、约定与坑、常用命令等对应章节）。**不要未经确认就自行写入。**
- 修改既有行为时同理：先确认，再同步更新本文件里已经过时的描述。
- 本文件是项目上下文的唯一事实来源；`CLAUDE.md` 已并入本文件。

## 项目是什么

BiliTabCapture：把**动态谱视频或乐谱文档**（哔哩哔哩视频 / 本地视频 / 图片 / PDF）转换为 PDF/长图的工具，主要用于制谱截图。

- 后端：Flask 本地 Web 服务（`app.py`），默认 `http://127.0.0.1:5000`，启动后自动开浏览器。
- 启动器：`launcher.py`。`python app.py` / 双击 exe = 原来的前台服务；`python app.py --bvid BV…` = 复用/后台拉起常驻服务、自动导入并打开对应标签页（供 agent/脚本调用，见「关键约定」第 20 条）。
- 前端：`templates/index.html` + `static/app.js` + `static/styles.css`（原生 JS，无框架）。
- 流程四步：**导入 → 截图(第2步) → 调整裁剪/小节线(第3步) → 排版导出(第4步)**。
  第 4 步导出 PDF / 长图 / **`.psz`**（`.psz` 是 PDF Score 的谱面包，见下面「关键约定」第 18 条）。
- 四类源：B站视频(`bilibili`) / 本地视频(`local`) / 图片(`image`) / PDF(`pdf`)。
  其中 `image` 与 `pdf` 在“卡片/截图”层面**共用同一套逻辑**（`app.IMAGE_LIKE_TYPES`），
  区别只在缓存目录、渲染方式与第 2 步的取值单位。

## 目录与模块职责

| 文件 | 职责 |
| --- | --- |
| `app.py` | Flask 服务与全部 HTTP 路由；任务(Job)/源(Source)内存管理；缓存与历史记录；PDF/长图/psz 导出。 |
| `launcher.py` | **启动/并发入口**：`--bvid` 快捷启动（发现/复用常驻服务，或 detached 拉起一个；经 HTTP 调 `/api/import`，再打开 `/?source=<id>`，最后输出 JSON）；`--serve` 是真正的前台服务实现（独占端口、写 `server.json`、并发锁）。 |
| `tab_extractor.py` | 视频抽帧、去重、裁剪、小节线识别、长图/PDF 绘制；**同时也可作为独立 CLI** (`python tab_extractor.py <url/文件>`)。`build_pdf_from_images` 的 `geometry` 出参与 `build_score_meta` 负责 psz 的排版几何 → `score.json`。 |
| `bili.py` | 哔哩哔哩扫码登录(二维码)与视频下载。**所有平台统一走这里**，不使用 `bilix.exe`/yt-dlp。同时是**所有 B 站 HTTP 请求的唯一出口**：`bili_get()` / `get_json()` / `resolve_short_url()`（curl_cffi 浏览器指纹 + 主页热身 + 412 降级，见「关键约定」第 19 条）。 |
| `pdf_render.py` | **PDF 渲染**：`render_pdf_pages` 批量逐页渲染、`render_pdf_page` 单页渲染、`pdf_page_count` 探页数、`clamp_dpi` 收敛 DPI（50–600，默认 200）。依赖 PyMuPDF，**延迟导入**——缺依赖时只有真正导入 PDF 才报错，视频/图片不受影响。 |
| `runtime_paths.py` | 打包(PyInstaller)与源码运行时的路径解析：`resource_dir()`(只读资源)/`data_dir()`(可写数据, cookie/缓存)。 |
| `static/app.js` | 前端全部逻辑（导入、预览、裁剪、小节线、排版、上传、历史记录、更新检查、复位图标等）。 |
| `static/icons/*.svg` | 工具栏/按钮图标，均为独立 SVG 文件，**直接改文件即可替换图形**（`loadIcons()` 读取）。 |
| `build_exe.bat` / `build_mac.sh` | 一键打包（免环境运行）。产物写入 `dist/`。 |
| `VERSION` | 版本号，渲染进页面；更新检查用它比较。 |

## 数据与缓存结构（`BiliTabCapture_cache/`）

**持久缓存（可恢复进度；不要在清理逻辑里误删）**
- `bv/<bvid>/`：B 站视频。含下载的视频文件（`.m4s` DASH 视频轨，或回退的 `.mp4`）、**`audio.m4a`**（DASH 音频轨，导出 psz 的伴奏）、`crops/`(截图)、`state.json`。
- `local/<hash>/`：本地视频（按文件内容 SHA-256 哈希）。
- `img/<hash>/`：图片（`master.*` 原图 + `crops/` + `state.json`）。
- `pdf/<hash>/`：PDF（按文件内容 SHA-256 哈希）。含：
  - `master.pdf` 原文件；
  - `pages/`：**按当前 DPI 渲染的整份 PDF 逐页位图**（`page_0001.png`…），带 `.dpi<N>.txt` 版本戳，DPI 变化时整体重渲；
  - `crops/`：裁剪后的截图（一页一张，即第 3 步的卡片）；
  - `state.json`。

**中间产物（不参与恢复，可安全清理）**
- `runs/<job_id>/`：一次“生成截图/导出”的工作目录（`crops/`、`final/`、导出的 PDF/长图/psz）。
- `uploads/<source_id>/`：导入上传的中转副本。
- `previews/`：预览帧 jpg 与 B 站预览视频。

**`state.json` 关键字段**：`captures`(截图列表)、`images`(前端卡片状态：裁剪/隐藏/小节线)、`capture_params`、`metadata`(标题/作者/来源)、`layout`、`maxStage`、`params2`(第 2 步截图参数，见下)。PDF 额外有 `pdf_dpi`(渲染分辨率)、`page_count`(总页数)、`page_range`(`{start,end}` 页码区间)。

## 关键约定与“坑”（务必遵守）

1. **下载清晰度/角标以“实测”为准**：不要用 B 站返回的 qn 标签当作真实清晰度（会出现“标 1080P60 实际很糊”）。下载后写 `.dlqh.txt`（OpenCV 实测帧高），预览角标优先用它；`.dlqn.txt` 仅为回退。
2. **DASH 视频轨制谱、音频轨只给 psz 用**：制谱只需画面。`bili.py` 从视频页 SSR(`__playinfo__`) 取 DASH **视频轨(codecid=7/AVC)** 直接下 `video.m4s`，OpenCV 可直接读取 `.m4s`；**不做 ffmpeg 合并**。`with_audio=True` 时顺带把 **AAC 音频轨**下成同目录的 `audio.m4a`（给第 4 步导出 psz 当伴奏用，抽帧不看它）。音频轨认的是 `dash.audio[].id`（30216/30232/30280），**不是 `codecid`**（实测音频轨 codecid 一律是 0）。SSR 拿不到时才回退单文件 `durl` 接口 —— 那条路上音视频是混在一起的，**没有独立音轨**，导出的 psz 不带音频。
3. **登录态可能触发防盗链 404**：个别视频带登录 cookie 请求高清直链会 404/403，游客反而可用。下载逻辑需保留「先登录→失败退游客」的重试与逐档降清晰度。
4. **`.m4s` 是一等公民**：新增/修改“视频文件”识别时，务必把 `.m4s` 一并纳入（`tab_extractor.VIDEO_FILE_EXTENSIONS`、`app.ALLOWED_VIDEO_EXTENSIONS`、历史记录 `_playable`、前端 `accept` 与 `isImportableFile`）。漏掉会导致历史记录灰显、导入提示不支持。**反向也有一条**：`find_video_file()` 是按扩展名 glob 整个目录的，所以缓存目录里的音轨只能叫 `audio.m4a`（`AUDIO_FILE_EXTENSIONS` 一个都不能落进 `VIDEO_FILE_EXTENSIONS`）——改名成 `audio.m4s` / `audio.mp4` 就会被当成视频挑走（新下的音频 mtime 更新，必中），抽帧直接报错。
5. **历史记录 = 直接遍历缓存目录**：目的是“管理本地存储占用”，**不能用 `state.json` 是否存在来筛选**——只下载未截图的目录也要列出（标题缺失时回退为 key）。恢复时：有截图态→复原卡片/排版；无截图态→进入第 2 步并回填 `params2`。
6. **第 2 步参数提前持久化**：截图参数（起止时间、采样间隔、差异阈值、条带半宽、比较窗口、去色容差/平滑、抽帧前裁剪框 `crop*`）通过 `POST /api/save_capture_prefs` 存进 `state.json` 的 `params2`（改动即存、防抖）。读取时 `read_capture_prefs()` 会为缺失字段**回退默认值以兼容旧版缓存**。**视频截图完成写 state 时只替换 `captures/capture_params/metadata/images`，必须保留 `params2/layout`**——整份覆盖会丢掉第 2/4 步设置（已修）。
7. **“从新开始”（命中弹窗）会删除整个缓存目录**（`/api/cache_clear` + `full:true`）：之后必须避免继续引用已删除的文件（B 站会重新导入下载，本地/图片则回到第 1 步让用户重选）。
8. **中文标题字体**：PDF/长图的标题渲染依赖跨平台 CJK 字体查找（`tab_extractor.load_font`）。修改字体逻辑要保证 macOS（Hiragino/Songti/STHeiti）与 Windows（msyh 等）都能命中，否则中文会变“豆腐块”。
9. **端口被占自动顺延**：启动逻辑已移到 `launcher.py`：5000 被占（macOS AirPlay / 另一个实例）时按 5001、5002… 顺延，`--port 0` 交给系统分配；`BTAB_NO_BROWSER=1` 可禁止自动开浏览器。
10. **更新检查走后端代理**：浏览器直连 Gitee API 会 403/CORS。前端只请求同源 `GET /api/latest_version`（后端依次尝试 Gitee tags → GitHub releases）。
11. **PDF 与图片共用卡片逻辑**：`app.IMAGE_LIKE_TYPES = {"image", "pdf"}`。凡是判断“非视频源”，**必须用 `IMAGE_LIKE_TYPES` 而不是 `== "image"`**（如 `/api/captures` 分流、`/api/source_image`）。漏掉会导致 PDF 走视频抽帧分支直接报错。前端对应的是 `state.sourceType`。
12. **第 2 步时间轴是双单位的**：视频按「秒」、PDF 按「页」，共用同一条轨道/把手/进度条。前端用 `state.timeUnit`（`"sec"`/`"page"`）与 `isPageMode()` 切换，取值走 `currentStartTime()/currentEndTime()`，换算走 `timeToRatio()/ratioToTime()`。页模式用 `(page-1)/(total-1)` 让首尾页贴住两端。**新增第 2 步参数要同步 `STEP2_PARAM_IDS` + `collectStep2Params()` + `applyStep2Params()`**，否则改动不会持久化。
13. **PDF 预览复用 `/api/preview`**：请求体里 `time` 对 PDF 是**页号**（1 基），后端渲染该页并缩成 jpg 返回，结构与视频预览完全一致——前端不必为 PDF 分叉预览逻辑。
14. **PDF 渲染缓存按 DPI 失效**：`pdf/<hash>/pages/` 存整份渲染图，用 `.dpi<N>.txt` 当版本戳；改 DPI 必须整体重渲（`_pages_complete()` 逐页校验，缺页即重渲）。`cache_clear` 非 full 分支也要清 `pages/`，否则「从新开始」后仍命中旧 DPI 图。
15. **缓存源（图片/PDF）的截图必须落 `cache/crops`**：`start_image_capture()` 的 `crops_dir` 取自 `cache_dir/crops` 并写 `state.json`；否则历史记录里该条目永远没有 `crops`、封面 404、无法恢复进度。`runs/<job_id>/crops` 只用于没有缓存目录的源。
16. **`.field[hidden]` 不生效**：`.field { display: grid }` 会覆盖 `hidden` 属性默认样式，样式表末尾有 `.field[hidden]/.time-field[hidden] { display: none !important }` 兜底。新增可隐藏字段给它加 `.field` 类即可生效。
17. **PyMuPDF 是新依赖**：`requirements.txt` 已加 `pymupdf`，两个 `.spec` 的 `hiddenimports` 也加了 `pymupdf`/`fitz`。**打包机器同样需要安装**，否则 PDF 导入会提示缺少支持库。
18. **导出 `.psz`（PDF Score 的谱面包）**：`POST /api/psz` 打包、`GET /api/jobs/<job_id>/psz` 下载，用 Python 自带 `zipfile`（前端没有压缩库，别改成前端打包）。所有条目都放在**包根目录** —— 多一层子目录，PDF Score 就会把它认成第二张乐谱：
    - `score.pdf`：正文，就是 `/api/pdf` 那一份（同一套排版参数、同一次 `prepare_final_strips`）。
    - `score.json`：标题 + 行/小节线 + 音频信息。**每一行都只有两条小节线（= 每行一个小节）时**那份 `systems/bars` 什么也没描述出来：**有音频就只写 `pages: []`**（留住标题与音频对齐 —— PDF Score 的自动识别是无条件跑的、只跳过已经有行的页，所以留空正好等于让它自己认），**没有音频就干脆不放**（`build_score_meta()` 返回 None，接口回 `has_meta: false`）。
    - `audio.m4a`：只有 B 站源有（缓存目录里那份，见第 2 条）。**音频起点固定 `startOffset: 0`**（下下来的就是整条音轨）；`duration` 与波形峰一律留空，由 PDF Score 自己解码现算 —— 这边不解码 AAC，所以包里**没有** `peaks.f32`。
    - `cover.<ext>`：只有 B 站源有，就是**视频封面**。复用 `/api/history/cover` 已经在用的 `_bilibili_real_cover()`（自带短 TTL 缓存，别另写一套拉封面的），**扩展名按魔数认**（jpg/png/webp/gif/bmp/avif），不要一律写 `.jpg`。
    四条硬约定：
    - **几何必须复用同一次排版**：`build_pdf_from_images(geometry=...)` 出参带出每页每行的像素落点，`build_score_meta()` 换算成 pt 写进 `pages/systems/bars`。**不要在别处重算排版**，否则 PDF 与小节线会错位。
    - **`pt = px × 72 / PDF_RESOLUTION`**（`tab_extractor.PDF_RESOLUTION = 150.0`，与落盘时的 `resolution=` 是同一个常量）：页面边长就是像素 × 72/150。改 PDF 分辨率必须走这个常量，写死别的数会让全部小节线偏移。
    - **每行要标首尾两条竖线**：PDF Score 里「一行相邻两条线之间 = 一个小节」，所以行内每条 final 竖条的左边界 + 最后一根的右边界都要写；漏了首尾小节就没了。final 竖条本来就是按小节线切的，**每条竖条正好一个小节**。
    - `y0` 取上沿（值大）、`y1` 取下沿（值小）；`pages` 非空时张数与顺序必须与 PDF 完全一致（PDF Score 会用 PDF 的真实页数/尺寸覆盖它们）。`segments` 留空即可——BiliTabCapture 拿不到速度信息，PDF Score 会补一条默认 120 BPM 4/4 的「开头」段落。
19. **B 站请求一律走 `bili` 的 curl_cffi 入口，禁止用 urllib / 裸 requests**：B 站风控按 **TLS/HTTP 指纹**判定脚本，命中就返回 **`HTTP 412 Precondition Failed`**，覆盖面包括 `api.bilibili.com` 的 `x/web-interface/view`、视频页 HTML、b23.tv 短链——**补齐 Cookie 与整套浏览器头依然 412**（实测：urllib 全 412，换 `impersonate="chrome124"` 立刻 200）。海外 / 机房 IP 的判定阈值更低，所以海外用户的表现就是「一导入 B 站视频就报 412」。统一写法：
    - `bili.bili_get(url, cookie=…)` —— 通用 GET（响应对象）
    - `bili.get_json(url, cookie=…)` —— JSON 接口
    - `bili.resolve_short_url(url)` —— 展开 b23.tv 短链
    这三者内部负责 `impersonate` + 首次主页热身（拿 `buvid3`/`b_nut`）+ 412 时「登录态 → 游客态并重新热身」降级重试。**新增任何 B 站接口调用都必须接进这套入口**；`tab_extractor.py` 里那个 urllib 的 `_NO_PROXY_OPENER` 现在没有调用方，**别再拿它去请求 B 站**（`_resolve_url` / `_bilibili_api_json` 就是踩了这个坑）。
    - **412 ≠ 区域限制**：番剧/版权视频被墙返回的是 `-10403 您所在地区不可观看`（或 404），那只能挂代理，改代码没用。
    - `curl_cffi` 的 session 默认 `trust_env=True`，会读 `http_proxy` / `https_proxy` 环境变量；原来那两处 urllib 是显式跳过系统代理的，环境变量代理的机器上行为会有这点差异。
20. **agent 快捷启动（`--bvid`）**：`app.py` 的 `__main__` 现在只负责委托给 `launcher.main()`。`--bvid` **不是在前台起服务**：
    - 先读 `BiliTabCapture_cache/server.json`，`GET /api/version` 返回 `app: "BiliTabCapture"` **且 `data_dir` 与本副本一致**才复用；否则只快速探测 5000–5002，仍没有才 detached 拉起 `--serve`（用唯一 `--info-file` 回传实际端口，避免与 canonical 文件抢写）。**不同目录的源码/exe 副本缓存根不同，必须各自用各自的服务**，不能按端口互相复用。
    - 发现/拉起用 `BiliTabCapture_cache/launcher.lock` 串行化，多个 agent 并发调用只会起一个服务；不同 BV 的导入/下载在同一个服务里并行。
    - 导入在服务端完成（`POST /api/import`，form 字段 `bvid`），随后打开 `/?source=<source_id>`；前端 `openSourceFromQuery()` 调 `GET /api/source/<id>` 后 `applySource()`。**`source` 参数故意保留在 URL 上**，刷新/浏览器重启后还能恢复同一个源。该接口在源已有截图态时会附带 `restore`（复用/创建 cached job），刷新后会弹“恢复进度”，**不会退回第 2 步丢卡片**。
    - stdout 只输出 JSON（`ensure_ascii=True`，避免 Windows GBK 把中文变成乱码）；退出码 0=全部成功、1=有 BV 导入失败。`--wait N` 会先把所有 BV 导入完，再统一等下载。日志在 `BiliTabCapture_cache/launcher-server.log`。
    - **服务是 detached 的**（没有窗口可关）：用 `--status` 查看、`--stop` 停止（会清理 `server.json`/`launcher.lock`）；也可以手动结束 `server.json` 里的 `pid`。删 `server.json` 停不了服务（默认端口段内的实例下次仍会被探测复用）。没有 `app` + 相同 `data_dir` 标识的旧版/其他副本服务不会被复用。
    - 打包脚本已加 `--hidden-import launcher`；**改完必须重新打包**，旧 exe 不认 `--bvid`。
21. **并发相关的几个硬点**：
    - **端口必须独占**：Windows 的 `SO_REUSEADDR` 允许两个进程同时监听同一端口（实测两个实例都能 bind 5000，请求随机落到其中一个）。`launcher.serve()` 用 `allow_reuse_address = False` 的 `ThreadedWSGIServer` 子类；werkzeug bind 失败走的是 `sys.exit(1)`，必须接住 `SystemExit` 才能继续试下一个端口。**不要改回 `app.run` / `make_server` 的默认值**。
    - **启动前不要全端口段扫描**：`serve()` 里曾为“判断是否还有别的实例”而探测 5000–5029，遇到不响应的端口会让首个服务 30 秒才就绪；等待方把**活锁**当过期，结果重复起了两个服务（实测复现）。现在只探测 canonical + 5000–5002，且 `_LaunchLock` 会检查锁文件里的 owner pid（活着就不抢，死了立即回收），不再只靠 mtime。
    - **B 站请求线程隔离**：`bili.py` 用 `threading.local()` 的 `_get_session()`；**不要再引入模块级共享 `Session`**（多标签并发导入/下载会共用同一个 curl handle）。Cookie 仍然逐请求显式传。
    - **同一个 BV 只下载一次**：`bilibili_download_lock()` 按缓存目录串行化；`cached_bilibili_video()` 只认下载完成后重命名的 `BVxxx.*`，**不要把下载中的 `video.m4s` / `video.mp4` 当缓存**（并发导入会误判“已下好”）。新增下载命名方式时两处都要同步。
    - **缓存恢复的 job 必须带 `video_path`**：`make_cached_job()` 通过 `cached_job_video_path()` 找回成品视频，否则从历史/刷新恢复后“补插截图”会 409。
    - 另外：`launcher.serve()` 在启动清理前会探测是否已有实例在跑，有就跳过 `cleanup_orphan_transient()`，否则会删掉别的实例的 `runs/`、`previews/`。

## 常用命令

```bash
# 开发运行（建议虚拟环境）
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
.venv/bin/python app.py                         # 打开 http://127.0.0.1:5000

# agent 快捷启动：复用/拉起常驻服务，自动导入并打开标签页（--bvid 可重复）
.venv/bin/python app.py --bvid BV1xx411c7mD
.venv/bin/python app.py --bvid BV1... --bvid BV2... --wait 300

# 查看/停止常驻服务
.venv/bin/python app.py --status
.venv/bin/python app.py --stop

# 常驻服务是 detached 的；也可以手动结束 server.json 里的 pid
# （Windows: Stop-Process -Id <pid>）

# 仅当参数解析/抽帧算法改动时，用 CLI 快速验证
.venv/bin/python tab_extractor.py "<BV号或本地文件>" --output out.pdf

# 打包
build_exe.bat        # Windows → dist/BiliTabCapture.exe
bash build_mac.sh    # macOS   → dist/BiliTabCapture_mac

# 语法自检（无需运行 GUI）
.venv/bin/python -m py_compile app.py launcher.py tab_extractor.py bili.py runtime_paths.py pdf_render.py
node --check static/app.js

# 造一个多页测试 PDF（不需要任何外部工具）
.venv/bin/python -c "
from PIL import Image
ps=[Image.new('RGB',(1240,1754),'white') for _ in range(5)]
ps[0].save('/tmp/t.pdf', save_all=True, append_images=ps[1:])"
```

## 修改指引

- 改后端路由/任务：先读 `app.py` 里相近的路由与 `JOBS/SOURCES` 的用法，保持异常都返回 `json_error(...)`。
- 改前端：`static/app.js` 顶部有 `els`（DOM 引用）与 `els` 之外的 `state`；新增 UI 记得在 `index.html` 加元素、在 `els` 注册，样式追加到 `styles.css` 末尾并复用主题变量（`--accent` / `--accent-weak` / `--accent-strong` / `--line` / `--input-bg` / `--muted` 等）。
- 图标：优先放 `static/icons/<name>.svg` 并在 `loadIcons()` 的映射中登记，不要在 JS 里内联大段 SVG（除非占位回退）。
- 依赖：保持精简（现为 Flask / numpy / opencv-python / Pillow / curl_cffi / qrcode / **pymupdf**）。**不要引入 ffmpeg 或新的重型依赖**，除非明确讨论过。
- **启动/并发改动**：`app.py` 的 `__main__` 只委托 `launcher.main()`；新增启动参数放 `launcher.main()` 的 argparse，不要再恢复 `app.py` 里旧的 `_port_free` / `app.run` 逻辑。前端 `?source=` 参数要保留（刷新恢复依赖它）。
- **无头验证后端**：Flask 自带 `app.test_client()` 就能把整条链路跑通（导入→预览→生成截图→导出 PDF），不必开浏览器。记得 `BTAB_NO_BROWSER=1`，跑完清掉 `BiliTabCapture_cache/` 里的测试残留。

## 验证要求

- 至少跑上面的 `py_compile` 与 `node --check`。
- 涉及下载/清晰度：实际下载一个 B 站视频确认能被 OpenCV 打开并读出帧尺寸。
- 涉及缓存/历史：确认持久缓存 `bv/local/img/pdf` 不被误删，中间产物可被清理。
- 涉及 PDF：确认导入能拿到页数、改 DPI 会整体重渲、页码区间能正确产出对应页数的卡片。
- 涉及 psz：`POST /api/psz` 后把包读回来，确认条目**都在根目录** —— 多一个子目录就会在 PDF Score 那边变成两张乐谱；再对一遍四条分支：有小节线→`score.json` 带 `pages`；每行一个小节且无音频→**没有** `score.json`；每行一个小节但有音频→`score.json` 在且 `pages` 为空数组。带 `score.json` 且 `pages` 非空时，还要确认 `pages` 的张数、页面尺寸与 PDF 实际的页数、MediaBox 一致（PDF Score 会拿 PDF 的真实值覆盖它们）。
- 涉及启动器/并发：确认无服务时能 detached 拉起、有服务时返回 `reused: true`、两个启动器并发只起一个服务；端口被占能顺延到 5001；`?source=` 页面能进第 2 步且刷新后仍能恢复；同一个 BV 并发导入只下载一次（可 mock `download_bilibili_video` 验证调用次数）。
- 涉及打包：重新执行 `build_exe.bat` / `build_mac.sh` 后，至少确认 `--bvid` 可用；未重新打包要明确说明“旧 exe 不包含该功能”。
- 无浏览器自动化环境时，明确说明“未运行 GUI，需人工点验”。
