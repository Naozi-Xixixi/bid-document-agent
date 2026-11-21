from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import math

import httpx
from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from app.api.routes.multi_depot import _PacedAmapClient
from app.core.config import settings
from app.services import advanced_dispatch

router=APIRouter(prefix='/maps/advanced-dispatch')


def demo_data() -> dict[str,Any]:
    return {
        'nodes':['虚构配送中心','虚构学校'],
        'vehicles':[
            {'id':'冷链车A','depot':'虚构配送中心','capacity_kg':800,'capacity_m3':2,'temperature_c':4,'shift_start':'2026-10-30T23:00:00+08:00','shift_end':'2026-10-31T04:00:00+08:00','reload_minutes':5,'max_trips':3},
            {'id':'禁行车B','depot':'虚构配送中心','capacity_kg':800,'capacity_m3':2,'temperature_c':4,'shift_start':'2026-10-30T23:00:00+08:00','shift_end':'2026-10-31T04:00:00+08:00','reload_minutes':5,'max_trips':3},
            {'id':'常温车C','depot':'虚构配送中心','capacity_kg':800,'capacity_m3':2,'temperature_c':20,'shift_start':'2026-10-30T23:00:00+08:00','shift_end':'2026-10-31T04:00:00+08:00','reload_minutes':5,'max_trips':3},
        ],
        'orders':[{'id':'测试订单','destination':'虚构学校','units':3,'unit_weight_kg':500,'unit_volume_m3':1.5,'allow_split':True,'temperature_min_c':2,'temperature_max_c':8,'window_start':'2026-10-30T23:50:00+08:00','window_end':'2026-10-31T02:00:00+08:00','service_minutes':5}],
        'roads':[{'origin':'虚构配送中心','destination':'虚构学校','distance_m':10000,'duration_s':1200,'forbidden_vehicles':['禁行车B']},
                 {'origin':'虚构学校','destination':'虚构配送中心','distance_m':10000,'duration_s':1200,'forbidden_vehicles':['禁行车B']}],
        'search_limit':50000,
    }


@router.get('/demo')
async def demo():
    scenario=advanced_dispatch.Scenario.model_validate(demo_data())
    return {'scenario':demo_data(),'result':await run_in_threadpool(advanced_dispatch.solve,scenario)}


@router.post('/simulate')
async def simulate(payload: advanced_dispatch.Scenario):
    return await run_in_threadpool(advanced_dispatch.solve,payload)


