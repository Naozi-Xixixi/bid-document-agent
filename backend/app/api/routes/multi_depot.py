from __future__ import annotations

import asyncio
import math
import re
import unicodedata
import json
import uuid
from contextlib import closing
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes import maps
from app.core.config import settings
from app.services import multi_depot_route, storage
from app.core.tenant_context import get_current_tenant

router = APIRouter(prefix='/maps')
TIME = r'^\d{2}:\d{2}$'

class Network(maps.RouteStop):
    model_config = ConfigDict(extra="forbid")
    point_id: str = Field(min_length=1,max_length=64)
    city: str = Field(default='',max_length=80)

class Demand(maps.RouteStop):
    model_config = ConfigDict(extra="forbid")
    point_id: str = Field(min_length=1,max_length=64)
    weight_kg: float = Field(gt=0,le=1_000_000,allow_inf_nan=False)
    window_start: str = Field(pattern=TIME)
    window_end: str = Field(pattern=TIME)
    service_minutes: int = Field(default=0,ge=0,le=1440)

class Vehicle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vehicle_id: str = Field(min_length=1,max_length=64)
    name: str = Field(min_length=1,max_length=80)
    network_point_id: str = Field(min_length=1,max_length=64)
    capacity_kg: float = Field(gt=0,le=1_000_000,allow_inf_nan=False)
    departure_time: str = Field(pattern=TIME)

class RequestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str = Field(default='',max_length=80)
    network_points: list[Network] = Field(min_length=1,max_length=20)
    demand_points: list[Demand] = Field(min_length=1,max_length=14)
    vehicles: list[Vehicle] = Field(min_length=1,max_length=10)
    return_to_start: bool = True


def validate(payload: RequestPlan):
    for values,label,key in [(payload.network_points,'网点','point_id'),(payload.demand_points,'需求点','point_id'),(payload.vehicles,'车辆','vehicle_id')]:
        ids=[getattr(x,key).strip() for x in values]
        if not all(ids) or len(set(ids)) != len(ids): raise HTTPException(422,f'{label}编号不能为空或重复')
    network_ids = {x.point_id for x in payload.network_points}
    if any(v.network_point_id not in network_ids for v in payload.vehicles): raise HTTPException(422,'车辆所属网点不存在')
    try:
        for vehicle in payload.vehicles: multi_depot_route.minute(vehicle.departure_time)
        for point in payload.demand_points:
            if multi_depot_route.minute(point.window_start) > multi_depot_route.minute(point.window_end): raise ValueError('送达时间窗不能跨日')
    except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc)) from exc


class _PacedAmapClient:
    """Serialize and pace calls for low-QPS Amap keys; retry rate limits."""
    interval_seconds = 1.1
    def __init__(self, client):
        self.client = client
        self.lock = asyncio.Lock()
        self.next_request = 0.0

    async def get(self, url, *, params):
        async with self.lock:
            for attempt in range(3):
                wait = self.next_request - asyncio.get_running_loop().time()
                if wait > 0: await asyncio.sleep(wait)
                response = await self.client.get(url, params=params)
                self.next_request = asyncio.get_running_loop().time() + self.interval_seconds
                try:
                    info = str(response.json().get('info') or '')
                except (ValueError, AttributeError):
                    return response
                if info not in {'CUQPS_HAS_EXCEEDED_THE_LIMIT', 'USER_QPS_HAS_EXCEEDED_THE_LIMIT', 'QPS_HAS_EXCEEDED_THE_LIMIT'}:
                    return response
                if attempt == 2:
                    raise HTTPException(429, '高德服务请求频率受限，重试后仍未恢复，请稍后重试或检查账户配额')
            return response


