from __future__ import annotations

import base64
import html
import json
import logging
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.core.config import settings
from app.services.qwen_client import chat_completion
from app.services.model_preferences import selected_model

logger = logging.getLogger(__name__)

DiagramType = Literal["framework", "flow", "architecture", "timeline", "organization", "smartart", "smartart_layer", "cycle", "matrix", "swimlane", "platform_layer"]
SmartArtTemplateId = Literal[
    "basic_hierarchy",
    "arrow_process",
    "vertical_card_list",
    "timeline_milestone",
    "radial_relation",
    "hexagon_matrix",
    "quadrant_matrix",
    "step_hierarchy",
    "circle_category",
]
def diagram_model() -> str:
    return selected_model("diagram")

TYPE_LABELS: dict[str, str] = {
    "framework": "框架图",
    "flow": "流程图",
    "architecture": "架构关系图",
    "timeline": "时间轴",
    "organization": "组织架构图",
    "smartart": "SmartArt 层级图",
    "cycle": "循环闭环图",
    "matrix": "矩阵关系图",
    "swimlane": "泳道流程图",
    "platform_layer": "平台分层架构图",
}

SMART_CHART_TYPES: dict[str, str] = {
    "framework": "infographic / concept",
    "flow": "flowchart",
    "architecture": "architecture",
    "timeline": "timeline",
    "organization": "organizational chart",
    "smartart": "hierarchy smartart",
    "cycle": "cycle diagram",
    "matrix": "matrix chart",
    "swimlane": "swimlane process diagram",
    "platform_layer": "layered platform architecture diagram",
}

SIZE_MAP: dict[str, tuple[int, int]] = {
    "wide": (1280, 720),
    "classic43": (1200, 900),
    "a4": (900, 1270),
    "ultrawide": (1680, 720),
    "square": (1024, 1024),
    "portrait": (720, 1280),
}

PALETTE = ["#EAF3F8", "#F5EFE3", "#E9F5EF", "#F8EDEB", "#EEF0FA", "#FFF6D8"]
STROKES = ["#3A3A32", "#3F6F82", "#5E8C61", "#8A6F3D", "#6B7280"]

FONT_PRESETS: dict[str, dict[str, str]] = {
    "default_heiti": {"label": "默认黑体", "css": "SimHei, 'Microsoft YaHei', 'Noto Sans SC', sans-serif", "prompt": "默认黑体"},
    "source_han_sans": {"label": "思源黑体", "css": "'Source Han Sans SC', 'Noto Sans SC', 'Microsoft YaHei', sans-serif", "prompt": "思源黑体"},
    "microsoft_yahei": {"label": "微软雅黑", "css": "'Microsoft YaHei', 'Noto Sans SC', sans-serif", "prompt": "微软雅黑"},
    "simsun": {"label": "宋体", "css": "SimSun, 'Songti SC', serif", "prompt": "宋体"},
    "fangsong": {"label": "仿宋", "css": "FangSong, STFangsong, serif", "prompt": "仿宋"},
}

STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "tech": {
        "label": "商务科技风",
        "prompt": "蓝绿渐变、圆角卡片、轻微阴影、科技但不花哨",
        "palette": ["#EAF3F8", "#E9F5EF", "#EEF0FA", "#F5EFE3", "#E8F4F8", "#EAF7F0"],
        "strokes": ["#3F6F82", "#5E8C61", "#3A3A32", "#6B7280"],
        "shadow": True,
        "radius": 18,
    },
    "mono": {
        "label": "极简黑白风",
        "prompt": "黑白灰、直角矩形、弱阴影或无阴影，适配暗标",
        "palette": ["#F7F7F5", "#EFEDEA", "#FFFFFF", "#E5E2DC", "#F2F2F0"],
        "strokes": ["#2B2B26", "#55534D", "#77736A"],
        "shadow": False,
        "radius": 6,
    },
    "business": {
        "label": "简约商务风",
        "prompt": "蓝色系、轻微阴影、清晰层级、正式商务",
        "palette": ["#EAF2FB", "#F1F6FB", "#E8EEF7", "#F7F4EA", "#EEF3F8"],
        "strokes": ["#2F5F87", "#4E6F91", "#3A3A32"],
        "shadow": True,
        "radius": 14,
    },
    "office": {
        "label": "清爽办公风",
        "prompt": "浅灰底色、细边框、少阴影、清爽办公文档风",
        "palette": ["#F6F7F7", "#EEF4F2", "#F7F3EA", "#F3F5F8", "#FFFFFF"],
        "strokes": ["#6B7280", "#5E8C61", "#3A3A32"],
        "shadow": False,
        "radius": 12,
    },
    "cartoon": {
        "label": "卡通手绘风",
        "prompt": "圆润粗描边、马卡龙渐变配色、圆润清晰字体、轻微投影，活泼但保持商务专业",
        "palette": ["#FFE8EC", "#E4F6F1", "#FFF0C7", "#E9ECFF", "#EAF7D8", "#F5E5FF"],
        "strokes": ["#6B4E71", "#4D7C74", "#8A6A32", "#53628F", "#5F7F45"],
        "shadow": True,
        "radius": 26,
        "stroke_width": 3.2,
        "font_css": "'Microsoft YaHei', 'Noto Sans SC', 'Arial Rounded MT Bold', sans-serif",
    },
    "poster_business": {
        "label": "商务海报风",
        "prompt": "高饱和渐变背景、醒目标题胶囊、立体卡片、轻微光斑装饰，适合汇报展示和标书亮点页",
        "palette": ["#F7C6D8", "#DFF7C4", "#FFF06A", "#D9F2FF", "#F3E7FF", "#E8F7E1"],
        "strokes": ["#202124", "#477A16", "#8A5A00", "#2F5F87"],
        "shadow": True,
        "radius": 24,
        "stroke_width": 2.8,
        "render_mode": "poster_business",
        "font_css": "'Microsoft YaHei', 'Noto Sans SC', SimHei, sans-serif",
    },
    "sticker_cute": {
        "label": "卡通贴纸风",
        "prompt": "马卡龙渐变背景、贴纸卡片、粗描边、柔和阴影和轻量涂鸦装饰，活泼但保证中文清晰",
        "palette": ["#FFB7D5", "#B9F3EE", "#FFF1A8", "#C9C4FF", "#BDF3C4", "#FFD6A5"],
        "strokes": ["#6A4A78", "#4D7C74", "#9C6B1F", "#5B5FA8"],
        "shadow": True,
        "radius": 30,
        "stroke_width": 3.4,
        "render_mode": "sticker_cute",
        "font_css": "'Microsoft YaHei', 'Noto Sans SC', SimHei, sans-serif",
    },
    "bid_process": {
        "label": "标书流程图风格",
        "prompt": "参考正式标书中的流程图与平台架构图，深蓝边框、浅灰渐变底、白色模块、绿色流程节点、细线箭头、泳道分栏，整体克制专业",
        "palette": ["#FFFFFF", "#EFF5FB", "#DCEBFA", "#BFE7A1", "#8FD14F", "#00B050"],
        "strokes": ["#0B376D", "#6B7280", "#2F5597", "#5C8A24"],
        "shadow": True,
        "radius": 6,
        "stroke_width": 1.6,
        "font_css": "'Microsoft YaHei', SimHei, 'Noto Sans SC', sans-serif",
    },
}


SMARTART_TEMPLATES: dict[str, dict[str, Any]] = {
    "basic_hierarchy": {
        "name": "基础层级图",
        "description": "原有上下层级圆角卡片，作为兼容兜底模板",
        "shape": "rounded_rect",
        "layout_rule": "自上而下分层排布，同层节点严格对齐，使用垂直连接线。",
        "office_layout_id": "HorizontalMultilevelList",
        "scene": "通用层级关系、体系框架",
    },
    "arrow_process": {
        "name": "彩色箭头流程",
        "description": "横向连续拼接箭头节点，不同节点渐变配色，节点内文字居中，无单独连线",
        "shape": "arrow_block",
        "layout_rule": "横向连续箭头流程，节点首尾拼接，表达阶段递进和流程步骤。",
        "office_layout_id": "ContinuousArrowProcess",
        "scene": "阶段递进、流程步骤",
    },
    "vertical_card_list": {
        "name": "垂直卡片列表",
        "description": "纵向圆角卡片，左侧圆形序号标记，卡片错落配色，右侧保留说明位",
        "shape": "vertical_card",
        "layout_rule": "纵向列表排布，左侧序号标记清楚，适合模块说明和能力清单。",
        "office_layout_id": "VerticalBlockList",
        "scene": "模块说明、能力清单",
    },
    "timeline_milestone": {
        "name": "时间轴里程碑",
        "description": "中间横向时间主线，圆形节点标记里程碑，上下交替排布说明卡片",
        "shape": "timeline_card",
        "layout_rule": "横向时间轴，里程碑节点沿主线均匀分布，说明卡片上下交替。",
        "office_layout_id": "MilestoneTimeline",
        "scene": "项目周期、实施阶段",
    },
    "radial_relation": {
        "name": "中心辐射关系图",
        "description": "中心主节点圆形，向外辐射多个子节点，子节点为圆角矩形",
        "shape": "radial",
        "layout_rule": "中心主节点位于画布中央，子节点环绕分布并与中心连接。",
        "office_layout_id": "RadialCycle",
        "scene": "核心能力、体系框架",
    },
    "hexagon_matrix": {
        "name": "六边形矩阵",
        "description": "横向排列六边形节点，节点内文字居中，节点间带连接箭头，渐变填充",
        "shape": "hexagon",
        "layout_rule": "六边形节点按行列错落排布，表达并列分类与模块组合。",
        "office_layout_id": "HexagonCluster",
        "scene": "分类说明、并列模块",
    },
    "quadrant_matrix": {
        "name": "四象限卡片矩阵",
        "description": "2x2 网格布局，卡片带图标位、标题和说明，不同象限不同主题色",
        "shape": "quadrant_card",
        "layout_rule": "四象限网格，卡片大小一致、行列对齐，适合维度分析。",
        "office_layout_id": "GridMatrix",
        "scene": "维度分析、能力矩阵",
    },
    "step_hierarchy": {
        "name": "阶梯式层级图",
        "description": "从上到下逐级缩进，阶梯状排布，节点为标签形，层级区分清晰",
        "shape": "tag_step",
        "layout_rule": "自上而下逐级缩进形成阶梯结构，突出组织架构和层级体系。",
        "office_layout_id": "TableHierarchy",
        "scene": "组织架构、层级体系",
    },
    "circle_category": {
        "name": "圆形分类图",
        "description": "纵向圆形序号/图标节点，右侧对应说明矩形，连接线连接",
        "shape": "circle_category",
        "layout_rule": "纵向分类列表，左侧圆形标记与右侧说明卡片一一对应。",
        "office_layout_id": "CircleArrowProcess",
        "scene": "分类说明、要点列举",
    },
}

SMARTART_TEMPLATE_ENUM = tuple(SMARTART_TEMPLATES.keys())
SMARTART_TEMPLATE_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "smartart_template_manifest.json"


# 标书商务图表专用系统提示词。按用户要求单独提取，后续维护只改这里。
BID_BUSINESS_DIAGRAM_SYSTEM_PROMPT = """
你是专业的 Excalidraw 商务图表生成专家，专门为正式标书、项目方案生成标准、规整、专业的图表。
用户输入业务逻辑描述后，你必须输出合法的 Excalidraw JSON 格式元素数据，严格遵守以下所有规则，不得违反：

【布局强制规则】
1. 流程图默认纵向(TB)从上到下排布，框架图默认横向(LR)从左到右排布
2. 同一层级节点必须严格对齐（水平/垂直方向），节点间距均匀一致，禁止高低错落
3. 所有连线强制使用 elbowed:true 的直角折线，绝对禁止直线、斜线、曲线、交叉线、重叠线
4. 连线必须自动绕开节点，禁止穿过节点；同方向连线保持平行间距，分支从节点右侧/下侧引出、左侧/上侧汇入
5. 判断闭环、整改返回类箭头必须从图形外侧绕行，不与主流程连线交叉
6. 整体图表在画布居中，四周留白充足，节点不贴边，整体比例适配指定画幅

【节点内容规则】
1. 单个节点文字严格控制在 3-6 个汉字，只保留核心概念/动作，禁止长句、解释说明、括号补充
2. 节点统一使用圆角矩形，尺寸统一；仅评审/判断类节点可使用菱形
3. 层级≥2层时，用分组(group)或深浅底色区分层级，提升可读性
4. 单张图主节点控制在 4-8 个，总节点≤12 个；内容过多自动合并次要信息，禁止堆砌节点

【逻辑规范规则】
1. 准确识别业务的先后顺序、包含关系、并行分支、评审闭环
2. 评审不通过、整改类逻辑必须画出返回箭头，形成闭环
3. 并行工作节点放在同一层级，用分支连线明确标识
4. 禁止出现孤立节点、悬空连线、无指向箭头

【输出要求】
仅输出合法的 Excalidraw 元素 JSON 数组，包裹在 ```json 代码块中，禁止输出任何解释、说明、寒暄内容，确保可直接被 Excalidraw 渲染。
""".strip()

CHART_LAYOUT_RULES: dict[str, str] = {
    "flow": "当前图表类型为流程图，必须优先使用纵向 TB 布局；主流程自上而下，分支左右展开后回到主线，避免横向长链。",
    "framework": "当前图表类型为框架图，必须优先使用横向 LR 或中心-分组布局；同层模块严格对齐，避免散点式摆放。",
    "architecture": "当前图表类型为架构关系图，必须使用分层结构；平台层、能力层、执行层上下对齐，跨层连线尽量垂直。",
    "timeline": "当前图表类型为时间轴，必须横向按时间顺序排布，节点带阶段标识，线性递进，避免分支杂乱。",
    "organization": "当前图表类型为组织架构图，必须使用树状层级结构，从上到下逐级分支，同层级节点严格对齐。",
    "smartart": "当前图表类型为 SmartArt 层级图，必须严格从上到下分层排布，突出层级/阶段/模块关系，不生成流程返回线。",
    "cycle": "当前图表类型为循环闭环图，必须首尾相连形成完整闭环，箭头沿外圈顺时针连接，禁止交叉。",
    "matrix": "当前图表类型为矩阵关系图，必须使用行列网格排布，展示多维度对应关系，行列对齐清楚。",
    "swimlane": "当前图表类型为泳道流程图，必须按角色、部门、岗位或系统分列，步骤放入对应泳道，跨泳道用水平或直角箭头连接。",
    "platform_layer": "当前图表类型为平台分层架构图，必须使用上中下分层：顶部平台/业务环节，中部业务动作/设备/对象，底部系统支撑模块，跨层连线清晰。",
}

SMARTART_LAYER_RULES = """
【SmartArt 层级图专属规则】
1. 仅提取核心层级、阶段、模块作为主节点；自动过滤“评审不通过返回修改、整改闭环、循环迭代”等流程分支逻辑。
2. 评审/卡点类信息不要生成菱形节点，不要单独占用画布空间，可压缩为普通层级节点短标题。
3. 主层级不超过 6 层，单层节点不超过 5 个，总节点不超过 12 个；超出时合并次要信息。
4. 布局必须严格自上而下分层，同一层节点处于同一水平线，水平间距完全均等，整体画布居中。
5. 节点统一为圆角矩形且尺寸一致，层级越深底色越浅，整体符合 Office SmartArt 的干净规整视觉。
6. 连线全部为上下垂直正交线，从上一层节点底部中点连接到下一层节点顶部中点；禁止折线、斜线、绕线、交叉线。
""".strip()

DIAGRAM_TYPE_ENUM = tuple(TYPE_LABELS.keys())

USER_PROMPT_PREFIX_TEMPLATE = """
请生成一份标准商务风格的{chart_type}，严格遵守上述系统规则。
以下是业务内容：
""".strip()

USER_PROMPT_SUFFIX = """
请严格遵循规则生成，确保层级清晰、对齐工整、无交叉连线，逻辑顺序准确。
""".strip()


