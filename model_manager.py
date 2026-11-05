"""Download complete model snapshots to this project; never fetch during inference."""
import project_env
import json
import os
import threading
from pathlib import Path


def bind_audio_tokenizer(folder):
    manifest = folder / "mlx_manifest.json"
    document = json.loads(manifest.read_text()) if manifest.exists() else {}
    document["audio_tokenizer_dir"] = "../mimo-tokenizer"
    document.pop("audio_tokenizer_repo", None)
    temporary = folder / f"manifest-local-{os.getpid()}.tmp"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2))
    temporary.replace(manifest)


def ensure_model(name, emit=lambda **event: None):
    if name not in project_env.MODEL_CATALOG:
        raise ValueError("未知模型")
    info = project_env.MODEL_CATALOG[name]
    for dependency in info.get("dependencies", []):
        ensure_model(dependency, emit)
    folder = project_env.MODELS / name
    if project_env.model_ready(name):
        if name == "mimo-v2.5":
            bind_audio_tokenizer(folder)
        return folder
    from huggingface_hub import snapshot_download
    emit(status="downloading", stage="download", message=f"正在下载 {name}，首次使用需要联网…", progress=None)
    repo = info.get("repo") or ("mobiuslabsgmbh/faster-whisper-large-v3-turbo" if name == "large-v3-turbo" else f"Systran/faster-whisper-{name}")
    patterns = ["*.json", "*.safetensors", "*.npz", "*.txt", "*.tiktoken", "*.model", "*.jinja", "*.bin"]
    stop_monitor = threading.Event()

    def report_download():
        last = -1
        while not stop_monitor.wait(3):
            paths = list(folder.glob("*.safetensors")) + list(folder.glob("*.npz")) + list(folder.glob("*.bin")) + list(folder.glob(".cache/huggingface/download/*.incomplete"))
            size = sum(path.stat().st_size for path in paths if path.exists())
            if size != last:
                last = size
                emit(status="downloading", stage="download", progress=None, download_bytes=size,
                     message=f"正在下载 {name} · 本地已接收 {size / 1024**3:.2f} GB")
    monitor = threading.Thread(target=report_download, daemon=True)
    monitor.start()
    try:
        snapshot_download(repo, local_dir=str(folder), allow_patterns=patterns, max_workers=2)
    finally:
        stop_monitor.set()
        monitor.join(timeout=4)
    if not (folder / "config.json").is_file():
        raise RuntimeError("模型缺少配置，请重新下载。")
    if info.get("engine"):
        if not (list(folder.glob("*.safetensors")) or list(folder.glob("*.npz"))):
            raise RuntimeError("模型缺少权重，请重新下载。")
    else:
        for required in ("model.bin", "tokenizer.json"):
            if not (folder / required).is_file():
                raise RuntimeError(f"模型缺少 {required}，请重新下载。")
    if name == "mimo-v2.5":
        # The upstream runtime resolves this field. Make the dependency local
        # so it cannot silently fall back to a Hub download in an offline run.
        bind_audio_tokenizer(folder)
    (folder / ".ready").write_text(json.dumps({"repo": repo, "local": True}) + "\n")
    return folder


if __name__ == "__main__":
    import sys
    for name in sys.argv[1:] or ["mimo-v2.5", "whisper-large-v3-mlx"]:
        ensure_model(name, lambda **event: print(event["message"], flush=True))
        print(f"READY: {name}", flush=True)