def verify_geocoded_address(source: maps.RouteStop, result: dict) -> None:
    """Do not route a numbered address when Amap returns a different number."""
    def numbers(value: str) -> set[str]:
        normalized = re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))
        return set(re.findall(r'(?<![0-9A-Za-z])[A-Za-z]?\d+(?:-\d+)?号', normalized))
    requested = numbers(source.address)
    provider_address = str(result.get('geocode_formatted_address', result.get('formatted_address')) or '')
    resolved = numbers(provider_address)
    if requested and not requested.intersection(resolved):
        raise HTTPException(422, f'地址定位结果与输入门牌号不一致：{source.name}；输入“{source.address}”，高德定位“{provider_address or "未知"}”。请核对地址或使用更准确的定位。')


def parse_route(data: dict[str,Any]) -> dict:
    paths = (data.get('route') or {}).get('paths') or []
    if data.get('status') != '1' or not paths:
        raise HTTPException(422,'高德未能计算道路路线：' + maps._amap_error(data))
    path = paths[0]
    try:
        distance, duration = int(path['distance']), int(path['duration'])
        if distance < 0 or duration < 0: raise ValueError()
        polyline=[]
        for step in path.get('steps') or []:
            for pair in str(step.get('polyline') or '').split(';'):
                if not pair: continue
                lon,lat = pair.split(','); point=[float(lon),float(lat)]
                if not all(math.isfinite(x) for x in point): raise ValueError()
                if not polyline or polyline[-1] != point: polyline.append(point)
        if len(polyline) < 2: raise ValueError()
    except (KeyError,ValueError,TypeError):
        raise HTTPException(502,'高德路线缺少有效的里程、时间或路线坐标，无法完成规划')
    return {'distance_meters':distance,'duration_seconds':duration,'polyline':polyline}


@router.post('/multi-depot-route')
async def multi_depot_route_api(payload: RequestPlan):
    validate(payload)
    key=settings.amap_web_service_key.strip()
    if not key: raise HTTPException(503,'尚未配置高德地图 Web 服务 Key，无法规划真实道路路线')
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0,connect=8.0)) as http_client:
        client = _PacedAmapClient(http_client)
        locations = await asyncio.gather(*(maps._geocode(client,p,p.city or payload.city,key) for p in payload.network_points),
                                         *(maps._geocode(client,p,payload.city,key) for p in payload.demand_points))
        for source, result in zip([*payload.network_points, *payload.demand_points], locations, strict=True):
            verify_geocoded_address(source, result)
        networks=[{**p.model_dump(),**loc} for p,loc in zip(payload.network_points,locations[:len(payload.network_points)],strict=True)]
        demands=[{**p.model_dump(),**loc} for p,loc in zip(payload.demand_points,locations[len(payload.network_points):],strict=True)]
        async def get_leg(start, end):
            if start == end: return {'distance_meters':0,'duration_seconds':0,'polyline':[start,end]}
            try:
                response=await client.get(f'{maps.AMAP_BASE_URL}/direction/driving',params={'key':key,'origin':','.join(map(str,start)), 'destination':','.join(map(str,end)), 'strategy':'0','extensions':'all','output':'JSON'})
                response.raise_for_status()
                return parse_route(response.json())
            except httpx.HTTPError as exc:
                raise HTTPException(502,'道路路线服务请求失败，请稍后重试') from exc
        try:
            return await multi_depot_route.plan(networks,demands,[v.model_dump() for v in payload.vehicles],get_leg,payload.return_to_start)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc


