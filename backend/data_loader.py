"""
测图数据加载器 v3 - 从 MySQL 读取（凭据通过环境变量注入）
"""
import os
import time
import threading
import json
from collections import defaultdict
import pymysql

try:
    import redis as redis_lib
except ImportError:  # 本地未安装 redis 时仍可回退到原有内存缓存
    redis_lib = None

DB_CONFIG = {
    'host': os.environ.get('MYSQL_HOST', '192.168.16.38'),
    'port': int(os.environ.get('MYSQL_PORT', '3306')),
    'user': os.environ.get('MYSQL_USER', 'root'),
    'password': os.environ.get('MYSQL_PASSWORD', 'root'),
    'database': os.environ.get('MYSQL_DATABASE', 'creative testing data'),
    'charset': 'utf8mb4',
    'connect_timeout': 5,
}
TABLE = '全部数据'


def _get_conn():
    return pymysql.connect(**DB_CONFIG)


def _parse_number(val):
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().rstrip('%')
    try:
        return float(s)
    except ValueError:
        return 0


def _parse_percent(val):
    if val is None or val == '' or val == '-':
        return 0
    if isinstance(val, (int, float)):
        return float(val) / 100 if float(val) > 1 else float(val)
    s = str(val).strip()
    if s.endswith('%'):
        try:
            return float(s.rstrip('%')) / 100
        except ValueError:
            return 0
    try:
        v = float(s)
        return v / 100 if v > 1 else v
    except ValueError:
        return 0


def _parse_row(row_dict):
    def num(key):
        return _parse_number(row_dict.get(key))
    def text(key):
        value = row_dict.get(key)
        return '' if value is None else str(value).strip()
    metric_keys = ('曝光量', '点击量', '交易额(元)', '成交笔数')
    metrics_empty = all(
        row_dict.get(key) is None or str(row_dict.get(key)).strip() in {'', '-'}
        for key in metric_keys
    )
    
    return {
        'store_name': text('店铺名称'),
        'product_id': text('商品ID'),
        'date': text('日期'),
        'product_title': text('商品标题'),
        'brand': text('品牌'),
        'product_code': text('商品编码'),
        'image_url': text('推广创意'),
        'image_type': text('图片类型'),
        'status': text('审核状态'),
        'metrics_empty': metrics_empty,
        'transaction_amount': num('交易额(元)'),
        'order_count': int(num('成交笔数')),
        'avg_order_amount': num('每笔成交金额(元)'),
        'impressions': int(num('曝光量')),
        'clicks': int(num('点击量')),
        'ctr': _parse_percent(row_dict.get('点击率')),
        'conversion_rate': _parse_percent(row_dict.get('点击转化率')),
        'net_transaction': num('净交易额(元)'),
        'net_order_count': int(num('净成交笔数')),
        'net_avg_order_amount': num('每笔净成交金额(元)'),
        'net_transaction_share': num('净交易额占比'),
        'net_order_share': num('净成交笔数占比'),
    }


# ---- 缓存 ----
_cache = None
_cache_time = 0
_empty_cache = None
_empty_cache_time = 0
try:
    CACHE_TTL = max(1, int(os.environ.get('DATA_CACHE_TTL', '300')))
except ValueError:
    CACHE_TTL = 300
_load_all_data_lock = threading.Lock()

# L2 共享缓存：多个 API worker/容器实例可复用同一份 MySQL 数据。
# Redis 不可用时自动降级为现有的进程内缓存，不影响看板可用性。
REDIS_URL = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0').strip()
REDIS_CACHE_PREFIX = os.environ.get('REDIS_CACHE_PREFIX', 'cetu-dashboard:data:v1')
_redis_client = None
_redis_lock = threading.Lock()
_redis_retry_after = 0.0


def _redis_key(include_empty=False):
    suffix = 'with-empty' if include_empty else 'metrics'
    return f'{REDIS_CACHE_PREFIX}:{suffix}'