TEXT_STRUCTURE_DECISION_SYSTEM_PROMPT = """
你是标书商务逻辑图的“结构分析 + 图表类型决策”专家。
你的任务是在生成图表前，先把用户输入的业务文本稳定解析为 title + modules + items，并自动选择最适合的图表类型。

【输出格式】
只输出一个 JSON 代码块，不要解释、不要寒暄、不要 Markdown 列表：
```json
{
  "title": "完整图表主题",
  "modules": [
    {"name": "一级模块名", "items": ["二级子项1", "二级子项2"]}
  ],
  "chart_type": "card_matrix",
  "template_id": "quadrant_matrix",
  "confidence": 0.9,
  "reason": "4个并列模块带子项，适合卡片矩阵展示"
}
```

当 chart_type="swimlane" 时，必须额外返回 sequence，按原文真实发生顺序列出 5-12 个步骤；
每个步骤只归属一个已存在的模块/角色，label 为 2-10 个字的完整动作短语：
```json
"sequence": [
  {"lane": "收货人员", "label": "信息确认"},
  {"lane": "软件系统", "label": "调阅采购单据"},
  {"lane": "收货人员", "label": "验货称重"}
]
```

【层级解析规则】
1. title：整个图表的总主题，通常取首句完整核心主题，禁止半句、过渡句。
2. modules：并列一级模块，必须是完整名词短语，禁止把修饰语、连接词、半截短语当模块。
3. items：模块下的具体能力/内容。原文出现“A：B、C、D”时，A 必须是 module，B/C/D 必须进入 items。
4. 模块数量优先保留 2-8 个；超过 8 个时合并次要模块为“其他要点”。
5. 节点名称控制在 2-12 个汉字，保留完整语义，不要碎片化。

【图表类型决策规则】
1. 含角色/部门/岗位/系统分工，且步骤需要跨角色流转 → chart_type="swimlane"。
2. 含平台、系统模块、业务环节、上下层支撑、追溯/监管/数据上传 → chart_type="platform_layer"。
3. 含时间顺序、阶段、步骤、先后流程 → chart_type="flow"。
4. 含里程碑、周期、时间节点、阶段递进 → chart_type="timeline"。
5. 含总-分结构、并列模块、每个模块带子能力：模块数 2-4 个 → chart_type="card_matrix", template_id="quadrant_matrix"；模块数 5-8 个 → chart_type="vertical_card_list", template_id="vertical_card_list"。
6. 含层级、体系、组织、上下级、分管关系 → chart_type="organization"。
7. 含循环、闭环、迭代、持续优化、首尾相连 → chart_type="cycle"。
8. 含维度、矩阵、象限、分类对应关系 → chart_type="matrix"。
9. 结构不明确、通用说明性文本 → chart_type="smartart_hierarchy", template_id="basic_hierarchy"。
10. confidence 必须是 0-1 数字；把握不足时低于 0.7，系统会自动降级为稳定层级图。

【Few-shot 示例】
输入：
智慧园区四大核心能力模块，并列支撑整体运营：
安防管控模块：视频监控、周界防护、人员通行
设施运维模块：设备巡检、故障告警、能耗管理
办公服务模块：访客登记、会议预约、物业服务
数据驾驶舱：全局看板、数据分析、预警调度

正确输出：
```json
{
  "title": "智慧园区四大核心能力模块",
  "modules": [
    {"name": "安防管控模块", "items": ["视频监控", "周界防护", "人员通行"]},
    {"name": "设施运维模块", "items": ["设备巡检", "故障告警", "能耗管理"]},
    {"name": "办公服务模块", "items": ["访客登记", "会议预约", "物业服务"]},
    {"name": "数据驾驶舱", "items": ["全局看板", "数据分析", "预警调度"]}
  ],
  "chart_type": "card_matrix",
  "template_id": "quadrant_matrix",
  "confidence": 0.9,
  "reason": "4个并列模块带子项，适合卡片矩阵展示"
}
```

输入：
加工人员获取加工指令，完成挑选、清洗和装箱后交由运输人员接货，运输人员识别托盘标签并完成入库，软件系统同步生成加工指令、出库单并更新生产进度。

正确输出：
```json
{
  "title": "净菜加工协同流程",
  "modules": [
    {"name": "加工人员", "items": ["获取加工指令", "挑选清洗复检", "装箱推托"]},
    {"name": "运输人员", "items": ["接货入库", "识别标签", "运输交付"]},
    {"name": "软件系统", "items": ["生成指令", "生成出库单", "更新进度"]}
  ],
  "sequence": [
    {"lane": "软件系统", "label": "生成加工指令"},
    {"lane": "加工人员", "label": "获取加工指令"},
    {"lane": "加工人员", "label": "挑选清洗复检"},
    {"lane": "加工人员", "label": "装箱推托"},
    {"lane": "运输人员", "label": "接货入库"},
    {"lane": "软件系统", "label": "更新生产进度"}
  ],
  "chart_type": "swimlane",
  "template_id": "basic_hierarchy",
  "confidence": 0.9,
  "reason": "文本包含多个角色和跨角色流程流转，适合泳道流程图"
}
```

输入：
重要产品追溯平台覆盖生产、流通、销售三个环节，RFID周转箱采集标签信息，检测和追溯码信息上传至平台，底部由SRM、WMS、FPS、TMS、OMS等系统支撑。

正确输出：
```json
{
  "title": "重要产品追溯平台",
  "modules": [
    {"name": "生产环节", "items": ["RFID周转箱", "标签查询", "信息返回"]},
    {"name": "流通环节", "items": ["进场补录", "检测上传", "追溯码生成"]},
    {"name": "销售环节", "items": ["销售上传", "物流采集", "终端追溯"]},
    {"name": "支撑系统", "items": ["SRM", "WMS", "FPS", "TMS", "OMS"]}
  ],
  "chart_type": "platform_layer",
  "template_id": "basic_hierarchy",
  "confidence": 0.9,
  "reason": "文本包含平台、业务环节和底部系统支撑，适合平台分层架构图"
}
```
""".strip()


SMARTART_STRUCTURE_SYSTEM_PROMPT = """
你是标书商务图表的 SmartArt 结构解析专家。你的任务不是写文案，也不是直接生成 SVG/Excalidraw，而是把用户输入的业务段落解析为稳定的结构化 JSON，用于后端生成 SmartArt 图表。

【层级定义】
1. title：整个图表的总主题。通常来自文段首句的完整核心主题，必须是完整语义，禁止截取半句。
2. modules：一级核心模块。它们必须是并列的独立单元，每个模块 name 必须是完整语义的名词短语。
3. items：每个模块下的二级子项。items 必须与对应 module 是“包含/组成/能力/内容”关系。

【强制输出格式】
仅输出以下 JSON，必须包裹在 ```json 代码块中，禁止输出解释文字：
```json
{
  "title": "完整图表主题",
  "modules": [
    {
      "name": "一级模块名称",
      "items": ["二级子项1", "二级子项2"]
    }
  ]
}
```

【输入示例】
智慧园区四大核心能力模块，并列支撑整体运营：
安防管控模块：视频监控、周界防护、人员通行
设施运维模块：设备巡检、故障告警、能耗管理
办公服务模块：访客登记、会议预约、物业服务
数据驾驶舱：全局看板、数据分析、预警调度

【正确输出示例】
```json
{
  "title": "智慧园区四大核心能力模块",
  "modules": [
    {
      "name": "安防管控模块",
      "items": ["视频监控", "周界防护", "人员通行"]
    },
    {
      "name": "设施运维模块",
      "items": ["设备巡检", "故障告警", "能耗管理"]
    },
    {
      "name": "办公服务模块",
      "items": ["访客登记", "会议预约", "物业服务"]
    },
    {
      "name": "数据驾驶舱",
      "items": ["全局看板", "数据分析", "预警调度"]
    }
  ]
}
```

【错误示例，必须避免】
1. 把“智慧园区四大”“并列支撑整体”当成节点 —— 这是半截语义，严重错误。
2. 把“并列支撑”“整体运营”“以及”“同时”等修饰语、过渡句、连接词当成模块 —— 严重错误。
3. 遇到“A：B、C、D”却输出 A、B、C、D 四个并列模块 —— 严重错误；正确结构是 A 为 module，B/C/D 为 items。
4. 遗漏冒号后面的子项 —— 严重错误。
5. 输出 SVG、Excalidraw、Markdown 列表、解释说明 —— 严重错误。

【硬性规则】
1. module.name 长度控制在 2-12 个汉字，确需保留行业词时最多 15 个汉字。
2. item 长度控制在 2-12 个汉字，去掉冗余修饰词，只保留核心名词/动作名词。
3. modules 数量 1-8 个；信息过多时合并次要模块，不要碎片化。
4. items 可为空数组；但原文出现“A：B、C、D”结构时，必须把 B/C/D 放入 A 的 items。
5. 严格基于原文，不编造原文没有的模块和子项。
6. 只输出 JSON 代码块，不要输出任何寒暄或解释。
""".strip()


class DiagramImageServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def build_diagram_prompt(
    paragraph: str,
    *,
    diagram_type: str = "framework",
    style_hint: str = "商务科技风",
    template_id: str = "basic_hierarchy",
) -> str:
    cleaned = "\n".join(line.strip() for line in (paragraph or "").splitlines() if line.strip())[:2200]
    if len(re.sub(r"\s+", "", cleaned)) < 20:
        raise DiagramImageServiceError("请先输入一段需要转成图示的标书逻辑内容，至少20字")

    diagram_label = TYPE_LABELS.get(diagram_type, "框架图")
    template = SMARTART_TEMPLATES.get(template_id, SMARTART_TEMPLATES["basic_hierarchy"])
    smartart_line = f"\nSmartArt 样式：{template['name']}；适用场景：{template['scene']}。" if diagram_type == "smartart" else ""
    return f"""
{USER_PROMPT_PREFIX_TEMPLATE.format(chart_type=diagram_label)}
{smartart_line}
{cleaned}

{USER_PROMPT_SUFFIX}
""".strip()


def build_system_prompt(*, diagram_type: str, style_hint: str, size: str, font_family: str = "default_heiti", style_preset: str = "tech", template_id: str = "basic_hierarchy") -> str:
    """Inject frontend parameters into the dedicated bid-diagram system prompt."""

    diagram_label = TYPE_LABELS.get(diagram_type, "框架图")
    smart_type = SMART_CHART_TYPES.get(diagram_type, "flowchart")
    style = (style_hint or "商务科技风").strip()[:100]
    aspect = _aspect_label(size)
    layout_rule = CHART_LAYOUT_RULES.get(diagram_type, CHART_LAYOUT_RULES["framework"])
    font = FONT_PRESETS.get(font_family, FONT_PRESETS["default_heiti"])
    style_config = STYLE_PRESETS.get(style_preset, STYLE_PRESETS["tech"])
    template = SMARTART_TEMPLATES.get(template_id, SMARTART_TEMPLATES["basic_hierarchy"])
    smartart_rules = f"\n\n{SMARTART_LAYER_RULES}" if diagram_type == "smartart" else ""
    template_rules = (
        f"\n\n【SmartArt 模板规则】\n"
        f"1. 当前模板：{template['name']}（{template_id}）。\n"
        f"2. 模板样式：{template['description']}。\n"
        f"3. 排版要求：{template['layout_rule']}\n"
        f"4. 适用场景：{template['scene']}；节点数量仍需控制在 4-8 个，最多 12 个。"
        if diagram_type == "smartart"
        else ""
    )
    external_reference = _smartart_template_reference_prompt(template_id) if diagram_type == "smartart" else ""
    return f"""
{BID_BUSINESS_DIAGRAM_SYSTEM_PROMPT}
{smartart_rules}
{template_rules}
{external_reference}

【前端动态参数】
1. 当前图表类型：{diagram_label}（Excalidraw参考类型：{smart_type}）
2. 当前画幅比例：{aspect}，输出坐标必须按该比例居中规划，四周至少保留 70px 安全边距
3. 当前字体：{font['prompt']}，所有节点 label 与文字元素必须统一使用该字体风格
4. 当前美化风格：{style_config['label']}；风格说明：{style_config['prompt']}；用户补充：{style}
5. {layout_rule}

【Excalidraw JSON 细化约束】
1. 数组元素只允许 rectangle、diamond、arrow、text；普通节点优先 rectangle，判断节点才允许 diamond
2. rectangle/diamond 节点必须包含 id、x、y、width、height、backgroundColor、strokeColor、label.text
3. arrow 必须包含 start.id、end.id、strokeColor、elbowed:true；禁止使用未绑定的悬空箭头
4. label.text 必须为中文短词，严格 3-6 个汉字；不得包含逗号、句号、括号、长解释
5. 不要输出 title/summary 对象，只输出 Excalidraw 元素 JSON 数组
6. 所有 arrow 必须带 elbowed:true，返回/闭环箭头必须从图形外侧绕行，禁止穿过任何节点
""".strip()


def _aspect_label(size: str) -> str:
    if size == "square":
        return "1:1"
    if size in {"portrait", "a4"}:
        return "A4竖向" if size == "a4" else "9:16"
    if size == "classic43":
        return "4:3"
    if size == "ultrawide":
        return "21:9"
    if size == "portrait":
        return "9:16"
    return "16:9"


async def generate_diagram_image(
    paragraph: str,
    *,
    diagram_type: str = "framework",
    template_id: str = "basic_hierarchy",
    style_hint: str = "商务科技风",
    size: str = "wide",
    font_family: str = "default_heiti",
    style_preset: str = "tech",
    auto_match: bool = False,
    auto_mode: bool = True,
) -> dict[str, Any]:
    if not settings.qwen_api_key.strip():
        logger.warning("[Smart-Excalidraw] QWEN_API_KEY is empty; rule fallback diagram will be used.")

    requested_diagram_type = _normalize_diagram_type(diagram_type) or "framework"
    resolved_diagram_type = requested_diagram_type
    requested_template_id = _normalize_smartart_template(template_id) or "basic_hierarchy"
    resolved_template_id = requested_template_id
    template_auto_matched = False
    structure_analysis: dict[str, Any] | None = None
    analysis_reason = ""
    analysis_confidence: float | None = None

    if auto_mode:
        try:
            structure_analysis = await analyze_text_structure(paragraph)
            resolved_diagram_type, resolved_template_id = _resolve_analysis_target(
                structure_analysis,
                fallback_diagram_type="smartart",
                fallback_template_id="basic_hierarchy",
            )
            template_auto_matched = resolved_template_id != requested_template_id
            analysis_reason = str(structure_analysis.get("reason") or "")[:120]
            analysis_confidence = _safe_float(structure_analysis.get("confidence"), 0.0, 0.0, 1.0)
        except Exception as exc:  # noqa: BLE001 - demo mode must always return a stable diagram.
            logger.exception("[DiagramAnalyze] auto analysis failed; stable SmartArt fallback used. error=%s", exc)
            fallback_structure = _fallback_parse_smartart_structure(paragraph)
            structure_analysis = {
                **fallback_structure,
                "chart_type": "smartart_hierarchy",
                "template_id": "basic_hierarchy",
                "confidence": 0.0,
                "reason": "自动分析异常，已降级为稳定层级图",
            }
            resolved_diagram_type = "smartart"
            resolved_template_id = "basic_hierarchy"
            template_auto_matched = resolved_template_id != requested_template_id
            analysis_reason = str(structure_analysis.get("reason") or "")
            analysis_confidence = 0.0
    else:
        if auto_match and requested_diagram_type != "smartart":
            resolved_diagram_type = await _classify_diagram_type(paragraph, requested_diagram_type)
        if resolved_diagram_type == "smartart" and auto_match:
            resolved_template_id = await _classify_smartart_template(paragraph, requested_template_id)
            template_auto_matched = resolved_template_id != requested_template_id

    width, height = SIZE_MAP.get(size, SIZE_MAP["wide"])
    prompt = build_diagram_prompt(paragraph, diagram_type=resolved_diagram_type, style_hint=style_hint, template_id=resolved_template_id)
    style_config = STYLE_PRESETS.get(style_preset, STYLE_PRESETS["tech"])
    font_config = FONT_PRESETS.get(font_family, FONT_PRESETS["default_heiti"])
    smartart_structure: dict[str, Any] | None = None
    parsed: dict[str, Any]
    if auto_mode and structure_analysis:
        smartart_structure = {
            "title": structure_analysis.get("title") or "标书逻辑结构",
            "modules": structure_analysis.get("modules") or [],
            "sequence": structure_analysis.get("sequence") or [],
        }
        if str(structure_analysis.get("chart_type") or "") == "swimlane":
            smartart_structure = _normalize_swimlane_structure(paragraph, smartart_structure)
        parsed = {"title": smartart_structure["title"], "summary": analysis_reason or "AI 已完成结构分析并自动匹配图表类型"}
        elements = _structure_to_diagram_elements(smartart_structure, resolved_diagram_type, resolved_template_id)
    elif resolved_diagram_type in {"smartart", "swimlane", "platform_layer"}:
        smartart_structure = await _parse_smartart_structure(paragraph, template_id=resolved_template_id)
        if resolved_diagram_type == "swimlane":
            smartart_structure = _normalize_swimlane_structure(paragraph, smartart_structure)
        parsed = {"title": smartart_structure["title"], "summary": "根据标书逻辑解析出的 SmartArt 结构"}
        elements = _structure_to_diagram_elements(smartart_structure, resolved_diagram_type, resolved_template_id)
    else:
        system_prompt = build_system_prompt(diagram_type=resolved_diagram_type, style_hint=style_hint, size=size, font_family=font_family, style_preset=style_preset, template_id=resolved_template_id)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        logger.info("[Smart-Excalidraw] requesting diagram elements model=%s type=%s", diagram_model(), resolved_diagram_type)
        result = await chat_completion(messages, temperature=0.12, max_tokens=3600, fallback="", json_mode=False, model=diagram_model())
        parsed = _parse_llm_json(result.content)
        if not parsed:
            logger.warning("[Smart-Excalidraw] LLM JSON parse failed; fallback diagram used. error=%s", result.error)
            parsed = _fallback_diagram(paragraph, resolved_diagram_type)

        elements = _sanitize_elements(parsed.get("elements"), paragraph, resolved_diagram_type)
        if not elements:
            elements = _fallback_diagram(paragraph, resolved_diagram_type)["elements"]
    elements = _auto_layout_elements(elements, width, height, resolved_diagram_type, style_config, resolved_template_id)

    template = SMARTART_TEMPLATES.get(resolved_template_id, SMARTART_TEMPLATES["basic_hierarchy"])
    template_reference = _smartart_template_reference(resolved_template_id) if resolved_diagram_type == "smartart" else None
    title = str(parsed.get("title") or (template["name"] if resolved_diagram_type == "smartart" else TYPE_LABELS.get(resolved_diagram_type, "逻辑图")))[:18]
    summary = ""
    svg = _render_svg(elements, width, height, title, summary, font_config, style_config)
    image_url = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")

    return {
        "model": f"smart-excalidraw + {diagram_model()}",
        "image_url": image_url,
        "svg": svg,
        "elements": elements,
        "prompt": prompt,
        "size": f"{width}*{height}",
        "title": title,
        "summary": summary,
        "used_image_model": False,
        "font_family": font_family,
        "style_preset": style_preset,
        "diagram_type": resolved_diagram_type,
        "diagram_type_label": TYPE_LABELS.get(resolved_diagram_type, "逻辑图"),
        "template_id": resolved_template_id if resolved_diagram_type == "smartart" else None,
        "template_name": template["name"] if resolved_diagram_type == "smartart" else None,
        "template_scene": template["scene"] if resolved_diagram_type == "smartart" else None,
        "template_source": template_reference,
        "smartart_structure": smartart_structure,
        "auto_matched": bool(auto_mode or auto_match),
        "template_auto_matched": template_auto_matched,
        "auto_mode": bool(auto_mode),
        "analysis_reason": analysis_reason,
        "analysis_confidence": analysis_confidence,
        "analysis_chart_type": structure_analysis.get("chart_type") if structure_analysis else None,
    }


