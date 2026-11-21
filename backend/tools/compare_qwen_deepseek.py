"""Compare two text models on identical synthetic input. Live calls require --live."""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import settings
from app.services import qwen_client

PROMPT = (
    '请仅依据以下虚构采购信息，写一段不超过150字的投标响应，并将无法确认的事项明确标为待核实：'
    '某学校拟采购食品配送服务，要求每周工作日配送；未提供报价、许可证信息及车辆数量。'
    '请分别写配送安排、需核实事项，不得编造报价、资质或车辆。'
)

async def main():
    parser = argparse.ArgumentParser(description='使用相同虚构数据对比千问和 DeepSeek。默认不调用任何外部模型。')
    parser.add_argument('--live', action='store_true', help='明确发起付费 API 调用')
    parser.add_argument('--deepseek-model', default='', help='填写从 DeepSeek 模型列表获得的真实模型 ID')
    parser.add_argument('--qwen-model', default=settings.qwen_generation_model)
    args = parser.parse_args()
    out_dir = ROOT / '.cache' / 'knowledge-recovery-20261109' / 'model-comparison'
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {'mode': 'live' if args.live else 'dry-run', 'prompt': PROMPT, 'results': []}
    if args.live:
        if not args.deepseek_model.strip() or not qwen_client.qwen_api_key() or not qwen_client.deepseek_api_key():
            raise SystemExit('真实对比需要 --deepseek-model、千问 API Key 和 DeepSeek API Key；密钥仅从本地配置读取。')
        for provider, model in [('qwen', args.qwen_model), ('deepseek', 'deepseek/' + args.deepseek_model.removeprefix('deepseek/'))]:
            started = time.monotonic()
            r = await qwen_client.chat_completion([{'role':'user','content':PROMPT}], model=model, max_tokens=500, temperature=0.1, timeout_seconds=60)
            payload['results'].append({'provider':provider, 'model':model, 'elapsed_seconds':round(time.monotonic()-started,2), 'used_api':r.usedApi, 'finish_reason':r.finish_reason, 'content':r.content if r.usedApi else '', 'error':r.error if not r.usedApi else None})
    else:
        payload['results'] = [{'provider':'qwen','status':'未调用'}, {'provider':'deepseek','status':'未调用'}]
    path = out_dir / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf8')
    print('结果文件：',path)
    if args.live and not all(x['used_api'] for x in payload['results']):
        raise SystemExit('至少一个模型未成功，不能将本次结果作为能力对比。')

if __name__ == '__main__':
    asyncio.run(main())