@router.get('/multi-depot-route/demo')
async def multi_depot_demo():
    # Fictional points are generated in memory only. Coordinates are arbitrary,
    # no addresses are sent to Amap, and they are never stored as company data.
    network = [
        {'point_id':'demo-n1','name':'虚构网点 A','address':'虚构测试坐标','location':[117.16,31.90]},
        {'point_id':'demo-n2','name':'虚构网点 B','address':'虚构测试坐标','location':[117.32,31.79]},
    ]
    demands = [
        {'point_id':'d1','name':'虚构学校甲','address':'虚构测试坐标','location':[117.18,31.89],'weight_kg':500,'window_start':'07:30','window_end':'08:30','service_minutes':10},
        {'point_id':'d2','name':'虚构学校乙','address':'虚构测试坐标','location':[117.20,31.88],'weight_kg':600,'window_start':'08:00','window_end':'09:00','service_minutes':10},
        {'point_id':'d3','name':'虚构学校丙','address':'虚构测试坐标','location':[117.31,31.81],'weight_kg':450,'window_start':'07:30','window_end':'08:30','service_minutes':10},
        {'point_id':'d4','name':'虚构学校丁','address':'虚构测试坐标','location':[117.34,31.80],'weight_kg':550,'window_start':'08:00','window_end':'09:30','service_minutes':10},
        {'point_id':'d5','name':'虚构超载测试点','address':'虚构测试坐标','location':[117.28,31.83],'weight_kg':5000,'window_start':'08:00','window_end':'10:00','service_minutes':10},
    ]
    vehicles = [
        {'vehicle_id':'demo-v1','name':'虚构车辆 A1','network_point_id':'demo-n1','capacity_kg':800,'departure_time':'07:00'},
        {'vehicle_id':'demo-v2','name':'虚构车辆 A2','network_point_id':'demo-n1','capacity_kg':800,'departure_time':'07:00'},
        {'vehicle_id':'demo-v3','name':'虚构车辆 B1','network_point_id':'demo-n2','capacity_kg':1200,'departure_time':'07:00'},
    ]
    async def fake_leg(a,b):
        meters=round(multi_depot_route.geographic_distance(a,b)*111_000*1.35)
        return {'distance_meters':meters,'duration_seconds':max(60,round(meters/30000*3600)),'polyline':[a,b]}
    return await multi_depot_route.plan(network,demands,vehicles,fake_leg,True,simulated=True)


class SavePlan(BaseModel):
    title: str = Field(min_length=1,max_length=120)
    inputs: RequestPlan
    result: dict[str,Any]


def _ensure_plan_schema():
    with closing(storage.connect()) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS multi_depot_plan (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            inputs_json TEXT NOT NULL, result_json TEXT NOT NULL)''')
        db.commit()


@router.post('/multi-depot-route/plans')
async def save_plan(payload: SavePlan):
    validate(payload.inputs)
    if not payload.title.strip(): raise HTTPException(400, '请填写方案名称')
    if payload.result.get('simulated') is not False or payload.result.get('provider') != 'amap':
        raise HTTPException(400,'虚构测试结果不能保存为正式路线方案')
    source = json.dumps(payload.inputs.model_dump(),ensure_ascii=False)
    result = json.dumps(payload.result,ensure_ascii=False)
    if len(source.encode()) + len(result.encode()) > 10 * 1024 * 1024: raise HTTPException(413,'路线方案过大')
    _ensure_plan_schema()
    tenant = get_current_tenant()
    if tenant is None: raise HTTPException(401,'请先登录')
    plan_id=uuid.uuid4().hex; created=storage.now_utc().isoformat()
    with closing(storage.connect()) as db:
        db.execute('INSERT INTO multi_depot_plan (id,title,created_by,created_at,inputs_json,result_json) VALUES (?,?,?,?,?,?)',
                   (plan_id,payload.title.strip(),tenant.username,created,source,result))
        db.commit()
    return {'id':plan_id,'title':payload.title.strip(),'created_by':tenant.username,'created_at':created}


@router.get('/multi-depot-route/plans')
async def list_plans():
    _ensure_plan_schema()
    with closing(storage.connect()) as db:
        rows=db.execute('SELECT id,title,created_by,created_at FROM multi_depot_plan ORDER BY created_at DESC LIMIT 100').fetchall()
        return {'plans':[dict(row) for row in rows]}


@router.get('/multi-depot-route/plans/{plan_id}')
async def get_plan(plan_id: str):
    _ensure_plan_schema()
    with closing(storage.connect()) as db:
        row=db.execute('SELECT * FROM multi_depot_plan WHERE id=?',(plan_id,)).fetchone()
        if not row: raise HTTPException(404,'路线方案不存在或无权访问')
        return {'id':row['id'],'title':row['title'],'created_by':row['created_by'],'created_at':row['created_at'],
                'inputs':json.loads(row['inputs_json']),'result':json.loads(row['result_json'])}