class ReplanState(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scenario: advanced_dispatch.Scenario
    now: datetime
    completed_units: dict[str,int]
    completed_trips: dict[str,int]
    vehicle_positions: dict[str,str]


@router.post('/simulate-replan')
async def simulate_replan(payload: ReplanState):
    try: now=advanced_dispatch.aware(payload.now)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    original=payload.scenario
    orders_by_id={o.id:o for o in original.orders}
    vehicles_by_id={v.id:v for v in original.vehicles}
    if set(payload.completed_units)-orders_by_id.keys() or set(payload.completed_trips)-vehicles_by_id.keys() or set(payload.vehicle_positions)!=vehicles_by_id.keys():
        raise HTTPException(422,'订单、车辆编号或车辆位置不完整')
    pending=[]
    for order in original.orders:
        count=payload.completed_units.get(order.id,0)
        if count<0 or count>order.units or (not order.allow_split and count not in (0,order.units)):
            raise HTTPException(422,'已完成数量无效或违反不可拆单规则')
        if count < order.units: pending.append(order.model_copy(update={'units':order.units-count}))
    if any(payload.vehicle_positions[v.id]!=v.depot for v in original.vehicles):
        raise HTTPException(422,'当前模拟重排只支持车辆全部回到各自网点、没有在途装货的状态')
    vehicles=[]
    for vehicle in original.vehicles:
        trips=payload.completed_trips.get(vehicle.id,0)
        if trips<0 or trips>vehicle.max_trips:raise HTTPException(422,'已完成趟数无效')
        start=max(now,advanced_dispatch.aware(vehicle.shift_start))
        from datetime import timedelta
        start += timedelta(minutes=vehicle.reload_minutes)
        if trips<vehicle.max_trips and start < advanced_dispatch.aware(vehicle.shift_end):
            vehicles.append(vehicle.model_copy(update={'shift_start':start,'max_trips':vehicle.max_trips-trips}))
    if not pending:
        return {'simulation':True,'status':'complete','routes':[],'unassigned':[],'total_distance_m':0,'completed_units':payload.completed_units,
                'notice':'模拟：全部订单已经完成，没有待配送内容。'}
    if not vehicles:
        return {'simulation':True,'status':'no_available_vehicles','routes':[],'unassigned':[{'order_id':o.id,'unassigned_units':o.units} for o in pending],
                'total_distance_m':0,'completed_units':payload.completed_units,'notice':'模拟：没有可用车辆或剩余趟数。'}
    active_ids={v.id for v in vehicles}
    roads=[r.model_copy(update={'forbidden_vehicles':[x for x in r.forbidden_vehicles if x in active_ids]}) for r in original.roads]
    scenario=original.model_copy(update={'orders':pending,'vehicles':vehicles,'roads':roads})
    result=await run_in_threadpool(advanced_dispatch.solve,scenario)
    result['completed_units']=payload.completed_units
    result['notice']='模拟重排：仅保留未完成单元；车辆均已回网点且无在途货物。'+result['notice']
    return result


class TruckLeg(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    origin: list[float]=Field(min_length=2,max_length=2)
    destination: list[float]=Field(min_length=2,max_length=2)
    size: int=Field(ge=1,le=4)
    height_m: float=Field(gt=0,le=25.5)
    width_m: float=Field(gt=0,le=25.5)
    total_weight_t: float=Field(gt=0,le=100)
    rated_load_t: float=Field(gt=0,lt=100)
    axles: int=Field(ge=2,le=255)
    province: str=Field(min_length=1,max_length=1)
    plate_number: str=Field(pattern=r'^[A-Z0-9]{6,7}$')
    cartype: int=Field(ge=0,le=2)


def parse_truck(payload: dict):
    if str(payload.get('errcode')) != '0':
        raise HTTPException(502,'货车路线服务不可用、未授权或请求失败；请检查服务权限和车辆信息。')
    data=payload.get('data') or {}
    paths=((data.get('route') or {}).get('paths') or [])
    if not paths: raise HTTPException(422,'货车服务未返回可用路线')
    path=paths[0]
    try:
        restriction=int(path['restriction']);distance=int(path['distance']);duration=int(path['duration'])
        if restriction not in (-1,0,1,2,3,4,5) or distance<0 or duration<0:raise ValueError()
        poly=[]
        for step in path.get('steps') or []:
            for pair in str(step.get('polyline') or '').split(';'):
                if not pair:continue
                lng,lat=pair.split(',');pt=[float(lng),float(lat)]
                if not all(math.isfinite(v) for v in pt):raise ValueError()
                if not poly or poly[-1]!=pt:poly.append(pt)
        if len(poly)<2:raise ValueError()
    except (KeyError,TypeError,ValueError): raise HTTPException(502,'货车服务返回的数据不完整，不能判断是否可通行')
    return {'distance_meters':distance,'duration_seconds':duration,'polyline':poly,'restriction_code':restriction,
            'usable':restriction in (-1,1),'checked_at':datetime.now(timezone.utc).isoformat(),
            'notice':'高德货车路径的当前结果仅供参考；须按实际车型、车牌、证件、道路标识和政策人工复核。'}


def truck_params(payload: TruckLeg, key: str) -> dict:
    return {'key':key,'origin':','.join(map(str,payload.origin)),'destination':','.join(map(str,payload.destination)),
            'size':payload.size,'height':payload.height_m,'width':payload.width_m,'load':payload.total_weight_t,'weight':payload.rated_load_t,
            'axis':payload.axles,'province':payload.province,'number':payload.plate_number,'cartype':payload.cartype,'strategy':11,'showpolyline':1}


@router.post('/truck-leg')
async def truck_leg(payload: TruckLeg):
    if any(not math.isfinite(x) for x in payload.origin+payload.destination) or not (-180<=payload.origin[0]<=180 and -90<=payload.origin[1]<=90 and -180<=payload.destination[0]<=180 and -90<=payload.destination[1]<=90):
        raise HTTPException(422,'坐标不合法')
    if payload.total_weight_t < payload.rated_load_t:
        raise HTTPException(422,'车辆总重不能小于核定载重')
    key=settings.amap_web_service_key.strip()
    if not key:raise HTTPException(503,'未配置高德 Web 服务 Key')
    params=truck_params(payload,key)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0,connect=8.0)) as raw:
            response=await _PacedAmapClient(raw).get('https://restapi.amap.com/v4/direction/truck',params=params)
            response.raise_for_status()
            return parse_truck(response.json())
    except (httpx.HTTPError,ValueError) as exc:
        raise HTTPException(502,'货车道路服务请求失败') from exc


