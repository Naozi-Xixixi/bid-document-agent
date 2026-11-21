"""CPU OCR with bundled models; explicit failure, no silent empty-string fallback."""
from dataclasses import dataclass
from pathlib import Path
import threading
import os


@dataclass(frozen=True)
class OCRResult:
    text: str
    status: str  # recognized / no_text; exceptions indicate failure
    confidence: float = 0.0
    engine: str = "rapidocr-onnx-v1.4.4"


_engine = None
_lock = threading.Lock()


def recognize(path: Path) -> OCRResult:
    global _engine
    with _lock:
        if _engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                _engine = RapidOCR(intra_op_num_threads=int(os.environ.get("LOCAL_OCR_THREADS", "3")), inter_op_num_threads=1,
                                   det_limit_side_len=1280, det_limit_type="max", rec_batch_num=1)
            except Exception as exc:
                raise RuntimeError("本地 OCR 不可用，请在后端环境安装 requirements-ocr.txt：" + str(exc)) from exc
        try:
            rows, _ = _engine(str(path))
        except Exception as exc:
            raise RuntimeError("图片 OCR 执行失败：" + str(exc)) from exc
        if not rows:
            return OCRResult("", "no_text")
        texts = [str(row[1]).strip() for row in rows if str(row[1]).strip()]
        text = "\n".join(texts)
        scores = [float(row[2]) for row in rows if str(row[1]).strip()]
        return OCRResult(text, "recognized" if text else "no_text", sum(scores) / len(scores) if scores else 0.0)


def recognize_cached(path: Path, cache_dir: Path) -> OCRResult:
    """Tenant-local, content-addressed cache; failed OCR is never cached."""
    import hashlib
    import json
    from dataclasses import asdict
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / ("rapidocr-v1-" + digest.hexdigest() + ".json")
    if target.exists():
        try:
            return OCRResult(**json.loads(target.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            pass
    result = recognize_isolated(path)
    import uuid
    temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return result


def recognize_isolated(path: Path) -> OCRResult:
    """A native ONNX crash or hung image cannot terminate the web service."""
    import json
    import subprocess
    import sys
    try:
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "app.modules.knowledge.local_ocr", str(path.resolve())],
            cwd=str(Path(__file__).resolve().parents[3]),
            capture_output=True, text=True, encoding="utf-8",
            timeout=120, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("图片 OCR 超过 120 秒，已停止该图片任务，可重试") from exc
    if completed.returncode:
        raise RuntimeError(f"图片 OCR 子进程异常退出（{completed.returncode}）：{completed.stderr[-600:]}")
    try:
        return OCRResult(**json.loads(completed.stdout))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("图片 OCR 子进程没有返回有效结果") from exc


if __name__ == "__main__":
    import json
    import sys
    from dataclasses import asdict
    print(json.dumps(asdict(recognize(Path(sys.argv[1]))), ensure_ascii=False))