async def analyze_text_structure(text: str) -> dict[str, Any]:
    """Analyze text once: extract structure and recommend the safest chart type."""

    cleaned = "\n".join(line.strip() for line in (text or "").splitlines() if line.strip())[:2600]
    fallback = _fallback_analyze_text_structure(cleaned)
    if not settings.qwen_api_key.strip():
        return fallback

    messages = [
        {"role": "system", "content": TEXT_STRUCTURE_DECISION_SYSTEM_PROMPT},
        {"role": "user", "content": f"请分析以下标书业务文本：\n{cleaned}"},
    ]
    logger.info("[DiagramAnalyze] requesting structure decision model=%s", diagram_model())
    result = await chat_completion(messages, temperature=0.02, max_tokens=2200, fallback="", json_mode=False, model=diagram_model())
    parsed = _parse_llm_json(result.content)
    ok, validated, errors = _validate_smartart_structure(parsed)
    chart_type = _normalize_analysis_chart_type(str(parsed.get("chart_type") or "")) if isinstance(parsed, dict) else None
    confidence = _safe_float(parsed.get("confidence") if isinstance(parsed, dict) else None, 0.0, 0.0, 1.0)
    if ok and chart_type:
        template_id = _analysis_template_id(chart_type, validated, str(parsed.get("template_id") or "") if isinstance(parsed, dict) else "")
        if confidence < 0.7:
            chart_type = "smartart_hierarchy"
            template_id = "basic_hierarchy"
        return {
            **validated,
            "chart_type": chart_type,
            "template_id": template_id,
            "confidence": confidence,
            "reason": str(parsed.get("reason") or _analysis_reason(chart_type, validated))[:120],
        }

    logger.warning("[DiagramAnalyze] validation failed; retrying once. errors=%s raw=%s", errors, result.content[:300])
    retry_prompt = f"""
上一次输出无法用于绘图，请严格修正为指定 JSON 结构。
校验错误：{'; '.join(errors) or 'chart_type 缺失或非法'}

原始文本：
{cleaned}

上一次输出：
{result.content[:1800]}
""".strip()
    retry = await chat_completion(
        [
            {"role": "system", "content": TEXT_STRUCTURE_DECISION_SYSTEM_PROMPT},
            {"role": "user", "content": retry_prompt},
        ],
        temperature=0,
        max_tokens=2200,
        fallback="",
        json_mode=False,
        model=diagram_model(),
    )
    retry_parsed = _parse_llm_json(retry.content)
    retry_ok, retry_validated, retry_errors = _validate_smartart_structure(retry_parsed)
    retry_chart_type = _normalize_analysis_chart_type(str(retry_parsed.get("chart_type") or "")) if isinstance(retry_parsed, dict) else None
    retry_confidence = _safe_float(retry_parsed.get("confidence") if isinstance(retry_parsed, dict) else None, 0.0, 0.0, 1.0)
    if retry_ok and retry_chart_type:
        retry_template_id = _analysis_template_id(retry_chart_type, retry_validated, str(retry_parsed.get("template_id") or "") if isinstance(retry_parsed, dict) else "")
        if retry_confidence < 0.7:
            retry_chart_type = "smartart_hierarchy"
            retry_template_id = "basic_hierarchy"
        return {
            **retry_validated,
            "chart_type": retry_chart_type,
            "template_id": retry_template_id,
            "confidence": retry_confidence,
            "reason": str(retry_parsed.get("reason") or _analysis_reason(retry_chart_type, retry_validated))[:120],
        }

    logger.warning("[DiagramAnalyze] retry failed; rule fallback used. errors=%s raw=%s", retry_errors, retry.content[:300])
    return fallback


def _fallback_analyze_text_structure(text: str) -> dict[str, Any]:
    structure = _fallback_parse_smartart_structure(text)
    chart_type = _heuristic_analysis_chart_type(text, structure)
    template_id = _analysis_template_id(chart_type, structure, "")
    return {
        **structure,
        "chart_type": chart_type,
        "template_id": template_id,
        "confidence": 0.72 if chart_type != "smartart_hierarchy" else 0.62,
        "reason": _analysis_reason(chart_type, structure),
    }


def _resolve_analysis_target(analysis: dict[str, Any], *, fallback_diagram_type: str, fallback_template_id: str) -> tuple[str, str]:
    confidence = _safe_float(analysis.get("confidence"), 0.0, 0.0, 1.0)
    chart_type = _normalize_analysis_chart_type(str(analysis.get("chart_type") or ""))
    if not chart_type or confidence < 0.7:
        return "smartart", "basic_hierarchy"
    mapping: dict[str, tuple[str, str]] = {
        "flow": ("flow", fallback_template_id),
        "timeline": ("timeline", "timeline_milestone"),
        "card_matrix": ("smartart", "quadrant_matrix"),
        "vertical_card_list": ("smartart", "vertical_card_list"),
        "organization": ("organization", "step_hierarchy"),
        "cycle": ("cycle", "circle_category"),
        "matrix": ("matrix", "quadrant_matrix"),
        "swimlane": ("swimlane", "basic_hierarchy"),
        "platform_layer": ("platform_layer", "basic_hierarchy"),
        "smartart_hierarchy": ("smartart", "basic_hierarchy"),
    }
    return mapping.get(chart_type, (fallback_diagram_type, fallback_template_id))


def _structure_to_diagram_elements(structure: dict[str, Any], diagram_type: str, template_id: str) -> list[dict[str, Any]]:
    if diagram_type == "smartart":
        return _smartart_structure_to_elements(structure, template_id)

    modules = _normalize_modules_for_template(structure.get("modules") if isinstance(structure.get("modules"), list) else [], max_modules=10 if diagram_type in {"flow", "timeline"} else 8)
    if diagram_type in {"flow", "timeline", "cycle"} and len(modules) == 1 and modules[0].get("items"):
        modules = [{"name": item, "items": []} for item in modules[0]["items"][:10]]
    elements: list[dict[str, Any]] = []
    for index, module in enumerate(modules[:10], 1):
        name = _clean_smartart_text(str(module.get("name") or ""), max_len=12, allow_short=False) or f"节点{index}"
        items = module.get("items") if isinstance(module.get("items"), list) else []
        if diagram_type in {"framework", "architecture", "organization", "matrix"} and items:
            label_text = name
        else:
            label_text = name
        elements.append(
            {
                "id": f"node{index}",
                "type": "rectangle",
                "x": 80 + (index - 1) * 210,
                "y": 260,
                "width": 190,
                "height": 86,
                "label": {"text": _short_node_text(label_text), "fontSize": 18, "strokeColor": "#2B2B26"},
                "moduleName": name,
                "items": [_clean_smartart_text(str(item), max_len=12, allow_short=True) for item in items if str(item).strip()][:8],
            }
        )
        if index > 1 and diagram_type in {"flow", "timeline", "cycle"}:
            elements.append({"id": f"arrow{index - 1}", "type": "arrow", "start": {"id": f"node{index - 1}"}, "end": {"id": f"node{index}"}, "elbowed": True})
    return elements or _fallback_diagram(str(structure.get("title") or "标书逻辑结构"), diagram_type)["elements"]


def _normalize_swimlane_structure(text: str, structure: dict[str, Any]) -> dict[str, Any]:
    """Convert long narrative text into stable role lanes and concise action steps."""

    source = re.sub(r"\s+", "", text or "")
    role_candidates = [
        "加工人员", "收货人员", "检测人员", "运输人员", "采购人员", "仓储人员", "质检人员",
        "操作人员", "管理人员", "项目人员", "客服人员", "司机", "管理员", "软件系统", "业务系统", "系统", "平台",
    ]
    role_pattern = re.compile("|".join(re.escape(role) for role in sorted(role_candidates, key=len, reverse=True)))
    occurrences = [(match.start(), match.end(), match.group(0)) for match in role_pattern.finditer(source)]
    roles = _unique_keep_order([role for _, _, role in occurrences])
    roles = [role for role in roles if not any(other != role and role in other for other in roles)]
    roles = roles[:5]
    if len(roles) < 2:
        return structure

    role_set = set(roles[:5])
    model_sequence: list[dict[str, str]] = []
    raw_model_sequence = structure.get("sequence")
    if isinstance(raw_model_sequence, list):
        for step in raw_model_sequence[:12]:
            if not isinstance(step, dict):
                continue
            lane = str(step.get("lane") or "").strip()
            label = _clean_smartart_text(str(step.get("label") or ""), max_len=12, allow_short=True)
            matched_lane = next((role for role in roles[:5] if lane == role or lane.endswith(role) or role.endswith(lane)), "")
            if matched_lane in role_set and label and _cjk_len(label) >= 2:
                model_sequence.append({"lane": matched_lane, "label": label})

    action_patterns = [
        (r"信息确认", "信息确认"),
        (r"调阅采购单[^，；。]*物流单据", "调阅采购物流单据"),
        (r"验货", "验货"),
        (r"清点称重", "清点称重"),
        (r"取样检测", "取样检测"),
        (r"(?:录入|上传)[^，；。]*检测结果", "上传检测结果"),
        (r"暂估分配库位", "暂估分配库位"),
        (r"关联入场批次", "关联入场批次"),
        (r"正式分配库位", "正式分配库位"),
        (r"倒框(?:[/／、]拆箱)?", "倒框拆箱"),
        (r"装箱堆托", "装箱堆托"),
        (r"整托称重", "整托称重"),
        (r"识别托盘标签", "识别托盘标签"),
        (r"录入[^，；。]*(?:品类|箱数)[^，；。]*信息", "录入品类箱数"),
        (r"计算每箱均重", "计算每箱均重"),
        (r"收货区暂存", "收货区暂存"),
        (r"完成入库作业", "完成入库作业"),
        (r"生成入库单", "生成入库单"),
        (r"计算损耗", "计算损耗"),
    ]

    sequence: list[dict[str, str]] = []
    actions_by_role: dict[str, list[str]] = {role: [] for role in roles}
    for index, (_, end, raw_role) in enumerate(occurrences):
        role = raw_role
        if role == "系统" and "软件系统" in role_set:
            role = "软件系统"
        elif role == "系统" and "业务系统" in role_set:
            role = "业务系统"
        if role not in role_set:
            continue
        next_start = occurrences[index + 1][0] if index + 1 < len(occurrences) else len(source)
        clause = source[end:next_start]
        matched_actions: list[tuple[int, str]] = []
        for pattern, label in action_patterns:
            match = re.search(pattern, clause)
            if match:
                matched_actions.append((match.start(), label))
        matched_actions.sort(key=lambda item: item[0])
        labels = _unique_keep_order([label for _, label in matched_actions])
        if not labels:
            fallback_part = re.split(r"[，；。]", clause, maxsplit=1)[0]
            fallback_part = re.sub(r"^(先|再|继续|同步|随后|按照|开展|完成|进行|执行|负责|将|后)", "", fallback_part)
            fallback_label = _clean_smartart_text(fallback_part, max_len=10, allow_short=True)
            if fallback_label and _cjk_len(fallback_label) >= 2 and fallback_label not in {"交由", "之后", "检测完成后"}:
                labels = [fallback_label]
        for label in labels[:3]:
            actions_by_role[role].append(label)
            sequence.append({"lane": role, "label": label})

    modules = [
        {"name": role, "items": _unique_keep_order(actions_by_role.get(role, []))[:6]}
        for role in roles
        if actions_by_role.get(role)
    ]

    if len(modules) < 2:
        return structure
    valid_lanes = {module["name"] for module in modules}
    clean_sequence = [step for step in model_sequence if step["lane"] in valid_lanes][:12]
    if len(clean_sequence) < 6:
        clean_sequence = [step for step in sequence if step["lane"] in valid_lanes][:12]
    if not clean_sequence:
        clean_sequence = [
            {"lane": module["name"], "label": item}
            for module in modules
            for item in module.get("items", [])[:3]
        ][:12]
    if "货车到位" in source and not any("货车到位" in step["label"] for step in clean_sequence):
        clean_sequence.insert(0, {"lane": modules[0]["name"], "label": "货车到位"})
    clean_sequence = clean_sequence[:12]
    return {
        "title": structure.get("title") or _fallback_smartart_title(source),
        "modules": modules,
        "sequence": clean_sequence,
    }


def _normalize_analysis_chart_type(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z_\-\u4e00-\u9fff]", "", text)
    aliases = {
        "flow": "flow",
        "flowchart": "flow",
        "流程图": "flow",
        "timeline": "timeline",
        "time_axis": "timeline",
        "时间轴": "timeline",
        "card_matrix": "card_matrix",
        "cardmatrix": "card_matrix",
        "quadrant_matrix": "card_matrix",
        "卡片矩阵": "card_matrix",
        "四象限": "card_matrix",
        "vertical_card_list": "vertical_card_list",
        "verticalcardlist": "vertical_card_list",
        "垂直卡片列表": "vertical_card_list",
        "organization": "organization",
        "org_chart": "organization",
        "组织架构图": "organization",
        "cycle": "cycle",
        "循环闭环图": "cycle",
        "闭环图": "cycle",
        "matrix": "matrix",
        "矩阵关系图": "matrix",
        "矩阵图": "matrix",
        "swimlane": "swimlane",
        "swimlane_process": "swimlane",
        "泳道": "swimlane",
        "泳道图": "swimlane",
        "泳道流程图": "swimlane",
        "platform_layer": "platform_layer",
        "layered_platform": "platform_layer",
        "平台分层": "platform_layer",
        "平台分层图": "platform_layer",
        "平台分层架构图": "platform_layer",
        "smartart_hierarchy": "smartart_hierarchy",
        "smartarthierarchy": "smartart_hierarchy",
        "smartart层级图": "smartart_hierarchy",
        "层级图": "smartart_hierarchy",
    }
    return aliases.get(text)


