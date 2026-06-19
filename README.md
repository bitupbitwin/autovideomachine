# 自动视频脚本工作台

这是一个本地桌面软件，用来把网址或长文本整理成短视频故事、分集脚本和每个镜头的 AI 视频提示词。界面采用毛玻璃 + 橙色调的现代风格（基于 PyWebView 渲染原生窗口）。

## 核心工作流

1. **编排故事**：输入网址或粘贴长文本，生成故事标题、梗概、人物/要素和视频风格，并保存到本地项目文件夹。几万字的长文会**分块通读全文**（模型模式用 map-reduce 归并摘要，本地模式跨全篇采样），不再只看开头。
2. **生成多个脚本（剧本式）**：编排故事时会产出**角色表 cast**（外貌/性格）与**场景表 locations**；生成脚本时按集拆分为**场景 + 对白节拍**（speaker / line / action / 画面提示词 / 时长），用于多人物对话短剧。每集**标题力求概括且有吸引力**。模型模式产出真正的角色对白；本地模式降级为旁白节拍（仅预览结构）。
3. **选择标题开始执行 → 生成成片**：选择某个脚本，自动调用 **Grok（xAI）生成视频**、**Gemini（Google）TTS 配音**，并用 ffmpeg 拼接、对齐音频、烧录字幕，输出到项目的 `videos/`。

### 视频制作能力（第 3 步）

- **真实出片**：逐镜头调用 Grok Imagine 视频生成 + Gemini TTS 配音 + 自动字幕，合成带声音与字幕的 mp4。
- **人物/场景一致性（强一致 + 帧衔接 + 资产库）**：为每个角色生成一张**定妆参考图**、每个场景一张**参考图**（存 `assets/characters/`、`assets/scenes/`，项目级缓存、跨集复用，保证几十集人物不崩）。渲染每个镜头时：换说话人/换场景就用该角色定妆图重新锚定身份，同一人同场景内则用上一镜尾帧帧衔接。也可在设置里切到「轻量一致」省成本。
- **字幕**：按旁白与时长生成 `.srt`，并烧录进画面（同时保留 `.srt` 旁车文件）。
- **配音**：Gemini TTS，多音色可选（Kore/Puck/Zephyr…）。
- **背景音乐**：暂未接入（已预留位置，后续可加）。

> 依赖：需安装 **ffmpeg**，并在「设置」中填入 **xAI(Grok) API Key** 与 **Gemini API Key**。中文字幕烧录需系统装有中文字体（如 Noto Sans CJK），否则会自动退化为软字幕。

## 界面亮点

- 顶部实时显示「已连接模型 / 本地规则模式」，点击即可打开 **API 设置弹窗**（保存即生效，无需重启）。
- 可调 **单集时长** 与 **期望集数**（集数填 0 表示自动按内容长度拆分）。
- 网址模式支持 **抓取正文预览**，确认后再编排。
- 运行中按钮自动锁定并显示动效，避免重复触发。
- 一键 **打开输出文件夹**；操作结果以浮窗提示。

## 运行方式

需要 Python 3.10+。首次运行先安装依赖：

```bash
pip install pywebview
```

视频制作（第 3 步）还需要系统安装 **ffmpeg**（Windows 可从 ffmpeg.org 下载并加入 PATH；macOS `brew install ffmpeg`；Linux `sudo apt install ffmpeg`）。

> 各平台运行时：Windows 自带 WebView2；macOS 自带 WebKit；Linux 需安装 `webkit2gtk`（如 `sudo apt install gir1.2-webkit2-4.1 python3-gi`）。

然后在项目目录运行：

```bash
python app.py
```

## 输出位置

每次完成「编排故事」后，会在 `outputs` 目录下创建一个新项目文件夹，例如：

```text
outputs/
  20260619_153000_故事标题/
    metadata.json
    story/
      story.json
      story.md
    scripts/
      001_脚本标题.json
      001_脚本标题.md
    runs/
      20260619_153500_001_脚本标题.json
```

## API 配置

不配置 API 时，会用本地规则生成故事和脚本，方便先验证软件流程。

要使用真实大模型，点击界面右上角的 **设置**（或状态标签），填入接口地址、Key、模型名即可（按 OpenAI Chat Completions 兼容格式调用）。也可手动把 `config.example.json` 复制为 `config.json` 填写：

```json
{
  "api_url": "https://你的接口地址/v1/chat/completions",
  "api_key": "你的 API Key",
  "model": "你的模型名",
  "temperature": 0.7
}
```

## 项目结构

```text
app.py        PyWebView 桌面外壳 + JS↔Python 桥接
engine.py     核心逻辑：故事编排、脚本生成、文本模型调用、配置/主题、项目存储
providers.py  外部生成式 API 客户端：Grok 视频/图像（xAI）、Gemini TTS（Google）
pipeline.py   视频制作流水线：强一致帧衔接 + TTS + 字幕 + ffmpeg 合成
web/          界面层（index.html / style.css / app.js）
outputs/      生成的项目输出（含 videos/ 成片与 .srt 字幕）
```

## 下一步建议

- 在第 3 步接入视频生成、配音、字幕和合成 API。
- 增加项目历史列表，方便打开以前生成过的故事和脚本。
- 支持脚本逐条编辑与单集重新生成。
