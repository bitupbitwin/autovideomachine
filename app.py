import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from tkinter import END, BOTH, LEFT, RIGHT, VERTICAL, StringVar, Tk, Text, messagebox
from tkinter import filedialog
from tkinter import ttk


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "outputs"
CONFIG_PATH = APP_DIR / "config.json"


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name(text: str, fallback: str = "untitled") -> str:
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text).strip(" ._")
    text = re.sub(r"\s+", "_", text)
    return text[:60] or fallback


def compact_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class PlainTextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip = True
        if tag in {"p", "br", "div", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            value = data.strip()
            if value:
                self.parts.append(value)

    def text(self) -> str:
        return compact_text(" ".join(self.parts))


def fetch_url_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "VideoAutoStudio/0.1 (+local desktop app)",
            "Accept": "text/html,application/xhtml+xml,text/plain",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read(2_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
    decoded = raw.decode(charset, errors="ignore")
    parser = PlainTextHTMLParser()
    parser.feed(decoded)
    text = parser.text()
    return text or compact_text(decoded)


@dataclass
class ScriptItem:
    index: int
    title: str
    summary: str
    narration: str
    shots: list


class ProjectStorage:
    def __init__(self):
        OUTPUT_DIR.mkdir(exist_ok=True)
        self.current_dir = None

    def create_story_project(self, title: str, source_kind: str, source_preview: str) -> Path:
        folder_name = f"{now_id()}_{safe_name(title, 'story')}"
        self.current_dir = OUTPUT_DIR / folder_name
        (self.current_dir / "story").mkdir(parents=True, exist_ok=True)
        (self.current_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (self.current_dir / "runs").mkdir(parents=True, exist_ok=True)
        self.write_json(
            self.current_dir / "metadata.json",
            {
                "title": title,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "source_kind": source_kind,
                "source_preview": source_preview[:500],
            },
        )
        return self.current_dir

    def save_story(self, story: dict) -> None:
        assert self.current_dir
        self.write_json(self.current_dir / "story" / "story.json", story)
        (self.current_dir / "story" / "story.md").write_text(
            f"# {story['title']}\n\n## 故事梗概\n\n{story['outline']}\n\n"
            f"## 主要人物/要素\n\n{story['characters']}\n\n"
            f"## 视频风格\n\n{story['style']}\n",
            encoding="utf-8",
        )

    def save_scripts(self, scripts: list[ScriptItem]) -> None:
        assert self.current_dir
        scripts_dir = self.current_dir / "scripts"
        for item in scripts:
            data = {
                "index": item.index,
                "title": item.title,
                "summary": item.summary,
                "narration": item.narration,
                "shots": item.shots,
            }
            stem = f"{item.index:03d}_{safe_name(item.title)}"
            self.write_json(scripts_dir / f"{stem}.json", data)
            shot_text = "\n".join(
                f"{i + 1}. 时长: {shot['duration']} 秒\n"
                f"   画面提示词: {shot['visual_prompt']}\n"
                f"   旁白: {shot['voiceover']}"
                for i, shot in enumerate(item.shots)
            )
            (scripts_dir / f"{stem}.md").write_text(
                f"# {item.title}\n\n## 摘要\n\n{item.summary}\n\n"
                f"## 旁白脚本\n\n{item.narration}\n\n## 镜头提示词\n\n{shot_text}\n",
                encoding="utf-8",
            )

    def save_run(self, item: ScriptItem, status: str, note: str) -> Path:
        assert self.current_dir
        run_path = self.current_dir / "runs" / f"{now_id()}_{item.index:03d}_{safe_name(item.title)}.json"
        self.write_json(
            run_path,
            {
                "script_index": item.index,
                "title": item.title,
                "status": status,
                "note": note,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "next_step": "接入视频生成 API 后，在这里记录素材、音频、画面和最终视频文件路径。",
            },
        )
        return run_path

    @staticmethod
    def write_json(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class ModelClient:
    def __init__(self):
        self.config = self.load_config()

    def load_config(self) -> dict:
        if not CONFIG_PATH.exists():
            return {}
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def available(self) -> bool:
        return bool(self.config.get("api_url") and self.config.get("api_key"))

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        if not self.available():
            raise RuntimeError("未配置 API")
        payload = {
            "model": self.config.get("model", "gpt-4.1"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.get("temperature", 0.7),
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.config["api_url"],
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config['api_key']}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"]


class StoryEngine:
    def __init__(self):
        self.model = ModelClient()

    def arrange_story(self, text: str) -> dict:
        text = compact_text(text)
        if self.model.available():
            return self._arrange_with_model(text)
        return self._arrange_locally(text)

    def _arrange_with_model(self, text: str) -> dict:
        prompt = (
            "请把下面素材编排为适合短视频系列的故事。只输出 JSON，字段为 "
            "title, outline, characters, style。outline 要保留核心情节和信息密度。\n\n"
            f"{text[:12000]}"
        )
        raw = self.model.chat("你是短视频故事编排与分镜策划专家。", prompt)
        return json.loads(self._extract_json(raw))

    def _arrange_locally(self, text: str) -> dict:
        title = self._guess_title(text)
        sentences = self._sentences(text)
        lead = " ".join(sentences[:12]) if sentences else text[:1200]
        keywords = self._keywords(text)
        return {
            "title": title,
            "outline": lead[:3000],
            "characters": "、".join(keywords[:10]) or "待大模型进一步提取",
            "style": "节奏清晰、画面感强、适合 1 分钟短视频；先给出冲突或钩子，再推进关键事件，最后留下转折或结论。",
        }

    def generate_scripts(self, story: dict, source_text: str, seconds_per_video: int = 60) -> list[ScriptItem]:
        if self.model.available():
            try:
                return self._scripts_with_model(story, source_text, seconds_per_video)
            except Exception:
                pass
        return self._scripts_locally(story, source_text, seconds_per_video)

    def _scripts_with_model(self, story: dict, source_text: str, seconds_per_video: int) -> list[ScriptItem]:
        total = max(1, math.ceil(len(source_text) / 1800))
        prompt = (
            f"请根据故事生成 {total} 个短视频脚本，每个约 {seconds_per_video} 秒。"
            "只输出 JSON 数组，每项字段为 index,title,summary,narration,shots。"
            "shots 每项字段为 duration,visual_prompt,voiceover。\n\n"
            f"故事:\n{json.dumps(story, ensure_ascii=False)}\n\n原文:\n{source_text[:16000]}"
        )
        raw = self.model.chat("你是短视频脚本与 AI 视频提示词专家。", prompt)
        data = json.loads(self._extract_json(raw))
        return [
            ScriptItem(
                index=int(item["index"]),
                title=item["title"],
                summary=item["summary"],
                narration=item["narration"],
                shots=item["shots"],
            )
            for item in data
        ]

    def _scripts_locally(self, story: dict, source_text: str, seconds_per_video: int) -> list[ScriptItem]:
        sentences = self._sentences(source_text or story["outline"])
        if not sentences:
            sentences = [story["outline"]]
        chunk_size = 12
        chunks = [sentences[i : i + chunk_size] for i in range(0, len(sentences), chunk_size)]
        max_items = max(1, min(30, len(chunks)))
        scripts = []
        for idx, chunk in enumerate(chunks[:max_items], 1):
            summary = " ".join(chunk[:3])[:320]
            narration = self._build_narration(chunk)
            title = f"第 {idx} 集：{self._title_from_summary(summary, story['title'])}"
            shots = self._build_shots(chunk, seconds_per_video)
            scripts.append(ScriptItem(idx, title, summary, narration, shots))
        return scripts

    def _build_narration(self, chunk: list[str]) -> str:
        lines = []
        for i, sentence in enumerate(chunk[:10]):
            prefix = "开场钩子：" if i == 0 else "旁白："
            lines.append(f"{prefix}{sentence}")
        lines.append("结尾：这一段的变化，正把故事推向下一个关键节点。")
        return "\n".join(lines)

    def _build_shots(self, chunk: list[str], seconds_per_video: int) -> list[dict]:
        shot_count = min(8, max(4, math.ceil(len(chunk) / 2)))
        duration = max(5, seconds_per_video // shot_count)
        shots = []
        for i in range(shot_count):
            sentence = chunk[min(i * 2, len(chunk) - 1)]
            shots.append(
                {
                    "duration": duration,
                    "visual_prompt": (
                        f"电影感短视频画面，第 {i + 1} 镜，围绕“{sentence[:80]}”展开；"
                        "真实细节，清晰主体，情绪明确，适合 AI 视频生成。"
                    ),
                    "voiceover": sentence,
                }
            )
        return shots

    def _guess_title(self, text: str) -> str:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        first = re.sub(r"^[#\s\d.、-]+", "", first)
        return first[:28] or "自动视频项目"

    def _title_from_summary(self, summary: str, fallback: str) -> str:
        cleaned = re.sub(r"[，。！？；：,.!?;:].*", "", summary).strip()
        return (cleaned or fallback)[:24]

    def _sentences(self, text: str) -> list[str]:
        parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", compact_text(text))
        return [part.strip() for part in parts if len(part.strip()) > 8]

    def _keywords(self, text: str) -> list[str]:
        candidates = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,12}", text)
        stop = {"一个", "这个", "那个", "他们", "我们", "因为", "所以", "但是", "如果", "然后", "可以", "进行"}
        seen = []
        for word in candidates:
            if word not in stop and word not in seen:
                seen.append(word)
        return seen

    def _extract_json(self, raw: str) -> str:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()
        first_obj = raw.find("{")
        first_arr = raw.find("[")
        starts = [pos for pos in [first_obj, first_arr] if pos >= 0]
        if not starts:
            return raw
        start = min(starts)
        end = max(raw.rfind("}"), raw.rfind("]"))
        return raw[start : end + 1]


class VideoAutoStudioApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("自动视频脚本工作台")
        self.root.geometry("1180x760")
        self.engine = StoryEngine()
        self.storage = ProjectStorage()
        self.source_text = ""
        self.story = None
        self.scripts: list[ScriptItem] = []
        self.status_story = StringVar(value="未开始")
        self.status_scripts = StringVar(value="未开始")
        self.status_run = StringVar(value="未开始")
        self.project_path = StringVar(value="尚未创建项目")
        self.input_mode = StringVar(value="text")
        self._build_ui()

    def _build_ui(self):
        root_frame = ttk.Frame(self.root, padding=12)
        root_frame.pack(fill=BOTH, expand=True)

        top = ttk.Frame(root_frame)
        top.pack(fill="x")
        ttk.Label(top, text="输入类型").pack(side=LEFT)
        ttk.Radiobutton(top, text="粘贴长文本/小说", value="text", variable=self.input_mode).pack(side=LEFT, padx=8)
        ttk.Radiobutton(top, text="网址", value="url", variable=self.input_mode).pack(side=LEFT)
        ttk.Button(top, text="导入 txt 文件", command=self.import_text_file).pack(side=RIGHT)

        input_frame = ttk.LabelFrame(root_frame, text="素材输入")
        input_frame.pack(fill=BOTH, expand=True, pady=10)
        self.input_text = Text(input_frame, height=12, wrap="word")
        input_scroll = ttk.Scrollbar(input_frame, orient=VERTICAL, command=self.input_text.yview)
        self.input_text.configure(yscrollcommand=input_scroll.set)
        self.input_text.pack(side=LEFT, fill=BOTH, expand=True)
        input_scroll.pack(side=RIGHT, fill="y")

        actions = ttk.Frame(root_frame)
        actions.pack(fill="x", pady=4)
        ttk.Button(actions, text="1. 编排故事", command=self.arrange_story).pack(side=LEFT, padx=(0, 8))
        ttk.Label(actions, textvariable=self.status_story).pack(side=LEFT, padx=(0, 20))
        ttk.Button(actions, text="2. 根据故事生成多个脚本", command=self.generate_scripts).pack(side=LEFT, padx=(0, 8))
        ttk.Label(actions, textvariable=self.status_scripts).pack(side=LEFT, padx=(0, 20))
        ttk.Button(actions, text="3. 选择标题并开始执行", command=self.run_selected).pack(side=LEFT, padx=(0, 8))
        ttk.Label(actions, textvariable=self.status_run).pack(side=LEFT)

        ttk.Label(root_frame, textvariable=self.project_path).pack(fill="x", pady=(4, 8))

        result_pane = ttk.PanedWindow(root_frame, orient="horizontal")
        result_pane.pack(fill=BOTH, expand=True)

        story_frame = ttk.LabelFrame(result_pane, text="故事结果")
        self.story_text = Text(story_frame, height=14, wrap="word")
        self.story_text.pack(fill=BOTH, expand=True)
        result_pane.add(story_frame, weight=2)

        script_frame = ttk.LabelFrame(result_pane, text="脚本标题列表")
        self.script_list = ttk.Treeview(script_frame, columns=("title", "shots"), show="headings", height=12)
        self.script_list.heading("title", text="标题")
        self.script_list.heading("shots", text="镜头数")
        self.script_list.column("title", width=360)
        self.script_list.column("shots", width=80, anchor="center")
        self.script_list.bind("<<TreeviewSelect>>", self.show_selected_script)
        self.script_list.pack(fill=BOTH, expand=True)
        result_pane.add(script_frame, weight=1)

        detail_frame = ttk.LabelFrame(root_frame, text="选中脚本详情")
        detail_frame.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.script_detail = Text(detail_frame, height=10, wrap="word")
        self.script_detail.pack(fill=BOTH, expand=True)

    def import_text_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        self.input_text.delete("1.0", END)
        self.input_text.insert("1.0", text)
        self.input_mode.set("text")

    def arrange_story(self):
        self._run_async(self._arrange_story_task)

    def _arrange_story_task(self):
        self._set_var(self.status_story, "运行中")
        try:
            raw_input = self.input_text.get("1.0", END).strip()
            if not raw_input:
                raise ValueError("请先输入网址或粘贴文本")
            if self.input_mode.get() == "url":
                self.source_text = fetch_url_text(raw_input)
                source_kind = "url"
                source_preview = raw_input
            else:
                self.source_text = compact_text(raw_input)
                source_kind = "text"
                source_preview = self.source_text
            self.story = self.engine.arrange_story(self.source_text)
            project_dir = self.storage.create_story_project(self.story["title"], source_kind, source_preview)
            self.storage.save_story(self.story)
            self._set_var(self.project_path, f"项目保存位置：{project_dir}")
            self._set_text(
                self.story_text,
                f"标题：{self.story['title']}\n\n故事梗概：\n{self.story['outline']}\n\n"
                f"主要人物/要素：\n{self.story['characters']}\n\n视频风格：\n{self.story['style']}",
            )
            self._set_var(self.status_story, "成功")
        except Exception as exc:
            self._set_var(self.status_story, "失败")
            self._show_error(str(exc))

    def generate_scripts(self):
        self._run_async(self._generate_scripts_task)

    def _generate_scripts_task(self):
        self._set_var(self.status_scripts, "运行中")
        try:
            if not self.story:
                raise ValueError("请先执行第 1 步：编排故事")
            self.scripts = self.engine.generate_scripts(self.story, self.source_text)
            self.storage.save_scripts(self.scripts)
            self.root.after(0, self._refresh_script_list)
            self._set_var(self.status_scripts, f"成功，共 {len(self.scripts)} 个脚本")
        except Exception as exc:
            self._set_var(self.status_scripts, "失败")
            self._show_error(str(exc))

    def run_selected(self):
        selected = self._selected_script()
        if not selected:
            messagebox.showwarning("请选择脚本", "请先在脚本标题列表里选择一个标题。")
            return
        self._run_async(lambda: self._run_selected_task(selected))

    def _run_selected_task(self, item: ScriptItem):
        self._set_var(self.status_run, "运行中")
        time.sleep(0.5)
        path = self.storage.save_run(
            item,
            "success",
            "当前版本已完成脚本执行准备；后续接入视频生成 API 后，会在这里开始生成画面、配音和最终视频。",
        )
        self._set_var(self.status_run, "成功")
        self._show_info(f"已生成执行记录：\n{path}")

    def show_selected_script(self, _event=None):
        item = self._selected_script()
        if not item:
            return
        shots = "\n".join(
            f"{i + 1}. {shot['duration']} 秒\n画面提示词：{shot['visual_prompt']}\n旁白：{shot['voiceover']}\n"
            for i, shot in enumerate(item.shots)
        )
        self._set_text(
            self.script_detail,
            f"{item.title}\n\n摘要：\n{item.summary}\n\n旁白脚本：\n{item.narration}\n\n镜头提示词：\n{shots}",
        )

    def _refresh_script_list(self):
        for row in self.script_list.get_children():
            self.script_list.delete(row)
        for item in self.scripts:
            self.script_list.insert("", END, iid=str(item.index), values=(item.title, len(item.shots)))
        if self.scripts:
            self.script_list.selection_set("1")
            self.show_selected_script()

    def _selected_script(self):
        selected = self.script_list.selection()
        if not selected:
            return None
        index = int(selected[0])
        return next((item for item in self.scripts if item.index == index), None)

    def _run_async(self, fn):
        def wrapped():
            try:
                fn()
            finally:
                pass

        threading.Thread(target=wrapped, daemon=True).start()

    def _set_text(self, widget: Text, value: str):
        self.root.after(0, lambda: self._set_text_now(widget, value))

    def _set_text_now(self, widget: Text, value: str):
        widget.delete("1.0", END)
        widget.insert("1.0", value)

    def _set_var(self, var: StringVar, value: str):
        self.root.after(0, lambda: var.set(value))

    def _show_error(self, message: str):
        self.root.after(0, lambda: messagebox.showerror("执行失败", message))

    def _show_info(self, message: str):
        self.root.after(0, lambda: messagebox.showinfo("完成", message))


def main():
    root = Tk()
    app = VideoAutoStudioApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
