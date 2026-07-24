# -*- coding: utf-8 -*-
"""
换图 RPA 监听器 - 部署到目标电脑
接收来自看板后端的换图命令，调用 Playwright 脚本执行

启动方式（Windows 命令行）:
    cd C:/Users/s/Desktop/cetu-rpa
    "C:/Program Files/ShadowBot/shadowbot-6.2.14/python/python.exe" swap_listener.py
"""
import json
import sys
import os
import subprocess
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime

PORT = 8767
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "swap_log.txt")

PYTHON_EXE = r"C:\Program Files\ShadowBot\shadowbot-6.2.14\python\python.exe"
SWAP_SCRIPT = os.path.join(SCRIPT_DIR, "swap_rpa.py")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] {}".format(ts, msg)
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class SwapHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log("HTTP: " + (fmt % args))

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/status":
            self._send_json({"status": "running", "listener": "swap_rpa", "port": PORT})
        elif path == "/log":
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-50:]
                self._send_json({"log": lines})
            except Exception:
                self._send_json({"log": []})
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/execute":
            self._send_json({"error": "Not found"}, 404)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            command = json.loads(body.decode("utf-8"))
        except Exception as e:
            self._send_json({"success": False, "error": "JSON error: {}".format(e)}, 400)
            return

        action = command.get("action", "")
        if action != "swap_image":
            self._send_json({"success": False, "error": "unknown action: {}".format(action)}, 400)
            return

        source = command.get("source", {})
        targets = command.get("targets", [])
        target_ids = [t.get("product_id", "?") for t in targets]

        log("收到换图: source={}, targets={}".format(source.get("product_id"), target_ids))

        try:
            cmd_json = json.dumps(command, ensure_ascii=False)
            subprocess.Popen(
                [PYTHON_EXE, "-u", SWAP_SCRIPT, "--command", cmd_json],
                cwd=SCRIPT_DIR,
                stdout=open(LOG_FILE, "a", encoding="utf-8"),
                stderr=subprocess.STDOUT,
            )
            log("RPA 已启动")
            self._send_json({
                "success": True,
                "message": "已启动: {} -> {}个目标".format(source.get("product_id"), len(targets)),
                "source": source.get("product_id"),
                "target_count": len(targets),
            })
        except Exception as e:
            log("启动失败: {}".format(traceback.format_exc()))
            self._send_json({"success": False, "error": str(e)}, 500)


def main():
    log("换图监听器启动, 端口 {}".format(PORT))
    log("Python: {}".format(sys.executable))
    log("RPA: {}".format(SWAP_SCRIPT))

    server = HTTPServer(("0.0.0.0", PORT), SwapHandler)
    log("监听: http://0.0.0.0:{}".format(PORT))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("监听器停止")
        server.shutdown()


if __name__ == "__main__":
    main()
