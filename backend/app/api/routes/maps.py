from __future__ import annotations

import asyncio
import html
import math
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.tenant_context import get_current_tenant
from app.services import bid_parse_service, storage


router = APIRouter(prefix="/maps")
AMAP_BASE_URL = "https://restapi.amap.com/v3"


class RouteStop(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    address: str = Field(min_length=2, max_length=240)


class VehicleRouteRequest(BaseModel):
    city: str = Field(default="", max_length=80)
    origin: RouteStop
    stops: list[RouteStop] = Field(default_factory=list, max_length=14)
    destination: RouteStop


class DeliveryNetworkPoint(RouteStop):
    """A Delivery-owned distribution center or vegetable delivery outlet."""

    point_id: str = Field(min_length=1, max_length=64)
    point_type: str = Field(default="配送网点", max_length=30)
    city: str = Field(default="", max_length=80)


class DeliveryDemandPoint(RouteStop):
    """A school or organization that must be served for the current tender."""

    point_id: str = Field(min_length=1, max_length=64)
    demand_type: str = Field(default="学校", max_length=30)


class DeliveryRouteRequest(BaseModel):
    company_name: str = Field(default="示例配送公司", min_length=1, max_length=80)
    city: str = Field(default="", max_length=80)
    network_points: list[DeliveryNetworkPoint] = Field(min_length=1, max_length=20)
    demand_points: list[DeliveryDemandPoint] = Field(min_length=1, max_length=14)
    start_point_id: str | None = Field(default=None, max_length=64)
    return_to_start: bool = True
    auto_order: bool = True
    auto_nearest: bool = False


class DeliveryStaticMapRequest(BaseModel):
    """Viewport used to export a real Amap base map for a completed route."""

    center: list[float] = Field(min_length=2, max_length=2)
    zoom: float = Field(default=11, ge=3, le=20)
    locations: list[list[float]] = Field(min_length=1, max_length=100)
    width: int = Field(default=1024, ge=320, le=1024)
    height: int = Field(default=560, ge=240, le=1024)


class NetworkPointCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    address: str = Field(min_length=2, max_length=240)
    city: str = Field(default="", max_length=80)
    point_type: str = Field(default="配送网点", max_length=30)


class NetworkPointUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    address: str | None = Field(default=None, min_length=2, max_length=240)
    city: str | None = Field(default=None, max_length=80)
    point_type: str | None = Field(default=None, max_length=30)
    enabled: bool | None = None


def _amap_error(payload: dict[str, Any]) -> str:
    return str(payload.get("info") or payload.get("infocode") or "高德地图服务暂不可用")


def _route_error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    """Keep route failures machine-readable without exposing the Amap key."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **extra},
    )


def _flatten_parse_value(value: Any, depth: int = 0) -> str:
    if depth > 3 or value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                import json

                return _flatten_parse_value(json.loads(text), depth + 1)
            except Exception:
                return text
        return text
    if isinstance(value, dict):
        return "\n".join(f"{key}：{_flatten_parse_value(item, depth + 1)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return "\n".join(_flatten_parse_value(item, depth + 1) for item in value)
    return str(value)


def _extract_tender_demand_points(bid_id: int) -> dict[str, Any]:
    info = bid_parse_service.get_parse_info(bid_id)
    if info.get("parse_status") != "completed":
        raise HTTPException(status_code=409, detail="招标文件尚未完成解读，完成后才能提取学校或单位位置")
    source_parts = [
        info.get("full_markdown"),
        info.get("raw_markdown"),
        info.get("raw_full_text"),
        info.get("parsed_project_overview"),
        info.get("parsed_biz_requirements"),
        info.get("qualification_requirements"),
        info.get("bid_key_points"),
    ]
    source = "\n".join(part for part in (_flatten_parse_value(value) for value in source_parts) if part)
    entity_pattern = r"[^，,。；;：:\n]{1,50}(?:学校|幼儿园|小学|初中|高中|中学|大学|学院|医院|卫生院|机关单位|事业单位|机关|单位)"
    address_pattern = r"(?:学校地址|单位地址|供货单位所在地|采购人地址|项目地址|地址|地点|校址|院址|位于|坐落于|项目所在地|供货地点|配送地点|交货地点)\s*[：:]?\s*([^，,。；;\n]{2,100})"
    address_hint = re.compile(r"(?:省|市|区|县|乡|镇|街道|路|街|大道|号|园区|校区)")
    seen: set[tuple[str, str]] = set()
    candidates: list[dict[str, Any]] = []
    lines = [re.sub(r"\s+", " ", line).strip(" -•*\t") for line in source.splitlines()]
    for index, line in enumerate(lines):
        if not line or not re.search(entity_pattern, line):
            continue
        name_source = re.split(r"[：:]", line, maxsplit=1)[-1] if re.search(r"[：:]", line) else line
        name_source = re.split(r"[，,。；;]", name_source, maxsplit=1)[0]
        entity_match = re.search(entity_pattern, name_source) or re.search(entity_pattern, line)
        if entity_match is None:
            continue
        name = entity_match.group(0).split("：")[-1].split(":")[-1]
        name = re.sub(r"^(?:本项目|采购人|招标人|供货人|服务对象|配送对象|项目单位)[为是\s]*", "", name)
        name = re.sub(r"^[\d.（）()、\s]+", "", name).strip(" ：:，,；;")
        if name in {"配送单位", "采购单位", "供货单位", "招标单位", "服务单位", "项目单位", "机关单位", "事业单位"}:
            continue
        address_match = re.search(address_pattern, line)
        address = address_match.group(1).strip(" ：:，,；;") if address_match else ""
        excerpt = line
        if not address and index + 1 < len(lines) and address_hint.search(lines[index + 1]):
            address = lines[index + 1].strip(" ：:，,；;")
            excerpt = f"{line} {lines[index + 1]}"
        key = (name.casefold(), address.casefold())
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "point_id": f"tender-{len(candidates) + 1}",
            "name": name[:80],
            "address": address[:240],
            "demand_type": "学校" if re.search(r"学校|幼儿园|小学|初中|高中|中学|大学|学院", name) else "单位",
            "confidence": 0.9 if address_match or address_hint.search(address) else 0.65,
            "source_excerpt": excerpt[:360],
        })
        if len(candidates) >= 20:
            break
    return {
        "bid_id": bid_id,
        "tender_file_name": info.get("tender_file_name"),
        "candidates": candidates,
        "requires_confirmation": True,
        "message": "已从招标文件中提取候选学校/单位，请核对名称和地址后再规划路线。" if candidates else "招标文件中暂未识别到明确的学校或单位地址，请手动添加。",
    }


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_network_point_schema() -> None:
    with storage.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS map_network_point (
                point_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                city TEXT NOT NULL DEFAULT '',
                point_type TEXT NOT NULL DEFAULT '配送网点',
                enabled INTEGER NOT NULL DEFAULT 1,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_map_network_point_enabled ON map_network_point(enabled, update_time)")
        tenant = get_current_tenant()
        tenant_name = tenant.company_name if tenant is not None else ""
        default_hub_name = "示例配送公司配送中心"
        default_hub_address = "示例市示例区示例路 1 号"
        has_default_hub = conn.execute(
            "SELECT 1 FROM map_network_point WHERE name = ? AND address = ? LIMIT 1",
            (default_hub_name, default_hub_address),
        ).fetchone()
        if not has_default_hub and (tenant is None or "示例公司" in tenant_name or tenant.company_id == "company_default"):
            now = _now_text()
            conn.execute(
                "INSERT INTO map_network_point (name, address, city, point_type, enabled, create_time, update_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (default_hub_name, default_hub_address, "示例市", "配送中心", 1, now, now),
            )
            conn.commit()


def _network_point_row(row: Any) -> dict[str, Any]:
    return {
        "point_id": int(row["point_id"]),
        "name": str(row["name"] or ""),
        "address": str(row["address"] or ""),
        "city": str(row["city"] or ""),
        "point_type": str(row["point_type"] or "配送网点"),
        "enabled": bool(row["enabled"]),
        "create_time": str(row["create_time"] or ""),
        "update_time": str(row["update_time"] or ""),
    }


@router.get("/network-points")
async def list_network_points(include_disabled: bool = False) -> dict[str, Any]:
    """List the current tenant's Delivery-owned delivery network."""
    _ensure_network_point_schema()
    with storage.connect() as conn:
        sql = "SELECT * FROM map_network_point"
        params: tuple[Any, ...] = ()
        if not include_disabled:
            sql += " WHERE enabled = ?"
            params = (1,)
        sql += " ORDER BY enabled DESC, update_time DESC, point_id ASC"
        rows = conn.execute(sql, params).fetchall()
    return {"points": [_network_point_row(row) for row in rows]}


@router.post("/network-points")
async def create_network_point(payload: NetworkPointCreateRequest) -> dict[str, Any]:
    _ensure_network_point_schema()
    now = _now_text()
    with storage.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO map_network_point (name, address, city, point_type, enabled, create_time, update_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payload.name.strip(), payload.address.strip(), payload.city.strip(), payload.point_type.strip() or "配送网点", 1, now, now),
        )
        point_id = int(cursor.lastrowid or 0)
        conn.commit()
        row = conn.execute("SELECT * FROM map_network_point WHERE point_id = ?", (point_id,)).fetchone()
    return {"point": _network_point_row(row)}


