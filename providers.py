"""外部生成式 API 客户端：Grok 视频/图像（xAI）、Gemini TTS（Google）。

仅依赖标准库 urllib。所有响应解析都做了多形态兜底，因为各家字段可能随版本微调；
若某家返回结构与此处不符，改动集中在本文件即可。
"""

import base64
import json
import re
import time
import urllib.request


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 120) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download_bytes(url: str, timeout: int = 120) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    req = urllib.request.Request(url, headers={"User-Agent": "VideoAutoStudio/0.3"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _data_uri(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")


def _dig(obj, *paths):
    """按多组路径尝试取值，返回第一个命中的非空值。path 为 key/索引序列。"""
    for path in paths:
        cur = obj
        ok = True
        for key in path:
            try:
                cur = cur[key]
            except (KeyError, IndexError, TypeError):
                ok = False
                break
        if ok and cur:
            return cur
    return None


class GrokClient:
    """xAI Grok Imagine：图像生成 + 文/图生视频（异步轮询）。"""

    BASE = "https://api.x.ai/v1"

    def __init__(self, api_key: str, video_model: str = "grok-imagine-video",
                 image_model: str = "grok-2-image"):
        if not api_key:
            raise RuntimeError("未配置 xAI(Grok) API Key")
        self.api_key = api_key
        self.video_model = video_model
        self.image_model = image_model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def generate_image(self, prompt: str, timeout: int = 180) -> bytes:
        resp = _post_json(
            f"{self.BASE}/images/generations",
            self._headers(),
            {"model": self.image_model, "prompt": prompt, "n": 1, "response_format": "b64_json"},
            timeout=timeout,
        )
        b64 = _dig(resp, ["data", 0, "b64_json"])
        if b64:
            return base64.b64decode(b64)
        url = _dig(resp, ["data", 0, "url"])
        if url:
            return _download_bytes(url)
        raise RuntimeError(f"图像生成返回异常：{json.dumps(resp)[:300]}")

    def generate_video(self, prompt: str, image_bytes: bytes | None = None, duration: int = 6,
                       aspect_ratio: str = "9:16", resolution: str = "720p",
                       poll_interval: int = 5, timeout: int = 900) -> bytes:
        payload = {
            "model": self.video_model,
            "prompt": prompt,
            "duration": int(duration),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        if image_bytes:
            # 图生视频：以参考图/上一镜尾帧作为首帧（数据 URI）
            payload["image"] = {"url": _data_uri(image_bytes)}

        start = _post_json(f"{self.BASE}/videos/generations", self._headers(), payload, timeout=120)
        # 同步即返回（部分实现）
        direct = _dig(start, ["url"], ["video", "url"], ["data", 0, "url"])
        if direct:
            return _download_bytes(direct)

        request_id = _dig(start, ["request_id"], ["id"])
        if not request_id:
            raise RuntimeError(f"视频生成未返回 request_id：{json.dumps(start)[:300]}")

        deadline = time.time() + timeout
        while time.time() < deadline:
            status_resp = _get_json(f"{self.BASE}/videos/{request_id}", self._headers(), timeout=60)
            status = str(_dig(status_resp, ["status"]) or "").lower()
            if status in {"done", "succeeded", "completed", "success"}:
                url = _dig(status_resp, ["url"], ["video", "url"], ["data", 0, "url"], ["output", "url"])
                if not url:
                    raise RuntimeError(f"视频已完成但未找到下载地址：{json.dumps(status_resp)[:300]}")
                return _download_bytes(url)
            if status in {"failed", "error", "canceled", "cancelled"}:
                raise RuntimeError(f"视频生成失败：{json.dumps(status_resp)[:300]}")
            time.sleep(poll_interval)
        raise RuntimeError("视频生成超时")


class GeminiTTSClient:
    """Google Gemini 文本转语音，返回 WAV 字节。"""

    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-preview-tts", voice: str = "Kore"):
        if not api_key:
            raise RuntimeError("未配置 Gemini API Key")
        self.api_key = api_key
        self.model = model
        self.voice = voice

    def synthesize(self, text: str, voice: str | None = None, timeout: int = 120) -> bytes:
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or self.voice}}
                },
            },
        }
        resp = _post_json(
            f"{self.BASE}/models/{self.model}:generateContent",
            {"x-goog-api-key": self.api_key},
            payload,
            timeout=timeout,
        )
        part = _dig(resp, ["candidates", 0, "content", "parts", 0, "inlineData"],
                    ["candidates", 0, "content", "parts", 0, "inline_data"])
        if not part:
            raise RuntimeError(f"TTS 返回异常：{json.dumps(resp)[:300]}")
        b64 = part.get("data")
        mime = part.get("mimeType") or part.get("mime_type") or "audio/L16;rate=24000"
        pcm = base64.b64decode(b64)
        rate_match = re.search(r"rate=(\d+)", mime)
        rate = int(rate_match.group(1)) if rate_match else 24000
        return pcm16_to_wav(pcm, rate)


def pcm16_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """把裸 16-bit PCM 包装成 WAV。"""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()