def _get_redis_client():
    """Create a short-timeout Redis client lazily; return None on failure."""
    global _redis_client, _redis_retry_after
    if redis_lib is None or not REDIS_URL:
        return None
    now = time.monotonic()
    if now < _redis_retry_after:
        return None
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            client = redis_lib.Redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
                health_check_interval=30,
            )
            client.ping()
            _redis_client = client
            return client
        except Exception as exc:
            _redis_retry_after = now + 10
            print(f'[DataLoader] Redis unavailable, using memory cache: {exc}')
            return None


def _redis_load(include_empty=False):
    global _redis_client, _redis_retry_after
    client = _get_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_redis_key(include_empty))
        return json.loads(raw) if raw else None
    except Exception as exc:
        _redis_client = None
        _redis_retry_after = time.monotonic() + 10
        print(f'[DataLoader] Redis read failed, using MySQL: {exc}')
        return None


def _redis_store(data, include_empty=False):
    global _redis_client, _redis_retry_after
    client = _get_redis_client()
    if client is None:
        return
    try:
        client.setex(
            _redis_key(include_empty),
            CACHE_TTL,
            json.dumps(data, ensure_ascii=False, separators=(',', ':'), default=str),
        )
    except Exception as exc:
        _redis_client = None
        _redis_retry_after = time.monotonic() + 10
        print(f'[DataLoader] Redis write failed, continuing without shared cache: {exc}')


def clear_data_cache():
    """Clear both local and Redis copies after an explicit data refresh."""
    global _cache, _cache_time, _empty_cache, _empty_cache_time, _redis_client
    with _load_all_data_lock:
        _cache = None
        _cache_time = 0
        _empty_cache = None
        _empty_cache_time = 0
        client = _get_redis_client()
        if client is not None:
            try:
                client.delete(_redis_key(False), _redis_key(True))
            except Exception:
                _redis_client = None


def _load_all_data_impl(force=False, include_empty=False):
    """Load dashboard rows; swap workbench can opt into image rows with zero metrics."""
    global _cache, _cache_time, _empty_cache, _empty_cache_time
    cache = _empty_cache if include_empty else _cache
    cache_time = _empty_cache_time if include_empty else _cache_time
    now = time.time()
    if not force and cache is not None and (now - cache_time) < CACHE_TTL:
        return cache

    if not force:
        shared_cache = _redis_load(include_empty=include_empty)
        if shared_cache is not None:
            if include_empty:
                _empty_cache = shared_cache
                _empty_cache_time = now
            else:
                _cache = shared_cache
                _cache_time = now
            return shared_cache

    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM `{TABLE}`")
    columns = [d[0] for d in cur.description]
    
    all_records = []
    products = {}
    stores = set()
    date_range = set()

    for row in cur.fetchall():
        row_dict = dict(zip(columns, row))
        rec = _parse_row(row_dict)

        # 概览等核心指标继续忽略空行；换图工作台必须保留有图片地址的空指标图片。
        if (not include_empty and
                rec['impressions'] == 0 and rec['transaction_amount'] == 0 and rec['clicks'] == 0):
            continue

        pid = rec['product_id']
        if pid and pid not in products:
            products[pid] = {
                'product_id': pid,
                'product_title': rec['product_title'],
                'brand': rec['brand'],
                'product_code': rec['product_code'],
                'store_name': rec['store_name'],
            }

        stores.add(rec['store_name'])
        if rec['date']:
            date_range.add(rec['date'])
        all_records.append(rec)

    conn.close()

    result = {
        'records': all_records,
        'products': list(products.values()),
        'stores': sorted(stores),
        'dates': sorted(date_range),
        'total_creatives': len(all_records),
    }
    if include_empty:
        _empty_cache = result
        _empty_cache_time = now
    else:
        _cache = result
        _cache_time = now
    _redis_store(result, include_empty=include_empty)

    print(f"[DataLoader] MySQL → {len(all_records)} rows, {len(products)} products, "
          f"{len(stores)} stores, {len(date_range)} dates")
    return result