@router.patch("/network-points/{point_id}")
async def update_network_point(point_id: int, payload: NetworkPointUpdateRequest) -> dict[str, Any]:
    _ensure_network_point_schema()
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=422, detail="没有需要修改的网点信息")
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in values.items():
        if key == "enabled":
            assignments.append("enabled = ?")
            params.append(1 if value else 0)
        else:
            assignments.append(f"{key} = ?")
            params.append(str(value).strip())
    assignments.append("update_time = ?")
    params.extend([_now_text(), point_id])
    with storage.connect() as conn:
        cursor = conn.execute(f"UPDATE map_network_point SET {', '.join(assignments)} WHERE point_id = ?", params)
        if cursor.rowcount <= 0:
            raise HTTPException(status_code=404, detail="配送网点不存在")
        conn.commit()
        row = conn.execute("SELECT * FROM map_network_point WHERE point_id = ?", (point_id,)).fetchone()
    return {"point": _network_point_row(row)}


@router.delete("/network-points/{point_id}")
async def delete_network_point(point_id: int) -> dict[str, Any]:
    _ensure_network_point_schema()
    with storage.connect() as conn:
        cursor = conn.execute("UPDATE map_network_point SET enabled = 0, update_time = ? WHERE point_id = ?", (_now_text(), point_id))
        if cursor.rowcount <= 0:
            raise HTTPException(status_code=404, detail="配送网点不存在")
        conn.commit()
    return {"point_id": point_id, "enabled": False}


