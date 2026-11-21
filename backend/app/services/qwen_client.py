from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

import httpx

from app.core.config import settings
from app.services.model_preferences import selected_model


Message = dict[str, Any]


@dataclass(slots=True)
class LLMResult:
    content: str
    provider: str
    model: str
    usedApi: bool
    error: str | None = None
    finish_reason: str | None = None
    error_kind: str | None = None


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str
    model: str
    usedApi: bool
    error: str | None = None


def _chat_url() -> str:
    base_url = settings.qwen_base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def _embedding_url() -> str:
    base_url = settings.qwen_base_url.rstrip("/")
    if base_url.endswith("/embeddings"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/embeddings"
    return f"{base_url}/v1/embeddings"


def qwen_api_key() -> str:
    return settings.qwen_api_key.strip() or os.getenv("QWEN_API_KEY", "").strip() or os.getenv("DASHSCOPE_API_KEY", "").strip()


def deepseek_api_key() -> str:
    return settings.deepseek_api_key.strip() or os.getenv("DEEPSEEK_API_KEY", "").strip()


def _generation_target(model: str) -> tuple[str, str, str, str]:
    if model.startswith("deepseek/"):
        from urllib.parse import urlsplit
        base = settings.deepseek_base_url.rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("DeepSeek 服务地址必须是有效的 HTTPS 地址")
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        return "deepseek", model.removeprefix("deepseek/"), deepseek_api_key(), url
    return "qwen", model, qwen_api_key(), _chat_url()


def has_generation_api_key(scene: str) -> bool:
    return bool(_generation_target(selected_model(scene))[2])


def has_qwen_api_key() -> bool:
    return bool(qwen_api_key())


def embedding_vectors(texts: Iterable[str], *, model: str | None = None, dimensions: int | None = None) -> EmbeddingResult:
    """Call Qwen/DashScope OpenAI-compatible embeddings endpoint.

    The vector store keeps a local hash-vector fallback, so embedding failures
    should never block ingestion or search.  This function therefore returns an
    empty vector list with the provider error instead of raising.
    """

    text_list = [str(item or "").strip() for item in texts]
    if not text_list:
        return EmbeddingResult(vectors=[], provider="qwen", model=model or selected_model("embedding"), usedApi=False)

    model = model or selected_model("embedding")
    api_key = qwen_api_key()
    if not api_key:
        return EmbeddingResult(vectors=[], provider="qwen", model=model, usedApi=False, error="QWEN_API_KEY is not configured")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    batch_size = max(1, min(int(settings.qwen_embedding_batch_size or 10), 25))
    vectors: list[list[float]] = []

    try:
        with httpx.Client(timeout=settings.qwen_embedding_timeout_seconds) as client:
            for start in range(0, len(text_list), batch_size):
                batch = text_list[start : start + batch_size]
                payload: dict[str, Any] = {"model": model, "input": batch, "encoding_format": "float"}
                if dimensions:
                    payload["dimensions"] = dimensions
                response = client.post(_embedding_url(), headers=headers, json=payload)
                if response.status_code == 400 and "dimensions" in payload:
                    payload.pop("dimensions", None)
                    response = client.post(_embedding_url(), headers=headers, json=payload)
                if response.is_error:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text.strip()[:1000]}")
                data = response.json().get("data", [])
                ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
                for item in ordered:
                    vector = item.get("embedding")
                    if not isinstance(vector, list):
                        raise RuntimeError("embedding response missing vector")
                    vectors.append([float(value) for value in vector])
        if len(vectors) != len(text_list):
            raise RuntimeError(f"embedding count mismatch: expected {len(text_list)}, got {len(vectors)}")
        return EmbeddingResult(vectors=vectors, provider="qwen", model=model, usedApi=True)
    except Exception as exc:
        print(f"[QwenEmbedding] failed model={model}: {_clean_error(exc)}", flush=True)
        return EmbeddingResult(vectors=[], provider="qwen", model=model, usedApi=False, error=_clean_error(exc))


