# -*- coding: utf-8 -*-
"""
换图任务 Excel 接收器

主动轮询测图看板服务器，领取任务并将 Excel 下载到 received_excel。
该程序只负责接收文件，不启动浏览器，也不执行换图 RPA。
"""
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = int(os.getenv("SWAP_LISTENER_PORT", "8767"))
SERVER_URL = os.getenv("SWAP_SERVER_URL", "http://127.0.0.1:8766").rstrip("/")
LISTENER_ID = os.getenv("SWAP_LISTENER_ID", socket.gethostname()).strip() or socket.gethostname()
POLL_INTERVAL = max(2, int(os.getenv("SWAP_POLL_INTERVAL", "5")))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "swap_log.txt")
RECEIVED_DIR = os.path.join(SCRIPT_DIR, "received_excel")
os.makedirs(RECEIVED_DIR, exist_ok=True)

STATE_LOCK = threading.Lock()
STATE = {
    "status": "starting",
    "listener_id": LISTENER_ID,
    "server_url": SERVER_URL,
    "last_job_id": "",
    "last_excel": "",
    "last_error": "",
    "downloaded_count": 0,
}


def update_state(**changes):
    with STATE_LOCK:
        STATE.update(changes)


def get_state():
    with STATE_LOCK:
        return dict(STATE)


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (timestamp, message)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as file:
            file.write(line + "\n")
    except Exception:
        pass


def request_json(method, path_or_url, payload=None, timeout=15):
    url = path_or_url if path_or_url.startswith("http") else SERVER_URL + path_or_url
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def report_status(job_id, status, phase, **extra):
    payload = {
        "status": status,
        "phase": phase,
        "listener_id": LISTENER_ID,
    }
    payload.update(extra)
    try:
        request_json("POST", "/api/swap-tasks/%s/status" % job_id, payload, timeout=10)
    except Exception as error:
        log("job=%s status report failed: %s" % (job_id, error))


def available_output_path(filename):
    safe_name = os.path.basename(filename)
    stem, extension = os.path.splitext(safe_name)
    candidate = os.path.join(RECEIVED_DIR, safe_name)
    sequence = 2
    while os.path.exists(candidate):
        candidate = os.path.join(RECEIVED_DIR, "%s_%d%s" % (stem, sequence, extension))
        sequence += 1
    return candidate


def download_excel(job_id, excel_url, excel_file):
    url = urljoin(SERVER_URL + "/", excel_url.lstrip("/"))
    filename = excel_file or ("换图任务_%s.xlsx" % job_id)
    output_path = available_output_path(filename)
    request = Request(
        url,
        headers={"Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    )
    with urlopen(request, timeout=60) as response, open(output_path, "wb") as output:
        output.write(response.read())

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("downloaded Excel is empty")
    return output_path


def polling_loop():
    log("polling server: %s (listener=%s)" % (SERVER_URL, LISTENER_ID))
    update_state(status="waiting")

    while True:
        try:
            response = request_json(
                "GET",
                "/api/swap-tasks/pending?listener_id=%s" % quote(LISTENER_ID),
                timeout=15,
            )
            task = response.get("task")
            if not task:
                update_state(status="waiting", last_error="")
                time.sleep(POLL_INTERVAL)
                continue

            job_id = task["job_id"]
            update_state(status="downloading", last_job_id=job_id, last_error="")
            log("job=%s claimed, downloading Excel" % job_id)

            try:
                output_path = download_excel(
                    job_id,
                    task["excel_url"],
                    task.get("excel_file", ""),
                )
                report_status(
                    job_id,
                    "done",
                    "excel_downloaded",
                    received_excel=output_path,
                    message="Excel 已由接收电脑下载",
                )
                state = get_state()
                update_state(
                    status="waiting",
                    last_job_id=job_id,
                    last_excel=output_path,
                    last_error="",
                    downloaded_count=state["downloaded_count"] + 1,
                )
                log("job=%s Excel received: %s" % (job_id, output_path))
            except Exception as error:
                report_status(
                    job_id,
                    "failed",
                    "excel_download_failed",
                    error=str(error),
                )
                update_state(status="waiting", last_job_id=job_id, last_error=str(error))
                log("job=%s download failed: %s" % (job_id, error))
        except Exception as error:
            update_state(status="connection_error", last_error=str(error))
            log("poll failed: %s" % error)
            time.sleep(POLL_INTERVAL)


class StatusHandler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *args):
        return

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/status":
            self.send_json(get_state())
            return
        if path == "/log":
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as file:
                    lines = file.readlines()[-100:]
            except Exception:
                lines = []
            self.send_json({"log": lines})
            return
        self.send_json({"error": "Not found"}, 404)


def main():
    log("Excel receiver start, status port %d" % PORT)
    log("Python: %s" % sys.executable)
    threading.Thread(target=polling_loop, daemon=True).start()
    server = HTTPServer(("127.0.0.1", PORT), StatusHandler)
    log("local status: http://127.0.0.1:%d/status" % PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
        server.shutdown()


if __name__ == "__main__":
    main()
