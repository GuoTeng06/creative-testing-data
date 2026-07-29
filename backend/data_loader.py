"""
测图数据加载器 v3 - 从 MySQL 读取（凭据通过环境变量注入）
"""
import os
import time
from collections import defaultdict
import pymysql

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
    
    return {
        'store_name': str(row_dict.get('店铺名称', '')),
        'product_id': str(row_dict.get('商品ID', '')),
        'date': str(row_dict.get('日期', '')),
        'product_title': str(row_dict.get('商品标题', '')),
        'product_code': str(row_dict.get('商品编码', '')),
        'image_url': str(row_dict.get('推广创意', '')),
        'image_type': str(row_dict.get('图片类型', '')),
        'status': str(row_dict.get('审核状态', '')),
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
CACHE_TTL = 300


def load_all_data(force=False):
    global _cache, _cache_time
    now = time.time()
    if not force and _cache is not None and (now - _cache_time) < CACHE_TTL:
        return _cache

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

        # 跳过完全无数据的行
        if rec['impressions'] == 0 and rec['transaction_amount'] == 0 and rec['clicks'] == 0:
            continue

        pid = rec['product_id']
        if pid and pid not in products:
            products[pid] = {
                'product_id': pid,
                'product_title': rec['product_title'],
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
    _cache = result
    _cache_time = now

    print(f"[DataLoader] MySQL → {len(all_records)} rows, {len(products)} products, "
          f"{len(stores)} stores, {len(date_range)} dates")
    return result


def get_products(data=None):
    if data is None:
        data = load_all_data()
    return data['products']


def get_creatives_by_product(product_id, data=None):
    if data is None:
        data = load_all_data()
    return [r for r in data['records'] if r.get('product_id') == product_id]

def get_summary(data=None, date_from=None, date_to=None, store=None):
    """概览统计，支持日期范围 + 店铺筛选"""
    if data is None:
        data = load_all_data()
    all_records = data['records']
    
    if store:
        all_records = [r for r in all_records if r.get('store_name', '') == store]
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


def get_product_aggregates(data=None):
    if data is None:
        data = load_all_data()

    products = {}
    for r in data['records']:
        pid = r.get('product_id', 'unknown')
        if pid not in products:
            products[pid] = {
                'product_id': pid,
                'product_title': r.get('product_title', ''),
                'product_code': r.get('product_code', ''),
                'store_name': r.get('store_name', ''),
                'total_impressions': 0,
                'total_clicks': 0,
                'total_transaction': 0,
                'total_orders': 0,
                'creative_count': 0,
                'image_types': set(),
                'dates': set(),
            }
        p = products[pid]
        p['total_impressions'] += r.get('impressions', 0) or 0
        p['total_clicks'] += r.get('clicks', 0) or 0
        p['total_transaction'] += r.get('transaction_amount', 0) or 0
        p['total_orders'] += r.get('order_count', 0) or 0
        p['creative_count'] += 1
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
            'total_impressions': p['total_impressions'],
            'total_clicks': p['total_clicks'],
            'total_transaction': round(p['total_transaction'], 2),
            'total_orders': p['total_orders'],
            'creative_count': p['creative_count'],
            'image_types': sorted(p['image_types']),
            'ctr': round(p['total_clicks'] / p['total_impressions'], 4) if p['total_impressions'] > 0 else 0,
            'conversion_rate': round(p['total_orders'] / p['total_clicks'], 4) if p['total_clicks'] > 0 else 0,
            'date_count': len(p['dates']),
        })

    return sorted(result, key=lambda x: x['total_transaction'], reverse=True)
