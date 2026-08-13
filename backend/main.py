"""
测图数据看板 — FastAPI 后端（v2 — 适配新20列统一格式）
端口 8766，CORS 全开，数据来自 data_loader.py
"""
import sys
import os
import json
import re
import random
import threading
import uuid
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from data_loader import (
    load_all_data, get_summary, get_products, get_creatives_by_product,
    get_trends, get_product_aggregates, get_store_aggregates, get_brand_trends
)
from swap_workbook import build_swap_workbook, read_swap_workbook

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
def api_summary(
    date_from: str = Query(None),
    date_to: str = Query(None),
    store: str = Query(None),
    brand: str = Query(None),
    include_empty: bool = Query(False),
):
    data = load_all_data(include_empty=include_empty)
    return get_summary(data, date_from=date_from, date_to=date_to, store=store, brand=brand)


@app.get("/api/products")
def api_products():
    data = load_all_data()
    return get_products(data)


@app.get("/api/products/aggregates")
def api_product_aggregates(
    date: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    include_empty: bool = Query(False),
):
    data = load_all_data(include_empty=include_empty)
    return get_product_aggregates(data, date=date, date_from=date_from, date_to=date_to)


@app.get("/api/creatives")
def api_creatives(
    product_id: str = Query(...),
    date: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    include_empty: bool = Query(False),
):
    """某商品的所有创意明细，支持单日或日期范围筛选。"""
    data = load_all_data(include_empty=include_empty)
    all_creatives = get_creatives_by_product(product_id, data)
    creatives = all_creatives
    if date or date_from or date_to:
        if date:
            date_from = date_to = date
        creatives = [
            c for c in all_creatives
            if (not date_from or c.get('date', '') >= date_from)
            and (not date_to or c.get('date', '') <= date_to)
        ]
        if include_empty:
            # 日期范围只改变指标口径，不改变商品的完整图片集合。若某张图在所选
            # 范围没有记录，补一条 0 指标记录，方便运营仍能看到并识别该图位。
            dated_urls = {str(c.get('image_url', '') or '').strip() for c in creatives}
            representatives = {}
            for c in all_creatives:
                image_url = str(c.get('image_url', '') or '').strip()
                if not image_url:
                    continue
                current = representatives.get(image_url)
                score = (
                    c.get('impressions', 0) or 0,
                    c.get('clicks', 0) or 0,
                    c.get('transaction_amount', 0) or 0,
                )
                if current is None or score > current[0]:
                    representatives[image_url] = (score, c)
            for image_url, (_, representative) in representatives.items():
                if image_url in dated_urls:
                    continue
                empty_row = dict(representative)
                empty_row.update({
                    'date': date_to or date_from or '',
                    'metrics_empty': True,
                    'impressions': 0,
                    'clicks': 0,
                    'ctr': 0,
                    'conversion_rate': 0,
                    'transaction_amount': 0,
                    'order_count': 0,
                    'avg_order_amount': 0,
                    'net_transaction': 0,
                    'net_order_count': 0,
                })
                creatives.append(empty_row)
    creatives.sort(key=lambda x: x.get('impressions', 0) or 0, reverse=True)

    product_info = next((p for p in data['products'] if p['product_id'] == product_id), {})

    # 该商品有数据的日期列表
    # 换图工作台请求 include_empty=true 时，日期下拉也必须包含只有 0 指标的日期；
    # 否则用户虽然能拿到图片记录，却无法通过日期筛选定位到它们。
    available_dates = sorted(set(c.get('date') for c in all_creatives if c.get('date') and (
        include_empty or
        (c.get('impressions', 0) or 0) > 0 or (c.get('transaction_amount', 0) or 0) > 0
    )))

    # 返回简化版（去掉内部标记字段）
    clean = []
    for c in creatives:
        clean.append({
            'image_url': c.get('image_url', ''),
            'image_type': c.get('image_type', ''),
            'status': c.get('status', ''),
            'metrics_empty': bool(c.get('metrics_empty', False)),
            'impressions': c.get('impressions', 0) or 0,
            'clicks': c.get('clicks', 0) or 0,
            'ctr': round(c.get('ctr', 0) or 0, 4),
            'conversion_rate': round(c.get('conversion_rate', 0) or 0, 4),
            'transaction_amount': c.get('transaction_amount', 0) or 0,
            'order_count': c.get('order_count', 0) or 0,
            'avg_order_amount': c.get('avg_order_amount', 0) or 0,
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


@app.get("/api/stores/aggregates")
def api_store_aggregates(
    date_from: str = Query(None),
    date_to: str = Query(None),
    store: str = Query(None),
    brand: str = Query(None),
    include_empty: bool = Query(False),
):
    """按店铺返回概览指标，供 KPI 卡片的明细弹窗使用。"""
    data = load_all_data(include_empty=include_empty)
    return get_store_aggregates(data, date_from=date_from, date_to=date_to, store=store, brand=brand)


@app.get("/api/brands/trends")
def api_brand_trends(
    metric: str = Query('impressions'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    store: str = Query(None),
    brand: str = Query(None),
    include_empty: bool = Query(False),
):
    """按品牌返回每日核心指标趋势。"""
    data = load_all_data(include_empty=include_empty)
    return get_brand_trends(
        data,
        metric=metric,
        date_from=date_from,
        date_to=date_to,
        store=store,
        brand=brand,
    )


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
FIXED_SWAP_EXCEL_NAME = "换图任务.xlsx"
os.makedirs(SWAP_TASK_DIR, exist_ok=True)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _task_json_path(job_id):
    if not re.fullmatch(r"[a-f0-9]{12}", str(job_id or "")):
        raise ValueError("invalid job id")
    return os.path.join(SWAP_TASK_DIR, f"{job_id}.json")


def _task_excel_path(job_id):
    """Keep the fixed Excel filename isolated inside its task directory."""
    _task_json_path(job_id)
    return os.path.join(SWAP_TASK_DIR, job_id, FIXED_SWAP_EXCEL_NAME)


def _legacy_task_excel_path(excel_file):
    """Resolve files created before per-task directories were introduced."""
    safe_name = os.path.basename(str(excel_file or ""))
    if safe_name != str(excel_file or "") or not re.fullmatch(r"换图任务_[A-Za-z0-9_-]+\.xlsx", safe_name):
        return ""
    return os.path.join(SWAP_TASK_DIR, safe_name)


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
    data = load_all_data(include_empty=True)
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
    # 同一个链接可能有多天记录。先按图片地址聚合，保留指标为 0 的图片，
    # 再按“1 张主图 + 9 张轮播图”的商品图片位顺序截取，避免按日期重复显示。
    by_url = {}
    for c in creatives:
        url = str(c.get('image_url', '') or '').strip()
        if not url:
            continue
        score = (
            c.get('net_transaction', 0) or 0,
            c.get('impressions', 0) or 0,
            c.get('clicks', 0) or 0,
            c.get('order_count', 0) or 0,
        )
        current = by_url.get(url)
        if current is None:
            current = {
                "image_url": url,
                "image_type": c.get('image_type', '') or '',
                "status": c.get('status', '') or '',
                "net_transaction": 0,
                "impressions": 0,
                "clicks": 0,
                "ctr": 0,
                "transaction_amount": 0,
                "order_count": 0,
                "_best_score": score,
            }
            by_url[url] = current
        current['net_transaction'] += c.get('net_transaction', 0) or 0
        current['impressions'] += c.get('impressions', 0) or 0
        current['clicks'] += c.get('clicks', 0) or 0
        current['transaction_amount'] += c.get('transaction_amount', 0) or 0
        current['order_count'] += c.get('order_count', 0) or 0
        if score > current['_best_score']:
            current['_best_score'] = score
            current['image_type'] = c.get('image_type', '') or current['image_type']
            current['status'] = c.get('status', '') or current['status']
        current['ctr'] = current['clicks'] / current['impressions'] if current['impressions'] else 0

    images = list(by_url.values())
    for image in images:
        image.pop('_best_score', None)
    rank = lambda image: (
        image.get('net_transaction', 0),
        image.get('impressions', 0),
        image.get('clicks', 0),
    )
    main_types = {'主图', '主轮播图'}
    carousel_types = {'轮播图', '副轮播图'}
    primary = sorted((img for img in images if img.get('image_type') in main_types), key=rank, reverse=True)
    carousel = sorted((img for img in images if img.get('image_type') in carousel_types), key=rank, reverse=True)
    selected = []
    if primary:
        selected.append(primary[0])
    selected.extend(carousel[:9])
    # 数据源偶尔没有图片类型标记，或轮播图不足 9 张；用剩余唯一图片补足展示位。
    selected_urls = {img['image_url'] for img in selected}
    remaining = sorted((img for img in images if img['image_url'] not in selected_urls), key=rank, reverse=True)
    selected.extend(remaining[:max(0, TOTAL_IMAGE_SLOTS - len(selected))])
    selected = selected[:TOTAL_IMAGE_SLOTS]
    selected_urls = {img['image_url'] for img in selected}
    other_images = [img for img in images if img['image_url'] not in selected_urls]
    return selected, other_images


@app.post("/api/swap-image/preview")
def api_swap_preview(payload: dict):
    data = load_all_data(include_empty=True)
    source_id = payload.get('source_product_id', '')
    target_ids = payload.get('target_product_ids', [])
    target_image_urls = payload.get('target_image_urls', {}) or {}
    if not source_id or not target_ids:
        return {"error": "请选择源商品和至少一个目标商品"}

    source_main, source_other = _get_product_images(source_id, data)
    # _get_product_images 已经完成“1 主图 + 9 轮播图”的唯一图片位筛选，
    # 不再按是否有指标或类型标签过滤，确保空数据图片也能被选择。
    source_images = source_main
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


@app.post("/api/swap-image/auto-plan")
def api_swap_auto_plan(payload: dict):
    """Build a one-click swap plan for the same product code.

    The source selection determines the image types and counts. Target links
    are limited to other products with the same code and zero impressions on
    the matching image type. Carousel candidates are shuffled deterministically
    so repeated previews do not unexpectedly change the plan.
    """
    data = load_all_data(include_empty=True)
    source_id = str(payload.get('source_product_id', '') or '')
    source_urls = list(dict.fromkeys(str(url).strip() for url in (payload.get('source_image_urls') or []) if str(url).strip()))
    if not source_id or not source_urls:
        return {"success": False, "error": "请先选择源商品和源图片"}
    source_records = [row for row in data.get('records', []) if str(row.get('product_id', '')) == source_id]
    source_info = _get_product_info(source_id, data)
    if not source_records:
        return {"success": False, "error": f"找不到源商品 {source_id}"}
    source_code = source_info.get('product_code', '') or next((row.get('product_code', '') for row in source_records if row.get('product_code')), '')
    if not source_code:
        return {"success": False, "error": "源商品没有商品编码，无法按编码匹配"}

    source_main, source_other = _get_product_images(source_id, data)
    source_by_url = {img.get('image_url'): img for img in source_main + source_other}
    source_images = [source_by_url[url] for url in source_urls if url in source_by_url]
    if not source_images:
        return {"success": False, "error": "找不到已选源图片，请重新选择"}

    def is_main(image):
        return image.get('image_type') in {'主图', '主轮播图'}

    def is_carousel(image):
        return image.get('image_type') in {'轮播图', '副轮播图'}

    requested_main = sum(1 for image in source_images if is_main(image))
    requested_carousel = sum(1 for image in source_images if is_carousel(image))
    requested_other = len(source_images) - requested_main - requested_carousel
    products = {}
    for record in data.get('records', []):
        pid = str(record.get('product_id', '') or '')
        if not pid or pid == source_id or record.get('product_code', '') != source_code:
            continue
        products.setdefault(pid, []).append(record)

    def image_groups(records):
        by_url = {}
        for record in records:
            url = str(record.get('image_url', '') or '').strip()
            if not url:
                continue
            current = by_url.get(url)
            score = (record.get('impressions', 0) or 0, record.get('clicks', 0) or 0, record.get('transaction_amount', 0) or 0)
            if current is None or score > current['_score']:
                by_url[url] = {**record, 'image_url': url, '_score': score}
        values = list(by_url.values())
        return [x for x in values if is_main(x)], [x for x in values if is_carousel(x)], values

    def pick_zero(images, count, randomize=False):
        candidates = [image for image in images if (image.get('impressions', 0) or 0) == 0]
        if len(candidates) < count:
            return []
        if randomize:
            return random.SystemRandom().sample(candidates, count)
        return sorted(candidates, key=lambda image: image.get('image_url', ''))[:count]

    targets = []
    for pid, records in products.items():
        main, carousel, all_images = image_groups(records)
        picked = []
        picked.extend(pick_zero(main, requested_main))
        picked.extend(pick_zero(carousel, requested_carousel, randomize=True))
        if requested_other:
            others = [image for image in all_images if not is_main(image) and not is_carousel(image)]
            picked.extend(pick_zero(others, requested_other, randomize=True))
        if len(picked) != len(source_images):
            continue
        # Keep the target entry only when every requested slot is zero-exposure.
        if any((image.get('impressions', 0) or 0) != 0 for image in picked):
            continue
        targets.append({
            'product_id': pid,
            'store_name': records[0].get('store_name', ''),
            'brand': records[0].get('brand', ''),
            'product_code': source_code,
            'product_title': records[0].get('product_title', ''),
            'selected_images': [{k: image.get(k, '') for k in ('image_url', 'image_type', 'impressions', 'clicks')} for image in picked],
            'selected_count': len(picked),
        })
    targets.sort(key=lambda item: (item.get('store_name', ''), item.get('product_id', '')))
    return {
        'success': True,
        'source_product_id': source_id,
        'product_code': source_code,
        'source_image_count': len(source_images),
        'source_image_types': [image.get('image_type', '') for image in source_images],
        'target_count': len(targets),
        'targets': targets,
        'target_image_urls': {item['product_id']: [image['image_url'] for image in item['selected_images']] for item in targets},
    }


@app.post("/api/swap-image/execute")
def api_swap_execute(payload: dict):
    source_id = payload.get('source_product_id', '')
    source_image_urls = payload.get('source_image_urls', [])
    target_ids = payload.get('target_product_ids', [])
    target_image_urls = payload.get('target_image_urls', {}) or {}
    operator = str(payload.get('operator', '') or '').strip()[:100]
    if not source_id or not target_ids or not source_image_urls:
        return {"success": False, "error": "缺少参数"}

    data = load_all_data(include_empty=True)
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
        "operator": operator,
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
        target_info = _get_product_info(tid, data)
        targets.append({
            "product_id": tid,
            "product_code": target_info.get("product_code", ""),
            "empty_slots": empty_slots,
            "replace_image_urls": selected_urls,
            "replace_count": len(selected_urls),
        })
        replacement_rows.extend({
            "store_name": target_info["store_name"],
            "product_id": tid,
            "image_url": url,
            "product_code": target_info["product_code"],
            "operator": operator,
        } for url in selected_urls)

    swap_command = {
        "action": "swap_image",
        "operator": operator,
        "source": {
            "product_id": source_id,
            "product_code": source_info.get("product_code", ""),
            "images": [{"image_url": img["image_url"], "image_type": img.get('image_type', '')}
                       for img in source_imgs],
        },
        "targets": targets,
    }

    try:
        job_id = uuid.uuid4().hex[:12]
        with SWAP_TASK_LOCK:
            excel_path = _task_excel_path(job_id)
            os.makedirs(os.path.dirname(excel_path), exist_ok=True)
            build_swap_workbook(main_rows, replacement_rows, excel_path)
            task = {
                "job_id": job_id,
                "status": "queued",
                "phase": "waiting_listener",
                "created_at": _utc_now(),
                "claimed_at": "",
                "claimed_by": "",
                "excel_file": FIXED_SWAP_EXCEL_NAME,
                "operator": operator,
                "source_count": len(main_rows),
                "target_count": len(replacement_rows),
                "workbook_rows": {
                    "主图数据": main_rows,
                    "替换数据": replacement_rows,
                },
                "command": swap_command,
            }
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
        task["phase"] = "claimed_by_listener"
        task["claimed_at"] = _utc_now()
        task["claimed_by"] = listener_id
        _write_task(task)

    return {
        "task": {
            "job_id": task["job_id"],
            "status": task["status"],
            "excel_file": task["excel_file"],
            "excel_url": f"/api/swap-tasks/{task['job_id']}/excel",
            "operator": task.get("operator", ""),
            "created_at": task.get("created_at", ""),
            "command": task["command"],
        }
    }


@app.get("/api/swap-tasks/records")
def api_swap_task_records(limit: int = Query(default=500, ge=1, le=2000)):
    """Return task audit records for the submitter-computer MySQL recorder."""
    records = []
    with SWAP_TASK_LOCK:
        for filename in os.listdir(SWAP_TASK_DIR):
            if not filename.endswith(".json"):
                continue
            try:
                task = _read_task(filename[:-5])
                if task:
                    if not task.get("workbook_rows"):
                        excel_path = _task_excel_path(task["job_id"])
                        if not os.path.exists(excel_path):
                            excel_path = _legacy_task_excel_path(task.get("excel_file", ""))
                        if excel_path and os.path.exists(excel_path):
                            task["workbook_rows"] = read_swap_workbook(excel_path)
                            if "operator" not in task:
                                all_rows = sum(task["workbook_rows"].values(), [])
                                task["operator"] = next(
                                    (row.get("operator", "") for row in all_rows
                                     if row.get("operator")),
                                    "",
                                )
                            _write_task(task)
                    records.append(task)
            except Exception:
                continue
    records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"records": records[:limit], "count": min(len(records), limit)}


@app.get("/api/swap-tasks/queue")
def api_swap_task_queue(limit: int = Query(default=50, ge=1, le=200)):
    """Return compact queue items for the swap workspace."""
    # Older task records did not persist product codes; resolve them from the
    # current dataset so the queue remains readable after the schema update.
    try:
        product_data = load_all_data(include_empty=True)
    except Exception:
        product_data = {"records": []}

    tasks = []
    with SWAP_TASK_LOCK:
        for filename in os.listdir(SWAP_TASK_DIR):
            if not filename.endswith(".json"):
                continue
            try:
                task = _read_task(filename[:-5])
                if task:
                    tasks.append(task)
            except Exception:
                continue

    queued = sorted(
        (task for task in tasks if task.get("status") in {"queued", "pending"}),
        key=lambda item: item.get("created_at", ""),
    )
    queue_positions = {
        task["job_id"]: index for index, task in enumerate(queued, start=1)
    }
    tasks.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    items = []
    for task in tasks[:limit]:
        command = task.get("command") or {}
        source = command.get("source") or {}
        targets = command.get("targets") or []
        source_product_id = str(source.get("product_id", ""))
        source_product_code = source.get("product_code", "")
        if not source_product_code and source_product_id:
            source_product_code = _get_product_info(source_product_id, product_data).get("product_code", "")
        target_product_codes = []
        for target in targets:
            target_id = str(target.get("product_id", ""))
            target_code = target.get("product_code", "")
            if not target_code and target_id:
                target_code = _get_product_info(target_id, product_data).get("product_code", "")
            target_product_codes.append(str(target_code))
        status = task.get("status", "queued")
        items.append({
            "job_id": task["job_id"],
            "status": status,
            "phase": task.get("phase", ""),
            "created_at": task.get("created_at", ""),
            "updated_at": task.get("updated_at", ""),
            "claimed_by": task.get("claimed_by", ""),
            "source_product_id": source_product_id,
            "source_product_code": source_product_code,
            "source_count": task.get("source_count", 0),
            "target_count": len(targets),
            "target_product_ids": [
                str(target.get("product_id", "")) for target in targets
            ],
            "target_product_codes": target_product_codes,
            "queue_position": queue_positions.get(task["job_id"]),
            "cancelable": status in {"queued", "pending"},
            "error": task.get("error", ""),
        })
    return {"tasks": items, "queued_count": len(queued)}


@app.get("/api/swap-tasks/{job_id}")
def api_swap_task_detail(job_id: str):
    """Return the persisted source and target records for one swap task."""
    try:
        task = _read_task(job_id)
    except ValueError:
        return {"success": False, "error": "任务编号格式错误"}
    if not task:
        return {"success": False, "error": "任务不存在"}

    command = task.get("command") or {}
    source = command.get("source") or {}
    targets = command.get("targets") or []

    # Store and product-code values are also persisted in workbook_rows. Use
    # them first so historical records remain readable even if the database is
    # temporarily unavailable or a product has since changed.
    rows_by_product = {}
    workbook_rows = task.get("workbook_rows") or {}
    if not workbook_rows:
        excel_path = _task_excel_path(job_id)
        if not os.path.exists(excel_path):
            excel_path = _legacy_task_excel_path(task.get("excel_file", ""))
        if excel_path and os.path.exists(excel_path):
            try:
                workbook_rows = read_swap_workbook(excel_path)
            except Exception:
                workbook_rows = {}
    for sheet_rows in workbook_rows.values():
        if not isinstance(sheet_rows, list):
            continue
        for row in sheet_rows:
            if not isinstance(row, dict):
                continue
            product_id = str(row.get("product_id", ""))
            if product_id and product_id not in rows_by_product:
                rows_by_product[product_id] = row

    source_product_id = str(source.get("product_id", ""))
    source_sheet_rows = workbook_rows.get("主图数据") or []
    if not source_product_id and source_sheet_rows:
        source_product_id = str(source_sheet_rows[0].get("product_id", ""))
    source_row = rows_by_product.get(source_product_id, {})
    source_images = []
    for image in source.get("images") or []:
        if not isinstance(image, dict) or not image.get("image_url"):
            continue
        source_images.append({
            "image_url": image.get("image_url", ""),
            "image_type": image.get("image_type", ""),
        })
    if not source_images:
        source_images = [
            {"image_url": str(row.get("image_url", "")), "image_type": ""}
            for row in source_sheet_rows
            if isinstance(row, dict) and row.get("image_url")
        ]

    target_details = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        product_id = str(target.get("product_id", ""))
        row = rows_by_product.get(product_id, {})
        target_details.append({
            "product_id": product_id,
            "product_code": str(target.get("product_code") or row.get("product_code") or ""),
            "store_name": str(row.get("store_name", "")),
            "images": [
                {"image_url": str(image_url)}
                for image_url in (target.get("replace_image_urls") or [])
                if image_url
            ],
        })
    if not target_details:
        grouped_targets = {}
        for row in workbook_rows.get("替换数据") or []:
            if not isinstance(row, dict):
                continue
            product_id = str(row.get("product_id", ""))
            if not product_id:
                continue
            detail = grouped_targets.setdefault(product_id, {
                "product_id": product_id,
                "product_code": str(row.get("product_code", "")),
                "store_name": str(row.get("store_name", "")),
                "images": [],
            })
            if row.get("image_url"):
                detail["images"].append({"image_url": str(row.get("image_url"))})
        target_details = list(grouped_targets.values())

    status = task.get("status", "queued")
    return {
        "success": True,
        "job_id": task.get("job_id", job_id),
        "status": status,
        "phase": task.get("phase", ""),
        "created_at": task.get("created_at", ""),
        "updated_at": task.get("updated_at", ""),
        "claimed_at": task.get("claimed_at", ""),
        "claimed_by": task.get("claimed_by", ""),
        "operator": task.get("operator", ""),
        "cancelable": status in {"queued", "pending"},
        "source_count": len(source_images),
        "target_count": len(target_details),
        "source": {
            "product_id": source_product_id,
            "product_code": str(source.get("product_code") or source_row.get("product_code") or ""),
            "store_name": str(source_row.get("store_name", "")),
            "images": source_images,
        },
        "targets": target_details,
        "error": task.get("error", ""),
    }


@app.post("/api/swap-tasks/{job_id}/cancel")
def api_swap_task_cancel(job_id: str):
    """Cancel a task only while it is still waiting in the queue."""
    with SWAP_TASK_LOCK:
        task = _read_task(job_id)
        if not task:
            return {"success": False, "error": "任务不存在"}
        current_status = task.get("status", "queued")
        if current_status not in {"queued", "pending"}:
            return {
                "success": False,
                "error": "任务已被接收或已结束，无法取消",
                "status": current_status,
            }
        task["status"] = "stopped"
        task["phase"] = "cancelled_by_user"
        task["cancelled_at"] = _utc_now()
        _write_task(task)
    return {"success": True, "job_id": job_id, "status": "stopped"}


@app.get("/api/swap-tasks/{job_id}/excel")
def api_swap_task_excel(job_id: str):
    task = _read_task(job_id)
    excel_path = _task_excel_path(job_id) if task else ""
    if task and not os.path.exists(excel_path):
        excel_path = _legacy_task_excel_path(task.get("excel_file", ""))
    if not task or not os.path.exists(excel_path):
        return {"error": "Excel file not found"}
    return FileResponse(
        excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=FIXED_SWAP_EXCEL_NAME,
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
        protected = {
            "job_id", "command", "excel_file", "created_at", "workbook_rows",
            "operator", "source_count", "target_count",
        }
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
