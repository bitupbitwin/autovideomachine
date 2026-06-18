# 自动视频脚本工作台

这是一个本地桌面软件原型，用来把网址或长文本整理成短视频故事、分集脚本和每个镜头的 AI 视频提示词。

当前版本先完成核心工作流：

1. 编排故事：输入网址或粘贴长文本，生成故事标题、梗概、人物/要素和视频风格，并保存到本地项目文件夹。
2. 生成多个脚本：按约 1 分钟短视频拆分，生成多个脚本标题、旁白和镜头提示词，并逐个保存。
3. 选择标题开始执行：选择某个脚本，生成执行记录。后续接入视频生成 API 后，这一步会继续生成画面、配音和最终视频。

## 运行方式

在 Windows PowerShell 中进入本文件夹：

```powershell
cd I:\developapp\video_auto_studio
python app.py
```

不需要先安装第三方依赖，当前版本只使用 Python 标准库。

## 输出位置

每次完成“编排故事”后，会在 `outputs` 目录下创建一个新项目文件夹，例如：

```text
outputs/
  20260618_153000_故事标题/
    metadata.json
    story/
      story.json
      story.md
    scripts/
      001_脚本标题.json
      001_脚本标题.md
    runs/
      20260618_153500_001_脚本标题.json
```

## API 配置

当前没有 API 时，会用本地规则生成故事和脚本，方便先验证软件流程。

后期你提供 API 后，把 `config.example.json` 复制为 `config.json`，填入接口地址和 key。接口按 OpenAI Chat Completions 兼容格式调用。

```json
{
  "api_url": "https://你的接口地址/v1/chat/completions",
  "api_key": "你的 API Key",
  "model": "你的模型名",
  "temperature": 0.7
}
```

## 下一步建议

- 接入你提供的大模型 API，让“编排故事”和“生成脚本”使用真正的大模型输出。
- 在第 3 步接入视频生成、配音、字幕和合成 API。
- 增加项目历史列表，方便打开以前生成过的故事和脚本。