@router.get("/tenders/{bid_id}/demand-points")
async def extract_tender_demand_points(bid_id: int) -> dict[str, Any]:
    return _extract_tender_demand_points(bid_id)


@router.post("/delivery-route/static-map")
async def delivery_route_static_map(payload: DeliveryStaticMapRequest) -> Response:
    """Proxy an Amap static basemap so browser PNG export keeps road tiles.

    The browser's DOM screenshot APIs cannot reliably export Amap's cross-origin
    tiles. The key stays on the backend; the frontend draws the route and labels
    over this same viewport before downloading the final PNG.
    """
    key = settings.amap_web_service_key.strip()
    if not key:
        raise HTTPException(status_code=503, detail="尚未配置高德地图 Web 服务 Key")
    if any(len(location) != 2 for location in payload.locations):
        raise _route_error(422, "INVALID_STATIC_MAP_LOCATION", "路线地图坐标格式不正确", stage="static_map")
    longitude, latitude = payload.center
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise _route_error(422, "INVALID_STATIC_MAP_CENTER", "路线地图中心坐标不正确", stage="static_map")

    params = {
        "key": key,
        "location": f"{longitude},{latitude}",
        "zoom": str(max(3, min(17, int(payload.zoom)))),
        "size": f"{payload.width}*{payload.height}",
        "scale": "1",
        "traffic": "0",
        "output": "png",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            response = await client.get(f"{AMAP_BASE_URL}/staticmap", params=params)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"高德静态地图服务请求失败：{exc}") from exc

    content_type = response.headers.get("content-type", "image/png")
    if not content_type.startswith("image/"):
        raise _route_error(502, "AMAP_STATIC_MAP_FAILED", "高德静态地图未返回图片", stage="static_map")
    return Response(content=response.content, media_type=content_type)


