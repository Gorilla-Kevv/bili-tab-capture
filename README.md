# BiliTabCapture

将本地或哔哩哔哩的动态谱视频转换为PDF文件

相比[TabCapture](https://github.com/santiRostan/TabCapture)新增更多功能，移除Youtube视频下载功能

## 新增功能

### 支持更多种类的视频
- 方便地拖拽上传本地文件
- 通过哔哩哔哩BV号导入视频
- 登录哔哩哔哩账号获取更高分辨率视频
- 谱面背景为彩色的视频
- 全屏动态谱视频
- 不同段落的谱面高度不一致的视频
- 从图片导入谱面进行排版

### 支持手动调整
- 调整图片缩放
- 裁切或删去截图，去除重复部分
- 补插两次截图间隔之间缺失的截图
- 图像反色，去除彩色背景，自定义背景及字体颜色
- 页边距，图片间距设置
- 图像水平和竖直的对齐设置
- 纸张横竖方向设置
- 标题支持非英文字符
- 实时预览调整效果
- 更方便的视频时间裁切及预览

### 其他功能
- 导出为长图
- 自动识别小节线
- 自动裁剪去重
- 缓存恢复支持“恢复进度 / 重置进度 / 删除缓存”
- 页面刷新后可恢复已完成的截图，也会继续等待正在生成的任务
- 支持命令行按 BV 自动导入（供 agent / 脚本批量派发）
- 提供 MCP Server（stdio），供本地 agent / 工具调用

## 声明

仅供学习与练习使用，若UP主提供谱面购买链接请勿使用本工具，尊重辛苦扒谱劳动成果

## 致谢

- [santiRostan/TabCapture](https://github.com/santiRostan/TabCapture) 项目框架及截图算法
- [Carrot-shreds/score_capture](https://github.com/Carrot-shreds/score_capture) 自动识别小节线及裁剪去重

## 从源码运行

安装依赖
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

运行
```bash
python app.py
```

## 命令行自动导入（供 agent / 脚本使用）

```bash
python app.py --bvid BV1xx411c7mD

# 查看 / 停止后台服务（--stop-all 连随机端口的本副本实例一起停）
python app.py --status
python app.py --stop
python app.py --stop-all
```

- 会先复用已经运行的本地服务；没有就在后台拉起一个（端口与 pid 记录在 `BiliTabCapture_cache/server.json`）。
- 自动导入指定 BV，并在浏览器打开对应页面；后续裁剪、排版、导出仍在网页里手动完成。
- 页面地址带 `?source=`，刷新/浏览器重启后可恢复；已有截图时会提示“恢复进度”。
- 支持多个 `--bvid`；`--wait 300` 可等待视频下载完成后再退出；`--new-instance` 强制新开一个服务。
- `--status` 查看服务状态，`--stop` 停止服务（页面会断开，缓存保留）。
- 打包后的 `BiliTabCapture.exe` 使用相同参数（需要重新打包才会包含该功能）。

## MCP（给本地 agent / 工具）

MCP 是可选入口，依赖单独安装：

```bash
pip install -r requirements-mcp.txt
```

通常由 MCP 客户端以 stdio 启动 `mcp_server.py`。客户端配置示例：

```json
{
  "mcpServers": {
    "bili-tab-capture": {
      "command": "F:/path/to/.venv/Scripts/python.exe",
      "args": ["F:/path/to/mcp_server.py"]
    }
  }
}
```

macOS 把 command 换成 `.venv/bin/python`。

可用工具：

- `import_bvid`：导入 BV/av/链接，可等待下载完成
- `get_source` / `capture` / `get_job`：查看源、生成截图、查询任务
- `detect_measures` / `auto_crop`：小节线/自动裁剪，可写回 state
- `export_score`：导出 PDF / psz / 长图，默认使用已保存的裁剪和排版；`output` 建议传绝对路径
- `open_tab` / `status` / `stop_server` / `history`：打开页面、服务状态、停止、历史记录

MCP 会复用本地常驻服务，没有时自动拉起；默认不打开浏览器。当前 MCP 入口走源码/venv，尚未内置到打包 exe。

## 测试

```bash
python -m unittest discover -s tests -v
```

## 打包

| 平台    | 脚本          | 产物                    |
| ------- | ------------- | ----------------------- |
| Windows | build_exe.bat | dist/BiliTabCapture.exe |
| macOS   | build_mac.sh  | dist/BiliTabCapture_mac |

