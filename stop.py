"""Stop only this project's running local service."""
import json
from pathlib import Path
import urllib.request

ROOT = str(Path(__file__).resolve().parent)
found = False
for port in range(8765, 8785):
    url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=0.4) as response:
            info = json.load(response)
        if info.get("app") != "whisper-study" or info.get("project") != ROOT:
            continue
        request = urllib.request.Request(url + "/api/shutdown", data=json.dumps({"project": ROOT}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=3) as response:
            json.load(response)
        found = True
        print(f"已停止本项目服务：{url}")
    except Exception:
        continue
if not found:
    print("未找到本项目正在运行的服务，可能已经停止。")