async def _geocode(client: httpx.AsyncClient, stop: RouteStop, city: str, key: str) -> dict[str, Any]:
    try:
        response = await client.get(
            f"{AMAP_BASE_URL}/geocode/geo",
            params={"key": key, "address": stop.address, "city": city, "output": "JSON"},
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"地址解析服务请求失败：{exc}") from exc
    if payload.get("status") != "1" or not payload.get("geocodes"):
        raise _route_error(
            422,
            "AMAP_GEOCODE_FAILED",
            f"无法识别地址“{stop.address}”：{_amap_error(payload)}",
            stage="geocode",
            address=stop.address,
        )
    result = payload["geocodes"][0]
    location = str(result.get("location") or "")
    if "," not in location:
        raise _route_error(
            422,
            "AMAP_GEOCODE_EMPTY",
            f"高德地图未返回地址“{stop.address}”的坐标",
            stage="geocode",
            address=stop.address,
        )
    longitude, latitude = location.split(",", 1)
    return {
        "name": stop.name.strip(),
        "address": stop.address.strip(),
        "location": [float(longitude), float(latitude)],
        "formatted_address": result.get("formatted_address") or stop.address.strip(),
        "geocode_formatted_address": result.get("formatted_address") or "",
        "geocode_level": result.get("level") or "",
    }


def _distance_squared(left: list[float], right: list[float]) -> float:
    # This is only used to assign a demand point to the nearest own outlet and
    # to create a stable first-pass order before Amap calculates road distance.
    longitude_scale = math.cos(math.radians((left[1] + right[1]) / 2))
    return ((left[0] - right[0]) * longitude_scale) ** 2 + (left[1] - right[1]) ** 2


def _nearest_neighbor(points: list[dict[str, Any]], start: list[float]) -> list[dict[str, Any]]:
    remaining = list(points)
    ordered: list[dict[str, Any]] = []
    current = start
    while remaining:
        next_point = min(remaining, key=lambda item: _distance_squared(current, item["location"]))
        remaining.remove(next_point)
        ordered.append(next_point)
        current = next_point["location"]
    return ordered