def _analysis_template_id(chart_type: str, structure: dict[str, Any], raw_template_id: str) -> str:
    explicit = _normalize_smartart_template(raw_template_id)
    if explicit and chart_type == "smartart_hierarchy":
        return explicit
    module_count = len(structure.get("modules") or []) if isinstance(structure, dict) else 0
    if chart_type == "card_matrix":
        return "quadrant_matrix"
    if chart_type == "vertical_card_list":
        return "vertical_card_list"
    if chart_type == "timeline":
        return "timeline_milestone"
    if chart_type == "organization":
        return "step_hierarchy"
    if chart_type == "cycle":
        return "circle_category"
    if chart_type == "matrix":
        return "quadrant_matrix" if module_count <= 8 else "hexagon_matrix"
    return "basic_hierarchy"


def _heuristic_analysis_chart_type(text: str, structure: dict[str, Any]) -> str:
    compact = re.sub(r"\s+", "", text or "")
    module_count = len(structure.get("modules") or []) if isinstance(structure, dict) else 0
    if re.search(r"(泳道|角色|岗位|部门|人员|加工人员|运输人员|系统|软件系统|跨部门|协同流转|办理人)", compact) and re.search(r"(流程|流转|生成|接收|提交|审核|处理|入库|出库|上传|交付)", compact):
        return "swimlane"
    if re.search(r"(平台|系统模块|信息平台|追溯平台|监管平台|数据平台|支撑系统|底部系统|上层|下层)", compact) and re.search(r"(环节|模块|上传|采集|流通|销售|生产|SRM|WMS|TMS|OMS|FPS)", compact, re.IGNORECASE):
        return "platform_layer"
    if re.search(r"(闭环|循环|迭代|持续优化|反馈|复盘)", compact):
        return "cycle"
    if re.search(r"(里程碑|时间节点|周期|月份|日期|阶段递进|路线图)", compact):
        return "timeline"
    if re.search(r"(步骤|流程|先后|依次|流转|办理|审批)", compact):
        return "flow"
    if re.search(r"(组织|架构|上下级|分管|层级|体系)", compact):
        return "organization"
    if re.search(r"(矩阵|象限|维度|对应关系|分类对应)", compact):
        return "matrix"
    has_items = any(isinstance(module, dict) and module.get("items") for module in (structure.get("modules") or [])) if isinstance(structure, dict) else False
    if has_items or re.search(r"(模块|能力|分类|包括|包含|分为|四大|三大|五大|六大)", compact):
        return "card_matrix" if module_count <= 4 else "vertical_card_list"
    return "smartart_hierarchy"


def _analysis_reason(chart_type: str, structure: dict[str, Any]) -> str:
    module_count = len(structure.get("modules") or []) if isinstance(structure, dict) else 0
    reasons = {
        "flow": "文本包含步骤或先后流程，适合流程图展示",
        "timeline": "文本包含阶段或时间节点，适合时间轴展示",
        "card_matrix": f"识别到 {module_count} 个并列模块，适合卡片矩阵展示",
        "vertical_card_list": f"识别到 {module_count} 个模块，适合垂直卡片列表展示",
        "organization": "文本包含层级或组织关系，适合组织架构图展示",
        "cycle": "文本包含闭环或迭代逻辑，适合循环闭环图展示",
        "matrix": "文本包含维度或矩阵关系，适合矩阵关系图展示",
        "swimlane": "文本包含角色分工与跨角色流转，适合泳道流程图展示",
        "platform_layer": "文本包含平台、业务环节与系统支撑，适合平台分层架构图展示",
        "smartart_hierarchy": "结构较通用，使用稳定 SmartArt 层级图兜底",
    }
    return reasons.get(chart_type, "已根据文本结构自动匹配图表类型")


async def _classify_diagram_type(paragraph: str, fallback_type: str = "framework") -> str:
    """Use the configured Qwen diagram model for tiny type classification; fall back safely."""

    fallback = _normalize_diagram_type(fallback_type) or "framework"
    cleaned = "\n".join(line.strip() for line in (paragraph or "").splitlines() if line.strip())[:1200]
    heuristic = _heuristic_diagram_type(cleaned) or fallback
    messages = [
        {
            "role": "system",
            "content": (
                "你只负责给标书业务段落选择最适合的图表类型。"
                "必须且只能输出以下英文枚举之一："
                f"{', '.join(DIAGRAM_TYPE_ENUM)}。"
                "不要解释，不要标点，不要输出中文。"
            ),
        },
        {"role": "user", "content": cleaned},
    ]
    result = await chat_completion(messages, temperature=0, max_tokens=16, fallback=heuristic, json_mode=False, model=diagram_model())
    resolved = _normalize_diagram_type(result.content)
    if not resolved:
        logger.warning("[Smart-Excalidraw] diagram type classification fallback used. raw=%s error=%s", result.content, result.error)
    return resolved or heuristic


async def _classify_smartart_template(paragraph: str, fallback_template: str = "basic_hierarchy") -> str:
    """Lightweight SmartArt template matching; safe heuristic first, LLM only as a small helper."""

    fallback = _normalize_smartart_template(fallback_template) or "basic_hierarchy"
    cleaned = "\n".join(line.strip() for line in (paragraph or "").splitlines() if line.strip())[:1200]
    heuristic = _heuristic_smartart_template(cleaned) or fallback
    if not settings.qwen_api_key.strip():
        return heuristic
    messages = [
        {
            "role": "system",
            "content": (
                "你只负责给标书业务段落选择最适合的 SmartArt 模板。"
                "必须且只能输出以下英文枚举之一："
                f"{', '.join(SMARTART_TEMPLATE_ENUM)}。"
                "规则：阶段/步骤/流程优先 arrow_process 或 timeline_milestone；"
                "分类/模块/能力优先 vertical_card_list 或 hexagon_matrix；"
                "层级/架构/体系优先 step_hierarchy 或 radial_relation；"
                "维度/矩阵/象限优先 quadrant_matrix；"
                "要点列举优先 circle_category；置信度低输出 basic_hierarchy。"
                "不要解释，不要标点，不要输出中文。"
            ),
        },
        {"role": "user", "content": cleaned},
    ]
    result = await chat_completion(messages, temperature=0, max_tokens=18, fallback=heuristic, json_mode=False, model=diagram_model())
    resolved = _normalize_smartart_template(result.content)
    if not resolved:
        logger.warning("[Smart-Excalidraw] SmartArt template classification fallback used. raw=%s error=%s", result.content, result.error)
    return resolved or heuristic


