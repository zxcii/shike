# 拾课 · 本地学习工作台

把本地学习视频转成讲课全文，结合画面资料和独立识别复核，校对后交给其他大模型出题。

## 源码安装与示例

本仓库面向 Apple Silicon Mac，包含源码、固定版本依赖清单、文档和 `tests/assets/` 中的测试示例；不包含模型权重、Python 运行环境、缓存、个人学习记录或供应商密钥。

将仓库克隆或下载到本地后，在项目目录执行 `bash setup.sh`，再双击启动脚本。首次转写会联网下载所需模型到 `models/`，也可在界面中提前下载。模型、运行环境和使用数据由应用在本地创建，已通过 `.gitignore` 排除。测试示例与题集格式示例可以直接使用，实际课程和账号配置需自行添加。

## 启动与停止

在 macOS Finder 中双击 **启动拾课.command**，浏览器会自动打开本地界面，默认地址 **http://127.0.0.1:8765**。无需账号或 API Key。默认端口占用时会尝试 8766–8784。

双击 **停止拾课.command** 或在启动窗口按 `Control+C` 停止。仅关闭浏览器标签页不会停止服务。

如果 macOS 没有直接运行脚本，在终端执行：

```bash
cd /path/to/Whisper
bash 启动拾课.command
```

首次安装或补齐依赖：`bash setup.sh`。Python 和依赖只安装在当前目录，不修改全局 Python、shell 配置或自启动项，不需要 Homebrew、Xcode 或系统 FFmpeg。

## 默认高精度方案

新增 **快 / 中 / 慢** 三档运行速度，默认慢档低热运行。三档模型、精度和识别参数不变，运行中也可切换；慢档目标约 5% 计算工作占空比，并在系统报告偏热时暂停等待。详见 [速度与低热模式](docs/速度与低热模式.md)。

**MiMo-V2.5-ASR MLX 4-bit 主转写 + Whisper Large-v3 MLX FP16 独立复核 + macOS Vision OCR**，面向 Apple Silicon Mac，按 M4 / 16 GiB 配置分段、串行运行。

- 内容语言默认「中英混合 · 保留原文」。纯中文或英文也可手动指定，其他语言请选择 Whisper。
- 默认启用独立复核和画面识别，可以取消以节省时间。Base/Small 等 CPU 轻量模式继续保留，只识别语音。
- 首次下载模型需联网。下载后推理使用本地路径并设置离线模式。MiMo 4-bit 约 4.51 GB，音频编码器约 2.6 GB，Whisper Large-v3 约 3.1 GB；依赖和缓存另计。
- 每个 GPU 模型使用独立子进程，依次运行，退出后释放内存；取消任务也会终止模型子进程。
- 语音按停顿分成最长约 28 秒的块，时间戳是片段范围，非逐字精确对齐。
- 专业术语提示作用于 Whisper 复核，当前 MiMo 接口不使用该提示。

详细流程、模型边界和操作见 **[升级使用说明](docs/升级使用说明.md)**。

## 学习流程

1. 拖入本地 MP4、MOV、MKV、WebM 或 MP3、WAV、M4A、FLAC 等音视频。单文件最大 2 GB，音轨最长 2 小时；一次添加一个，最多排队 4 个任务。
2. 点击开始，等待画面提取、主转写和复核逐阶段完成。
3. 在「时间轴」回听，在「复核」比较两份结果，在「课程全文」修改并保存，在「画面资料」核对字幕和原图。
4. 分歧片段可保留主识别或采用复核文本；两份原始文本始终保留。已有全文校对时，应在全文修改，再标记片段已核对，避免覆盖人工修改。
5. 点击「复制内容，让 AI 出题」，粘贴到你选择的大模型。提示词要求先出题，等你作答后再评分、解释和巩固，不根据未确认分歧判分。

视频导入是复制到本项目，不是上传互联网。识别、OCR 与复核不调用云端 API。只有你主动将文字发送给其他大模型后，资料才离开本机。

## 语法课注意事项

课程可能故意展示 `He go to school.` 这样的错误例句。本应用不做语法润色，保留原始识别与关键帧供核对。OCR 关闭文字纠正，依然可能有误字或遗漏，也可能看不懂箭头、颜色、删除线和复杂公式。画面资料与口述内容分别保存，两份识别相同也不代表一定正确。

OCR 每秒抽样、忽略常见顶部弹幕区域、去重连续重复文字。非常短暂的文字可能遗漏。某些 MKV/HEVC 不能在浏览器预览，通常仍可提取音轨，可用本地播放器回听。

## 导出