def ollama_embedding_vectors(texts: Iterable[str], *, model: str | None = None) -> EmbeddingResult:
    """Generate local embeddings through Ollama's native embedding API.

    Ollama versions expose ``/api/embed`` for batched input. The legacy
    ``/api/embeddings`` endpoint is used as a compatibility fallback for older
    local installations. No cloud key or external API is involved.
    """

    text_list = [str(item or "").strip() for item in texts]
    model = model or settings.ollama_embedding_model or "bge-m3"
    if not text_list:
        return EmbeddingResult(vectors=[], provider="ollama", model=model, usedApi=False)

    base_url = settings.ollama_base_url.rstrip("/")
    timeout = max(5.0, float(settings.ollama_timeout_seconds or 180.0))
    batch_size = max(1, min(int(settings.ollama_embedding_batch_size or 8), 32))
    vectors: list[list[float]] = []
    try:
        with httpx.Client(timeout=timeout) as client:
            for start in range(0, len(text_list), batch_size):
                batch = text_list[start : start + batch_size]
                response = client.post(f"{base_url}/api/embed", json={"model": model, "input": batch})
                if response.status_code < 400:
                    data = response.json().get("embeddings", [])
                    if isinstance(data, list) and len(data) == len(batch):
                        vectors.extend(
                            [[float(value) for value in vector] for vector in data if isinstance(vector, list)]
                        )
                        if len(vectors) == start + len(batch):
                            continue

                # Compatibility path for Ollama releases without /api/embed.
                for text in batch:
                    legacy = client.post(f"{base_url}/api/embeddings", json={"model": model, "prompt": text})
                    if legacy.is_error:
                        raise RuntimeError(f"HTTP {legacy.status_code}: {legacy.text.strip()[:500]}")
                    vector = legacy.json().get("embedding")
                    if not isinstance(vector, list):
                        raise RuntimeError("Ollama embedding response missing embedding")
                    vectors.append([float(value) for value in vector])
        if len(vectors) != len(text_list):
            raise RuntimeError(f"embedding count mismatch: expected {len(text_list)}, got {len(vectors)}")
        return EmbeddingResult(vectors=vectors, provider="ollama", model=model, usedApi=False)
    except Exception as exc:
        print(f"[OllamaEmbedding] failed model={model}: {_clean_error(exc)}", flush=True)
        return EmbeddingResult(vectors=[], provider="ollama", model=model, usedApi=False, error=_clean_error(exc))


def _clean_error(exc: Exception) -> str:
    text = str(exc).strip()
    return (text or exc.__class__.__name__)[:500]


def _extract_stream_text(chunk: dict) -> str:
    """Extract visible text from OpenAI-compatible streaming chunks.

    Qwen/DashScope can emit several chunk shapes depending on model family and
    compatibility layer.  We intentionally ignore reasoning_content so the UI
    starts rendering the final answer as soon as it is available.
    """

    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0] or {}
    delta = choice.get("delta") or {}
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
    message = choice.get("message") or {}
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    text = choice.get("text")
    return text if isinstance(text, str) else ""


def _apply_qwen_generation_options(payload: dict) -> None:
    # Qwen3 thinking models may spend a long time outputting hidden/empty
    # reasoning chunks before normal content.  For bid writing we need fast,
    # visible streaming, so thinking is disabled by default but kept configurable.
    payload["enable_thinking"] = settings.qwen_enable_thinking


