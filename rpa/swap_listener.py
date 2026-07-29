# -*- coding: utf-8 -*-
"""
换图 RPA 监听器 v2 — 进度版
启动方式：ShadowBot Python 运行此文件
"""
import json
import sys
import os
import uuid
import subprocess
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime

if os.name == 'nt':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PORT = 8767
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "swap_log.txt")
PROGRESS_DIR = os.path.join(SCRIPT_DIR, "swap_progress")
os.makedirs(PROGRESS_DIR, exist_ok=True)

# auto-detect ShadowBot Python
for ver in ["6.2.23", "6.2.14", "6.2"]:
    candidate = r"C:\Program Files\ShadowBot\shadowbot-{}\python\python.exe".format(ver)
    if os.path.exists(candidate):
        PYTHON_EXE = candidate
        break
else:
    PYTHON_EXE = sys.executable

SWAP_SCRIPT = os.path.join(SCRIPT_DIR, "swap_rpa.py")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def progress_path(job_id):
    return os.path.join(PROGRESS_DIR, "%s.json" % job_id)


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
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/status":
            self._send_json({"status": "running", "listener": "swap_rpa_v2", "port": PORT})
        elif path.startswith("/progress/"):
            job_id = path.split("/progress/")[-1]
            pfile = progress_path(job_id)
            if os.path.exists(pfile):
                try:
                    with open(pfile, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._send_json(data)
                except Exception:
                    self._send_json({"error": "read progress failed"}, 500)
            else:
                self._send_json({"error": "job not found", "job_id": job_id}, 404)
        elif path == "/log":
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-100:]
                self._send_json({"log": lines})
            except Exception:
                self._send_json({"log": []})
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path != "/execute":
            self._send_json({"error": "Not found"}, 404)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            command = json.loads(body.decode("utf-8"))
        except Exception as e:
            self._send_json({"success": False, "error": "JSON error: %s" % e}, 400)
            return

        action = command.get("action", "")
        if action != "swap_image":
            self._send_json({"success": False, "error": "unknown action: %s" % action}, 400)
            return

        # 生成 job_id，创建进度文件
        job_id = uuid.uuid4().hex[:12]
        pfile = progress_path(job_id)
        with open(pfile, "w", encoding="utf-8") as f:
            json.dump({"phase": "starting", "status": "pending", "job_id": job_id}, f)

        source = command.get("source", {})
        targets = command.get("targets", [])
        target_ids = [t.get("product_id", "?") for t in targets]

        log("job=%s source=%s targets=%s" % (job_id, source.get("product_id"), target_ids))

        try:
            cmd_json = json.dumps(command, ensure_ascii=False)
            subprocess.Popen(
                [PYTHON_EXE, "-u", SWAP_SCRIPT, "--command", cmd_json, "--progress", pfile],
                cwd=SCRIPT_DIR,
                stdout=open(LOG_FILE, "a", encoding="utf-8"),
                stderr=subprocess.STDOUT,
            )
            log("job=%s RPA started" % job_id)
            self._send_json({
                "success": True,
                "job_id": job_id,
                "status": "started",
                "message": "RPA started: %s -> %d targets" % (source.get("product_id"), len(targets)),
            })
        except Exception as e:
            log("job=%s start failed: %s" % (job_id, traceback.format_exc()))
            self._send_json({"success": False, "error": str(e), "job_id": job_id}, 500)


def main():
    log("swap listener v2 start, port %d" % PORT)
    log("Python: %s" % sys.executable)
    log("RPA: %s" % SWAP_SCRIPT)

    server = HTTPServer(("0.0.0.0", PORT), SwapHandler)
    log("listening: http://0.0.0.0:%d" % PORT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
        server.shutdown()


if __name__ == "__main__":
    main()