| 格式 | 内容 |
| --- | --- |
| TXT | 已保存全文；未编辑时使用片段校对后的文本 |
| Markdown | 全文、原始时间轴、画面资料、待核对分歧和实测指标 |
| SRT | 主识别原始字幕及片段时间，不包含 OCR，不随全文校对重排 |
| 出题提示词 | 分来源资料、未确认说明、学习教练指令 |
| JSON | 原始两份识别、人工选择、全文、画面资料、运行指标 |

长课程资料可能超过其他模型的上下文长度，可分段粘贴。导出的图片引用只记录文件信息，原图保存在任务的 `frames/` 目录，不嵌入 TXT。

## 文件与卸载

所有本应用文件均在当前项目目录：

- `.runtime/`、`.venv/`：Python 和依赖；`.cache/`：下载与运行缓存。
- `models/`：全部模型与音频编码器。
- `data/jobs/<任务ID>/`：视频副本、`job.json`、`worker.log` 和 `frames/`。
- `data/tmp/`：临时文件；`data/server.log`：轮转日志。
- `app.py`：接口和队列；`worker.py`：流水线调度；`stage_runner.py`：单模型 GPU 推理。
- `model_manager.py`：模型下载；`visual_extract.py`：OCR 和字幕；`pipeline.py`：分段与差异；`exports.py`：导出。
- `static/`：中文界面，无外部字体或 CDN；`docs/`：方案与预览；`tests/`：检查与素材。

**卸载：先双击停止脚本，再删除整个 Whisper 目录。** 会移除本应用的代码、依赖、模型、缓存和学习记录，原视频不受影响。删除学习记录会立即删除项目内该任务的视频副本和结果，不能在应用中恢复。

浏览器下载、历史及操作系统自身的框架缓存由其各自管理；想把导出也集中保存，请在保存对话框选择本项目目录。移动整个目录后需重建 `.venv` 并运行 `bash setup.sh`；模型和数据可保留，编码器绑定使用相对路径。

## 开发与验证

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
node --check static/app.js
.venv/bin/python run.py --no-browser
.venv/bin/python model_manager.py mimo-v2.5 whisper-large-v3-mlx
.venv/bin/python tests/create_mixed_fixture.py
.venv/bin/python tests/check_ocr.py
# 以下需要本地服务已启动
.venv/bin/python tests/smoke_accurate.py
.venv/bin/python tests/smoke.py
```

测试视频由本项目和 macOS 自带语音生成，不是实际课堂准确率保证。12 分钟素材由重复讲解构造，用于长视频流程测试。`jfk.flac` 来自 [Whisper 官方测试样本](https://github.com/openai/whisper/blob/main/tests/jfk.flac)。

主要依赖：[MiMo 官方](https://github.com/XiaomiMiMo/MiMo-V2.5-ASR)、[MiMo MLX 4-bit](https://huggingface.co/mlx-community/MiMo-V2.5-ASR-MLX-4bit)、[MLX-Audio](https://github.com/Neywa-Labs/mlx-audio)、[MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[Apple Vision](https://developer.apple.com/documentation/vision/recognizing-text-in-images)。

重叠音频用于保留边界上下文，保守去重可能留下少量短语重复，需在全文校对。

## AI 出题与本地答题

在「练习与答题」中复制标准出题格式，让 AI 返回包含答案的 JSON，粘贴导入即可离线自测。支持单选、多选、判断、填空（任一等价答案、必须答全要点、数值范围）和简答；答案默认隐藏，逐题就近展开，提交后本地判分。作答自动保存在 `data/quizzes/`，可导出参考答案或自己的完整作答给 AI 评价。转写工作台原有的出题提示词也已使用同一协议。

完整说明见 [AI 出题与本地答题](docs/AI出题与本地答题.md)，标准示例见 [quiz-example.json](docs/quiz-example.json)。

## 书架与完整课程流程

「学习书架」按书本封面排列课程主题，支持命名、上传封面、书架排序与书内 8 层分类。打开课程后按「学习 → AI 出题 → 答题 → 分析」逐步完成；题集自动归入本课，作答后可保存 AI 反馈和复习计划。原有记录统一留在待整理区，可逐步归类，无需重新识别。

详见 [书架与课程学习流程](docs/书架与课程学习流程.md)。目录树与排序复用 jsTree / SortableJS，所有前端组件本地保存，来源与许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 直接调用模型供应商

右上方「模型供应商」可配置多个连接，直接在课程里生成题集、分析作答并保存反馈。调用层复用 LiteLLM 官方 SDK（`litellm-core==1.108.0`），供应商选择来自上游完整目录，支持原生供应商与自定义 OpenAI 兼容接口；手动复制方式仍保留。密钥保存在项目 `data/ai/` 内，仅限当前账户读写，不回显、不进入题集导出。

云端调用会发送本次课程文本或作答并可能计费；本地视频识别保持原方式。详细配置、数据范围和验证方法见 [模型供应商与自动出题](docs/模型供应商与自动出题.md)。