async def chat_completion(
    messages: Iterable[Message],
    *,
    temperature: float = 0.3,
    max_tokens: int = 2500,
    fallback: str = "",
    json_mode: bool = False,
    model: str | None = None,
    scene: str | None = None,
    timeout_seconds: float | None = None,
) -> LLMResult:
    """Call the scene-selected OpenAI-compatible text generation endpoint."""

    model = model or selected_model(scene or "tender_parse")
    fallback_text = fallback or "模型服务暂未返回内容。"
    provider, upstream_model, api_key, url = _generation_target(model)
    if not api_key:
        print(f"[{provider}] API Key is empty; generation fallback used.", flush=True)
        return LLMResult(content=fallback_text, provider=provider, model=model, usedApi=False, error=f"{provider.upper()}_API_KEY is not configured")

    payload = {
        "model": upstream_model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if provider == "qwen":
        _apply_qwen_generation_options(payload)
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        print(f"[{provider}] requesting generation model={model}", flush=True)
        timeout = max(5.0, float(timeout_seconds or settings.qwen_timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 400 and "enable_thinking" in payload:
                # Some newly released Qwen model ids are exposed through the
                # compatible endpoint before this optional field is supported.
                # Retry once with only the OpenAI-compatible core fields.
                minimal_payload = dict(payload)
                minimal_payload.pop("enable_thinking", None)
                response = await client.post(url, headers=headers, json=minimal_payload)
            if response.is_error:
                # Keep the provider's diagnostic body; DashScope uses it to
                # distinguish invalid model ids, permissions, and parameters.
                detail = response.text.strip()[:1000] if provider == "qwen" else "DeepSeek 请求失败，请检查模型、密钥和账户状态"
                raise RuntimeError(f"HTTP {response.status_code}: {detail}")
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        print(f"[{provider}] completed generation model={model}", flush=True)
        return LLMResult(content=str(content).strip() or fallback_text, provider=provider, model=model, usedApi=True,
                         finish_reason=data["choices"][0].get("finish_reason"))
    except Exception as exc:
        print(f"[{provider}] failed generation model={model}: {_clean_error(exc)}", flush=True)
        return LLMResult(content=fallback_text, provider=provider, model=model, usedApi=False, error=_clean_error(exc),
                         error_kind="network" if isinstance(exc, httpx.TransportError) else None)


async def image_ocr_completion(
    image_path: str | Path,
    prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.05,
    max_tokens: int = 1800,
    fallback: str = "",
    json_mode: bool = True,
) -> LLMResult:
    """Call an OpenAI-compatible Qwen OCR/VL model with a local image."""

    model = model or selected_model("knowledge_ocr")
    fallback_text = fallback or ""
    api_key = qwen_api_key()
    if not api_key:
        print("[QwenOCR] QWEN_API_KEY is empty; image OCR skipped.", flush=True)
        return LLMResult(content=fallback_text, provider="qwen", model=model, usedApi=False, error="QWEN_API_KEY is not configured")

    try:
        data_url = _image_data_url(Path(image_path))
    except Exception as exc:
        return LLMResult(content=fallback_text, provider="qwen", model=model, usedApi=False, error=_clean_error(exc))

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        print(f"[QwenOCR] requesting model={model} image={Path(image_path).name}", flush=True)
        async with httpx.AsyncClient(timeout=settings.qwen_ocr_timeout_seconds) as client:
            response = await client.post(_chat_url(), headers=headers, json=payload)
            if response.status_code == 400 and "response_format" in payload:
                minimal_payload = dict(payload)
                minimal_payload.pop("response_format", None)
                response = await client.post(_chat_url(), headers=headers, json=minimal_payload)
            if response.is_error:
                detail = response.text.strip()[:1000]
                raise RuntimeError(f"HTTP {response.status_code}: {detail}")
            data = response.json()
        content = data["choices"][0]["message"].get("content", "")
        if isinstance(content, list):
            content = "".join(str(item.get("text", "") if isinstance(item, dict) else item) for item in content)
        print(f"[QwenOCR] completed model={model} image={Path(image_path).name}", flush=True)
        return LLMResult(content=str(content).strip() or fallback_text, provider="qwen", model=model, usedApi=True)
    except Exception as exc:
        print(f"[QwenOCR] failed model={model} image={Path(image_path).name}: {_clean_error(exc)}", flush=True)
        return LLMResult(content=fallback_text, provider="qwen", model=model, usedApi=False, error=_clean_error(exc))


def _image_data_url(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(str(path))
    raw, mime = _prepare_image_bytes(path)
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _prepare_image_bytes(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if len(raw) <= 7 * 1024 * 1024:
        return raw, mime
    try:
        from PIL import Image  # type: ignore

        with Image.open(BytesIO(raw)) as img:
            img = img.convert("RGB")
            max_side = 2200
            scale = min(1.0, max_side / max(img.size))
            if scale < 1:
                img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=88, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except Exception:
        return raw, mime


class ModelStreamError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


async def stream_chat_completion(
    messages: Iterable[Message],
    *,
    temperature: float = 0.3,
    max_tokens: int = 2500,
    fallback: str = "",
    strict: bool = False,
    json_mode: bool = False,
    timeout_seconds: float | None = None,
    scene: str | None = None,
) -> AsyncIterator[str]:
    """Yield upstream deltas immediately; strict callers require a complete response."""
    model = selected_model(scene or "bid_content")
    fallback_text = fallback or "Qwen API 暂未返回内容。"
    provider, upstream_model, api_key, url = _generation_target(model)
    if not api_key:
        if strict:
            raise RuntimeError("未配置生成模型 API Key，无法开始生成")
        for idx in range(0, len(fallback_text), 80):
            yield fallback_text[idx : idx + 80]
        return

    payload = {"model": upstream_model, "messages": list(messages), "temperature": temperature,
               "max_tokens": max_tokens, "stream": True}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if provider == "qwen":
        _apply_qwen_generation_options(payload)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    received = False
    completed = False
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds or settings.qwen_timeout_seconds) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.is_error:
                    raise ModelStreamError(
                        f"生成服务请求失败（HTTP {response.status_code}）",
                        retryable=response.status_code in {408, 429} or response.status_code >= 500,
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        completed = True
                        break
                    try:
                        chunk = json.loads(data)
                    except (ValueError, TypeError) as exc:
                        if strict:
                            raise ModelStreamError("生成服务返回了无效的流式数据", retryable=True) from exc
                        continue
                    if not isinstance(chunk, dict):
                        if strict:
                            raise ModelStreamError("生成服务返回了无效的数据结构", retryable=True)
                        continue
                    if chunk.get("error"):
                        raise RuntimeError("生成服务返回错误，请稍后重试")
                    for choice in chunk.get("choices") or []:
                        reason = choice.get("finish_reason")
                        if strict and reason and reason != "stop":
                            message = "生成内容超出单次输出长度，请缩小生成范围后重试" if reason == "length" else "生成未正常完成，请检查输入后重试"
                            raise RuntimeError(message)
                        if reason == "stop":
                            completed = True
                    delta = _extract_stream_text(chunk)
                    if delta:
                        received = True
                        yield delta
        if strict and not completed:
            raise ModelStreamError("生成连接提前中断，内容尚未完成", retryable=True)
        if strict and not received:
            raise ModelStreamError("生成服务未返回正文内容", retryable=True)
    except Exception as exc:
        if strict or received:
            raise
        print(f"[{provider}] stream failed generation model={model}: {_clean_error(exc)}", flush=True)
        for idx in range(0, len(fallback_text), 80):
            yield fallback_text[idx : idx + 80]