async def _parse_smartart_structure(paragraph: str, *, template_id: str = "basic_hierarchy") -> dict[str, Any]:
    """Parse SmartArt content once into title + modules + items, with repair and regex fallback."""

    cleaned = "\n".join(line.strip() for line in (paragraph or "").splitlines() if line.strip())[:2600]
    template = SMARTART_TEMPLATES.get(template_id, SMARTART_TEMPLATES["basic_hierarchy"])
    user_prompt = f"""
请解析以下标书业务内容，输出 title + modules + items 结构。
当前图表模板：{template['name']}；模板只影响展示样式，不改变业务层级解析规则。

【待解析文本】
{cleaned}
""".strip()
    messages = [
        {"role": "system", "content": SMARTART_STRUCTURE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    logger.info("[SmartArtStructure] requesting structure parse model=%s template=%s", diagram_model(), template_id)
    result = await chat_completion(messages, temperature=0.03, max_tokens=1800, fallback="", json_mode=False, model=diagram_model())
    parsed = _parse_llm_json(result.content)
    ok, validated, errors = _validate_smartart_structure(parsed)
    if ok:
        return validated

    logger.warning("[SmartArtStructure] first validation failed errors=%s raw=%s", errors, result.content[:300])
    repair_prompt = f"""
上一次输出不合法，请严格按系统规则重新输出 JSON 代码块。

【校验错误】
{'; '.join(errors)}

【原始文本】
{cleaned}

【上一次错误输出】
{result.content[:1800]}
""".strip()
    retry = await chat_completion(
        [
            {"role": "system", "content": SMARTART_STRUCTURE_SYSTEM_PROMPT},
            {"role": "user", "content": repair_prompt},
        ],
        temperature=0,
        max_tokens=1800,
        fallback="",
        json_mode=False,
        model=diagram_model(),
    )
    retry_parsed = _parse_llm_json(retry.content)
    retry_ok, retry_validated, retry_errors = _validate_smartart_structure(retry_parsed)
    if retry_ok:
        return retry_validated

    logger.warning("[SmartArtStructure] retry validation failed; rule fallback used. errors=%s raw=%s", retry_errors, retry.content[:300])
    return _fallback_parse_smartart_structure(cleaned)


def _validate_smartart_structure(struct_data: Any) -> tuple[bool, dict[str, Any], list[str]]:
    errors: list[str] = []
    if not isinstance(struct_data, dict):
        return False, {}, ["输出必须是 JSON 对象"]

    raw_title = str(struct_data.get("title") or "").strip()
    title = _clean_smartart_text(raw_title, max_len=24, allow_short=False)
    if len(title) < 4:
        errors.append("title 缺失或过短")
    modules_raw = struct_data.get("modules")
    if not isinstance(modules_raw, list):
        return False, {}, [*errors, "modules 必须是数组"]

    modules: list[dict[str, Any]] = []
    for index, module in enumerate(modules_raw[:8], 1):
        if not isinstance(module, dict):
            errors.append(f"modules[{index}] 必须是对象")
            continue
        name = _clean_smartart_text(str(module.get("name") or ""), max_len=15, allow_short=False)
        if not (2 <= _cjk_len(name) <= 15):
            errors.append(f"模块名称长度不合法：{name or '<空>'}")
            continue
        if _is_incomplete_smartart_name(name):
            errors.append(f"模块名称疑似半截语义：{name}")
            continue
        raw_items = module.get("items")
        if raw_items in (None, ""):
            raw_items = []
        if not isinstance(raw_items, list):
            errors.append(f"模块 {name} 的 items 必须是字符串数组")
            raw_items = []
        items: list[str] = []
        for item in raw_items[:8]:
            item_text = _clean_smartart_text(str(item or ""), max_len=15, allow_short=True)
            if item_text and not _is_incomplete_smartart_name(item_text):
                items.append(item_text)
        modules.append({"name": name, "items": _unique_keep_order(items)})

    if not (1 <= len(modules) <= 8):
        errors.append("模块数量必须在 1-8 个之间")
    if len(modules_raw) > 8:
        errors.append("模块数量超过 8 个，已截断次要模块")

    valid_names = {module["name"] for module in modules}
    sequence: list[dict[str, str]] = []
    raw_sequence = struct_data.get("sequence")
    if isinstance(raw_sequence, list):
        for step in raw_sequence[:12]:
            if not isinstance(step, dict):
                continue
            lane = _clean_smartart_text(str(step.get("lane") or ""), max_len=15, allow_short=False)
            label = _clean_smartart_text(str(step.get("label") or ""), max_len=12, allow_short=True)
            if lane in valid_names and label and _cjk_len(label) >= 2:
                sequence.append({"lane": lane, "label": label})

    validated = {"title": title or "标书逻辑结构", "modules": modules[:8], "sequence": sequence}
    hard_errors = [error for error in errors if not error.startswith("模块数量超过")]
    return not hard_errors, validated, errors


def _fallback_parse_smartart_structure(paragraph: str) -> dict[str, Any]:
    text = "\n".join(line.strip() for line in (paragraph or "").splitlines() if line.strip())
    title = _fallback_smartart_title(text)
    modules: list[dict[str, Any]] = []

    colon_patterns = re.findall(r"([^：:；;\n]{2,24})[：:]([^\n；;]+)", text)
    for raw_name, raw_items in colon_patterns:
        name = _clean_smartart_text(raw_name, max_len=15, allow_short=False)
        if not name or _is_incomplete_smartart_name(name):
            continue
        items = [_clean_smartart_text(item, max_len=15, allow_short=True) for item in re.split(r"[、,，/／|]+", raw_items)]
        items = [item for item in items if item and not _is_incomplete_smartart_name(item)]
        modules.append({"name": name, "items": _unique_keep_order(items)})

    if not modules:
        body = re.sub(r"^[^：:\n]{4,40}[：:]", "", text, count=1)
        chunks = [_clean_smartart_text(part, max_len=15, allow_short=False) for part in re.split(r"[；;\n]+", body)]
        chunks = [chunk for chunk in chunks if chunk and not _is_incomplete_smartart_name(chunk)]
        for chunk in chunks[:8]:
            if "、" in chunk or "，" in chunk:
                parts = [_clean_smartart_text(part, max_len=15, allow_short=True) for part in re.split(r"[、,，]+", chunk)]
                parts = [part for part in parts if part]
                if parts:
                    modules.append({"name": parts[0], "items": parts[1:5]})
            else:
                modules.append({"name": chunk, "items": []})

    if not modules:
        fallback_items = [_clean_smartart_text(part, max_len=15, allow_short=False) for part in re.split(r"[、,，。；;\n]+", text)]
        fallback_items = [item for item in fallback_items if item and not _is_incomplete_smartart_name(item)]
        modules = [{"name": item, "items": []} for item in fallback_items[:4]] or [{"name": "核心逻辑", "items": []}]

    ok, validated, _ = _validate_smartart_structure({"title": title, "modules": modules})
    return validated if ok else {"title": title or "标书逻辑结构", "modules": modules[:8] or [{"name": "核心逻辑", "items": []}]}


def _fallback_smartart_title(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), text.strip())
    first = re.split(r"[：:]", first, maxsplit=1)[0]
    first = re.split(r"[，,。；;]", first, maxsplit=1)[0]
    first = re.sub(r"^(本项目|项目|系统|平台)", lambda match: match.group(0), first).strip()
    return _clean_smartart_text(first, max_len=24, allow_short=False) or "标书逻辑结构"


def _clean_smartart_text(value: str, *, max_len: int, allow_short: bool) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    text = re.sub(r"^[一二三四五六七八九十0-9]+[、.．)）-]*", "", text)
    text = text.strip("：:，,。；;、/／|（）()[]【】《》<>-—_ ")
    text = re.sub(r"^(包括|包含|分为|分别为|主要为|以及|和|与|并|同时)", "", text)
    text = text.strip("：:，,。；;、/／|（）()[]【】《》<>-—_ ")
    if not allow_short and _cjk_len(text) < 2:
        return ""
    return text[:max_len]


def _is_incomplete_smartart_name(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    bad_exact = {"并列支撑", "整体运营", "整体", "四大", "三大", "五大", "包括", "包含", "以及", "同时", "其中", "如下", "以下"}
    bad_suffix = ("的", "和", "与", "及", "并", "为", "包括", "包含", "分为", "如下", "以及", "整体", "四大", "三大", "五大")
    if text in bad_exact:
        return True
    return any(text.endswith(suffix) for suffix in bad_suffix)


def _cjk_len(value: str) -> int:
    return len(re.sub(r"\s+", "", str(value or "")))


def _unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _smartart_structure_to_elements(structure: dict[str, Any], template_id: str) -> list[dict[str, Any]]:
    modules = structure.get("modules") if isinstance(structure.get("modules"), list) else []
    modules = _normalize_modules_for_template(modules, max_modules=8)
    elements: list[dict[str, Any]] = []
    for index, module in enumerate(modules[:12], 1):
        name = str((module or {}).get("name") or "").strip()
        items = (module or {}).get("items") if isinstance((module or {}).get("items"), list) else []
        elements.append(
            {
                "id": f"module{index}",
                "type": "rectangle",
                "x": 80 + (index - 1) * 210,
                "y": 260,
                "width": 190,
                "height": 86,
                "label": {"text": name[:15], "fontSize": 18, "strokeColor": "#2B2B26"},
                "moduleName": name,
                "items": [_clean_smartart_text(str(item), max_len=12, allow_short=True) for item in items if str(item).strip()][:8],
            }
        )
    return elements or _fallback_diagram(structure.get("title") or "标书逻辑结构", "smartart")["elements"]


def _normalize_modules_for_template(modules: list[Any], *, max_modules: int = 8) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        name = _clean_smartart_text(str(module.get("name") or ""), max_len=15, allow_short=False)
        if not name or _is_incomplete_smartart_name(name):
            continue
        raw_items = module.get("items") if isinstance(module.get("items"), list) else []
        items = [_clean_smartart_text(str(item), max_len=15, allow_short=True) for item in raw_items]
        items = [item for item in items if item and not _is_incomplete_smartart_name(item)]
        normalized.append({"name": name, "items": _unique_keep_order(items)[:8]})

    if len(normalized) <= max_modules:
        return normalized

    keep_count = max(1, max_modules - 1)
    merged_items: list[str] = []
    for module in normalized[keep_count:]:
        merged_items.append(module["name"])
        merged_items.extend(module.get("items") or [])
    return [*normalized[:keep_count], {"name": "其他要点", "items": _unique_keep_order([item for item in merged_items if item])[:8]}]


def _quadrant_modules(modules: list[Any]) -> list[dict[str, Any]]:
    return _normalize_modules_for_template(modules, max_modules=8)


def _normalize_diagram_type(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z_\-\u4e00-\u9fff]", "", text)
    aliases = {
        "framework": "framework",
        "框架图": "framework",
        "flow": "flow",
        "flowchart": "flow",
        "流程图": "flow",
        "architecture": "architecture",
        "架构关系图": "architecture",
        "架构图": "architecture",
        "timeline": "timeline",
        "time_axis": "timeline",
        "时间轴": "timeline",
        "阶段路线图": "timeline",
        "organization": "organization",
        "org_chart": "organization",
        "组织架构图": "organization",
        "smartart": "smartart",
        "smartart_layer": "smartart",
        "smartartlayer": "smartart",
        "smartart层级图": "smartart",
        "层级图": "smartart",
        "cycle": "cycle",
        "循环闭环图": "cycle",
        "闭环图": "cycle",
        "matrix": "matrix",
        "矩阵关系图": "matrix",
        "矩阵图": "matrix",
        "swimlane": "swimlane",
        "swimlane_process": "swimlane",
        "泳道": "swimlane",
        "泳道图": "swimlane",
        "泳道流程图": "swimlane",
        "platform_layer": "platform_layer",
        "layered_platform": "platform_layer",
        "平台分层": "platform_layer",
        "平台分层图": "platform_layer",
        "平台分层架构图": "platform_layer",
    }
    return aliases.get(text)


def _normalize_smartart_template(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z_\-\u4e00-\u9fff]", "", text)
    aliases = {
        "basic_hierarchy": "basic_hierarchy",
        "basichierarchy": "basic_hierarchy",
        "基础层级图": "basic_hierarchy",
        "arrow_process": "arrow_process",
        "arrowprocess": "arrow_process",
        "彩色箭头流程": "arrow_process",
        "vertical_card_list": "vertical_card_list",
        "verticalcardlist": "vertical_card_list",
        "垂直卡片列表": "vertical_card_list",
        "timeline_milestone": "timeline_milestone",
        "timelinemilestone": "timeline_milestone",
        "时间轴里程碑": "timeline_milestone",
        "radial_relation": "radial_relation",
        "radialrelation": "radial_relation",
        "中心辐射关系图": "radial_relation",
        "hexagon_matrix": "hexagon_matrix",
        "hexagonmatrix": "hexagon_matrix",
        "六边形矩阵": "hexagon_matrix",
        "quadrant_matrix": "quadrant_matrix",
        "quadrantmatrix": "quadrant_matrix",
        "四象限卡片矩阵": "quadrant_matrix",
        "step_hierarchy": "step_hierarchy",
        "stephierarchy": "step_hierarchy",
        "阶梯式层级图": "step_hierarchy",
        "circle_category": "circle_category",
        "circlecategory": "circle_category",
        "圆形分类图": "circle_category",
    }
    return aliases.get(text)


@lru_cache(maxsize=1)
def _load_smartart_template_manifest() -> dict[str, Any]:
    if not SMARTART_TEMPLATE_MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(SMARTART_TEMPLATE_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - external template library must never break diagram generation.
        logger.warning("[SmartArtTemplate] manifest load failed: %s", exc)
        return {}


def _smartart_template_reference(template_id: str) -> dict[str, Any] | None:
    category = _normalize_smartart_template(template_id) or "basic_hierarchy"
    manifest = _load_smartart_template_manifest()
    recommended = ((manifest.get("recommended_by_category") or {}).get(category) or []) if isinstance(manifest, dict) else []
    if not recommended:
        return None
    item = recommended[0]
    return {
        "source_file": manifest.get("source_file"),
        "template_id": item.get("template_id"),
        "slide_number": item.get("slide_number"),
        "confidence": item.get("confidence"),
        "shape_counts": item.get("shape_counts"),
        "tags": item.get("tags"),
        "prompt_hint": item.get("prompt_hint"),
    }


def _smartart_template_reference_prompt(template_id: str) -> str:
    reference = _smartart_template_reference(template_id)
    if not reference:
        return ""
    shape_counts = reference.get("shape_counts") or {}
    top_shapes = "、".join(f"{key}×{value}" for key, value in list(shape_counts.items())[:5])
    return f"""

【外部 SmartArt 素材库参考】
已匹配本地素材库《{reference.get('source_file') or 'SmartArt图形素材'}》第 {reference.get('slide_number')} 页作为视觉参考。
参考特征：{top_shapes or '结构化 SmartArt 组合'}。
参考要求：{reference.get('prompt_hint') or '保持模板结构规整、商务正式、节点简洁。'}
注意：只借鉴布局与视觉结构，不复制素材库中的示例文字。
""".strip()


def _heuristic_diagram_type(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text or "")
    checks = [
        ("timeline", ("阶段", "里程碑", "周期", "时间", "进度", "计划", "排期")),
        ("organization", ("组织", "岗位", "部门", "团队", "负责人", "职责", "汇报")),
        ("matrix", ("矩阵", "维度", "对应", "横向", "纵向", "分类", "匹配")),
        ("cycle", ("闭环", "循环", "反馈", "复盘", "整改", "持续改进")),
        ("flow", ("流程", "步骤", "审批", "流转", "受理", "分派", "归档")),
        ("architecture", ("架构", "平台", "系统", "模块", "能力层", "数据层")),
        ("smartart", ("层级", "包含", "体系", "分层", "模块化", "框架体系")),
    ]
    for diagram_type, keywords in checks:
        if any(keyword in compact for keyword in keywords):
            return diagram_type
    return None


def _heuristic_smartart_template(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text or "")
    checks = [
        ("quadrant_matrix", ("四象限", "象限", "矩阵", "维度", "横向", "纵向", "坐标")),
        ("timeline_milestone", ("里程碑", "周期", "时间", "排期", "进度", "月", "季度", "阶段计划")),
        ("arrow_process", ("流程", "步骤", "阶段", "递进", "办理", "审批", "流转", "闭环")),
        ("radial_relation", ("核心", "围绕", "支撑", "辐射", "体系框架", "能力体系")),
        ("step_hierarchy", ("层级", "架构", "组织", "体系", "分层", "上级", "下级")),
        ("hexagon_matrix", ("模块", "分类", "并列", "能力", "要素", "组成")),
        ("vertical_card_list", ("清单", "说明", "要求", "条款", "能力清单", "模块说明")),
        ("circle_category", ("要点", "类别", "分类说明", "列举", "重点")),
    ]
    for template_id, keywords in checks:
        if any(keyword in compact for keyword in keywords):
            return template_id
    return "basic_hierarchy"


def _parse_llm_json(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text) or re.search(r"\{[\s\S]*\}", text)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    if isinstance(data, list):
        return {"elements": data}
    return data if isinstance(data, dict) else {}


def _sanitize_elements(raw_elements: Any, paragraph: str, diagram_type: str) -> list[dict[str, Any]]:
    if not isinstance(raw_elements, list):
        return []
    sanitized: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    node_count = 0

    for index, item in enumerate(raw_elements[:30], 1):
        if not isinstance(item, dict):
            continue
        element_type = str(item.get("type") or "").lower()
        if element_type not in {"rectangle", "ellipse", "diamond", "text", "arrow"}:
            continue
        element: dict[str, Any] = {"type": element_type, "id": _safe_id(str(item.get("id") or f"el{index}"))}
        if element_type == "arrow":
            start_id = _safe_id(str((item.get("start") or {}).get("id") or "")) if isinstance(item.get("start"), dict) else ""
            end_id = _safe_id(str((item.get("end") or {}).get("id") or "")) if isinstance(item.get("end"), dict) else ""
            if not start_id or not end_id:
                continue
            element["start"] = {"id": start_id}
            element["end"] = {"id": end_id}
            element["elbowed"] = True
            element["strokeColor"] = _safe_color(item.get("strokeColor"), "#3F6F82")
            element["strokeWidth"] = _safe_number(item.get("strokeWidth"), 2, 1, 5)
            label = _label_text(item.get("label"))
            if label:
                element["label"] = {"text": _short_node_text(label), "fontSize": 14}
        elif element_type == "text":
            element.update(
                {
                    "x": _safe_number(item.get("x"), 70, 0, 1250),
                    "y": _safe_number(item.get("y"), 70, 0, 690),
                        "text": str(item.get("text") or "").strip()[:32],
                    "fontSize": _safe_number(item.get("fontSize"), 22, 12, 34),
                    "strokeColor": _safe_color(item.get("strokeColor"), "#2B2B26"),
                }
            )
        else:
            node_count += 1
            element.update(
                {
                    "x": _safe_number(item.get("x"), 80 + (node_count - 1) * 210, 20, 1120),
                    "y": _safe_number(item.get("y"), 250, 80, 620),
                    "width": _safe_number(item.get("width"), 190, 120, 280),
                    "height": _safe_number(item.get("height"), 86, 60, 140),
                    "backgroundColor": _safe_color(item.get("backgroundColor"), PALETTE[(node_count - 1) % len(PALETTE)]),
                    "strokeColor": _safe_color(item.get("strokeColor"), STROKES[(node_count - 1) % len(STROKES)]),
                    "strokeWidth": _safe_number(item.get("strokeWidth"), 2, 1, 5),
                    "label": {
                        "text": _short_node_text(_label_text(item.get("label")) or _fallback_label(paragraph, node_count)),
                        "fontSize": _safe_number((item.get("label") or {}).get("fontSize") if isinstance(item.get("label"), dict) else None, 18, 12, 24),
                        "strokeColor": _safe_color((item.get("label") or {}).get("strokeColor") if isinstance(item.get("label"), dict) else None, "#2B2B26"),
                    },
                }
            )
            known_ids.add(element["id"])
        sanitized.append(element)

    # Keep arrows only when both endpoints exist.
    filtered: list[dict[str, Any]] = []
    for element in sanitized:
        if element["type"] == "arrow":
            if element["start"]["id"] not in known_ids or element["end"]["id"] not in known_ids:
                continue
        filtered.append(element)
    return filtered


def _auto_layout_elements(elements: list[dict[str, Any]], width: int, height: int, diagram_type: str, style_config: dict[str, Any] | None = None, template_id: str = "basic_hierarchy") -> list[dict[str, Any]]:
    """Re-layout model-generated elements into a safe, readable canvas.

    The LLM is useful for extracting labels and relationships, but its absolute
    coordinates are often unreliable.  This function treats coordinates as hints
    only and guarantees that nodes stay inside the viewBox without single-line
    overflow or overlap.
    """

    nodes = [element for element in elements if element.get("type") in {"rectangle", "ellipse", "diamond"}]
    arrows = [element for element in elements if element.get("type") == "arrow"]
    if not nodes:
        return elements

    if diagram_type == "smartart":
        nodes = _filter_smartart_nodes(nodes)[:12]
    else:
        nodes = nodes[:8]
    ordered_nodes = _order_nodes_for_layout(nodes, arrows)
    node_ids = {node["id"] for node in ordered_nodes}

    style_config = style_config or STYLE_PRESETS["tech"]

    if diagram_type == "flow":
        return _layout_flow_tb(ordered_nodes, width, height, style_config)
    if diagram_type == "timeline":
        return _layout_timeline_lr(ordered_nodes, width, height, style_config)
    if diagram_type == "smartart":
        return _layout_smartart_template(ordered_nodes, width, height, style_config, template_id)
    if diagram_type == "organization":
        return _layout_hierarchy(ordered_nodes, width, height, style_config, diagram_type)
    if diagram_type == "cycle":
        return _layout_cycle(ordered_nodes, width, height, style_config)
    if diagram_type == "matrix":
        return _layout_matrix(ordered_nodes, width, height, style_config)

    margin_x = 74 if width >= 900 else 46
    top_y = 210 if height <= 760 else 245
    bottom_margin = 92
    usable_w = max(360, width - margin_x * 2)
    usable_h = max(260, height - top_y - bottom_margin)
    count = len(ordered_nodes)

    if diagram_type in {"flow", "timeline"}:
        cols = _flow_columns(count, width)
        serpentine = True
    elif diagram_type == "architecture":
        cols = 3 if count >= 5 and width >= 900 else 2
        serpentine = False
    else:
        cols = 3 if count >= 5 and width >= 900 else min(2, count)
        serpentine = False
    cols = max(1, min(cols, count))
    rows = (count + cols - 1) // cols

    min_gap_x = _layout_gap_x(width, height)
    min_gap_y = _layout_gap_y(width, height, rows)
    card_w = min(240 if width >= 1500 else 220, max(145 if width < height else 158, (usable_w - min_gap_x * (cols - 1)) / cols))
    card_h = min(96, max(66 if width < height else 72, (usable_h - min_gap_y * (rows - 1)) / rows))
    if rows >= 3:
        card_h = min(card_h, 82)

    row_gap = (usable_h - rows * card_h) / max(1, rows - 1) if rows > 1 else 0
    row_gap = max(min_gap_y, min(row_gap, 92)) if rows > 1 else 0
    total_h = rows * card_h + row_gap * (rows - 1)
    start_y = top_y + max(0, (usable_h - total_h) / 2)

    laid_out: list[dict[str, Any]] = []
    for index, node in enumerate(ordered_nodes):
        row = index // cols
        col_in_order = index % cols
        remaining = count - row * cols
        cols_in_row = min(cols, remaining)
        visual_col = cols_in_row - 1 - col_in_order if serpentine and row % 2 == 1 else col_in_order
        row_w = cols_in_row * card_w + (cols_in_row - 1) * min_gap_x
        x = margin_x + (usable_w - row_w) / 2 + visual_col * (card_w + min_gap_x)
        y = start_y + row * (card_h + row_gap)
        next_node = dict(node)
        next_node["backgroundColor"] = style_config["palette"][index % len(style_config["palette"])]
        next_node["strokeColor"] = style_config["strokes"][index % len(style_config["strokes"])]
        next_node["strokeWidth"] = float(style_config.get("stroke_width") or 2)
        next_node.update({"x": round(x, 1), "y": round(y, 1), "width": round(card_w, 1), "height": round(card_h, 1)})
        if "label" in next_node and isinstance(next_node["label"], dict):
            label = dict(next_node["label"])
            label["fontSize"] = min(float(label.get("fontSize") or 18), 18 if card_h <= 82 else 19)
            next_node["label"] = label
        laid_out.append(next_node)

    # 新增：模型输出的箭头只作为语义参考，最终连线由服务端按规整布局重建，
    # 避免斜线、穿模、交叉、重叠等不稳定问题。
    valid_arrows = _style_arrows(_default_arrows_for_nodes(laid_out, diagram_type), style_config)

    return [*laid_out, *valid_arrows]


def _flow_columns(count: int, width: int) -> int:
    if width < 850:
        return 2
    if count <= 4:
        return count
    if count <= 6:
        return 3
    if count <= 8:
        return 4
    return 3


def _layout_gap_x(width: int, height: int) -> int:
    if width >= 1500:
        return 74
    if width < height:
        return 26
    return 48


def _layout_gap_y(width: int, height: int, rows: int) -> int:
    if height > width:
        return 26
    if rows >= 3:
        return 34
    return 48


def _layout_flow_tb(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Business flowcharts default to top-to-bottom layout."""

    count = len(nodes)
    if count == 0:
        return []
    top_y = 155 if height <= 760 else 190
    bottom_margin = 74
    usable_h = max(320, height - top_y - bottom_margin)
    card_w = min(330, max(220, width * (0.28 if width < height else 0.24)))
    card_h = 60 if height > width else (64 if count <= 6 else 52)
    gap_y = (usable_h - count * card_h) / max(1, count - 1) if count > 1 else 0
    gap_y = max(12, min(gap_y, 38)) if count > 1 else 0
    total_h = count * card_h + gap_y * (count - 1)
    start_y = top_y + max(0, (usable_h - total_h) / 2)
    x = (width - card_w) / 2

    laid_out: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        next_node = dict(node)
        label = next_node.get("label") if isinstance(next_node.get("label"), dict) else {}
        label_text = _short_node_text(_label_text(label) or f"节点{index + 1}")
        next_node.update(
            {
                "type": "diamond" if _is_decision_label(label_text) else "rectangle",
                "x": round(x, 1),
                "y": round(start_y + index * (card_h + gap_y), 1),
                "width": round(card_w, 1),
                "height": round(card_h, 1),
                "backgroundColor": style_config["palette"][index % len(style_config["palette"])],
                "strokeColor": style_config["strokes"][index % len(style_config["strokes"])],
                "strokeWidth": float(style_config.get("stroke_width") or 2),
                "label": {"text": label_text, "fontSize": 17 if count <= 6 else 15, "strokeColor": "#2B2B26"},
            }
        )
        laid_out.append(next_node)
    return [*laid_out, *_style_arrows(_default_arrows_for_nodes(laid_out, "flow"), style_config)]


def _layout_timeline_lr(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Horizontal timeline layout with optional second row for narrow canvases."""

    count = len(nodes)
    if count == 0:
        return []
    margin_x = 86 if width >= 1000 else 54
    top_y = height * 0.44 if height <= width else height * 0.30
    rows = 2 if count > 5 and width < 1300 else 1
    cols = math.ceil(count / rows)
    usable_w = max(420, width - margin_x * 2)
    card_w = min(210, max(150, (usable_w - (cols - 1) * 42) / cols))
    card_h = 74
    row_gap = 116 if rows == 2 else 0
    laid_out: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        row = index // cols
        col = index % cols
        cols_in_row = min(cols, count - row * cols)
        row_w = cols_in_row * card_w + (cols_in_row - 1) * 42
        x = margin_x + (usable_w - row_w) / 2 + col * (card_w + 42)
        y = top_y + row * row_gap
        next_node = _styled_node(node, index, style_config, x, y, card_w, card_h, 16)
        laid_out.append(next_node)
    return [*laid_out, *_style_arrows(_default_arrows_for_nodes(laid_out, "timeline"), style_config)]


def _layout_hierarchy(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any], diagram_type: str) -> list[dict[str, Any]]:
    """Tree-style layout for organization charts and SmartArt hierarchy."""

    count = len(nodes)
    if count == 0:
        return []
    margin_x = 76 if width >= 900 else 44
    top_y = 155 if height <= 760 else 190
    usable_w = max(360, width - margin_x * 2)
    if count <= 4:
        row_counts = [1, count - 1]
    else:
        second = min(3, count - 1)
        row_counts = [1, second, count - 1 - second]
    row_counts = [item for item in row_counts if item > 0]
    rows = len(row_counts)
    card_w = min(245, max(160, usable_w / max(row_counts) - 34))
    card_h = 76 if diagram_type == "organization" else 84
    row_gap = min(132, max(92, (height - top_y - 90 - rows * card_h) / max(1, rows - 1))) if rows > 1 else 0
    laid_out: list[dict[str, Any]] = []
    cursor = 0
    for row_index, row_count in enumerate(row_counts):
        row_w = row_count * card_w + (row_count - 1) * 46
        row_x = margin_x + (usable_w - row_w) / 2
        y = top_y + row_index * (card_h + row_gap)
        for col in range(row_count):
            if cursor >= count:
                break
            node = nodes[cursor]
            next_node = _styled_node(node, cursor, style_config, row_x + col * (card_w + 46), y, card_w, card_h, 16)
            laid_out.append(next_node)
            cursor += 1

    arrows: list[dict[str, Any]] = []
    if len(laid_out) > 1:
        second_row_end = 1 + (row_counts[1] if len(row_counts) > 1 else 0)
        for index, node in enumerate(laid_out[1:second_row_end], 1):
            arrows.append(_arrow_between("root", index, laid_out[0], node))
        if len(row_counts) > 2:
            parents = laid_out[1:second_row_end] or [laid_out[0]]
            for offset, node in enumerate(laid_out[second_row_end:], second_row_end):
                parent = parents[(offset - second_row_end) % len(parents)]
                arrows.append(_arrow_between("child", offset, parent, node))
    return [*laid_out, *_style_arrows(arrows, style_config)]


def _filter_smartart_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only core hierarchy/module nodes for SmartArt; remove flow-return noise."""

    noise_keywords = ("不通过", "返回", "退回", "修改", "整改", "闭环", "循环", "迭代", "复核", "判断", "判定", "卡点")
    filtered: list[dict[str, Any]] = []
    for node in nodes:
        label = _label_text(node.get("label"))
        if any(keyword in label for keyword in noise_keywords):
            continue
        next_node = dict(node)
        next_node["type"] = "rectangle"
        if isinstance(next_node.get("label"), dict):
            next_node["label"] = {**next_node["label"], "text": _short_node_text(label)}
        filtered.append(next_node)
    # Avoid over-filtering on short user input; SmartArt still needs at least two nodes.
    return filtered if len(filtered) >= 2 else [dict(node, type="rectangle") for node in nodes]


def _layout_smartart_layers(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Office SmartArt-like layered layout with strict row alignment and vertical connectors."""

    count = min(len(nodes), 12)
    if count == 0:
        return []
    # SmartArt is deliberately not a flowchart: use fixed columns + layered rows.
    if width < height:
        cols = 2 if count >= 3 else 1
    elif count <= 2:
        cols = 1
    elif count <= 6:
        cols = 2
    else:
        cols = 3
    cols = min(5, max(1, cols))
    rows = min(6, math.ceil(count / cols))

    margin_x = 92 if width >= 1000 else 56
    top_y = 160 if height <= 760 else 205
    bottom_margin = 92
    usable_w = max(360, width - margin_x * 2)
    usable_h = max(300, height - top_y - bottom_margin)
    gap_x = 58 if width >= 1200 else 42
    gap_y = min(112, max(76, (usable_h - rows * 78) / max(1, rows - 1))) if rows > 1 else 0
    card_w = min(260, max(165, (usable_w - gap_x * (cols - 1)) / cols))
    card_h = 78 if rows <= 4 else 68
    total_w = cols * card_w + (cols - 1) * gap_x
    total_h = rows * card_h + (rows - 1) * gap_y
    start_x = margin_x + max(0, (usable_w - total_w) / 2)
    start_y = top_y + max(0, (usable_h - total_h) / 2)

    laid_out: list[dict[str, Any]] = []
    for index, node in enumerate(nodes[:count]):
        row = index // cols
        col = index % cols
        x = start_x + col * (card_w + gap_x)
        y = start_y + row * (card_h + gap_y)
        next_node = _styled_node(node, index, style_config, x, y, card_w, card_h, 16)
        next_node["type"] = "rectangle"
        next_node["backgroundColor"] = _smartart_layer_fill(style_config, row)
        laid_out.append(next_node)

    arrows: list[dict[str, Any]] = []
    for index, node in enumerate(laid_out):
        row = index // cols
        col = index % cols
        if row == 0:
            continue
        parent_index = (row - 1) * cols + col
        if parent_index < len(laid_out):
            arrows.append(
                {
                    "id": f"arrow_smartart_{index}",
                    "type": "arrow",
                    "start": {"id": laid_out[parent_index]["id"]},
                    "end": {"id": node["id"]},
                    "strokeColor": style_config["strokes"][0],
                    "strokeWidth": float(style_config.get("stroke_width") or 2),
                    "elbowed": True,
                    "route": "smartart_vertical",
                }
            )
    return [*laid_out, *_style_arrows(arrows, style_config)]


def _layout_smartart_template(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any], template_id: str) -> list[dict[str, Any]]:
    template = _normalize_smartart_template(template_id) or "basic_hierarchy"
    if template == "arrow_process":
        return _layout_template_arrow_process(nodes, width, height, style_config)
    if template == "vertical_card_list":
        return _layout_template_vertical_card_list(nodes, width, height, style_config)
    if template == "timeline_milestone":
        return _layout_template_timeline_milestone(nodes, width, height, style_config)
    if template == "radial_relation":
        return _layout_template_radial_relation(nodes, width, height, style_config)
    if template == "hexagon_matrix":
        return _layout_template_hexagon_matrix(nodes, width, height, style_config)
    if template == "quadrant_matrix":
        return _layout_template_quadrant_matrix(nodes, width, height, style_config)
    if template == "step_hierarchy":
        return _layout_template_step_hierarchy(nodes, width, height, style_config)
    if template == "circle_category":
        return _layout_template_circle_category(nodes, width, height, style_config)
    return _layout_smartart_layers(nodes, width, height, style_config)


def _template_node(node: dict[str, Any], index: int, style_config: dict[str, Any], x: float, y: float, w: float, h: float, font_size: int, shape: str) -> dict[str, Any]:
    next_node = _styled_node(node, index, style_config, x, y, w, h, font_size)
    next_node["type"] = "ellipse" if shape == "circle" else "rectangle"
    next_node["shape"] = shape
    next_node["templateIndex"] = index + 1
    module_name = str(node.get("moduleName") or _label_text(node.get("label")) or "").strip()
    if module_name:
        next_node["moduleName"] = module_name[:15]
        next_node["label"] = {**(next_node.get("label") if isinstance(next_node.get("label"), dict) else {}), "text": module_name[:15], "fontSize": font_size, "strokeColor": "#2B2B26"}
    if isinstance(node.get("items"), list):
        next_node["items"] = [_clean_smartart_text(str(item), max_len=12, allow_short=True) for item in node["items"] if str(item).strip()]
    return next_node


def _layout_template_arrow_process(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    count = min(max(len(nodes), 1), 10)
    selected = nodes[:count]
    margin_x = 82 if width >= 1000 else 48
    usable_w = width - margin_x * 2
    gap = -18
    card_w = min(210, max(135, (usable_w - gap * (count - 1)) / count))
    card_h = 92 if height >= 760 else 78
    total_w = count * card_w + (count - 1) * gap
    x0 = margin_x + max(0, (usable_w - total_w) / 2)
    y0 = height * 0.48 - card_h / 2
    return [
        _template_node(node, index, style_config, x0 + index * (card_w + gap), y0, card_w, card_h, 17, "arrow_block")
        for index, node in enumerate(selected)
    ]


def _layout_template_vertical_card_list(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    count = min(max(len(nodes), 1), 10)
    selected = nodes[:count]
    card_w = min(650, width * 0.68)
    usable_h = max(360, height - 220)
    gap = 10 if count >= 8 else (14 if count >= 6 else 18)
    card_h = min(72, max(46, (usable_h - gap * (count - 1)) / count))
    total_h = count * card_h + (count - 1) * gap
    x = (width - card_w) / 2
    y0 = max(152, (height - total_h) / 2 + 42)
    return [
        _template_node(node, index, style_config, x + (18 if index % 2 else 0), y0 + index * (card_h + gap), card_w - (18 if index % 2 else 0), card_h, 16, "vertical_card")
        for index, node in enumerate(selected)
    ]


def _layout_template_timeline_milestone(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    count = min(max(len(nodes), 1), 10)
    selected = nodes[:count]
    margin_x = 90 if width >= 1000 else 54
    line_y = height * 0.52
    usable_w = width - margin_x * 2
    card_w = min(190, max(112, usable_w / max(count, 1) - 18))
    card_h = 64 if count >= 8 else 70
    gap = usable_w / max(count - 1, 1)
    elements: list[dict[str, Any]] = [
        {"id": "timeline_main", "type": "line", "x1": margin_x, "y1": line_y, "x2": width - margin_x, "y2": line_y, "strokeColor": style_config["strokes"][0], "strokeWidth": float(style_config.get("stroke_width") or 2.4)},
    ]
    for index, node in enumerate(selected):
        cx = margin_x + index * gap if count > 1 else width / 2
        above = index % 2 == 0
        y = line_y - card_h - 54 if above else line_y + 54
        next_node = _template_node(node, index, style_config, cx - card_w / 2, y, card_w, card_h, 15, "timeline_card")
        next_node["markerX"] = cx
        next_node["markerY"] = line_y
        elements.append(next_node)
    return elements


def _layout_template_radial_relation(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    selected = nodes[:9]
    if not selected:
        return []
    center_w = 170
    center_h = 170
    cx, cy = width / 2, height / 2 + 28
    center = _template_node(selected[0], 0, style_config, cx - center_w / 2, cy - center_h / 2, center_w, center_h, 18, "circle")
    children: list[dict[str, Any]] = []
    child_w = min(185, max(145, width * 0.145))
    child_h = 66
    radius_x = min(width * 0.34, max(250, (width - child_w - 180) / 2))
    radius_y = min(height * 0.30, max(175, (height - child_h - 210) / 2))
    child_count = len(selected) - 1
    for index, node in enumerate(selected[1:], 1):
        angle = -math.pi / 2 + (index - 1) * (2 * math.pi / max(child_count, 1))
        x = cx + math.cos(angle) * radius_x - child_w / 2
        y = cy + math.sin(angle) * radius_y - child_h / 2
        children.append(_template_node(node, index, style_config, x, y, child_w, child_h, 15, "rounded_rect"))
    arrows = [_arrow_between("radial", index, center, child) for index, child in enumerate(children, 1)]
    return [center, *children, *_style_arrows(arrows, style_config)]


def _layout_template_hexagon_matrix(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    count = min(max(len(nodes), 1), 8)
    selected = nodes[:count]
    cols = min(4, count) if width >= height else min(2, count)
    rows = math.ceil(count / cols)
    card_w = min(185, max(138, (width - 150 - (cols - 1) * 28) / cols))
    card_h = card_w * 0.72
    gap_x, gap_y = 30, 36
    total_h = rows * card_h + (rows - 1) * gap_y
    y0 = max(180, (height - total_h) / 2 + 40)
    laid_out: list[dict[str, Any]] = []
    for index, node in enumerate(selected):
        row = index // cols
        col = index % cols
        cols_in_row = min(cols, count - row * cols)
        row_w = cols_in_row * card_w + (cols_in_row - 1) * gap_x
        x = (width - row_w) / 2 + col * (card_w + gap_x)
        y = y0 + row * (card_h + gap_y) + (18 if row % 2 else 0)
        laid_out.append(_template_node(node, index, style_config, x, y, card_w, card_h, 15, "hexagon"))
    return [*laid_out, *_style_arrows(_default_arrows_for_nodes(laid_out, "flow"), style_config)]


def _layout_template_quadrant_matrix(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    count = min(max(len(nodes), 1), 8)
    selected = nodes[:count]
    if count <= 2:
        rows, cols = 1, 2
    elif count == 3:
        rows, cols = 1, 3
    elif count == 4:
        rows, cols = 2, 2
    elif count <= 6:
        rows, cols = 2, 3
    else:
        rows, cols = 2, 4
    gap_x = 26 if cols >= 4 else 30
    gap_y = 24 if rows >= 2 else 0
    usable_w = width - 150
    usable_h = height - 260
    card_w = min(360, max(170, (usable_w - gap_x * (cols - 1)) / cols))
    card_h = min(190, max(132, (usable_h - gap_y * (rows - 1)) / rows))
    if cols >= 4:
        card_h = min(card_h, 166)
    total_w = cols * card_w + (cols - 1) * gap_x
    total_h = rows * card_h + (rows - 1) * gap_y
    x0 = (width - total_w) / 2
    y0 = max(170, (height - total_h) / 2 + 34)
    elements: list[dict[str, Any]] = []
    for index, node in enumerate(selected):
        row = index // cols
        col = index % cols
        cols_in_row = min(cols, count - row * cols)
        row_w = cols_in_row * card_w + (cols_in_row - 1) * gap_x
        row_x0 = (width - row_w) / 2
        x = row_x0 + col * (card_w + gap_x)
        y = y0 + row * (card_h + gap_y)
        elements.append(_template_node(node, index, style_config, x, y, card_w, card_h, 16 if cols >= 4 else 17, "quadrant_card"))
    return elements


def _layout_template_step_hierarchy(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    count = min(max(len(nodes), 1), 8)
    selected = nodes[:count]
    card_w = min(430, width * 0.50)
    card_h = 64 if count >= 6 else 74
    gap = 16
    indent = min(52, max(28, (width - card_w - 150) / max(count, 1)))
    total_h = count * card_h + (count - 1) * gap
    x0 = max(76, (width - card_w - indent * (count - 1)) / 2)
    y0 = max(155, (height - total_h) / 2 + 40)
    laid_out = [
        _template_node(node, index, style_config, x0 + index * indent, y0 + index * (card_h + gap), card_w, card_h, 16, "tag_step")
        for index, node in enumerate(selected)
    ]
    return [*laid_out, *_style_arrows(_default_arrows_for_nodes(laid_out, "flow"), style_config)]


def _layout_template_circle_category(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    count = min(max(len(nodes), 1), 10)
    selected = nodes[:count]
    card_w = min(620, width * 0.66)
    usable_h = max(360, height - 220)
    gap = 10 if count >= 8 else (16 if count >= 6 else 20)
    card_h = min(68, max(46, (usable_h - gap * (count - 1)) / count))
    total_h = count * card_h + (count - 1) * gap
    x = (width - card_w) / 2
    y0 = max(150, (height - total_h) / 2 + 38)
    return [
        _template_node(node, index, style_config, x, y0 + index * (card_h + gap), card_w, card_h, 16, "circle_category")
        for index, node in enumerate(selected)
    ]


def _smartart_layer_fill(style_config: dict[str, Any], row: int) -> str:
    palette = style_config.get("palette") or PALETTE
    base = _safe_color(palette[min(row, len(palette) - 1)], "#EAF3F8")
    return _mix_with_white(base, min(0.12 * row, 0.42))


def _mix_with_white(color: str, amount: float) -> str:
    color = _safe_color(color, "#EAF3F8").lstrip("#")
    amount = max(0.0, min(1.0, amount))
    r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
    r = round(r + (255 - r) * amount)
    g = round(g + (255 - g) * amount)
    b = round(b + (255 - b) * amount)
    return f"#{r:02X}{g:02X}{b:02X}"


def _layout_cycle(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Circular closed-loop layout; arrows connect clockwise and return to start."""

    count = len(nodes)
    if count == 0:
        return []
    cx, cy = width / 2, height / 2 + 26
    card_w = min(205, max(150, width * 0.15))
    card_h = 72
    radius_x = max(220, min(width * 0.32, (width - card_w - 160) / 2))
    radius_y = max(150, min(height * 0.26, (height - card_h - 210) / 2))
    laid_out: list[dict[str, Any]] = []
    start_angle = -math.pi / 2
    for index, node in enumerate(nodes):
        angle = start_angle + index * (2 * math.pi / count)
        x = cx + math.cos(angle) * radius_x - card_w / 2
        y = cy + math.sin(angle) * radius_y - card_h / 2
        laid_out.append(_styled_node(node, index, style_config, x, y, card_w, card_h, 16))
    arrows = [
        {"id": f"arrow{index + 1}", "type": "arrow", "start": {"id": laid_out[index]["id"]}, "end": {"id": laid_out[(index + 1) % count]["id"]}, "strokeColor": style_config["strokes"][index % len(style_config["strokes"])], "strokeWidth": float(style_config.get("stroke_width") or 2), "elbowed": True}
        for index in range(count)
    ]
    return [*laid_out, *_style_arrows(arrows, style_config)]


def _layout_matrix(nodes: list[dict[str, Any]], width: int, height: int, style_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Grid layout for matrix relationship diagrams."""

    count = len(nodes)
    if count == 0:
        return []
    cols = 3 if count >= 5 and width >= height else 2
    rows = math.ceil(count / cols)
    margin_x = 86 if width >= 900 else 54
    top_y = 190 if height <= 900 else 245
    usable_w = max(380, width - margin_x * 2)
    usable_h = max(280, height - top_y - 96)
    gap_x = 52 if width >= 1000 else 34
    gap_y = 42 if rows >= 3 else 58
    card_w = min(245, max(155, (usable_w - gap_x * (cols - 1)) / cols))
    card_h = min(92, max(68, (usable_h - gap_y * (rows - 1)) / rows))
    total_h = rows * card_h + (rows - 1) * gap_y
    start_y = top_y + max(0, (usable_h - total_h) / 2)
    laid_out: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        row = index // cols
        col = index % cols
        x = margin_x + col * (card_w + gap_x) + (usable_w - (cols * card_w + (cols - 1) * gap_x)) / 2
        y = start_y + row * (card_h + gap_y)
        laid_out.append(_styled_node(node, index, style_config, x, y, card_w, card_h, 16))
    return [*laid_out, *_style_arrows(_default_arrows_for_nodes(laid_out, "matrix"), style_config)]


def _styled_node(node: dict[str, Any], index: int, style_config: dict[str, Any], x: float, y: float, w: float, h: float, font_size: int) -> dict[str, Any]:
    next_node = dict(node)
    label = next_node.get("label") if isinstance(next_node.get("label"), dict) else {}
    next_node.update(
        {
            "type": "diamond" if _is_decision_label(_label_text(label)) and h >= 74 else "rectangle",
            "x": round(x, 1),
            "y": round(y, 1),
            "width": round(w, 1),
            "height": round(h, 1),
            "backgroundColor": style_config["palette"][index % len(style_config["palette"])],
            "strokeColor": style_config["strokes"][index % len(style_config["strokes"])],
            "strokeWidth": float(style_config.get("stroke_width") or 2),
            "label": {"text": _short_node_text(_label_text(label) or f"节点{index + 1}"), "fontSize": font_size, "strokeColor": "#2B2B26"},
        }
    )
    return next_node


def _arrow_between(prefix: str, index: int, start: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"arrow_{prefix}_{index}", "type": "arrow", "start": {"id": start["id"]}, "end": {"id": end["id"]}, "strokeColor": "#3F6F82", "strokeWidth": 2, "elbowed": True}


def _style_arrows(arrows: list[dict[str, Any]], style_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply the selected visual preset to server-generated arrows."""

    stroke = style_config["strokes"][0]
    stroke_width = float(style_config.get("stroke_width") or 2)
    styled: list[dict[str, Any]] = []
    for arrow in arrows:
        next_arrow = dict(arrow)
        next_arrow["strokeColor"] = stroke
        next_arrow["strokeWidth"] = stroke_width
        next_arrow["elbowed"] = True
        styled.append(next_arrow)
    return styled


def _order_nodes_for_layout(nodes: list[dict[str, Any]], arrows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {node["id"]: node for node in nodes}
    original_order = {node["id"]: index for index, node in enumerate(nodes)}
    edges: dict[str, list[str]] = {node["id"]: [] for node in nodes}
    indegree: dict[str, int] = {node["id"]: 0 for node in nodes}
    for arrow in arrows:
        start_id = (arrow.get("start") or {}).get("id")
        end_id = (arrow.get("end") or {}).get("id")
        if start_id in by_id and end_id in by_id and end_id not in edges[start_id]:
            edges[start_id].append(end_id)
            indegree[end_id] += 1

    queue = sorted([node_id for node_id, degree in indegree.items() if degree == 0], key=lambda node_id: original_order[node_id])
    ordered_ids: list[str] = []
    while queue:
        node_id = queue.pop(0)
        ordered_ids.append(node_id)
        for target in sorted(edges[node_id], key=lambda item: original_order[item]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
                queue.sort(key=lambda item: original_order[item])
    if len(ordered_ids) != len(nodes):
        return nodes
    return [by_id[node_id] for node_id in ordered_ids]


def _default_arrows_for_nodes(nodes: list[dict[str, Any]], diagram_type: str) -> list[dict[str, Any]]:
    if len(nodes) <= 1:
        return []
    if diagram_type in {"framework", "architecture"} and len(nodes) >= 5:
        root = nodes[0]["id"]
        return [
            {"id": f"arrow{index}", "type": "arrow", "start": {"id": root}, "end": {"id": node["id"]}, "strokeColor": "#3F6F82", "strokeWidth": 2, "elbowed": True}
            for index, node in enumerate(nodes[1:], 1)
        ]
    return [
        {"id": f"arrow{index}", "type": "arrow", "start": {"id": nodes[index - 1]["id"]}, "end": {"id": nodes[index]["id"]}, "strokeColor": "#3F6F82", "strokeWidth": 2, "elbowed": True}
        for index in range(1, len(nodes))
    ]


def _clean_arrow(arrow: dict[str, Any], index: int) -> dict[str, Any]:
    cleaned = {
        "id": _safe_id(str(arrow.get("id") or f"arrow{index}")),
        "type": "arrow",
        "start": {"id": _safe_id(str((arrow.get("start") or {}).get("id") or ""))},
        "end": {"id": _safe_id(str((arrow.get("end") or {}).get("id") or ""))},
        "elbowed": True,
        "strokeColor": _safe_color(arrow.get("strokeColor"), "#3F6F82"),
        "strokeWidth": _safe_number(arrow.get("strokeWidth"), 2, 1, 5),
    }
    label = _label_text(arrow.get("label"))
    if label:
                cleaned["label"] = {"text": _short_node_text(label), "fontSize": 13}
    return cleaned


def _fallback_diagram(paragraph: str, diagram_type: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", paragraph or "")
    parts = [p.strip(" ，。；;、") for p in re.split(r"[。；;\n]+", paragraph or "") if p.strip()]
    if not parts:
        parts = [compact or "项目逻辑", "任务分派", "过程跟踪", "质量复核", "成果归档"]
    labels = [_compress_label(part) for part in parts[:6]]
    while len(labels) < 5:
        labels.append(["需求受理", "任务分派", "过程跟踪", "质量复核", "成果归档"][len(labels)])

    x0, y0, gap = 85, 315, 205
    elements: list[dict[str, Any]] = [
        {"id": "title", "type": "text", "x": 72, "y": 70, "text": TYPE_LABELS.get(diagram_type, "标书逻辑图"), "fontSize": 28, "strokeColor": "#2B2B26"},
    ]
    for index, label in enumerate(labels[:6]):
        node_id = f"node{index + 1}"
        shape = "ellipse" if index in {0, len(labels[:6]) - 1} and diagram_type == "flow" else "rectangle"
        elements.append(
            {
                "id": node_id,
                "type": shape,
                "x": x0 + index * gap,
                "y": y0 - (34 if index % 2 else 0),
                "width": 166,
                "height": 82,
                "backgroundColor": PALETTE[index % len(PALETTE)],
                "strokeColor": STROKES[index % len(STROKES)],
                "label": {"text": label, "fontSize": 18, "strokeColor": "#2B2B26"},
            }
        )
        if index:
            elements.append({"id": f"arrow{index}", "type": "arrow", "start": {"id": f"node{index}"}, "end": {"id": node_id}, "strokeColor": "#3F6F82", "elbowed": True})
    return {"title": TYPE_LABELS.get(diagram_type, "逻辑图"), "summary": "根据标书段落自动提炼生成", "elements": elements}


def _render_svg(elements: list[dict[str, Any]], width: int, height: int, title: str, summary: str, font_config: dict[str, str] | None = None, style_config: dict[str, Any] | None = None) -> str:
    font_config = font_config or FONT_PRESETS["default_heiti"]
    style_config = style_config or STYLE_PRESETS["tech"]
    nodes = {element["id"]: element for element in elements if element.get("type") in {"rectangle", "ellipse", "diamond"}}
    font_css = str(style_config.get("font_css") or font_config["css"])
    render_mode = str(style_config.get("render_mode") or "standard")
    marker_color = _safe_color((style_config.get("strokes") or ["#3F6F82"])[0], "#3F6F82")
    if render_mode == "poster_business":
        shadow_filter = '<filter id="shadow" x="-30%" y="-30%" width="160%" height="170%"><feDropShadow dx="0" dy="14" stdDeviation="10" flood-color="rgba(25,32,22,0.24)"/></filter>'
    elif render_mode == "sticker_cute":
        shadow_filter = '<filter id="shadow" x="-35%" y="-35%" width="170%" height="170%"><feDropShadow dx="0" dy="10" stdDeviation="7" flood-color="rgba(106,74,120,0.22)"/></filter>'
    else:
        shadow_filter = '<filter id="shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="8" stdDeviation="8" flood-color="rgba(58,58,50,0.13)"/></filter>' if style_config.get("shadow") else '<filter id="shadow"><feDropShadow dx="0" dy="0" stdDeviation="0" flood-color="transparent"/></filter>'
    body: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<defs>",
        f'<marker id="arrowHead" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="strokeWidth"><path d="M2,2 L10,6 L2,10 Z" fill="{marker_color}"/></marker>',
        shadow_filter,
        '<linearGradient id="posterBg" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#B8FF6A"/><stop offset="42%" stop-color="#89E66E"/><stop offset="100%" stop-color="#FFF45D"/></linearGradient>',
        '<linearGradient id="stickerBg" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#BDF6F1"/><stop offset="48%" stop-color="#FFE2EE"/><stop offset="100%" stop-color="#FFF5B7"/></linearGradient>',
        '<radialGradient id="softGlow" cx="50%" cy="35%" r="70%"><stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.72"/><stop offset="100%" stop-color="#FFFFFF" stop-opacity="0"/></radialGradient>',
        '<pattern id="softDots" width="18" height="18" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.1" fill="#FFFFFF" opacity="0.18"/></pattern>',
        "</defs>",
        f'<style>text{{font-family:{html.escape(font_css)};dominant-baseline:middle}}.small{{fill:#7A766D}}</style>',
    ]
    body.extend(_canvas_background_svg(render_mode, width, height))

    body.append(_title_svg(title, render_mode, width))

    for element in elements:
        if element.get("type") != "line":
            continue
        stroke = _safe_color(element.get("strokeColor"), marker_color)
        stroke_width = _safe_number(element.get("strokeWidth"), 2.4, 1, 6)
        body.append(
            f'<line x1="{float(element.get("x1") or 0):.1f}" y1="{float(element.get("y1") or 0):.1f}" '
            f'x2="{float(element.get("x2") or 0):.1f}" y2="{float(element.get("y2") or 0):.1f}" '
            f'stroke="{stroke}" stroke-width="{stroke_width:.1f}" stroke-linecap="round"/>'
        )

    # Draw arrows first so cards sit above lines.
    for element in elements:
        if element.get("type") == "arrow":
            start = nodes.get((element.get("start") or {}).get("id"))
            end = nodes.get((element.get("end") or {}).get("id"))
            if not start or not end:
                continue
            points = _smartart_vertical_points(start, end) if element.get("route") == "smartart_vertical" else _elbow_points(start, end)
            stroke = _safe_color(element.get("strokeColor"), "#3F6F82")
            stroke_width = _safe_number(element.get("strokeWidth"), 2.4, 1, 5)
            point_text = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            body.append(f'<polyline points="{point_text}" fill="none" stroke="{stroke}" stroke-width="{stroke_width:.1f}" marker-end="url(#arrowHead)" stroke-linecap="round" stroke-linejoin="round"/>')
            label = _label_text(element.get("label"))
            if label:
                mid = points[len(points) // 2]
                body.append(_svg_text(label, mid[0], mid[1] - 12, 13, "#5E625B", anchor="middle"))

    for element in elements:
        element_type = element.get("type")
        if element_type == "text":
            body.append(_svg_text(str(element.get("text") or ""), element.get("x", 0), element.get("y", 0), int(element.get("fontSize") or 20), _safe_color(element.get("strokeColor"), "#2B2B26"), weight="700"))
        elif element_type in {"rectangle", "ellipse", "diamond"}:
            body.append(_shape_svg(element, style_config))
    body.append("</svg>")
    return "".join(body)


def _canvas_background_svg(render_mode: str, width: int, height: int) -> list[str]:
    if render_mode == "poster_business":
        return [
            '<rect width="100%" height="100%" fill="url(#posterBg)"/>',
            '<rect width="100%" height="100%" fill="url(#softGlow)"/>',
            '<rect width="100%" height="100%" fill="url(#softDots)"/>',
            f'<circle cx="{width * 0.82:.1f}" cy="{height * 0.13:.1f}" r="{min(width, height) * 0.13:.1f}" fill="#FFFFFF" opacity="0.18"/>',
            f'<circle cx="{width * 0.13:.1f}" cy="{height * 0.72:.1f}" r="{min(width, height) * 0.16:.1f}" fill="#7B5CFF" opacity="0.16"/>',
            f'<rect x="32" y="32" width="{width - 64}" height="{height - 64}" rx="34" fill="rgba(255,255,255,0.28)" stroke="rgba(32,33,36,0.22)" stroke-width="2"/>',
        ]
    if render_mode == "sticker_cute":
        return [
            '<rect width="100%" height="100%" fill="url(#stickerBg)"/>',
            '<rect width="100%" height="100%" fill="url(#softDots)"/>',
            f'<circle cx="{width * 0.18:.1f}" cy="{height * 0.23:.1f}" r="{min(width, height) * 0.10:.1f}" fill="#FF8FC7" opacity="0.28"/>',
            f'<circle cx="{width * 0.82:.1f}" cy="{height * 0.24:.1f}" r="{min(width, height) * 0.08:.1f}" fill="#7BE7DE" opacity="0.34"/>',
            f'<circle cx="{width * 0.20:.1f}" cy="{height * 0.78:.1f}" r="{min(width, height) * 0.08:.1f}" fill="#8F86FF" opacity="0.22"/>',
            f'<path d="M{width - 150:.1f},88 q38,-24 74,0" fill="none" stroke="#2B2B26" stroke-width="4" stroke-linecap="round" opacity="0.65"/>',
            f'<rect x="30" y="30" width="{width - 60}" height="{height - 60}" rx="32" fill="rgba(255,255,255,0.22)" stroke="rgba(106,74,120,0.20)" stroke-width="2"/>',
        ]
    return [
        '<rect width="100%" height="100%" fill="#FBFAF7"/>',
        '<rect x="28" y="28" width="calc(100% - 56px)" height="calc(100% - 56px)" rx="24" fill="#FFFFFF" stroke="#E0DDD4"/>',
    ]


def _title_svg(title: str, render_mode: str, width: int) -> str:
    safe_title = html.escape(str(title or "逻辑图"))
    if render_mode == "poster_business":
        capsule_w = min(max(360, len(str(title or "")) * 34 + 96), width - 140)
        return (
            f'<rect x="58" y="44" width="{capsule_w:.1f}" height="76" rx="30" fill="#F7C6D8" stroke="#202124" stroke-width="4" filter="url(#shadow)"/>'
            f'<text x="88" y="83" font-size="34" font-weight="900" fill="#111111" letter-spacing="1">{safe_title}</text>'
        )
    if render_mode == "sticker_cute":
        return (
            f'<text x="72" y="76" font-size="34" font-weight="900" fill="#FF70B8" stroke="#FFFFFF" stroke-width="7" paint-order="stroke" letter-spacing="1">{safe_title}</text>'
            f'<text x="72" y="76" font-size="34" font-weight="900" fill="#FF70B8" letter-spacing="1">{safe_title}</text>'
        )
    return _svg_text(title, 70, 74, 30, "#2B2B26", weight="700")


def _shape_svg(element: dict[str, Any], style_config: dict[str, Any] | None = None) -> str:
    style_config = style_config or STYLE_PRESETS["tech"]
    render_mode = str(style_config.get("render_mode") or "standard")
    x = float(element.get("x") or 0)
    y = float(element.get("y") or 0)
    w = float(element.get("width") or 180)
    h = float(element.get("height") or 80)
    fill = _safe_color(element.get("backgroundColor"), "#EAF3F8")
    stroke = _safe_color(element.get("strokeColor"), "#3A3A32")
    stroke_width = float(element.get("strokeWidth") or 2)
    element_type = element.get("type")
    variant = str(element.get("shape") or "")
    if variant == "arrow_block":
        tip = min(34, w * 0.18)
        notch = min(18, w * 0.08)
        points = f"{x:.1f},{y:.1f} {x + w - tip:.1f},{y:.1f} {x + w:.1f},{y + h / 2:.1f} {x + w - tip:.1f},{y + h:.1f} {x:.1f},{y + h:.1f} {x + notch:.1f},{y + h / 2:.1f}"
        shape = f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)"/>'
    elif variant == "hexagon":
        inset = w * 0.18
        points = f"{x + inset:.1f},{y:.1f} {x + w - inset:.1f},{y:.1f} {x + w:.1f},{y + h / 2:.1f} {x + w - inset:.1f},{y + h:.1f} {x + inset:.1f},{y + h:.1f} {x:.1f},{y + h / 2:.1f}"
        shape = f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)"/>'
    elif variant == "tag_step":
        cut = min(28, h * 0.42)
        points = f"{x:.1f},{y:.1f} {x + w - cut:.1f},{y:.1f} {x + w:.1f},{y + h / 2:.1f} {x + w - cut:.1f},{y + h:.1f} {x:.1f},{y + h:.1f}"
        shape = f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)"/>'
    elif variant == "vertical_card":
        radius = int(style_config.get("radius") or 14)
        badge_r = min(22, h * 0.34)
        badge_cx = x + 34
        badge_cy = y + h / 2
        label = element.get("label") if isinstance(element.get("label"), dict) else {}
        text = _label_text(label)
        font_size = int(label.get("fontSize") or 16)
        color = _safe_color(label.get("strokeColor"), "#2B2B26")
        shape = (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)"/>'
            f'<circle cx="{badge_cx:.1f}" cy="{badge_cy:.1f}" r="{badge_r:.1f}" fill="#FFFFFF" stroke="{stroke}" stroke-width="{stroke_width}"/>'
            f'{_svg_text(str(element.get("templateIndex") or ""), badge_cx, badge_cy + 1, 14, stroke, anchor="middle", weight="700")}'
        )
        return shape + _wrapped_svg_text(text, x + 74 + (w - 92) / 2, y + h / 2, w - 112, font_size, color)
    elif variant == "timeline_card":
        radius = int(style_config.get("radius") or 14)
        marker_x = float(element.get("markerX") or (x + w / 2))
        marker_y = float(element.get("markerY") or y)
        line_end_y = y + h if y < marker_y else y
        shape = (
            f'<line x1="{marker_x:.1f}" y1="{marker_y:.1f}" x2="{marker_x:.1f}" y2="{line_end_y:.1f}" stroke="{stroke}" stroke-width="{stroke_width}" stroke-linecap="round"/>'
            f'<circle cx="{marker_x:.1f}" cy="{marker_y:.1f}" r="10" fill="#FFFFFF" stroke="{stroke}" stroke-width="{stroke_width + 1}"/>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)"/>'
        )
    elif variant == "quadrant_card":
        radius = int(style_config.get("radius") or 14)
        label = element.get("label") if isinstance(element.get("label"), dict) else {}
        module_name = str(element.get("moduleName") or _label_text(label) or "").strip()
        items = _smartart_display_items(element.get("items"))
        font_size = int(label.get("fontSize") or 17)
        color = _safe_color(label.get("strokeColor"), "#2B2B26")
        opacity = 0.42 if element.get("emptyQuadrant") else 1
        poster_lift = ""
        if render_mode in {"poster_business", "sticker_cute"}:
            poster_lift = f'<rect x="{x + 8:.1f}" y="{y + 9:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius}" fill="#202124" opacity="0.10"/>'
        shape = poster_lift + f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)" opacity="{opacity}"/>'
        if element.get("emptyQuadrant") or not module_name:
            return shape
        title_svg = _wrapped_svg_text(module_name, x + w / 2, y + 38, w - 40, font_size + 1, color).replace('font-weight="600"', 'font-weight="700"')
        item_parts: list[str] = []
        start_y = y + 82
        for idx, item in enumerate(items[:4]):
            item_y = start_y + idx * 24
            item_parts.append(f'<circle cx="{x + 32:.1f}" cy="{item_y:.1f}" r="3.4" fill="{stroke}"/>')
            item_parts.append(_svg_text(item, x + 44, item_y + 1, 13, "#55534D", weight="500"))
        return shape + title_svg + "".join(item_parts)
    elif variant == "circle_category":
        radius = int(style_config.get("radius") or 14)
        circle_r = h * 0.48
        circle_cx = x + circle_r
        circle_cy = y + h / 2
        rect_x = x + circle_r * 1.35
        rect_w = w - circle_r * 1.35
        label = element.get("label") if isinstance(element.get("label"), dict) else {}
        text = _label_text(label)
        font_size = int(label.get("fontSize") or 16)
        color = _safe_color(label.get("strokeColor"), "#2B2B26")
        shape = (
            f'<rect x="{rect_x:.1f}" y="{y + 4:.1f}" width="{rect_w:.1f}" height="{h - 8:.1f}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)"/>'
            f'<circle cx="{circle_cx:.1f}" cy="{circle_cy:.1f}" r="{circle_r:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width + 1}"/>'
            f'{_svg_text(str(element.get("templateIndex") or ""), circle_cx, circle_cy + 1, 15, "#2B2B26", anchor="middle", weight="700")}'
        )
        return shape + _wrapped_svg_text(text, rect_x + rect_w / 2, y + h / 2, rect_w - 28, font_size, color)
    elif element_type == "ellipse":
        shape = f'<ellipse cx="{x + w / 2:.1f}" cy="{y + h / 2:.1f}" rx="{w / 2:.1f}" ry="{h / 2:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)"/>'
    elif element_type == "diamond":
        points = f"{x + w / 2:.1f},{y:.1f} {x + w:.1f},{y + h / 2:.1f} {x + w / 2:.1f},{y + h:.1f} {x:.1f},{y + h / 2:.1f}"
        shape = f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)"/>'
    else:
        shape = f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{int(style_config.get("radius") or 14)}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" filter="url(#shadow)"/>'
    if render_mode == "sticker_cute" and element_type in {"rectangle", "ellipse"} and variant not in {"quadrant_card", "vertical_card", "circle_category"}:
        radius = int(style_config.get("radius") or 24)
        shape = f'<rect x="{x - 5:.1f}" y="{y - 5:.1f}" width="{w + 10:.1f}" height="{h + 10:.1f}" rx="{radius + 4}" fill="#FFFFFF" opacity="0.72"/>' + shape
    elif render_mode == "poster_business" and element_type in {"rectangle", "ellipse"} and variant not in {"quadrant_card", "vertical_card", "circle_category"}:
        radius = int(style_config.get("radius") or 22)
        shape = f'<rect x="{x + 7:.1f}" y="{y + 8:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius}" fill="#202124" opacity="0.10"/>' + shape
    label = element.get("label") if isinstance(element.get("label"), dict) else {}
    text = _label_text(label)
    font_size = int(label.get("fontSize") or 17)
    color = _safe_color(label.get("strokeColor"), "#2B2B26")
    return shape + _wrapped_svg_text(text, x + w / 2, y + h / 2, w - 24, font_size, color)


def _edge_points(start: dict[str, Any], end: dict[str, Any]) -> tuple[float, float, float, float]:
    sx = float(start.get("x") or 0)
    sy = float(start.get("y") or 0)
    sw = float(start.get("width") or 180)
    sh = float(start.get("height") or 80)
    ex = float(end.get("x") or 0)
    ey = float(end.get("y") or 0)
    ew = float(end.get("width") or 180)
    eh = float(end.get("height") or 80)
    scx, scy = sx + sw / 2, sy + sh / 2
    ecx, ecy = ex + ew / 2, ey + eh / 2
    if abs(ecx - scx) >= abs(ecy - scy):
        if ecx >= scx:
            return sx + sw + 8, scy, ex - 8, ecy
        return sx - 8, scy, ex + ew + 8, ecy
    if ecy >= scy:
        return scx, sy + sh + 8, ecx, ey - 8
    return scx, sy - 8, ecx, ey + eh + 8


def _elbow_points(start: dict[str, Any], end: dict[str, Any]) -> list[tuple[float, float]]:
    """Return orthogonal polyline points that avoid diagonal connector穿模."""

    x1, y1, x2, y2 = _edge_points(start, end)
    sx = float(start.get("x") or 0)
    sy = float(start.get("y") or 0)
    sw = float(start.get("width") or 180)
    sh = float(start.get("height") or 80)
    ex = float(end.get("x") or 0)
    ey = float(end.get("y") or 0)
    ew = float(end.get("width") or 180)
    eh = float(end.get("height") or 80)
    scx, scy = sx + sw / 2, sy + sh / 2
    ecx, ecy = ex + ew / 2, ey + eh / 2

    # 新增：闭环/返回线从节点外侧绕行，避免向上/向左回连时穿过主流程节点。
    if ecy + 8 < scy:
        corridor_x = min(sx, ex) - 56
        return [(x1, y1), (corridor_x, y1), (corridor_x, y2), (x2, y2)]
    if ecx + 8 < scx:
        corridor_y = min(sy, ey) - 50
        return [(x1, y1), (x1, corridor_y), (x2, corridor_y), (x2, y2)]

    # Same column/row: keep clean vertical/horizontal straight orthogonal line.
    if abs(scx - ecx) < 8 or abs(scy - ecy) < 8:
        return [(x1, y1), (x2, y2)]

    # Mostly vertical relation: route through a horizontal corridor between nodes.
    if abs(ecy - scy) >= abs(ecx - scx):
        mid_y = (y1 + y2) / 2
        return [(x1, y1), (x1, mid_y), (x2, mid_y), (x2, y2)]

    # Mostly horizontal relation: route through a vertical corridor between nodes.
    mid_x = (x1 + x2) / 2
    return [(x1, y1), (mid_x, y1), (mid_x, y2), (x2, y2)]


def _smartart_vertical_points(start: dict[str, Any], end: dict[str, Any]) -> list[tuple[float, float]]:
    """Strict SmartArt connector: bottom-center to top-center vertical line."""

    sx = float(start.get("x") or 0)
    sy = float(start.get("y") or 0)
    sw = float(start.get("width") or 180)
    sh = float(start.get("height") or 80)
    ex = float(end.get("x") or 0)
    ey = float(end.get("y") or 0)
    ew = float(end.get("width") or 180)
    start_x = sx + sw / 2
    end_x = ex + ew / 2
    # SmartArt layout keeps paired nodes in the same fixed column.  If a model
    # edge ever slips across columns, snap to the target column to avoid slants.
    x = end_x if abs(start_x - end_x) <= sw else start_x
    return [(x, sy + sh + 6), (x, ey - 6)]


def _wrapped_svg_text(text: str, cx: float, cy: float, max_width: float, font_size: int, color: str) -> str:
    text = html.escape(str(text or ""))
    if not text:
        return ""
    max_chars = max(4, int(max_width / max(font_size, 12)))
    lines = [text[i : i + max_chars] for i in range(0, len(text), max_chars)][:3]
    start_y = cy - (len(lines) - 1) * font_size * 0.62
    tspans = "".join(f'<tspan x="{cx:.1f}" y="{start_y + idx * font_size * 1.25:.1f}">{line}</tspan>' for idx, line in enumerate(lines))
    return f'<text text-anchor="middle" font-size="{font_size}" font-weight="600" fill="{color}">{tspans}</text>'


def _smartart_display_items(raw_items: Any) -> list[str]:
    if not isinstance(raw_items, list):
        return []
    items = [_clean_smartart_text(str(item), max_len=12, allow_short=True) for item in raw_items if str(item or "").strip()]
    items = _unique_keep_order(items)
    if len(items) > 4:
        return [*items[:3], "等"]
    return items[:4]


def _svg_text(text: str, x: float, y: float, font_size: int, color: str, *, anchor: str = "start", weight: str = "500") -> str:
    return f'<text x="{float(x):.1f}" y="{float(y):.1f}" text-anchor="{anchor}" font-size="{font_size}" font-weight="{weight}" fill="{color}">{html.escape(str(text or ""))}</text>'


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", value or "")[:40]
    return cleaned or "el"


def _safe_color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        return text.upper()
    if re.fullmatch(r"[0-9A-Fa-f]{6}", text):
        return f"#{text.upper()}"
    return fallback


def _safe_number(value: Any, fallback: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _safe_float(value: Any, fallback: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _label_text(label: Any) -> str:
    if isinstance(label, dict):
        return str(label.get("text") or "").strip()
    return ""


def _fallback_label(paragraph: str, index: int) -> str:
    parts = [p for p in re.split(r"[，。；;、\n]+", paragraph or "") if p.strip()]
    if 0 <= index - 1 < len(parts):
        return _short_node_text(_compress_label(parts[index - 1]))
    return ["需求识别", "任务分派", "协同执行", "质量复核", "成果归档", "持续优化"][(index - 1) % 6]


def _compress_label(text: str) -> str:
    cleaned = re.sub(r"[\s，。；;、：:（）()]+", "", text or "")
    cleaned = re.sub(r"^(本项目|通过|建立|实现|确保|针对)", "", cleaned)
    return cleaned[:6] or "关键节点"


def _short_node_text(text: str) -> str:
    cleaned = re.sub(r"[\s，。；;、：:（）()《》<>【】\[\]{}]+", "", text or "")
    cleaned = re.sub(r"^(本项目|项目|通过|建立|实现|确保|针对|开展|进行|完成)", "", cleaned)
    cleaned = cleaned[:6]
    if len(cleaned) < 3:
        return (cleaned + "节点")[:4] if cleaned else "关键节点"
    return cleaned


def _is_decision_label(text: str) -> bool:
    return any(keyword in (text or "") for keyword in ("评审", "审核", "判断", "确认", "通过", "验收"))