class TruckProfile(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    size: int=Field(ge=1,le=4)
    height_m: float=Field(gt=0,le=25.5)
    width_m: float=Field(gt=0,le=25.5)
    total_weight_t: float=Field(gt=0,le=100)
    rated_load_t: float=Field(gt=0,lt=100)
    axles: int=Field(ge=2,le=255)
    province: str=Field(min_length=1,max_length=1)
    plate_number: str=Field(pattern=r'^[A-Z0-9]{6,7}$')
    cartype: int=Field(ge=0,le=2)


class RealTruckPlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scenario: advanced_dispatch.Scenario
    node_locations: dict[str,list[float]]
    trucks: dict[str,TruckProfile]


@router.post('/truck-plan')
async def truck_plan(payload: RealTruckPlan):
    """Small pre-dispatch scenario using actual vehicle-specific truck routes."""
    scenario=payload.scenario
    if len(scenario.nodes)>4 or len(scenario.vehicles)>2:
        raise HTTPException(422,'真实货车矩阵暂限 4 个地点、2 辆车；每次最多 24 次道路请求')
    if scenario.roads or scenario.use_vehicle_costs:
        raise HTTPException(422,'真实路线必须由货车接口生成，不接受自行提供的道路矩阵')
    ids={v.id for v in scenario.vehicles}
    if set(payload.node_locations)!=set(scenario.nodes) or set(payload.trucks)!=ids:
        raise HTTPException(422,'地点坐标或货车资料不完整')
    for coords in payload.node_locations.values():
        if len(coords)!=2 or not all(math.isfinite(x) for x in coords) or not (-180<=coords[0]<=180 and -90<=coords[1]<=90):
            raise HTTPException(422,'地点坐标不合法')
    for vehicle in scenario.vehicles:
        profile=payload.trucks[vehicle.id]
        if profile.total_weight_t<profile.rated_load_t or abs(vehicle.capacity_kg/1000-profile.rated_load_t)>0.001:
            raise HTTPException(422,'货车核定载重须与规划车辆载重一致，车辆总重不能小于核定载重')
    key=settings.amap_web_service_key.strip()
    if not key: raise HTTPException(503,'未配置高德 Web 服务 Key')
    checks=[]; roads=[]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0,connect=8.0)) as raw:
            client=_PacedAmapClient(raw)
            for origin in scenario.nodes:
                for destination in scenario.nodes:
                    if origin==destination: continue
                    costs={}
                    for vehicle in scenario.vehicles:
                        truck=TruckLeg(origin=payload.node_locations[origin],destination=payload.node_locations[destination],**payload.trucks[vehicle.id].model_dump())
                        response=await client.get('https://restapi.amap.com/v4/direction/truck',params=truck_params(truck,key))
                        response.raise_for_status()
                        route=parse_truck(response.json())
                        checks.append({'origin':origin,'destination':destination,'vehicle_id':vehicle.id,'restriction_code':route['restriction_code'],'usable':route['usable']})
                        if route['usable']:
                            costs[vehicle.id]=advanced_dispatch.RoadCost(distance_m=route['distance_meters'],duration_s=route['duration_seconds'])
                    roads.append(advanced_dispatch.Road(origin=origin,destination=destination,distance_m=0,duration_s=0,vehicle_costs=costs))
    except (httpx.HTTPError,ValueError) as exc:
        raise HTTPException(502,'货车道路矩阵请求失败，未使用普通驾车路线替代') from exc
    model=scenario.model_copy(update={'roads':roads,'use_vehicle_costs':True})
    result=await run_in_threadpool(advanced_dispatch.solve,model)
    result['simulation']=False
    result['provider']='amap_truck_current_snapshot'
    result['road_checks']=checks
    result['notice']='基于当前高德货车路线快照及用户输入的车辆参数；不是未来时段交通预测。限行未知的路段未使用，发车前须核对政策、通行证和实际条件。'
    return result
