"""自动视频脚本工作台 —— PyWebView 桌面外壳 + JS↔Python 桥接。

界面层在 web/ 目录（HTML/CSS/JS），本文件只负责：
1. 把核心逻辑（engine.py）封装成可被前端调用的 Api 类；
2. 启动一个原生桌面窗口加载界面。
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import engine
from jobs import ProjectJobs
from engine import (
    ProjectStorage,
    ScriptItem,
    StoryEngine,
    fetch_url_text,
    get_theme,
    load_config,
    save_config,
    save_theme,
)

APP_DIR = Path(__file__).resolve().parent


def _resource_dir() -> Path:
    """只读资源目录（web 界面）：打包(冻结)后用 PyInstaller 解包目录，开发时用源码目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return APP_DIR


WEB_INDEX = _resource_dir() / "web" / "index.html"


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
        # 用下划线前缀：pywebview 注入桥接时会递归枚举 js_api 的公开属性，
        # 若把 Window 对象挂成公开属性，会爬进 Window 的 dom/宽高等 getter
        # 导致阻塞，_createApi 永远不执行，前端卡死在「检测中…」
        self._window = None

    # ---- 配置 / 状态 ----
    def get_status(self) -> dict:
        cfg = load_config()
        return {
            "api_connected": self.engine.model.available(),
            "theme": get_theme(),
            "config": {
                "api_url": cfg.get("api_url", ""),
                "model": cfg.get("model", ""),
                "has_key": bool(cfg.get("api_key")),
                "temperature": cfg.get("temperature", 0.7),
                "has_xai": bool(cfg.get("xai_api_key")),
                "has_gemini": bool(cfg.get("gemini_api_key")),
                "gemini_voice": cfg.get("gemini_voice", "Kore"),
                "video_aspect_ratio": cfg.get("video_aspect_ratio", "9:16"),
                "video_resolution": cfg.get("video_resolution", "720p"),
                "consistency": cfg.get("consistency", "strong"),
            },
        }

    def _progress(self, msg: str) -> None:
        try:
            self._window.evaluate_js(
                "window.onProduceProgress && window.onProduceProgress(%s)" % json.dumps(msg)
            )
        except Exception:
            pass

    def set_theme(self, theme: str) -> dict:
        try:
            return {"ok": True, "theme": save_theme(theme)}
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

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

            result = self._window.create_file_dialog(
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

    def import_parsed_scripts(self, raw_json: str) -> dict:
        try:
            raw_json = (raw_json or "").strip()
            if not raw_json:
                raise ValueError("请先粘贴脚本提示词 JSON")

            try:
                data = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON format error: {exc}")

            if isinstance(data, dict):
                data = data.get("scripts") or data.get("episodes") or data.get("data") or [data]

            if not isinstance(data, list) or not data:
                raise ValueError("JSON must be a non-empty list of episodes")

            unique_characters = set()
            unique_locations = set()
            first_title = ""

            for item in data:
                if not isinstance(item, dict):
                    continue
                if not first_title:
                    first_title = str(item.get("title") or "")
                
                scenes = item.get("scenes", [])
                if isinstance(scenes, list):
                    for sc in scenes:
                        if isinstance(sc, dict):
                            loc = sc.get("location")
                            if loc:
                                unique_locations.add(str(loc))
                            for b in sc.get("beats", []):
                                if isinstance(b, dict):
                                    speaker = b.get("speaker")
                                    if speaker and speaker != "旁白":
                                        unique_characters.add(str(speaker))

            if not first_title:
                first_title = "Imported Script Project"

            raw_story = {
                "title": first_title,
                "outline": "Project created by importing external script JSON.",
                "characters": "、".join(sorted(list(unique_characters))),
                "style": "Cinematic visual style, clear subject, clear emotion.",
                "cast": [{"name": name, "appearance": "appearance description", "persona": "personality description", "voice": ""} for name in sorted(list(unique_characters))],
                "locations": [{"name": loc, "description": "location visual details"} for loc in sorted(list(unique_locations))]
            }

            self.story = self.engine._normalize_story(raw_story)

            project_dir = self.storage.create_story_project(
                self.story["title"], "script_json", raw_json
            )
            self.storage.save_story(self.story)

            self.scripts = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                idx = len(self.scripts) + 1
                scenes = self.engine._normalize_scenes(item.get("scenes", []), 60)
                shots = self.engine._scenes_to_shots(scenes, 60)
                if not shots:
                    shots = self.engine._normalize_shots(item.get("shots", []), 60)
                    scenes = self.engine._shots_to_scenes(shots)
                narration = "\n".join(s["voiceover"] for s in shots if s.get("voiceover"))

                self.scripts.append(
                    ScriptItem(
                        index=idx,
                        title=str(item.get("title") or f"Episode {idx}"),
                        summary=str(item.get("summary", "")),
                        narration=str(item.get("narration") or narration),
                        shots=shots,
                        scenes=scenes,
                    )
                )

            if not self.scripts:
                raise ValueError("No valid scripts parsed from JSON")

            self.storage.save_scripts(self.scripts)

            return {
                "ok": True,
                "story": self.story,
                "scripts": [s.to_dict() for s in self.scripts],
                "project_dir": str(project_dir),
            }
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

    # ---- 预估：开跑前给出调用量与粗略耗时 ----
    def estimate(self) -> dict:
        try:
            if not self.scripts:
                raise ValueError("请先完成第 2 步：生成脚本")
            cfg = load_config()
            shots = sum(len(s.shots) for s in self.scripts)
            voiced = sum(1 for s in self.scripts for sh in s.shots if (sh.get("voiceover") or "").strip())
            strong = cfg.get("consistency", "strong") == "strong"
            images = (len(self.story.get("cast", [])) + len(self.story.get("locations", []))) if strong else 0
            # 粗略：每段视频按 ~90 秒（生成+轮询）估
            minutes = round(shots * 1.5)
            return {
                "ok": True,
                "episodes": len(self.scripts),
                "shots": shots,
                "video_calls": shots,
                "image_calls": images,
                "tts_calls": voiced,
                "est_minutes": minutes,
            }
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    def get_jobs(self) -> dict:
        if not self.storage.current_dir:
            return {"episodes": {}}
        return {"episodes": ProjectJobs(self.storage.current_dir).all()}

    def _make_producer(self):
        """校验依赖与配置，返回 (producer, error)。"""
        if not self.story or not self.storage.current_dir:
            return None, "请先完成第 1、2 步"
        cfg = load_config()
        if not cfg.get("xai_api_key"):
            return None, "请先在「设置」中填写 xAI(Grok) API Key"
        if not cfg.get("gemini_api_key"):
            return None, "请先在「设置」中填写 Gemini API Key"
        from pipeline import VideoProducer, have_ffmpeg
        if not have_ffmpeg():
            return None, "未检测到 ffmpeg，请先安装 ffmpeg 后再制作视频"
        return VideoProducer(cfg, progress=self._progress), None

    def _produce_episode(self, item, producer, jobs):
        jobs.set(item.index, status="running", title=item.title)
        final = producer.produce(item, self.story, self.storage.current_dir)
        self.storage.save_run(item, "success", f"已生成视频：{final}")
        jobs.set(item.index, status="done", title=item.title, video_path=str(final), error="")
        return final

    def _clear_episode(self, index) -> None:
        """清掉某集的中间产物与状态，使其下次从头重做。"""
        if not self.storage.current_dir:
            return
        work = Path(self.storage.current_dir) / f"_work_{int(index):03d}"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        ProjectJobs(self.storage.current_dir).set(index, status="pending", video_path="", error="")

    # ---- 步骤 3：制作选中的一集（force=True 时强制重做）----
    def run_script(self, index: int, force: bool = False) -> dict:
        try:
            item = next((s for s in self.scripts if s.index == int(index)), None)
            if not item:
                raise ValueError("请先在脚本列表中选择一个标题")
            producer, err = self._make_producer()
            if err:
                raise ValueError(err)
            if force:
                self._clear_episode(item.index)
            jobs = ProjectJobs(self.storage.current_dir)
            final = self._produce_episode(item, producer, jobs)
            return {"ok": True, "video_path": str(final)}
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    # ---- 审校：编辑某集脚本（改标题/对白），保存后该集需重做 ----
    def update_script(self, index: int, data: dict) -> dict:
        try:
            item = next((s for s in self.scripts if s.index == int(index)), None)
            if not item:
                raise ValueError("未找到该集脚本")
            scenes = self.engine._normalize_scenes(data.get("scenes", []), 60)
            shots = self.engine._scenes_to_shots(scenes, 60)
            if not shots:
                raise ValueError("脚本内容为空，至少保留一句台词")
            item.title = str(data.get("title") or item.title)[:40]
            item.scenes = scenes
            item.shots = shots
            item.narration = "\n".join(s["voiceover"] for s in shots if s.get("voiceover"))
            self.storage.save_scripts(self.scripts)
            self._clear_episode(item.index)  # 改过的集需重做
            return {"ok": True, "script": item.to_dict()}
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    # ---- 审校：预览角色/场景参考图（渲染前确认画面基准）----
    def preview_refs(self) -> dict:
        try:
            if not self.story or not self.storage.current_dir:
                raise ValueError("请先完成第 1 步：编排故事")
            cfg = load_config()
            if not cfg.get("xai_api_key"):
                raise ValueError("请先在「设置」中填写 xAI(Grok) API Key")
            from pipeline import VideoProducer

            producer = VideoProducer(cfg, progress=self._progress)
            char_refs = producer._ensure_character_refs(self.story, self.storage.current_dir)
            scene_refs = producer._ensure_scene_refs(self.story, self.storage.current_dir)
            refs = []
            for name, path in char_refs.items():
                refs.append({"type": "角色", "name": name, "data": self._img_data_uri(path)})
            for name, path in scene_refs.items():
                refs.append({"type": "场景", "name": name, "data": self._img_data_uri(path)})
            return {"ok": True, "refs": refs}
        except Exception as exc:
            return {"ok": False, "error": friendly_error(exc)}

    @staticmethod
    def _img_data_uri(path) -> str:
        import base64

        return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")

    # ---- 批量制作全部集（跳过已完成，失败继续，可断点续跑）----
    def run_all(self) -> dict:
        try:
            if not self.scripts:
                raise ValueError("请先完成第 2 步：生成脚本")
            producer, err = self._make_producer()
            if err:
                raise ValueError(err)
            jobs = ProjectJobs(self.storage.current_dir)
            done, failed, skipped = 0, 0, 0
            total = len(self.scripts)
            for n, item in enumerate(self.scripts, 1):
                if jobs.status(item.index) == "done":
                    skipped += 1
                    self._progress(f"[{n}/{total}] 第 {item.index} 集已完成，跳过")
                    continue
                self._progress(f"[{n}/{total}] 制作第 {item.index} 集：{item.title}")
                try:
                    self._produce_episode(item, producer, jobs)
                    done += 1
                except Exception as exc:
                    failed += 1
                    jobs.set(item.index, status="failed", title=item.title, error=friendly_error(exc))
                    self._progress(f"  第 {item.index} 集失败：{friendly_error(exc)}（继续下一集）")
            return {"ok": True, "done": done, "failed": failed, "skipped": skipped, "total": total}
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


def _fatal(message: str) -> None:
    """启动失败时：写日志到程序目录，并尽量弹窗提示（Windows），避免静默退出。"""
    try:
        (engine.APP_DIR / "startup_error.log").write_text(message, encoding="utf-8")
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message[:1800], "自动视频脚本工作台 · 启动失败", 0x10)
    except Exception:
        print(message, file=sys.stderr)