def load_all_data(force=False, include_empty=False):
    """Serialize cold-cache reads so concurrent API calls share one MySQL load."""
    with _load_all_data_lock:
        return _load_all_data_impl(force=force, include_empty=include_empty)


def get_products(data=None):
    if data is None:
        data = load_all_data()
    return data['products']


def get_creatives_by_product(product_id, data=None):
    if data is None:
        data = load_all_data()
    return [r for r in data['records'] if r.get('product_id') == product_id]

def get_summary(data=None, date_from=None, date_to=None, store=None, brand=None):
    """概览统计，支持日期范围 + 店铺筛选"""
    if data is None:
        data = load_all_data()
    all_records = data['records']
    
    if store:
        stores = {s.strip() for s in str(store).split(',') if s.strip()}
        all_records = [r for r in all_records if r.get('store_name', '') in stores]
    if brand:
        brands = {b.strip() for b in str(brand).split(',') if b.strip()}
        all_records = [r for r in all_records if (r.get('brand') or '未标注品牌') in brands]
    if date_from:
        all_records = [r for r in all_records if r.get('date', '') >= date_from]
    if date_to:
        all_records = [r for r in all_records if r.get('date', '') <= date_to]
    
    if not all_records:
        return {'total_products': 0, 'total_creatives': 0, 'total_stores': 0,
                'date_range': [date_from or '', date_to or ''], 'total_impressions': 0,
                'total_clicks': 0, 'total_transaction': 0, 'total_orders': 0,
                'total_net_transaction': 0, 'overall_ctr': 0, 'overall_conversion': 0}

    total_impressions = sum(r.get('impressions', 0) or 0 for r in all_records)
    total_clicks = sum(r.get('clicks', 0) or 0 for r in all_records)
    total_transaction = sum(r.get('transaction_amount', 0) or 0 for r in all_records)
    total_orders = sum(r.get('order_count', 0) or 0 for r in all_records)
    total_net_transaction = sum(r.get('net_transaction', 0) or 0 for r in all_records)

    # 计算筛选范围内的日期 + 商品/店铺/创意
    filtered_dates = sorted(set(r.get('date') for r in all_records if r.get('date')))
    filtered_products = set(r.get('product_id') for r in all_records if r.get('product_id'))
    filtered_stores = set(r.get('store_name') for r in all_records if r.get('store_name'))

    return {
        'total_products': len(filtered_products),
        'total_creatives': len(all_records),
        'total_stores': len(filtered_stores),
        'date_range': [filtered_dates[0], filtered_dates[-1]] if filtered_dates else [date_from or '', date_to or ''],
        'total_impressions': total_impressions,
        'total_clicks': total_clicks,
        'total_transaction': round(total_transaction, 2),
        'total_orders': total_orders,
        'total_net_transaction': round(total_net_transaction, 2),
        'overall_ctr': round(total_clicks / total_impressions, 4) if total_impressions > 0 else 0,
        'overall_conversion': round(total_orders / total_clicks, 4) if total_clicks > 0 else 0,
    }


