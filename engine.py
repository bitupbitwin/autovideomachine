"""核心逻辑层：故事编排、脚本生成、模型调用与项目存储。

这一层不依赖任何 UI 框架，可被 PyWebView 桥接层（app.py）或测试直接调用。
"""

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "outputs"
CONFIG_PATH = APP_DIR / "config.json"


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name(text: str, fallback: str = "untitled") -> str:
    text = re.sub(r"[\\/:*?\"<>|：＊？“”《》｜\r\n\t]+", "_", text).strip(" ._")
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
    url = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "VideoAutoStudio/0.2 (+local desktop app)",
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

    def to_dict(self) -> dict:
        return asdict(self)


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
            stem = f"{item.index:03d}_{safe_name(item.title)}"
            self.write_json(scripts_dir / f"{stem}.json", item.to_dict())
            shot_text = "\n".join(
                f"{i + 1}. 时长: {shot.get('duration', '')} 秒\n"
                f"   画面提示词: {shot.get('visual_prompt', '')}\n"
                f"   旁白: {shot.get('voiceover', '')}"
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


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_config(config: dict) -> None:
    clean = {
        "api_url": str(config.get("api_url", "")).strip(),
        "api_key": str(config.get("api_key", "")).strip(),
        "model": str(config.get("model", "")).strip(),
    }
    try:
        clean["temperature"] = float(config.get("temperature", 0.7))
    except (TypeError, ValueError):
        clean["temperature"] = 0.7
    CONFIG_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


class ModelClient:
    def __init__(self):
        self.config = load_config()

    def reload(self) -> None:
        self.config = load_config()

    def available(self) -> bool:
        return bool(self.config.get("api_url") and self.config.get("api_key"))

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        if not self.available():
            raise RuntimeError("未配置 API")
        payload = {
            "model": self.config.get("model") or "gpt-4.1",
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
            try:
                return self._arrange_with_model(text)
            except Exception:
                pass
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

    def generate_scripts(self, story: dict, source_text: str, seconds_per_video: int = 60,
                         target_count: int = 0) -> list[ScriptItem]:
        if self.model.available():
            try:
                return self._scripts_with_model(story, source_text, seconds_per_video, target_count)
            except Exception:
                pass
        return self._scripts_locally(story, source_text, seconds_per_video, target_count)

    def _scripts_with_model(self, story: dict, source_text: str, seconds_per_video: int,
                            target_count: int) -> list[ScriptItem]:
        total = target_count if target_count > 0 else max(1, math.ceil(len(source_text) / 1800))
        prompt = (
            f"请根据故事生成 {total} 个短视频脚本，每个约 {seconds_per_video} 秒。"
            "只输出 JSON 数组，每项字段为 index,title,summary,narration,shots。"
            "shots 每项字段为 duration,visual_prompt,voiceover。\n\n"
            f"故事:\n{json.dumps(story, ensure_ascii=False)}\n\n原文:\n{source_text[:16000]}"
        )
        raw = self.model.chat("你是短视频脚本与 AI 视频提示词专家。", prompt)
        data = json.loads(self._extract_json(raw))
        if isinstance(data, dict):
            data = data.get("scripts") or data.get("data") or [data]
        if not isinstance(data, list):
            raise ValueError("模型返回的脚本格式不正确")
        # 忽略模型给的 index，统一重排为连续 1..N，避免缺失/重复/0 起始导致列表崩溃
        scripts = []
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = len(scripts) + 1
            scripts.append(
                ScriptItem(
                    index=idx,
                    title=str(item.get("title") or f"第 {idx} 集"),
                    summary=str(item.get("summary", "")),
                    narration=str(item.get("narration", "")),
                    shots=self._normalize_shots(item.get("shots", []), seconds_per_video),
                )
            )
        if not scripts:
            raise ValueError("模型未返回任何脚本")
        return scripts

    def _normalize_shots(self, shots, seconds_per_video: int) -> list[dict]:
        if not isinstance(shots, list) or not shots:
            return [{"duration": seconds_per_video, "visual_prompt": "", "voiceover": ""}]
        default_duration = max(1, seconds_per_video // len(shots))
        normalized = []
        for shot in shots:
            if not isinstance(shot, dict):
                shot = {"voiceover": str(shot)}
            duration = shot.get("duration", default_duration)
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                duration = default_duration
            normalized.append(
                {
                    "duration": duration,
                    "visual_prompt": str(shot.get("visual_prompt", "")),
                    "voiceover": str(shot.get("voiceover", "")),
                }
            )
        return normalized

    def _scripts_locally(self, story: dict, source_text: str, seconds_per_video: int,
                         target_count: int) -> list[ScriptItem]:
        sentences = self._sentences(source_text or story["outline"])
        if not sentences:
            sentences = [story["outline"]]
        if target_count > 0:
            chunk_size = max(1, math.ceil(len(sentences) / target_count))
        else:
            chunk_size = 12
        chunks = [sentences[i : i + chunk_size] for i in range(0, len(sentences), chunk_size)]
        max_items = target_count if target_count > 0 else max(1, min(30, len(chunks)))
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
        candidates = re.findall(r"[一-龥A-Za-z0-9]{2,12}", text)
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
