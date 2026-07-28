"""
测图数据看板 — FastAPI 后端（v2 — 适配新20列统一格式）
端口 8766，CORS 全开，数据来自 data_loader.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from data_loader import (
    load_all_data, get_summary, get_products, get_creatives_by_product,
    get_trends, get_product_aggregates
)
import requests
from operation_api import router as operation_router

app = FastAPI(title="测图数据看板 API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(operation_router)

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

RPA_TARGET = "http://192.168.16.38:8767"
TOTAL_IMAGE_SLOTS = 10
SLOT_IMAGE_TYPES = {'主轮播图', '副轮播图'}


def _get_product_images(product_id, data):
    creatives = get_creatives_by_product(product_id, data)
    main_images = []
    other_images = []
    for c in creatives:
        img = {
            "image_url": c.get('image_url', ''),
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
        targets.append({
            "product_id": tid,
            "has_data_count": has_data,
            "empty_slots": empty_slots,
            "main_images": target_main,
            "other_images": target_other,
        })

    return {
        "source_images": source_images,
        "source_id": source_id,
        "targets": targets,
        "plan": f"将源商品 {source_id} 的图替换到 {len(targets)} 个目标商品的空图位",
    }


@app.post("/api/swap-image/execute")
def api_swap_execute(payload: dict):
    source_id = payload.get('source_product_id', '')
    source_image_urls = payload.get('source_image_urls', [])
    target_ids = payload.get('target_product_ids', [])
    if not source_id or not target_ids or not source_image_urls:
        return {"success": False, "error": "缺少参数"}

    data = load_all_data()
    source_main, source_other = _get_product_images(source_id, data)
    source_all = source_main + source_other
    source_imgs = [img for img in source_all if img['image_url'] in source_image_urls]
    if not source_imgs:
        return {"success": False, "error": "找不到源图片"}

    swap_command = {
        "action": "swap_to_empty_slots",
        "source": {
            "product_id": source_id,
            "images": [{"image_url": u, "image_type": s.get('image_type', '')}
                       for u, s in zip(source_image_urls, source_imgs)],
        },
        "targets": [{"product_id": tid} for tid in target_ids],
        "note": f"将{len(source_image_urls)}张源图替换到{len(target_ids)}个目标商品的空图位",
    }

    try:
        resp = requests.post(f"{RPA_TARGET}/execute", json=swap_command, timeout=10)
        if resp.status_code == 200:
            return {"success": True, "result": resp.json()}
        else:
            return {"success": False, "error": f"RPA 返回 {resp.status_code}: {resp.text[:200]}"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": f"无法连接到目标电脑 {RPA_TARGET}，请确认监听器已启动"}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == '__main__':
    import uvicorn
    print("Starting 测图数据看板 on http://127.0.0.1:8766")
    uvicorn.run(app, host="0.0.0.0", port=8766)