def main():
    import traceback

    try:
        import webview
    except ImportError:
        _fatal(
            "缺少依赖 pywebview。请先安装：\n\n    pip install pywebview\n\n"
            "（Windows 需 WebView2 运行时；macOS 自带 WebKit；Linux 需 webkit2gtk）"
        )
        return

    try:
        if not WEB_INDEX.exists():
            raise RuntimeError(
                f"找不到界面文件：{WEB_INDEX}\n\n"
                "打包时 web 目录可能未被包含。请确认用 videostudio.spec 打包。"
            )

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
        api._window = window

        # Windows(WinForms 后端)的 System.Drawing.Icon 只接受 .ico，传 PNG 会在
        # .NET 线程抛异常直接崩溃，Python 的 try/except 兜不住
        if sys.platform == "win32":
            icon = _resource_dir() / "assets" / "icon.ico"
        else:
            icon = _resource_dir() / "assets" / "icon.png"
        try:
            if icon.exists():
                webview.start(icon=str(icon))
            else:
                webview.start()
        except Exception:
            # 图标参数在个别后端可能不被支持：去掉图标重试一次
            webview.start()
    except Exception:
        _fatal(
            "程序启动失败，错误信息如下（可截图发给开发者）：\n\n"
            + traceback.format_exc()
            + "\n\n常见原因：缺少 WebView2 运行时（去微软官网搜 “WebView2 Runtime” 安装一次）。"
        )


if __name__ == "__main__":
    main()
