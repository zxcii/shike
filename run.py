"""Launch on loopback; double-click 启动拾课.command on macOS."""
import project_env
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import socket
import threading
import time
import urllib.request
import webbrowser


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(project_env.DATA / "server.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8"), logging.StreamHandler()])
    port = args.port
    for candidate in range(port, port + 20):
        try:
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", candidate))
            port = candidate
            break
        except OSError:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{candidate}/api/health", timeout=1) as response:
                    info = json.load(response)
                if info.get("app") == "whisper-study" and info.get("project") == str(project_env.ROOT):
                    url = f"http://127.0.0.1:{candidate}"
                    print(f"拾课已在运行：{url}")
                    if not args.no_browser:
                        webbrowser.open(url)
                    return
            except Exception:
                pass
    else:
        raise SystemExit("没有可用端口，请关闭占用 8765–8784 的应用后再试。")
    url = f"http://127.0.0.1:{port}"
    print(f"\n拾课 · 本地学习工作台\n{url}\n按 Control+C 停止。所有文件均保存在：{project_env.ROOT}\n", flush=True)
    if not args.no_browser:
        def open_when_ready():
            for _ in range(60):
                try:
                    urllib.request.urlopen(url + "/api/health", timeout=1).close()
                    webbrowser.open(url)
                    return
                except Exception:
                    time.sleep(0.3)
        threading.Thread(target=open_when_ready, daemon=True).start()
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
