"""自动视频脚本工作台 —— PyWebView 桌面外壳 + JS↔Python 桥接。

界面层在 web/ 目录（HTML/CSS/JS），本文件只负责：
1. 把核心逻辑（engine.py）封装成可被前端调用的 Api 类；
2. 启动一个原生桌面窗口加载界面。
"""

import os
import subprocess
import sys
import traceback
from pathlib import Path

import engine
from engine import ProjectStorage, ScriptItem, StoryEngine, fetch_url_text, load_config, save_config

APP_DIR = Path(__file__).resolve().parent
WEB_INDEX = APP_DIR / "web" / "index.html"


def friendly_error(exc: Exception) -> str:
    """把原始异常转成对用户友好的中文提示。"""
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        return f"网址抓取失败（HTTP {exc.code}）。请检查链接是否正确，或换一个可访问的网址。"
    if isinstance(exc, urllib.error.URLError):
        return "网址无法访问。请检查网络连接或链接是否正确。"
    if isinstance(exc, ValueError):
        return str(exc)
    return f"操作失败：{exc}"


class Api:
    """暴露给前端 JS 调用的接口。每个方法返回可被 JSON 序列化的 dict。"""

    def __init__(self):
        self.engine = StoryEngine()
        self.storage = ProjectStorage()
        self.story = None
        self.source_text = ""
        self.scripts: list[ScriptItem] = []
        self.window = None

    # ---- 配置 / 状态 ----
    def get_status(self) -> dict:
        cfg = load_config()
        return {
            "api_connected": self.engine.model.available(),
            "config": {
                "api_url": cfg.get("api_url", ""),
                "model": cfg.get("model", ""),
                "has_key": bool(cfg.get("api_key")),
                "temperature": cfg.get("temperature", 0.7),
            },
        }

    def save_settings(self, cfg: dict) -> dict:
        try:
            current = load_config()
            # 若用户留空 key，则沿用已保存的 key（避免误清空）
            if not str(cfg.get("api_key", "")).strip() and current.get("api_key"):
                cfg["api_key"] = current["api_key"]
            save_config(cfg)
            self.engine.model.reload()  # 热重载，无需重启
            return {"ok": True, "api_connected": self.engine.model.available()}
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    # ---- 输入 ----
    def import_txt(self) -> dict:
        try:
            import webview

            result = self.window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("文本文件 (*.txt)", "所有文件 (*.*)"),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            text = Path(result[0]).read_text(encoding="utf-8", errors="ignore")
            return {"ok": True, "text": text}
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    def fetch_preview(self, url: str) -> dict:
        try:
            if not url.strip():
                raise ValueError("请输入网址")
            text = fetch_url_text(url)
            return {"ok": True, "text": text}
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    # ---- 步骤 1：编排故事 ----
    def arrange(self, raw_input: str, mode: str) -> dict:
        try:
            raw_input = (raw_input or "").strip()
            if not raw_input:
                raise ValueError("请先输入网址或粘贴文本")
            if mode == "url":
                self.source_text = fetch_url_text(raw_input)
                source_kind, source_preview = "url", raw_input
            else:
                self.source_text = engine.compact_text(raw_input)
                source_kind, source_preview = "text", self.source_text

            self.story = self.engine.arrange_story(self.source_text)
            project_dir = self.storage.create_story_project(
                self.story["title"], source_kind, source_preview
            )
            self.storage.save_story(self.story)
            self.scripts = []
            return {
                "ok": True,
                "story": self.story,
                "project_dir": str(project_dir),
                "used_model": self.engine.model.available(),
            }
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    # ---- 步骤 2：生成脚本 ----
    def generate(self, seconds_per_video: int = 60, target_count: int = 0) -> dict:
        try:
            if not self.story:
                raise ValueError("请先完成第 1 步：编排故事")
            try:
                seconds_per_video = max(10, int(seconds_per_video))
            except (TypeError, ValueError):
                seconds_per_video = 60
            try:
                target_count = max(0, int(target_count))
            except (TypeError, ValueError):
                target_count = 0
            self.scripts = self.engine.generate_scripts(
                self.story, self.source_text, seconds_per_video, target_count
            )
            self.storage.save_scripts(self.scripts)
            return {
                "ok": True,
                "scripts": [s.to_dict() for s in self.scripts],
                "used_model": self.engine.model.available(),
            }
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    # ---- 步骤 3：执行选中脚本 ----
    def run_script(self, index: int) -> dict:
        try:
            item = next((s for s in self.scripts if s.index == int(index)), None)
            if not item:
                raise ValueError("请先在脚本列表中选择一个标题")
            path = self.storage.save_run(
                item,
                "success",
                "当前版本已完成脚本执行准备；后续接入视频生成 API 后，会在这里开始生成画面、配音和最终视频。",
            )
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    # ---- 打开输出文件夹 ----
    def open_output(self) -> dict:
        try:
            target = self.storage.current_dir or engine.OUTPUT_DIR
            target = Path(target)
            target.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return {"ok": True, "path": str(target)}
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}


def main():
    try:
        import webview
    except ImportError:
        print(
            "缺少依赖 pywebview。请先安装：\n\n    pip install pywebview\n\n"
            "（Windows 自带 WebView2；macOS 自带 WebKit；Linux 需安装 webkit2gtk）",
            file=sys.stderr,
        )
        sys.exit(1)

    api = Api()
    window = webview.create_window(
        "自动视频脚本工作台",
        url=str(WEB_INDEX),
        js_api=api,
        width=1240,
        height=820,
        min_size=(980, 680),
        background_color="#FFF3E8",
    )
    api.window = window
    webview.start()


if __name__ == "__main__":
    main()