def get_store_aggregates(data=None, date_from=None, date_to=None, store=None, brand=None):
    """按店铺汇总概览指标，复用概览页的日期与店铺筛选规则。"""
    if data is None:
        data = load_all_data()
    records = data['records']
    if store:
        stores = {s.strip() for s in str(store).split(',') if s.strip()}
        records = [r for r in records if r.get('store_name', '') in stores]
    if brand:
        brands = {b.strip() for b in str(brand).split(',') if b.strip()}
        records = [r for r in records if (r.get('brand') or '未标注品牌') in brands]
    if date_from:
        records = [r for r in records if r.get('date', '') >= date_from]
    if date_to:
        records = [r for r in records if r.get('date', '') <= date_to]

    grouped = {}
    for record in records:
        name = record.get('store_name') or '未命名店铺'
        if name not in grouped:
            grouped[name] = {
                'store_name': name,
                'total_impressions': 0,
                'total_clicks': 0,
                'total_transaction': 0,
                'total_orders': 0,
                'creative_count': 0,
                'product_ids': set(),
            }
        item = grouped[name]
        item['total_impressions'] += record.get('impressions', 0) or 0
        item['total_clicks'] += record.get('clicks', 0) or 0
        item['total_transaction'] += record.get('transaction_amount', 0) or 0
        item['total_orders'] += record.get('order_count', 0) or 0
        item['creative_count'] += 1
        if record.get('product_id'):
            item['product_ids'].add(record['product_id'])

    result = []
    for item in grouped.values():
        impressions = item['total_impressions']
        clicks = item['total_clicks']
        result.append({
            'store_name': item['store_name'],
            'total_impressions': item['total_impressions'],
            'total_clicks': item['total_clicks'],
            'total_transaction': round(item['total_transaction'], 2),
            'total_orders': item['total_orders'],
            'creative_count': item['creative_count'],
            'product_count': len(item['product_ids']),
            'ctr': round(clicks / impressions, 4) if impressions > 0 else 0,
            'conversion_rate': round(item['total_orders'] / clicks, 4) if clicks > 0 else 0,
        })
    return sorted(result, key=lambda item: item['total_impressions'], reverse=True)


def get_brand_trends(data=None, metric='impressions', date_from=None, date_to=None, store=None, brand=None):
    """按品牌和日期聚合核心指标，供概览页的品牌趋势钻取使用。"""
    if data is None:
        data = load_all_data()
    records = data['records']
    if date_from:
        records = [r for r in records if r.get('date', '') >= date_from]
    if date_to:
        records = [r for r in records if r.get('date', '') <= date_to]
    if store:
        stores = {s.strip() for s in str(store).split(',') if s.strip()}
        records = [r for r in records if r.get('store_name', '') in stores]
    if brand:
        brands = {b.strip() for b in str(brand).split(',') if b.strip()}
        records = [r for r in records if (r.get('brand') or '未标注品牌') in brands]

    grouped = defaultdict(lambda: {
        'impressions': 0, 'clicks': 0, 'orders': 0, 'transaction': 0,
    })
    totals = defaultdict(lambda: {
        'impressions': 0, 'clicks': 0, 'orders': 0, 'transaction': 0,
    })
    dates = set()
    for record in records:
        date_value = record.get('date')
        if not date_value:
            continue
        brand_value = record.get('brand') or '未标注品牌'
        values = {
            'impressions': record.get('impressions', 0) or 0,
            'clicks': record.get('clicks', 0) or 0,
            'orders': record.get('order_count', 0) or 0,
            'transaction': record.get('transaction_amount', 0) or 0,
        }
        dates.add(date_value)
        for key, value in values.items():
            grouped[(brand_value, date_value)][key] += value
            totals[brand_value][key] += value

    def metric_value(values):
        if metric == 'ctr':
            return values['clicks'] / values['impressions'] if values['impressions'] else 0
        if metric == 'cvr':
            return values['orders'] / values['clicks'] if values['clicks'] else 0
        return values.get(metric, 0)

    ordered_dates = sorted(dates)
    ordered_brands = sorted(totals, key=lambda name: metric_value(totals[name]), reverse=True)
    if not brand:
        ordered_brands = ordered_brands[:8]
    series = []
    for brand_value in ordered_brands:
        points = []
        for date_value in ordered_dates:
            values = grouped[(brand_value, date_value)]
            points.append({
                'date': date_value,
                'value': round(metric_value(values), 4 if metric in {'ctr', 'cvr'} else 2),
                'impressions': values['impressions'],
                'clicks': values['clicks'],
                'orders': values['orders'],
                'transaction': round(values['transaction'], 2),
            })
        series.append({
            'brand': brand_value,
            'total': round(metric_value(totals[brand_value]), 4 if metric in {'ctr', 'cvr'} else 2),
            'points': points,
        })
    return {'metric': metric, 'dates': ordered_dates, 'series': series}