def _build_route_svg(
    company_name: str,
    city: str,
    route_stops: list[dict[str, Any]],
    polyline: list[list[float]],
    distance_meters: int,
    duration_seconds: int,
    return_polyline: list[list[float]] | None = None,
) -> str:
    """Create a printable route diagram without requiring a second image service."""
    coordinates = (polyline + (return_polyline or [])) or [item["location"] for item in route_stops]
    longitudes = [float(item[0]) for item in coordinates]
    latitudes = [float(item[1]) for item in coordinates]
    min_longitude, max_longitude = min(longitudes), max(longitudes)
    min_latitude, max_latitude = min(latitudes), max(latitudes)
    longitude_range = max(max_longitude - min_longitude, 0.001)
    latitude_range = max(max_latitude - min_latitude, 0.001)
    width, height = 1200, 720
    padding_left, padding_right, padding_top, padding_bottom = 72, 72, 112, 58
    plot_width = width - padding_left - padding_right
    plot_height = height - padding_top - padding_bottom

    def point(location: list[float]) -> tuple[float, float]:
        x = padding_left + (float(location[0]) - min_longitude) / longitude_range * plot_width
        y = padding_top + (max_latitude - float(location[1])) / latitude_range * plot_height
        return round(x, 2), round(y, 2)

    def escape(value: Any) -> str:
        return html.escape(str(value or ""), quote=True)

    route_points = [point(item) for item in (polyline or [stop["location"] for stop in route_stops])]
    polyline_value = " ".join(f"{x},{y}" for x, y in route_points)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="1200" height="720" fill="#f5f7f3"/>',
        '<path d="M0 92H1200" stroke="#dce5d8" stroke-width="2"/>',
        f'<text x="48" y="42" font-family="Microsoft YaHei,Arial,sans-serif" font-size="26" font-weight="700" fill="#26352b">{escape(company_name)}配送路线图</text>',
        f'<text x="48" y="73" font-family="Microsoft YaHei,Arial,sans-serif" font-size="15" fill="#64756a">{escape(city)} · 招标学校/单位配送路线 · 按规划顺序行驶</text>',
        '<rect x="48" y="126" width="1104" height="530" rx="18" fill="#ffffff" stroke="#dfe8dc"/>',
        '<path d="M72 616H1128M72 500H1128M72 384H1128M72 268H1128" stroke="#edf1eb" stroke-width="1"/>',
    ]
    if polyline_value:
        lines.append(f'<polyline points="{polyline_value}" fill="none" stroke="#ffffff" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>')
        lines.append(f'<polyline points="{polyline_value}" fill="none" stroke="#3b7f55" stroke-width="7" stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>')
    if return_polyline:
        back_points = " ".join(f"{x},{y}" for x, y in map(point, return_polyline))
        lines.append(f'<polyline points="{back_points}" fill="none" stroke="#ffffff" stroke-width="7" stroke-dasharray="12 9" stroke-linecap="round" stroke-linejoin="round"/>')
        lines.append(f'<polyline points="{back_points}" fill="none" stroke="#1746a2" stroke-width="5" stroke-dasharray="12 9" stroke-linecap="round" stroke-linejoin="round"/>')
        lines.append('<text x="72" y="105" font-family="Microsoft YaHei,Arial,sans-serif" font-size="13" fill="#3b7f55">绿色实线：配送</text>')
        lines.append('<text x="215" y="105" font-family="Microsoft YaHei,Arial,sans-serif" font-size="13" fill="#1746a2">蓝色虚线：返程</text>')
    for index, stop in enumerate(route_stops):
        x, y = point(stop["location"])
        is_start = index == 0
        is_end = index == len(route_stops) - 1
        fill = "#2c8551" if is_start else "#bd6534" if is_end else "#356ba9"
        label = "起" if is_start else "终" if is_end else str(index)
        lines.extend([
            f'<circle cx="{x}" cy="{y}" r="15" fill="{fill}" stroke="#ffffff" stroke-width="4"/>',
            f'<text x="{x}" y="{y + 5}" text-anchor="middle" font-family="Arial,sans-serif" font-size="12" font-weight="700" fill="#ffffff">{escape(label)}</text>',
            f'<rect x="{x + 22}" y="{y - 22}" width="{min(230, max(100, len(str(stop["name"])) * 16 + 24))}" height="34" rx="6" fill="#ffffff" stroke="#e1e8df"/>',
            f'<text x="{x + 34}" y="{y + 1}" font-family="Microsoft YaHei,Arial,sans-serif" font-size="13" fill="#26352b">{escape(stop["name"])}</text>',
        ])
    distance_text = f"{distance_meters / 1000:.1f} 公里" if distance_meters >= 1000 else f"{distance_meters} 米"
    seconds = max(0, int(duration_seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    duration_text = f"{hours} 小时 {minutes} 分 {seconds} 秒" if hours else f"{minutes} 分 {seconds} 秒"
    lines.extend([
        '<rect x="760" y="26" width="160" height="42" rx="9" fill="#edf6ec"/>',
        f'<text x="780" y="52" font-family="Microsoft YaHei,Arial,sans-serif" font-size="15" font-weight="700" fill="#39724b">全程 {escape(distance_text)}</text>',
        '<rect x="932" y="26" width="220" height="42" rx="9" fill="#fff2e8"/>',
        f'<text x="952" y="52" font-family="Microsoft YaHei,Arial,sans-serif" font-size="15" font-weight="700" fill="#9a5d35">预计 {escape(duration_text)}</text>',
        '<text x="72" y="686" font-family="Microsoft YaHei,Arial,sans-serif" font-size="12" fill="#7a887c">路线依据高德地图驾车路线服务生成，实际配送请结合车辆、时段和道路情况确认。</text>',
        '</svg>',
    ])
    return "".join(lines)


@router.post("/vehicle-route")
async def plan_vehicle_route(payload: VehicleRouteRequest) -> dict[str, Any]:
    """Geocode a depot and ordered delivery stops, then request one driving route."""
    key = settings.amap_web_service_key.strip()
    if not key:
        raise HTTPException(status_code=503, detail="尚未配置高德地图 Web 服务 Key，请在 backend/.env 设置 AMAP_WEB_SERVICE_KEY")

    input_stops = [payload.origin, *payload.stops, payload.destination]
    if len(input_stops) > 16:
        raise HTTPException(status_code=422, detail="一次最多规划 16 个地址点")
    if len({stop.address.strip().casefold() for stop in input_stops}) < 2:
        raise HTTPException(status_code=422, detail="请至少填写两个不同的路线地址")

    timeout = httpx.Timeout(25.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        geocoded = await asyncio.gather(*(_geocode(client, stop, payload.city.strip(), key) for stop in input_stops))
        params: dict[str, str] = {
            "key": key,
            "origin": ",".join(map(str, geocoded[0]["location"])),
            "destination": ",".join(map(str, geocoded[-1]["location"])),
            "strategy": "0",
            "extensions": "all",
            "output": "JSON",
        }
        if len(geocoded) > 2:
            params["waypoints"] = ";".join(",".join(map(str, item["location"])) for item in geocoded[1:-1])
        try:
            response = await client.get(f"{AMAP_BASE_URL}/direction/driving", params=params)
            response.raise_for_status()
            route_payload = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"驾车路线服务请求失败：{exc}") from exc

    paths = (route_payload.get("route") or {}).get("paths") or []
    if route_payload.get("status") != "1" or not paths:
        raise HTTPException(status_code=422, detail=f"高德地图未能规划路线：{_amap_error(route_payload)}")
    path = paths[0]
    polyline: list[list[float]] = []
    steps = []
    for step in path.get("steps") or []:
        step_points = []
        for pair in str(step.get("polyline") or "").split(";"):
            try:
                longitude, latitude = pair.split(",", 1)
                step_points.append([float(longitude), float(latitude)])
            except (ValueError, TypeError):
                continue
        if step_points:
            polyline.extend(step_points if not polyline else step_points[1:] if polyline[-1] == step_points[0] else step_points)
        steps.append({"instruction": step.get("instruction") or "", "road": step.get("road") or ""})

    return {
        "city": payload.city.strip(),
        "stops": geocoded,
        "distance_meters": int(float(path.get("distance") or 0)),
        "duration_seconds": int(float(path.get("duration") or 0)),
        "polyline": polyline,
        "steps": steps,
        "provider": "amap",
    }


@router.post("/delivery-route")
async def plan_delivery_route(payload: DeliveryRouteRequest) -> dict[str, Any]:
    """Plan a tender delivery route from Delivery's own network to demand points.

    The network is used for outlet assignment and the selected outlet is the
    route start. Demand points are optionally reordered by geographic distance;
    Amap then calculates the actual road route. This keeps the first release
    useful for bid material while leaving vehicle capacity/time-window
    splitting for a later version.
    """
    key = settings.amap_web_service_key.strip()
    if not key:
        raise HTTPException(status_code=503, detail="尚未配置高德地图 Web 服务 Key，请在 backend/.env 设置 AMAP_WEB_SERVICE_KEY")
    network_ids = [point.point_id.strip() for point in payload.network_points]
    if len(set(network_ids)) != len(network_ids):
        raise _route_error(422, "DUPLICATE_NETWORK_POINT_ID", "配送网点编号不能重复", stage="validate")
    demand_ids = [point.point_id.strip() for point in payload.demand_points]
    if len(set(demand_ids)) != len(demand_ids):
        raise _route_error(422, "DUPLICATE_DEMAND_POINT_ID", "招标学校/单位编号不能重复", stage="validate")
    if payload.auto_nearest and len(payload.demand_points) != 1:
        raise _route_error(422, "AUTO_NEAREST_REQUIRES_ONE_DEMAND", "自动选择最近网点仅支持一个需求点，请选择单个需求点或手动选择起点", stage="validate")
    start_id = payload.start_point_id.strip() if payload.start_point_id and not payload.auto_nearest else network_ids[0]
    if start_id not in network_ids:
        raise _route_error(422, "START_NETWORK_POINT_NOT_FOUND", "选择的起始配送网点不存在", stage="validate")

    timeout = httpx.Timeout(25.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        from app.api.routes.multi_depot import _PacedAmapClient
        request_client = _PacedAmapClient(client) if payload.auto_nearest else client
        geocoded_network = await asyncio.gather(*(_geocode(request_client, point, point.city.strip() or payload.city.strip(), key) for point in payload.network_points))
        geocoded_demand = await asyncio.gather(*(_geocode(request_client, point, payload.city.strip(), key) for point in payload.demand_points))

        network_by_id: dict[str, dict[str, Any]] = {}
        for source, result in zip(payload.network_points, geocoded_network, strict=True):
            network_by_id[source.point_id] = {**result, "point_id": source.point_id, "point_type": source.point_type, "city": source.city.strip() or payload.city.strip()}
        demand_values: list[dict[str, Any]] = []
        for source, result in zip(payload.demand_points, geocoded_demand, strict=True):
            demand_values.append({**result, "point_id": source.point_id, "demand_type": source.demand_type})

        nearest_comparison = []
        if payload.auto_nearest:
            for point in network_by_id.values():
                try:
                    response = await request_client.get(f"{AMAP_BASE_URL}/direction/driving", params={
                        "key": key, "origin": ",".join(map(str, point["location"])),
                        "destination": ",".join(map(str, demand_values[0]["location"])), "strategy": "0", "output": "JSON",
                    })
                    response.raise_for_status()
                    body = response.json()
                    paths = (body.get("route") or {}).get("paths") or []
                    if body.get("status") != "1" or not paths:
                        raise ValueError("高德未返回路线")
                    distance = int(paths[0]["distance"])
                    duration = int(paths[0]["duration"])
                    if distance < 0 or duration < 0:
                        raise ValueError("路线数据无效")
                    nearest_comparison.append({"point_id": point["point_id"], "name": point["name"], "distance_meters": distance, "duration_seconds": duration})
                except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                    raise _route_error(502, "NEAREST_COMPARISON_FAILED", f"无法比较网点“{point['name']}”的道路距离，请检查地址或稍后重试", stage="nearest") from exc
            nearest_comparison.sort(key=lambda item: (item["distance_meters"], item["duration_seconds"], item["point_id"]))
            start_id = nearest_comparison[0]["point_id"]
        start_point = network_by_id[start_id]
        assignments = []
        for demand in demand_values:
            assigned = start_point
            assignments.append({
                "demand_point_id": demand["point_id"],
                "demand_name": demand["name"],
                "network_point_id": assigned["point_id"],
                "network_point_name": assigned["name"],
                "distance_degrees": round(math.sqrt(_distance_squared(assigned["location"], demand["location"])), 6),
            })

        ordered_demand = _nearest_neighbor(demand_values, start_point["location"]) if payload.auto_order else demand_values
        route_input = [start_point, *ordered_demand]
        if len(route_input) > 16:
            raise _route_error(422, "TOO_MANY_ROUTE_STOPS", "高德单次路线最多支持 14 个招标学校/单位", stage="validate")
        params: dict[str, str] = {
            "key": key,
            "origin": ",".join(map(str, route_input[0]["location"])),
            "destination": ",".join(map(str, route_input[-1]["location"])),
            "strategy": "0",
            "extensions": "all",
            "output": "JSON",
        }
        if len(route_input) > 2:
            params["waypoints"] = ";".join(",".join(map(str, item["location"])) for item in route_input[1:-1])
        try:
            response = await request_client.get(f"{AMAP_BASE_URL}/direction/driving", params=params)
            response.raise_for_status()
            route_payload = response.json()
            return_payload = None
            if payload.return_to_start:
                return_params = {**params, "origin": params["destination"], "destination": params["origin"]}
                return_params.pop("waypoints", None)
                response = await request_client.get(f"{AMAP_BASE_URL}/direction/driving", params=return_params)
                response.raise_for_status()
                return_payload = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"驾车路线服务请求失败：{exc}") from exc

    paths = (route_payload.get("route") or {}).get("paths") or []
    if route_payload.get("status") != "1" or not paths:
        raise _route_error(
            422,
            "AMAP_ROUTE_FAILED",
            f"高德地图未能规划路线：{_amap_error(route_payload)}",
            stage="driving",
        )
    path = paths[0]
    return_paths = (return_payload.get("route") or {}).get("paths") or [] if return_payload else []
    if payload.return_to_start and (not return_payload or return_payload.get("status") != "1" or not return_paths):
        raise _route_error(422, "AMAP_RETURN_ROUTE_FAILED", f"高德地图未能规划返程：{_amap_error(return_payload or {})}", stage="driving")

    def extract_path(route: dict[str, Any]) -> tuple[list[list[float]], list[dict[str, str]]]:
        points: list[list[float]] = []
        instructions: list[dict[str, str]] = []
        for step in route.get("steps") or []:
            step_points = []
            for pair in str(step.get("polyline") or "").split(";"):
                try:
                    longitude, latitude = pair.split(",", 1)
                    step_points.append([float(longitude), float(latitude)])
                except (ValueError, TypeError):
                    continue
            if step_points:
                points.extend(step_points if not points else step_points[1:] if points[-1] == step_points[0] else step_points)
            instructions.append({"instruction": step.get("instruction") or "", "road": step.get("road") or ""})
        return points, instructions

    outbound_polyline, outbound_steps = extract_path(path)
    return_path = return_paths[0] if return_paths else {}
    return_polyline, return_steps = extract_path(return_path)
    polyline = outbound_polyline + (return_polyline[1:] if outbound_polyline and return_polyline and outbound_polyline[-1] == return_polyline[0] else return_polyline)
    steps = outbound_steps + return_steps

    route_stops = [
        {**start_point, "role": "配送起点"},
        *[{**item, "role": "招标服务点"} for item in ordered_demand],
    ]
    if payload.return_to_start:
        route_stops.append({**start_point, "role": "返回配送中心"})
    distance_meters = int(float(path.get("distance") or 0)) + int(float(return_path.get("distance") or 0))
    duration_seconds = int(float(path.get("duration") or 0)) + int(float(return_path.get("duration") or 0))
    route_svg = _build_route_svg(payload.company_name.strip(), payload.city.strip(), route_stops, outbound_polyline, distance_meters, duration_seconds, return_polyline)
    return {
        "company_name": payload.company_name.strip(),
        "city": payload.city.strip(),
        "network_points": list(network_by_id.values()),
        "demand_points": demand_values,
        "assignments": assignments,
        "start_point_id": start_id,
        "auto_nearest": payload.auto_nearest,
        "nearest_comparison": nearest_comparison,
        "auto_order": payload.auto_order,
        "return_to_start": payload.return_to_start,
        "stops": route_stops,
        "distance_meters": distance_meters,
        "duration_seconds": duration_seconds,
        "polyline": polyline,
        "outbound_polyline": outbound_polyline,
        "return_polyline": return_polyline,
        "steps": steps,
        "route_svg": route_svg,
        "provider": "amap",
    }
