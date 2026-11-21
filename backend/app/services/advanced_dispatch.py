"""Bounded exact search for a small, explicitly parcelized dispatch scenario.

Offline scenario solver. Every parcel is indivisible, vehicles reload only at
their own depot, and all time windows are timezone-aware datetimes.
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None: raise ValueError('时间必须含时区，例如 +08:00')
    return value.astimezone(timezone.utc)


class Vehicle(Strict):
    id: str = Field(min_length=1,max_length=50)
    depot: str = Field(min_length=1,max_length=50)
    capacity_kg: float = Field(gt=0,le=100000)
    capacity_m3: float = Field(gt=0,le=1000)
    temperature_c: float = Field(ge=-40,le=50)
    shift_start: datetime
    shift_end: datetime
    reload_minutes: int = Field(ge=0,le=1440)
    max_trips: int = Field(ge=1,le=5)

    @model_validator(mode='after')
    def times(self):
        if aware(self.shift_end) <= aware(self.shift_start): raise ValueError('车辆工作结束时间必须晚于开始时间')
        return self


class Order(Strict):
    id: str = Field(min_length=1,max_length=50)
    destination: str = Field(min_length=1,max_length=50)
    units: int = Field(ge=1,le=8)
    unit_weight_kg: float = Field(gt=0,le=100000)
    unit_volume_m3: float = Field(gt=0,le=1000)
    allow_split: bool
    temperature_min_c: float = Field(ge=-40,le=50)
    temperature_max_c: float = Field(ge=-40,le=50)
    window_start: datetime
    window_end: datetime
    service_minutes: int = Field(ge=0,le=1440)

    @model_validator(mode='after')
    def constraints(self):
        if self.temperature_min_c > self.temperature_max_c: raise ValueError('温度范围无效')
        if aware(self.window_start) > aware(self.window_end): raise ValueError('时间窗结束早于开始')
        return self


class RoadCost(Strict):
    distance_m: int = Field(ge=0,le=10000000)
    duration_s: int = Field(ge=0,le=604800)


class Road(Strict):
    origin: str
    destination: str
    distance_m: int = Field(ge=0,le=10000000)
    duration_s: int = Field(ge=0,le=604800)
    forbidden_vehicles: list[str] = Field(default_factory=list)
    vehicle_costs: dict[str,RoadCost] = Field(default_factory=dict)


class Scenario(Strict):
    nodes: list[str] = Field(min_length=1,max_length=20)
    vehicles: list[Vehicle] = Field(min_length=1,max_length=4)
    orders: list[Order] = Field(min_length=1,max_length=8)
    roads: list[Road] = Field(max_length=400)
    search_limit: int = Field(default=50000,ge=100,le=200000)
    use_vehicle_costs: bool = False

    @model_validator(mode='after')
    def references(self):
        if len(set(self.nodes))!=len(self.nodes): raise ValueError('地点编号不能重复')
        vehicle_ids={v.id for v in self.vehicles}
        if len(vehicle_ids)!=len(self.vehicles) or len({o.id for o in self.orders})!=len(self.orders): raise ValueError('车辆和订单编号不能重复')
        if any(v.depot not in self.nodes for v in self.vehicles) or any(o.destination not in self.nodes for o in self.orders): raise ValueError('车辆网点或订单地点不存在')
        if sum(o.units if o.allow_split else 1 for o in self.orders)>8: raise ValueError('模拟精确搜索最多 8 个拆分单元')
        keys=set()
        for road in self.roads:
            key=(road.origin,road.destination)
            if road.origin not in self.nodes or road.destination not in self.nodes or key in keys: raise ValueError('道路端点不存在或道路重复')
            if not set(road.forbidden_vehicles).issubset(vehicle_ids) or not set(road.vehicle_costs).issubset(vehicle_ids): raise ValueError('道路中的车辆编号不存在')
            keys.add(key)
        return self


def solve(scenario: Scenario) -> dict[str,Any]:
    # An unsplittable order is one group; split orders may be allocated by unit.
    groups=[]
    for order in scenario.orders:
        for _ in range(order.units if order.allow_split else 1):
            n=1 if order.allow_split else order.units
            groups.append({'order':order,'units':n,'weight':n*order.unit_weight_kg,'volume':n*order.unit_volume_m3})
    roads={(r.origin,r.destination):r for r in scenario.roads}
    def leg(vehicle, origin, destination):
        if origin==destination: return (0,0)
        road=roads.get((origin,destination))
        if road is None or vehicle.id in road.forbidden_vehicles: return None
        if scenario.use_vehicle_costs:
            cost = road.vehicle_costs.get(vehicle.id)
            return (cost.distance_m,cost.duration_s) if cost else None
        return (road.distance_m,road.duration_s)
    states=[{'vehicle':v,'current':v.depot,'time':aware(v.shift_start),'trips':1,'weight':0.0,'volume':0.0,'distance':0,'events':[]} for v in scenario.vehicles]
    count=0;cutoff=False; best=None
    deadline = time.monotonic() + 5.0
    def finish(state):
        if not state['events']: return (0, state['time'])
        back=leg(state['vehicle'],state['current'],state['vehicle'].depot)
        if back is None:return None
        end=state['time']+timedelta(seconds=back[1])
        return None if end>aware(state['vehicle'].shift_end) else (back[0],end)
    def record(rem):
        nonlocal best
        finishes=[finish(s) for s in states]
        if any(x is None for x in finishes): return
        done=sum(g['units'] for i,g in enumerate(groups) if i not in rem)
        distance=sum(s['distance']+f[0] for s,f in zip(states,finishes,strict=True))
        key=(done,-distance)
        if best is None or key>best['key']:
            best={'key':key,'states':copy.deepcopy(states),'remaining':set(rem),'finishes':finishes,'distance':distance}
    def search(rem):
        nonlocal count,cutoff
        if count>=scenario.search_limit or time.monotonic() >= deadline: cutoff=True;return
        count+=1;record(rem)
        if not rem:return
        for index in sorted(rem):
            g=groups[index];o=g['order']
            # Equivalent units of the same split order are interchangeable.
            if any(j<index and groups[j]['order'].id==o.id for j in rem):continue
            for vindex,state in enumerate(states):
                v=state['vehicle']
                if not (o.temperature_min_c <= v.temperature_c <= o.temperature_max_c):continue
                if g['weight']>v.capacity_kg+1e-9 or g['volume']>v.capacity_m3+1e-9:continue
                variants=[]
                if state['weight']+g['weight']<=v.capacity_kg+1e-9 and state['volume']+g['volume']<=v.capacity_m3+1e-9:
                    variants.append(False)
                if state['events'] and state['trips']<v.max_trips:
                    variants.append(True)
                for reload in variants:
                    clock=state['time'];origin=state['current'];extra_distance=0
                    if reload:
                        back=leg(v,origin,v.depot)
                        if back is None:continue
                        clock+=timedelta(seconds=back[1],minutes=v.reload_minutes);extra_distance+=back[0];origin=v.depot
                    forward=leg(v,origin,o.destination)
                    if forward is None:continue
                    arrival=clock+timedelta(seconds=forward[1]);start=max(arrival,aware(o.window_start))
                    if start>aware(o.window_end):continue
                    end=start+timedelta(minutes=o.service_minutes)
                    back=leg(v,o.destination,v.depot)
                    if back is None or end+timedelta(seconds=back[1])>aware(v.shift_end):continue
                    saved=copy.deepcopy(state)
                    if reload: state['trips']+=1;state['weight']=0;state['volume']=0
                    state['weight']+=g['weight'];state['volume']+=g['volume'];state['time']=end;state['current']=o.destination
                    state['distance']+=extra_distance+forward[0]
                    state['events'].append({'order_id':o.id,'destination':o.destination,'units':g['units'],'weight_kg':g['weight'],'volume_m3':g['volume'],'trip':state['trips'],'reload_before':reload,'arrival':arrival.isoformat(),'service_start':start.isoformat(),'depart':end.isoformat()})
                    search(rem-{index})
                    states[vindex]=saved;state=saved
                    if cutoff:return
    search(set(range(len(groups))))
    if best is None: raise ValueError('没有可计算的车辆状态')
    delivered={o.id:0 for o in scenario.orders}
    routes=[]
    for state,finish_data in zip(best['states'],best['finishes'],strict=True):
        if not state['events']:continue
        for e in state['events']:delivered[e['order_id']]+=e['units']
        routes.append({'vehicle_id':state['vehicle'].id,'depot':state['vehicle'].depot,'trips':state['trips'],'events':state['events'],
                       'return_at':finish_data[1].isoformat(),'distance_m':state['distance']+finish_data[0]})
    missing=[{'order_id':o.id,'unassigned_units':o.units-delivered[o.id]} for o in scenario.orders if delivered[o.id]<o.units]
    return {'simulation':True,'status':'best_found' if cutoff else 'optimal_for_model', 'search_states':count, 'search_limit':scenario.search_limit,
            'objective':'优先完成最多单位，其次最小模拟道路里程','routes':routes,'unassigned':missing,'total_distance_m':best['distance'],
            'notice':'虚构道路、虚构禁行和人工提供的温控参数；仅验证算法，不能用于真实发车。搜索截止时不保证最优；结果不代表实际道路通行。'}