def get_trends(product_id=None, metric='transaction_amount', data=None, date_from=None, date_to=None):
    if data is None:
        data = load_all_data()
    records = data['records']
    if product_id:
        records = [r for r in records if r.get('product_id') == product_id]
    if date_from:
        records = [r for r in records if r.get('date', '') >= date_from]
    if date_to:
        records = [r for r in records if r.get('date', '') <= date_to]

    by_date = defaultdict(lambda: {'value': 0, 'count': 0})
    for r in records:
        d = r.get('date')
        if not d:
            continue
        val = r.get(metric, 0) or 0
        by_date[d]['value'] += val
        by_date[d]['count'] += 1

    return [{'date': d, 'value': round(v['value'], 2)}
            for d, v in sorted(by_date.items())]


def get_product_aggregates(
    data=None,
    date=None,
    date_from=None,
    date_to=None,
    store=None,
    brand=None,
):
    if data is None:
        data = load_all_data()

    # Keep the product list unique while allowing the UI to scope metrics to a date.
    records = data['records']
    if date:
        records = [r for r in records if r.get('date', '') == date]
    else:
        if date_from:
            records = [r for r in records if r.get('date', '') >= date_from]
        if date_to:
            records = [r for r in records if r.get('date', '') <= date_to]
    if store:
        stores = {value.strip() for value in str(store).split(',') if value.strip()}
        records = [r for r in records if r.get('store_name', '') in stores]
    if brand:
        brands = {value.strip() for value in str(brand).split(',') if value.strip()}
        records = [r for r in records if (r.get('brand') or '未标注品牌') in brands]

    products = {}
    for r in records:
        pid = r.get('product_id', 'unknown')
        if pid not in products:
            products[pid] = {
                'product_id': pid,
                'product_title': r.get('product_title', ''),
                'product_code': r.get('product_code', ''),
                'store_name': r.get('store_name', ''),
                'brand': r.get('brand', ''),
                'main_image_url': '',
                'total_impressions': 0,
                'total_clicks': 0,
                'total_transaction': 0,
                'total_orders': 0,
                'creative_count': 0,
                'image_types': set(),
                'dates': set(),
            }
        p = products[pid]
        if not p['brand'] and r.get('brand'):
            p['brand'] = r.get('brand', '')
        p['total_impressions'] += r.get('impressions', 0) or 0
        p['total_clicks'] += r.get('clicks', 0) or 0
        p['total_transaction'] += r.get('transaction_amount', 0) or 0
        p['total_orders'] += r.get('order_count', 0) or 0
        p['creative_count'] += 1
        if r.get('image_url') and (not p['main_image_url'] or '主' in (r.get('image_type') or '')):
            p['main_image_url'] = r.get('image_url')
        if r.get('image_type'):
            p['image_types'].add(r['image_type'])
        if r.get('date'):
            p['dates'].add(r['date'])

    result = []
    for pid, p in products.items():
        result.append({
            'product_id': p['product_id'],
            'product_title': p['product_title'],
            'product_code': p['product_code'],
            'store_name': p['store_name'],
            'brand': p['brand'],
            'main_image_url': p['main_image_url'],
            'total_impressions': p['total_impressions'],
            'total_clicks': p['total_clicks'],
            'total_transaction': round(p['total_transaction'], 2),
            'total_orders': p['total_orders'],
            'creative_count': p['creative_count'],
            'image_types': sorted(p['image_types']),
            'ctr': round(p['total_clicks'] / p['total_impressions'], 4) if p['total_impressions'] > 0 else 0,
            'conversion_rate': round(p['total_orders'] / p['total_clicks'], 4) if p['total_clicks'] > 0 else 0,
            'date_count': len(p['dates']),
            'date_min': min(p['dates']) if p['dates'] else '',
            'date_max': max(p['dates']) if p['dates'] else '',
        })

    return sorted(result, key=lambda x: x['total_transaction'], reverse=True)
