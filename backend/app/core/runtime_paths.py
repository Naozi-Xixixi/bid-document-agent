"""Keep application caches and temporary artifacts beside the backend, off the system drive."""
import os
from pathlib import Path
import tempfile


def configure_runtime_paths():
    base = Path(os.environ.get("WORDAGENT_RUNTIME_DIR") or Path(__file__).resolve().parents[2] / "data" / "runtime").resolve()
    for name, folder in {"TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp", "PIP_CACHE_DIR": "pip",
                         "HF_HOME": "hf", "MODELSCOPE_CACHE": "modelscope", "PADDLE_PDX_CACHE_HOME": "paddlex",
                         "PADDLE_HOME": "paddle", "XDG_CACHE_HOME": "cache"}.items():
        path = base / folder
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    tempfile.tempdir = str(base / "tmp")
    return base
