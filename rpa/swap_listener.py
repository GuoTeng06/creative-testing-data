# -*- coding: utf-8 -*-
"""FIFO receiver that only downloads the latest swap-task Excel file."""
import json
import os
import re
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
FIXED_EXCEL_NAME = "换图任务.xlsx"
REQUEST_DIR = os.path.abspath(os.path.expandvars(
    os.getenv("SWAP_REQUEST_DIR", r"C:\Listen_for_requests")
))
LOG_FILE = os.path.join(REQUEST_DIR, "swap_log.txt")
FINAL_EXCEL_PATH = os.path.join(REQUEST_DIR, FIXED_EXCEL_NAME)
os.makedirs(REQUEST_DIR, exist_ok=True)

STATE_LOCK = threading.Lock()
STATE = {
    "status": "starting",
    "listener_id": LISTENER_ID,
    "server_url": SERVER_URL,
    "last_job_id": "",
    "last_request_json": "",
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
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
    print(line, flush=True)
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
    payload = {"status": status, "phase": phase, "listener_id": LISTENER_ID}
    payload.update(extra)
    try:
        request_json("POST", "/api/swap-tasks/%s/status" % job_id, payload, timeout=10)
    except Exception as error:
        log("job=%s status report failed: %s" % (job_id, error))


def save_task_json(task):
    job_id = str(task.get("job_id", "")).strip()
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise ValueError("task JSON contains an invalid job_id")
    output_path = os.path.join(REQUEST_DIR, "swap_task_%s.json" % job_id)
    temp_path = output_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as output:
        json.dump(task, output, ensure_ascii=False, indent=2)
    os.replace(temp_path, output_path)
    return output_path


def download_excel(job_id, excel_url):
    if os.path.exists(FINAL_EXCEL_PATH):
        raise RuntimeError(
            "%s still exists; waiting for the previous task to be consumed"
            % FINAL_EXCEL_PATH
        )
    url = urljoin(SERVER_URL + "/", excel_url.lstrip("/"))
    temp_path = os.path.join(REQUEST_DIR, ".换图任务.%s.tmp.xlsx" % job_id)
    request = Request(
        url,
        headers={"Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    )
    with urlopen(request, timeout=60) as response, open(temp_path, "wb") as output:
        output.write(response.read())
    if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
        raise RuntimeError("downloaded Excel is empty")
    if os.path.exists(FINAL_EXCEL_PATH):
        os.remove(temp_path)
        raise RuntimeError(
            "%s appeared while downloading; the task will be retried"
            % FINAL_EXCEL_PATH
        )
    os.replace(temp_path, FINAL_EXCEL_PATH)
    return FINAL_EXCEL_PATH


def process_task(task):
    job_id = task["job_id"]
    request_json_path = save_task_json(task)
    update_state(
        status="downloading",
        last_job_id=job_id,
        last_request_json=request_json_path,
        last_error="",
    )
    log("job=%s claimed from FIFO queue" % job_id)
    output_path = download_excel(job_id, task["excel_url"])
    report_status(
        job_id,
        "done",
        "excel_downloaded",
        received_excel=output_path,
        received_request_json=request_json_path,
        message="Excel downloaded by receiver computer",
    )
    state = get_state()
    update_state(
        status="waiting_excel_consumed",
        last_job_id=job_id,
        last_excel=output_path,
        last_error="",
        downloaded_count=state["downloaded_count"] + 1,
    )
    log("job=%s Excel received: %s" % (job_id, output_path))


def polling_loop():
    log("polling server: %s (listener=%s)" % (SERVER_URL, LISTENER_ID))
    update_state(status="waiting")
    waiting_for_consumption = False
    while True:
        try:
            if os.path.exists(FINAL_EXCEL_PATH):
                if not waiting_for_consumption:
                    log(
                        "queue paused: waiting for previous file to be moved or deleted: %s"
                        % FINAL_EXCEL_PATH
                    )
                waiting_for_consumption = True
                update_state(
                    status="waiting_excel_consumed",
                    last_excel=FINAL_EXCEL_PATH,
                    last_error="",
                )
                time.sleep(POLL_INTERVAL)
                continue
            if waiting_for_consumption:
                log("previous Excel consumed; resuming FIFO queue")
                waiting_for_consumption = False
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
            try:
                process_task(task)
            except Exception as error:
                report_status(
                    task["job_id"],
                    "claimed",
                    "excel_download_failed",
                    error=str(error),
                    message="Task returns to the FIFO queue after the claim lease expires",
                )
                update_state(status="waiting", last_job_id=task["job_id"], last_error=str(error))
                log("job=%s download failed: %s" % (task["job_id"], error))
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
    log("Excel-only receiver start, status port %d" % PORT)
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
