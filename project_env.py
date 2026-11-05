"""Keep every writable runtime/cache path inside this project."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MODELS = ROOT / "models"
for name, value in {
    "HF_HOME": ROOT / ".cache" / "huggingface",
    "HF_HUB_CACHE": ROOT / ".cache" / "huggingface" / "hub",
    "HF_XET_CACHE": ROOT / ".cache" / "huggingface" / "xet",
    "XDG_CACHE_HOME": ROOT / ".cache",
    "TORCH_HOME": ROOT / ".cache" / "torch",
    "NUMBA_CACHE_DIR": ROOT / ".cache" / "numba",
    "MPLCONFIGDIR": ROOT / ".cache" / "matplotlib",
    "TMPDIR": ROOT / "data" / "tmp",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_XET": "1",
    "HF_HUB_DOWNLOAD_TIMEOUT": "60",
    "HF_HUB_ETAG_TIMEOUT": "15",
    "TOKENIZERS_PARALLELISM": "false",
}.items():
    os.environ[name] = str(value)
for path in (DATA, MODELS, ROOT / ".cache", DATA / "tmp", DATA / "jobs"):
    path.mkdir(parents=True, exist_ok=True)

MODEL_CATALOG = {
    "mimo-v2.5": {"label": "中英混合 · 推荐", "size": "约 7.1 GB（含音频编码器）", "description": "MiMo 4-bit · Apple GPU 本地识别，可串行复核", "engine": "mimo", "repo": "mlx-community/MiMo-V2.5-ASR-MLX-4bit", "dependencies": ["mimo-tokenizer"]},
    "whisper-large-v3-mlx": {"label": "独立复核", "size": "约 3.1 GB", "description": "Whisper Large-v3 FP16 · Apple GPU", "engine": "mlx-whisper", "repo": "mlx-community/whisper-large-v3-mlx"},
    "mimo-tokenizer": {"label": "MiMo 音频编码器", "size": "约 2.6 GB", "description": "MiMo 识别所需组件，自动下载", "engine": "component", "repo": "mlx-community/MiMo-Audio-Tokenizer", "selectable": False},
    "tiny": {"label": "极速", "size": "约 75 MB", "description": "快速试用，中文准确率较低"},
    "base": {"label": "轻量", "size": "约 145 MB", "description": "入门首选，内存占用较低"},
    "small": {"label": "标准", "size": "约 485 MB", "description": "推荐中文课程，准确率更好"},
    "medium": {"label": "精准", "size": "约 1.5 GB", "description": "专业术语更准确，耗时较长"},
    "large-v3-turbo": {"label": "高精度", "size": "约 1.6 GB", "description": "高精度多语言，需要更多内存"},
}


def model_ready(name):
    if name not in MODEL_CATALOG:
        return False
    folder = MODELS / name
    info = MODEL_CATALOG[name]
    if "engine" in info:
        weights = list(folder.glob("*.safetensors")) + list(folder.glob("*.npz"))
        return (folder / ".ready").is_file() and (folder / "config.json").is_file() and bool(weights) and all(model_ready(dependency) for dependency in info.get("dependencies", []))
    return all((folder / f).is_file() for f in ("model.bin", "config.json", "tokenizer.json")) and (folder / ".ready").exists()
