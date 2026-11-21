"""Public procurement notices: bounded official-source retrieval, tenant storage."""
from __future__ import annotations

import asyncio
import hashlib
from contextlib import closing
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from lxml import html

from app.services import storage
from app.core.tenant_context import get_current_tenant

router = APIRouter()
SOURCES = {
    'ccgp_consultation': ('中国政府采购网·竞争性磋商', 'https://www.ccgp.gov.cn/cggg/dfgg/jzxcs/'),
    'ccgp_tender': ('中国政府采购网·公开招标', 'https://www.ccgp.gov.cn/cggg/dfgg/gkzb/'),
    'ccgp_negotiation': ('中国政府采购网·竞争性谈判', 'https://www.ccgp.gov.cn/cggg/dfgg/jzxtp/'),
    'ccgp_inquiry': ('中国政府采购网·询价', 'https://www.ccgp.gov.cn/cggg/dfgg/xjgg/'),
}
DATE = re.compile(r'20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2})?')
_refresh_locks: dict[str, asyncio.Lock] = {}
KEYWORDS = ('食材','食品','农副产品','蔬菜','肉类','水产','食堂','餐饮配送','粮油')


def ensure_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS tender_notice_tracking (notice_id VARCHAR(64) PRIMARY KEY, state VARCHAR(20) NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS tender_source_status (source VARCHAR(100) PRIMARY KEY, ok INTEGER NOT NULL, item_count INTEGER NOT NULL, error VARCHAR(100) NOT NULL, checked_at VARCHAR(32) NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS tender_notice (notice_id VARCHAR(64) PRIMARY KEY, title VARCHAR(500) NOT NULL, url VARCHAR(1000) NOT NULL, source VARCHAR(100) NOT NULL, published_at VARCHAR(32) NOT NULL, region VARCHAR(80) NOT NULL, buyer VARCHAR(300) NOT NULL, fetched_at VARCHAR(32) NOT NULL)")


def parse_notices(content: bytes, source: str, source_url: str):
    tree = html.fromstring(content, base_url=source_url)
    results = []
    recognized = False
    for li in tree.xpath('//li[a[@href]]'):
        full = ' '.join(li.text_content().split())
        match = DATE.search(full)
        if not match:
            continue
        recognized = True
        if not any(word in full for word in KEYWORDS):
            continue
        for a in li.xpath('./a[@href]'):
            title = (a.get('title') or a.text_content()).strip()
            link = urljoin(source_url, a.get('href'))
            host = (urlparse(link).hostname or '').lower()
            if not title or host not in ('www.ccgp.gov.cn', 'ccgp.gov.cn') or urlparse(link).scheme not in ('http', 'https'):
                continue
            if not re.search(r'\.s?html?$', urlparse(link).path, re.I):
                continue
            area = re.search(r'地域[：:]\s*([^\s]+)', full)
            buyer = re.search(r'采购人[：:]\s*(.+)$', full)
            pub = match.group().replace('年','-').replace('月','-').replace('日','').replace('/','-').replace('.','-')
            results.append({'notice_id': hashlib.sha256(link.encode()).hexdigest(), 'title': title, 'url': link, 'source': source, 'published_at': pub, 'region': area.group(1) if area else '', 'buyer': buyer.group(1).strip() if buyer else ''})
            break
    if not recognized:
        raise ValueError("公告列表格式无法识别")
    return results


def save_notices(items):
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with closing(storage.connect()) as conn:
        ensure_table(conn)
        for x in items:
            row = conn.execute('SELECT notice_id FROM tender_notice WHERE notice_id=?', (x['notice_id'],)).fetchone()
            if row:
                conn.execute('UPDATE tender_notice SET title=?,source=?,published_at=?,region=?,buyer=?,fetched_at=? WHERE notice_id=?', (x['title'],x['source'],x['published_at'],x['region'],x['buyer'],now,x['notice_id']))
            else:
                conn.execute('INSERT INTO tender_notice(notice_id,title,url,source,published_at,region,buyer,fetched_at) VALUES (?,?,?,?,?,?,?,?)', (x['notice_id'],x['title'],x['url'],x['source'],x['published_at'],x['region'],x['buyer'],now))
        conn.commit()


def save_source_status(results, checked_at):
    with closing(storage.connect()) as conn:
        ensure_table(conn)
        for r in results:
            conn.execute('DELETE FROM tender_source_status WHERE source=?', (r['source'],))
            conn.execute('INSERT INTO tender_source_status(source,ok,item_count,error,checked_at) VALUES (?,?,?,?,?)', (r['source'],int(r['ok']),r['count'],r.get('error',''),checked_at))
        conn.commit()


def get_source_status():
    with closing(storage.connect()) as conn:
        ensure_table(conn)
        rows = conn.execute('SELECT source,ok,item_count,error,checked_at FROM tender_source_status ORDER BY source').fetchall()
    return [{'source': r['source'], 'ok': bool(r['ok']), 'count': r['item_count'], 'error': r['error'], 'checked_at': r['checked_at']} for r in rows]


def list_notices(region='', keyword=''):
    with closing(storage.connect()) as conn:
        ensure_table(conn)
        rows = [dict(r) for r in conn.execute("SELECT n.notice_id,n.title,n.url,n.source,n.published_at,n.region,n.buyer,n.fetched_at,COALESCE(t.state,'未标记') AS follow_state FROM tender_notice n LEFT JOIN tender_notice_tracking t ON t.notice_id=n.notice_id ORDER BY n.published_at DESC LIMIT 1000").fetchall()]
    def priority(row):
        s = row['region']+' '+row['title']+' '+row['buyer']
        return 0 if '示例' in s else 1 if '示例甲' in s else 2 if '示例乙' in s else 3 if '华东' in s else 4 if row['region'] else 5
    if region:
        rows = [r for r in rows if (priority(r)=={'示例':0,'示例甲':1,'示例乙':2,'华东其他':3,'全国其他':4,'待核实':5}.get(region, -1))]
    if keyword.strip():
        q=keyword.strip().casefold()
        rows=[r for r in rows if q in (r['title']+' '+r['buyer']).casefold()]
    rows.sort(key=priority)
    return rows


@router.get('/tender-search/notices')
def get_notices(region: str = '', keyword: str = ''):
    return {'success': True, 'notices': list_notices(region, keyword), 'sources': get_source_status(), 'coverage': '仅包含已成功获取栏目首页中的相关公告，不能代表全部项目；截止时间和投标资格请查原文。'}


@router.post('/tender-search/refresh')
async def refresh_notices():
    tenant = get_current_tenant()
    key = tenant.company_id if tenant else 'company_default'
    lock = _refresh_locks.setdefault(key, asyncio.Lock())
    async with lock:
        return await _refresh_notices()


async def _refresh_notices():
    async def fetch_one(name, url, allowed):
        if not allowed:
            return {'source': name, 'ok': False, 'count': 0, 'error': '来源访问规则不允许或暂不可用', 'items': []}
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=True) as client:
                response = await client.get(url, headers={'User-Agent':'TenderAssistant/1.0'})
                response.raise_for_status()
            found = await asyncio.to_thread(parse_notices, response.content, name, url)
            # Empty relevant results do not prove whether the source layout changed.
            return {'source': name, 'ok': True, 'count': len(found), 'items': found}
        except Exception as exc:
            return {'source': name, 'ok': False, 'count': 0, 'error': type(exc).__name__, 'items': []}
    robot = RobotFileParser()
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=True) as client:
            rules = await client.get('https://www.ccgp.gov.cn/robots.txt', headers={'User-Agent': 'TenderAssistant/1.0'})
        if rules.status_code == 404:
            robot.parse([])
        else:
            rules.raise_for_status()
            robot.parse(rules.text.splitlines())
        permitted = lambda url: robot.can_fetch('TenderAssistant', url)
    except (httpx.HTTPError, ValueError):
        permitted = lambda url: False
    results = await asyncio.gather(*(fetch_one(name,url,permitted(url)) for name,url in SOURCES.values()))
    items = [x for r in results for x in r.pop('items')]
    if items:
        await asyncio.to_thread(save_notices, items)
    checked_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    await asyncio.to_thread(save_source_status, results, checked_at)
    return {'success': True, 'sources': results, 'total': len(items), 'checked_at': checked_at}


class TrackingUpdate(BaseModel):
    state: str


@router.put('/tender-search/notices/{notice_id}/tracking')
def update_tracking(notice_id: str, payload: TrackingUpdate):
    if payload.state not in ('未标记', '收藏', '跟进中', '不参与'):
        raise HTTPException(status_code=400, detail='无效的跟进状态')
    with closing(storage.connect()) as conn:
        ensure_table(conn)
        row = conn.execute('SELECT notice_id FROM tender_notice WHERE notice_id=?', (notice_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='公告不存在')
        conn.execute('DELETE FROM tender_notice_tracking WHERE notice_id=?', (notice_id,))
        if payload.state != '未标记':
            conn.execute('INSERT INTO tender_notice_tracking(notice_id,state) VALUES (?,?)', (notice_id,payload.state))
        conn.commit()
    return {'success': True, 'state': payload.state}
