"""视频制作流水线：把一集脚本变成带配音与字幕的最终视频。

强一致策略：先用 Grok 生成一张角色/场景参考图，每个镜头用「图生视频」从参考图出发，
并把上一镜的尾帧作为下一镜的首帧，实现人物/场景的连贯与连续。
配音用 Gemini TTS，字幕由旁白与时长生成，最后用 ffmpeg 拼接、对齐音频并烧录字幕。

注：本模块依赖外部 API Key（xAI / Gemini）与系统 ffmpeg；真实产出需在配置 Key 后运行。
"""

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from engine import safe_name
from providers import GeminiTTSClient, GrokClient

GROK_MAX_DURATION = 10  # Grok Imagine 单段约 10 秒上限


def _tool(name: str) -> str:
    """定位 ffmpeg/ffprobe：优先程序(exe)同目录，其次系统 PATH。"""
    exe = name + (".exe" if os.name == "nt" else "")
    here = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    local = here / exe
    if local.exists():
        return str(local)
    return shutil.which(name) or name


def have_ffmpeg() -> bool:
    def ok(name):
        return Path(_tool(name)).exists() or shutil.which(name) is not None
    return ok("ffmpeg") and ok("ffprobe")


def _cjk_fontfile() -> str | None:
    """找一个可用的中文字体用于字幕烧录，找不到返回 None。"""
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "C:\\Windows\\Fonts\\msyh.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _target_dims(aspect_ratio: str, resolution: str) -> tuple[int, int]:
    short = 1080 if "1080" in resolution else 720
    ratios = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:5": (4, 5)}
    rw, rh = ratios.get(aspect_ratio, (9, 16))
    if rw <= rh:  # 竖屏/方形：短边为宽
        w, h = short, round(short * rh / rw)
    else:         # 横屏：短边为高
        h, w = short, round(short * rw / rh)
    return (w - w % 2, h - h % 2)


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class VideoProducer:
    def __init__(self, config: dict, progress=None):
        self.cfg = config
        self.progress = progress or (lambda msg: None)
        self.consistency = config.get("consistency", "strong")
        self.aspect = config.get("video_aspect_ratio", "9:16")
        self.resolution = config.get("video_resolution", "720p")
        self.grok = GrokClient(
            config.get("xai_api_key", ""),
            config.get("xai_video_model", "grok-imagine-video"),
            config.get("xai_image_model", "grok-2-image"),
        )
        self.tts = GeminiTTSClient(
            config.get("gemini_api_key", ""),
            config.get("gemini_tts_model", "gemini-2.5-flash-preview-tts"),
            config.get("gemini_voice", "Kore"),
        )
        self.dims = _target_dims(self.aspect, self.resolution)

    # ---------- 对外入口 ----------
    def produce(self, item, story: dict, out_dir: Path) -> Path:
        if not have_ffmpeg():
            raise RuntimeError("未检测到 ffmpeg，请先安装 ffmpeg 后再制作视频。")
        if not item.shots:
            raise RuntimeError("该脚本没有镜头，无法制作视频。")

        out_dir = Path(out_dir)
        work = out_dir / f"_work_{item.index:03d}"
        work.mkdir(parents=True, exist_ok=True)

        # 项目级一致性资产库（跨集复用，已存在则不重复生成）
        char_refs, scene_refs, generic_ref = {}, {}, None
        if self.consistency == "strong":
            char_refs = self._ensure_character_refs(story, out_dir)
            scene_refs = self._ensure_scene_refs(story, out_dir)
            if not char_refs and not scene_refs:
                generic_ref = self._ensure_reference(story, out_dir)

        prefix = self._consistency_prefix(story)
        voice_map = {c.get("name", ""): c.get("voice", "") for c in story.get("cast", []) if c.get("name")}
        default_voice = self.cfg.get("gemini_voice", "Kore")
        normalized, segments = [], []
        prev_frame, prev_speaker, prev_location = None, None, None

        # 镜头级断点续跑：已完成的镜头记录在 work 清单里，重跑时直接复用
        manifest_path = work / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}

        for i, shot in enumerate(item.shots, 1):
            n = len(item.shots)
            speaker = shot.get("speaker", "")
            location = shot.get("location", "")
            voiceover = (shot.get("voiceover") or "").strip()
            norm = work / f"shot_{i:03d}.mp4"

            done = manifest.get(str(i))
            if done and norm.exists() and norm.stat().st_size > 0:
                self.progress(f"镜头 {i}/{n}：复用已完成片段")
                normalized.append(norm)
                segments.append((done.get("duration", 0), done.get("speaker", ""), done.get("voiceover", "")))
                if self.consistency == "strong":
                    frame = work / f"frame_{i:03d}.png"
                    if frame.exists() or self._extract_last_frame(norm, frame):
                        prev_frame = frame
                prev_speaker, prev_location = speaker, location
                continue

            # 先配音：用该角色固定音色，并用音频实际时长决定该镜时长（对话节奏自然）
            audio_path = None
            if voiceover:
                self.progress(f"镜头 {i}/{n}：配音（{speaker or '旁白'}）…")
                voice = voice_map.get(speaker) or default_voice
                wav = self.tts.synthesize(voiceover, voice=voice)
                audio_path = work / f"shot_{i:03d}.wav"
                audio_path.write_bytes(wav)
                audio_dur = self._media_duration(audio_path)
                duration = max(2, min(GROK_MAX_DURATION, int(math.ceil(audio_dur)) if audio_dur else 4))
            else:
                duration = max(1, min(GROK_MAX_DURATION, int(shot.get("duration", 6) or 6)))

            anchor = None
            if self.consistency == "strong":
                new_turn = (speaker != prev_speaker) or (location != prev_location) or (prev_frame is None)
                if new_turn:
                    # 换说话人/换场景：用该角色定妆图(或场景图)重新锚定身份，防止跨镜跨集漂移
                    anchor = char_refs.get(speaker) or scene_refs.get(location) or generic_ref
                if anchor is None:
                    anchor = prev_frame  # 同一人同场景内：用上一镜尾帧做连续衔接
            image_bytes = anchor.read_bytes() if (anchor and anchor.exists()) else None

            self.progress(f"镜头 {i}/{n}：生成视频…")
            prompt = f"{prefix} 本镜画面：{shot.get('visual_prompt', '')}"
            clip_bytes = self.grok.generate_video(
                prompt, image_bytes, duration, self.aspect, self.resolution
            )
            raw_clip = work / f"shot_{i:03d}_raw.mp4"
            raw_clip.write_bytes(clip_bytes)

            self.progress(f"镜头 {i}/{n}：规整音画…")
            self._normalize_clip(raw_clip, audio_path, duration, norm)
            normalized.append(norm)
            segments.append((duration, speaker, voiceover))

            # 记录到清单并落盘，崩溃后可从此镜之后续跑
            manifest[str(i)] = {"duration": duration, "speaker": speaker, "voiceover": voiceover}
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            if self.consistency == "strong":
                frame = work / f"frame_{i:03d}.png"
                if self._extract_last_frame(norm, frame):
                    prev_frame = frame
            prev_speaker, prev_location = speaker, location

        # 字幕
        srt_path = out_dir / "videos" / f"{item.index:03d}_{safe_name(item.title)}.srt"
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        self._build_srt(segments, srt_path)

        # 拼接 + 烧录字幕
        self.progress("合成最终视频（拼接 + 字幕）…")
        final_path = out_dir / "videos" / f"{item.index:03d}_{safe_name(item.title)}.mp4"
        concat_path = work / "concat.mp4"
        self._concat(normalized, concat_path)
        self._burn_subtitles(concat_path, srt_path, final_path)
        return final_path

    # ---------- 强一致参考图 ----------
    def _ensure_reference(self, story: dict, out_dir: Path) -> Path:
        ref = out_dir / "assets" / "reference.png"
        if not ref.exists():
            self.progress("生成统一的角色/场景参考图…")
            ref.parent.mkdir(parents=True, exist_ok=True)
            ref.write_bytes(self.grok.generate_image(self._reference_prompt(story)))
        return ref

    def _ensure_character_refs(self, story: dict, out_dir: Path) -> dict:
        """为每个角色生成/复用一张定妆参考图，返回 {角色名: 图片路径}。"""
        refs = {}
        cast = story.get("cast") or []
        char_dir = out_dir / "assets" / "characters"
        for c in cast:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            path = char_dir / f"{safe_name(name)}.png"
            if not path.exists():
                self.progress(f"生成角色定妆图：{name}…")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self.grok.generate_image(self._character_prompt(c, story)))
            refs[name] = path
        return refs

    def _ensure_scene_refs(self, story: dict, out_dir: Path) -> dict:
        """为每个场景生成/复用一张参考图，返回 {场景名: 图片路径}。"""
        refs = {}
        scene_dir = out_dir / "assets" / "scenes"
        for loc in story.get("locations") or []:
            name = (loc.get("name") or "").strip()
            if not name:
                continue
            path = scene_dir / f"{safe_name(name)}.png"
            if not path.exists():
                self.progress(f"生成场景参考图：{name}…")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self.grok.generate_image(self._scene_prompt(loc, story)))
            refs[name] = path
        return refs

    def _character_prompt(self, c: dict, story: dict) -> str:
        return (
            f"角色定妆参考图。角色：{c.get('name', '')}。外貌穿着：{c.get('appearance', '')}。"
            f"身份性格：{c.get('persona', '')}。整体画风：{story.get('style', '')}。"
            "要求：单人、五官清晰、半身或全身、中性背景，作为该角色在全剧所有镜头中的外貌基准，需可复用。"
        )

    def _scene_prompt(self, loc: dict, story: dict) -> str:
        return (
            f"场景参考图。地点：{loc.get('name', '')}。描述：{loc.get('description', '')}。"
            f"整体画风：{story.get('style', '')}。要求：空镜无人物，作为该场景的视觉基准。"
        )

    def _reference_prompt(self, story: dict) -> str:
        return (
            f"为短视频系列绘制统一的角色与场景基准画面。标题：{story.get('title', '')}。"
            f"主要人物/要素：{story.get('characters', '')}。整体风格：{story.get('style', '')}。"
            "要求主体清晰、画风统一，作为后续所有镜头的视觉基准。"
        )

    def _consistency_prefix(self, story: dict) -> str:
        return (
            f"统一画风：{story.get('style', '')}。固定角色与场景设定：{story.get('characters', '')}。"
            "请在所有镜头中保持人物外貌、服装、场景与画风的一致与连贯。"
        )

    # ---------- ffmpeg 封装 ----------
    def _run(self, args: list[str]) -> None:
        proc = subprocess.run([_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg 失败：{(proc.stderr or '').strip()[:400]}")

    def _normalize_clip(self, src: Path, audio: Path | None, duration: int, out: Path) -> None:
        w, h = self.dims
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},fps=30,format=yuv420p")
        if audio:
            self._run([
                "-i", str(src), "-i", str(audio),
                "-filter_complex", f"[0:v]{vf}[v];[1:a]apad[a]",
                "-map", "[v]", "-map", "[a]", "-t", str(duration),
                "-c:v", "libx264", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out),
            ])
        else:
            self._run([
                "-i", str(src),
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-filter_complex", f"[0:v]{vf}[v]",
                "-map", "[v]", "-map", "1:a", "-t", str(duration),
                "-c:v", "libx264", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out),
            ])

    def _extract_last_frame(self, src: Path, out: Path) -> bool:
        try:
            self._run(["-sseof", "-0.2", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(out)])
            if out.exists() and out.stat().st_size > 0:
                return True
        except RuntimeError:
            pass
        try:
            self._run(["-i", str(src), "-frames:v", "1", "-q:v", "2", str(out)])
            return out.exists() and out.stat().st_size > 0
        except RuntimeError:
            return False

    def _concat(self, clips: list[Path], out: Path) -> None:
        listfile = out.parent / "concat.txt"
        listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
        self._run([
            "-f", "concat", "-safe", "0", "-i", str(listfile),
            "-c:v", "libx264", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out),
        ])

    def _burn_subtitles(self, src: Path, srt: Path, out: Path) -> None:
        font = _cjk_fontfile()
        style = "Fontsize=18,Outline=1,Shadow=0,MarginV=40"
        sub_path = str(srt).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        if font:
            fontsdir = str(Path(font).parent).replace("\\", "/").replace(":", "\\:")
            vf = f"subtitles='{sub_path}':fontsdir='{fontsdir}':force_style='{style}'"
        else:
            vf = f"subtitles='{sub_path}':force_style='{style}'"
        try:
            self._run(["-i", str(src), "-vf", vf, "-c:a", "copy", str(out)])
        except RuntimeError:
            # 烧录失败（如缺中文字体）：退而求其次，把字幕作为软字幕封装，并保留 .srt 旁车
            self._run([
                "-i", str(src), "-i", str(srt),
                "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", str(out),
            ])

    def _media_duration(self, path: Path) -> float:
        proc = subprocess.run(
            [_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
        try:
            return float(proc.stdout.strip())
        except (ValueError, AttributeError):
            return 0.0

    def _build_srt(self, segments: list[tuple], path: Path) -> None:
        lines, t = [], 0.0
        idx = 1
        for duration, speaker, text in segments:
            if text:
                label = f"{speaker}：{text}" if speaker and speaker != "旁白" else text
                lines.append(f"{idx}\n{_srt_time(t)} --> {_srt_time(t + duration)}\n{label}\n")
                idx += 1
            t += duration
        path.write_text("\n".join(lines), encoding="utf-8")
