"""
测图数据看板 — FastAPI 后端（v2 — 适配新20列统一格式）
端口 8766，CORS 全开，数据来自 data_loader.py
"""
import sys
import os
import json
import re
import threading
import uuid
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from data_loader import (
    load_all_data, get_summary, get_products, get_creatives_by_product,
    get_trends, get_product_aggregates
)
from swap_workbook import build_swap_workbook

app = FastAPI(title="测图数据看板 API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'index.html')

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    with open(FRONTEND_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    resp = HTMLResponse(content=content)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.get("/api/summary")
def api_summary(date_from: str = Query(None), date_to: str = Query(None), store: str = Query(None)):
    data = load_all_data()
    return get_summary(data, date_from=date_from, date_to=date_to, store=store)


@app.get("/api/products")
def api_products():
    data = load_all_data()
    return get_products(data)


@app.get("/api/products/aggregates")
def api_product_aggregates():
    data = load_all_data()
    return get_product_aggregates(data)


@app.get("/api/creatives")
def api_creatives(product_id: str = Query(...), date: str = Query(None)):
    """某商品的所有创意明细（含推广创意URL、图片类型等新字段），可选日期筛选"""
    data = load_all_data()
    creatives = get_creatives_by_product(product_id, data)
    if date:
        creatives = [c for c in creatives if c.get('date') == date]
    creatives.sort(key=lambda x: x.get('impressions', 0) or 0, reverse=True)

    product_info = next((p for p in data['products'] if p['product_id'] == product_id), {})

    # 该商品有数据的日期列表
    all_creatives = get_creatives_by_product(product_id, data)
    available_dates = sorted(set(c.get('date') for c in all_creatives if c.get('date') and (
        (c.get('impressions', 0) or 0) > 0 or (c.get('transaction_amount', 0) or 0) > 0
    )))

    # 返回简化版（去掉内部标记字段）
    clean = []
    for c in creatives:
        clean.append({
            'image_url': c.get('image_url', ''),
            'image_type': c.get('image_type', ''),
            'status': c.get('status', ''),
            'impressions': c.get('impressions', 0) or 0,
            'clicks': c.get('clicks', 0) or 0,
            'ctr': round(c.get('ctr', 0) or 0, 4),
            'conversion_rate': round(c.get('conversion_rate', 0) or 0, 4),
            'transaction_amount': c.get('transaction_amount', 0) or 0,
            'order_count': c.get('order_count', 0) or 0,
            'net_transaction': c.get('net_transaction', 0) or 0,
            'net_order_count': c.get('net_order_count', 0) or 0,
            'date': c.get('date', ''),
        })
    return {
        'product_id': product_id,
        'product_title': product_info.get('product_title', ''),
        'brand': product_info.get('brand', ''),
        'product_code': product_info.get('product_code', ''),
        'store_name': product_info.get('store_name', ''),
        'creative_count': len(creatives),
        'available_dates': available_dates,
        'selected_date': date,
        'creatives': clean,
    }


@app.get("/api/trends")
def api_trends(
    product_id: str = Query(None),
    metric: str = Query('transaction_amount'),
    date_from: str = Query(None),
    date_to: str = Query(None),
):
    data = load_all_data()
    trends = get_trends(product_id, metric, data, date_from=date_from, date_to=date_to)
    return {
        'product_id': product_id or 'all',
        'metric': metric,
        'trends': trends,
    }


@app.get("/api/roi")
def api_roi(
    store: str = Query(None),
    product_id: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
):
    """ROI 分析：按日期聚合交易额/订单/点击/曝光（新格式无花费数据，用投产效率替代）"""
    data = load_all_data()
    records = data['records']
    if store:
        records = [r for r in records if r.get('store_name') == store]
    if product_id:
        records = [r for r in records if r.get('product_id') == product_id]
    if date_from:
        records = [r for r in records if r.get('date', '') >= date_from]
    if date_to:
        records = [r for r in records if r.get('date', '') <= date_to]

    from collections import defaultdict
    by_date = defaultdict(lambda: {'transaction': 0, 'orders': 0, 'clicks': 0, 'impressions': 0, 'net_transaction': 0})
    for r in records:
        d = r.get('date')
        if not d:
            continue
        by_date[d]['transaction'] += r.get('transaction_amount', 0) or 0
        by_date[d]['orders'] += r.get('order_count', 0) or 0
        by_date[d]['clicks'] += r.get('clicks', 0) or 0
        by_date[d]['impressions'] += r.get('impressions', 0) or 0
        by_date[d]['net_transaction'] += r.get('net_transaction', 0) or 0

    result = []
    for d, v in sorted(by_date.items()):
        ctr = round(v['clicks'] / v['impressions'], 4) if v['impressions'] > 0 else 0
        cvr = round(v['orders'] / v['clicks'], 4) if v['clicks'] > 0 else 0
        result.append({
            'date': d,
            'transaction': round(v['transaction'], 2),
            'net_transaction': round(v['net_transaction'], 2),
            'orders': v['orders'],
            'clicks': v['clicks'],
            'impressions': v['impressions'],
            'ctr': ctr,
            'cvr': cvr,
        })

    return {
        'store': store,
        'product_id': product_id,
        'roi_data': result,
    }


@app.get("/api/stores")
def api_stores():
    data = load_all_data()
    store_counts = {}
    for r in data['records']:
        s = r.get('store_name', '未知')
        store_counts[s] = store_counts.get(s, 0) + 1
    return [{'name': k, 'creative_count': v} for k, v in sorted(store_counts.items())]


@app.get("/api/dates")
def api_dates():
    data = load_all_data()
    return data['dates']


# ===== 换图 =====

TOTAL_IMAGE_SLOTS = 10
SLOT_IMAGE_TYPES = {'主轮播图', '副轮播图'}
SWAP_TASK_DIR = os.path.abspath(os.getenv(
    "SWAP_TASK_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "swap_tasks"),
))
SWAP_TASK_LEASE_SECONDS = int(os.getenv("SWAP_TASK_LEASE_SECONDS", "180"))
SWAP_TASK_LOCK = threading.Lock()
os.makedirs(SWAP_TASK_DIR, exist_ok=True)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _task_json_path(job_id):
    if not re.fullmatch(r"[a-f0-9]{12}", str(job_id or "")):
        raise ValueError("invalid job id")
    return os.path.join(SWAP_TASK_DIR, f"{job_id}.json")


def _task_excel_path(job_id):
    _task_json_path(job_id)
    return os.path.join(SWAP_TASK_DIR, f"换图任务_{job_id}.xlsx")


def _read_task(job_id):
    path = _task_json_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _write_task(task):
    path = _task_json_path(task["job_id"])
    temp_path = path + ".tmp"
    task["updated_at"] = _utc_now()
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(task, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _get_product_info(product_id, data):
    records = [row for row in data.get("records", []) if str(row.get("product_id", "")) == str(product_id)]
    if not records:
        return {"store_name": "", "product_code": ""}
    representative = max(records, key=lambda row: row.get("date", "") or "")
    return {
        "store_name": representative.get("store_name", "") or "",
        "product_code": representative.get("product_code", "") or "",
    }


@app.get("/api/swap-image/products")
def api_swap_products(date: str = '', date_from: str = '', date_to: str = ''):
    """换图工作台商品列表：可按外部日期或日期区间筛选，否则展示最近日期。"""
    data = load_all_data()
    grouped = {}
    for record in data['records']:
        pid = record.get('product_id', '')
        if not pid:
            continue
        grouped.setdefault(pid, []).append(record)

    rows = []
    for pid, records in grouped.items():
        if date:
            latest = [r for r in records if r.get('date', '') == date]
        elif date_from or date_to:
            latest = [
                r for r in records
                if (not date_from or r.get('date', '') >= date_from)
                and (not date_to or r.get('date', '') <= date_to)
            ]
        else:
            latest_date = max((r.get('date', '') for r in records), default='')
            latest = [r for r in records if r.get('date', '') == latest_date] if latest_date else records
        if not latest:
            continue
        selected_date = max((r.get('date', '') for r in latest), default='')
        representative = max(
            latest,
            key=lambda r: (
                r.get('image_type') == '主轮播图',
                r.get('impressions', 0) or 0,
                r.get('clicks', 0) or 0,
            ),
        )
        impressions = sum(r.get('impressions', 0) or 0 for r in latest)
        clicks = sum(r.get('clicks', 0) or 0 for r in latest)
        orders = sum(r.get('order_count', 0) or 0 for r in latest)
        rows.append({
            'product_id': pid,
            'store_name': representative.get('store_name', ''),
            'brand': representative.get('brand', ''),
            'product_code': representative.get('product_code', ''),
            'product_title': representative.get('product_title', ''),
            'date': selected_date,
            'image_url': representative.get('image_url', ''),
            'impressions': impressions,
            'clicks': clicks,
            'ctr': round(clicks / impressions, 4) if impressions else 0,
            'conversion_rate': round(orders / clicks, 4) if clicks else 0,
            'image_count': len({r.get('image_url') for r in records if r.get('image_url')}),
        })

    return sorted(rows, key=lambda row: (row['date'], row['impressions']), reverse=True)


def _get_product_images(product_id, data):
    creatives = get_creatives_by_product(product_id, data)
    # 先按净交易额排序，确保去重时保留数据最好的那条
    creatives.sort(key=lambda x: (x.get('net_transaction', 0) or 0), reverse=True)
    main_images = []
    other_images = []
    seen_urls = set()
    for c in creatives:
        url = c.get('image_url', '')
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        img = {
            "image_url": url,
            "image_type": c.get('image_type', ''),
            "status": c.get('status', ''),
            "net_transaction": c.get('net_transaction', 0) or 0,
            "impressions": c.get('impressions', 0) or 0,
            "clicks": c.get('clicks', 0) or 0,
            "ctr": c.get('ctr', 0) or 0,
            "transaction_amount": c.get('transaction_amount', 0) or 0,
            "order_count": c.get('order_count', 0) or 0,
        }
        if c.get('image_type', '') in SLOT_IMAGE_TYPES:
            main_images.append(img)
        else:
            other_images.append(img)
    main_images.sort(key=lambda x: x['net_transaction'], reverse=True)
    other_images.sort(key=lambda x: x['net_transaction'], reverse=True)
    return main_images, other_images


@app.post("/api/swap-image/preview")
def api_swap_preview(payload: dict):
    data = load_all_data()
    source_id = payload.get('source_product_id', '')
    target_ids = payload.get('target_product_ids', [])
    target_image_urls = payload.get('target_image_urls', {}) or {}
    if not source_id or not target_ids:
        return {"error": "请选择源商品和至少一个目标商品"}

    source_main, source_other = _get_product_images(source_id, data)
    source_display_types = {'主轮播图', '副轮播图', '创意图'}
    source_images = [img for img in source_main + source_other if img.get('image_type', '') in source_display_types]
    if not source_images:
        return {"error": f"找不到源商品 {source_id} 的创意数据"}

    targets = []
    for tid in target_ids:
        target_main, target_other = _get_product_images(tid, data)
        has_data = len(target_main)
        empty_slots = max(0, TOTAL_IMAGE_SLOTS - has_data)
        available_images = target_main + target_other
        available_by_url = {img.get('image_url'): img for img in available_images}
        requested_urls = list(dict.fromkeys(target_image_urls.get(tid, [])))
        selected_images = [available_by_url[url] for url in requested_urls if url in available_by_url]
        targets.append({
            "product_id": tid,
            "has_data_count": has_data,
            "empty_slots": empty_slots,
            "main_images": target_main,
            "other_images": target_other,
            "selected_images": selected_images,
            "selected_count": len(selected_images),
        })

    return {
        "source_images": source_images,
        "source_id": source_id,
        "targets": targets,
        "plan": f"将源商品 {source_id} 的图替换到 {len(targets)} 个目标商品的指定图片位",
        "supports_target_selection": True,
        "supports_server_queue": True,
    }


@app.post("/api/swap-image/execute")
def api_swap_execute(payload: dict):
    source_id = payload.get('source_product_id', '')
    source_image_urls = payload.get('source_image_urls', [])
    target_ids = payload.get('target_product_ids', [])
    target_image_urls = payload.get('target_image_urls', {}) or {}
    if not source_id or not target_ids or not source_image_urls:
        return {"success": False, "error": "缺少参数"}

    data = load_all_data()
    source_main, source_other = _get_product_images(source_id, data)
    source_all = source_main + source_other
    source_by_url = {img['image_url']: img for img in source_all}
    source_image_urls = list(dict.fromkeys(source_image_urls))
    source_imgs = [source_by_url[url] for url in source_image_urls if url in source_by_url]
    if not source_imgs:
        return {"success": False, "error": "找不到源图片"}

    source_info = _get_product_info(source_id, data)
    main_rows = [{
        "store_name": source_info["store_name"],
        "product_id": source_id,
        "image_url": img["image_url"],
        "product_code": source_info["product_code"],
        "operator": "",
    } for img in source_imgs]

    targets = []
    replacement_rows = []
    for tid in target_ids:
        target_main, target_other = _get_product_images(tid, data)
        available_by_url = {img['image_url']: img for img in target_main + target_other}
        requested_urls = list(dict.fromkeys(target_image_urls.get(tid, [])))
        selected_urls = [url for url in requested_urls if url in available_by_url]
        if not requested_urls:
            return {"success": False, "error": f"请选择目标商品 {tid} 需要替换的图片"}
        if requested_urls and not selected_urls:
            return {"success": False, "error": f"目标商品 {tid} 找不到所选图片"}
        if len(selected_urls) != len(requested_urls):
            return {"success": False, "error": f"目标商品 {tid} 的部分所选图片已失效，请重新选择"}
        if len(selected_urls) > len(source_imgs):
            return {"success": False, "error": f"目标商品 {tid} 选择了 {len(selected_urls)} 张图片，但源图只有 {len(source_imgs)} 张"}
        data_slots = len([img for img in target_main
                          if (img.get('impressions', 0) or 0) > 0
                          or (img.get('clicks', 0) or 0) > 0
                          or (img.get('transaction_amount', 0) or 0) > 0])
        empty_slots = max(0, TOTAL_IMAGE_SLOTS - data_slots)
        targets.append({
            "product_id": tid,
            "empty_slots": empty_slots,
            "replace_image_urls": selected_urls,
            "replace_count": len(selected_urls),
        })
        target_info = _get_product_info(tid, data)
        replacement_rows.extend({
            "store_name": target_info["store_name"],
            "product_id": tid,
            "image_url": url,
            "product_code": target_info["product_code"],
            "operator": "",
        } for url in selected_urls)

    swap_command = {
        "action": "swap_image",
        "source": {
            "product_id": source_id,
            "images": [{"image_url": img["image_url"], "image_type": img.get('image_type', '')}
                       for img in source_imgs],
        },
        "targets": targets,
    }

    try:
        job_id = uuid.uuid4().hex[:12]
        excel_path = _task_excel_path(job_id)
        build_swap_workbook(main_rows, replacement_rows, excel_path)
        task = {
            "job_id": job_id,
            "status": "queued",
            "phase": "waiting_listener",
            "created_at": _utc_now(),
            "claimed_at": "",
            "claimed_by": "",
            "excel_file": os.path.basename(excel_path),
            "source_count": len(main_rows),
            "target_count": len(replacement_rows),
            "command": swap_command,
        }
        with SWAP_TASK_LOCK:
            _write_task(task)
        return {
            "success": True,
            "job_id": job_id,
            "status": "queued",
            "message": "任务已提交，等待监听电脑接收 Excel",
            "excel_file": task["excel_file"],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/swap-image/status/{job_id}")
def api_swap_status(job_id: str):
    """Return the server-side queue and Excel receiving progress for a swap task."""
    try:
        task = _read_task(job_id)
        if not task:
            return {"status": "not_found", "error": "Job not found"}
        return {key: value for key, value in task.items() if key != "command"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/swap-tasks/pending")
def api_swap_task_pending(listener_id: str = Query(default="")):
    """Claim the oldest queued task. The listener polls this endpoint."""
    listener_id = (listener_id or "unnamed-listener").strip()[:100]
    now = datetime.now(timezone.utc)
    with SWAP_TASK_LOCK:
        candidates = []
        for filename in os.listdir(SWAP_TASK_DIR):
            if not filename.endswith(".json"):
                continue
            try:
                task = _read_task(filename[:-5])
                if not task:
                    continue
                if task.get("status") == "claimed" and task.get("claimed_at"):
                    claimed_at = datetime.fromisoformat(task["claimed_at"])
                    if (now - claimed_at).total_seconds() > SWAP_TASK_LEASE_SECONDS:
                        task["status"] = "queued"
                        task["phase"] = "listener_lease_expired"
                        task["claimed_at"] = ""
                        task["claimed_by"] = ""
                        _write_task(task)
                if task.get("status") == "queued":
                    candidates.append(task)
            except Exception:
                continue

        if not candidates:
            return {"task": None}

        task = min(candidates, key=lambda item: item.get("created_at", ""))
        task["status"] = "claimed"
        task["phase"] = "excel_received"
        task["claimed_at"] = _utc_now()
        task["claimed_by"] = listener_id
        _write_task(task)

    return {
        "task": {
            "job_id": task["job_id"],
            "status": task["status"],
            "excel_file": task["excel_file"],
            "excel_url": f"/api/swap-tasks/{task['job_id']}/excel",
            "command": task["command"],
        }
    }


@app.get("/api/swap-tasks/{job_id}/excel")
def api_swap_task_excel(job_id: str):
    task = _read_task(job_id)
    excel_path = _task_excel_path(job_id)
    if not task or not os.path.exists(excel_path):
        return {"error": "Excel file not found"}
    return FileResponse(
        excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=task.get("excel_file") or os.path.basename(excel_path),
    )


@app.post("/api/swap-tasks/{job_id}/status")
def api_swap_task_status(job_id: str, payload: dict):
    allowed_statuses = {"claimed", "pending", "running", "done", "failed", "stopped"}
    with SWAP_TASK_LOCK:
        task = _read_task(job_id)
        if not task:
            return {"success": False, "error": "Job not found"}
        status = payload.get("status", task.get("status", "claimed"))
        if status not in allowed_statuses:
            return {"success": False, "error": "Invalid status"}
        protected = {"job_id", "command", "excel_file", "created_at"}
        for key, value in payload.items():
            if key not in protected:
                task[key] = value
        task["status"] = status
        _write_task(task)
    return {"success": True, "job_id": job_id, "status": status}


if __name__ == '__main__':
    import uvicorn
    print("Starting 测图数据看板 on http://127.0.0.1:8766")
    uvicorn.run(app, host="0.0.0.0", port=8766)
