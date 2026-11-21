from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import rag_case_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan and ingest local historical bid/proposal PDF pairs into the RAG case library.")
    parser.add_argument(
        "--folder",
        default=None,
        help="Historical case folder. Defaults to the sibling folder named 示例集团提供的标书.",
    )
    parser.add_argument("--scan-only", action="store_true", help="Only print detected case pairs; do not write database rows.")
    parser.add_argument("--no-force", action="store_true", help="Do not clear existing chunks for matched cases before ingesting.")
    args = parser.parse_args()

    if args.scan_only:
        payload = rag_case_service.scan_case_folder(args.folder)
    else:
        payload = rag_case_service.ingest_case_folder(args.folder, force=not args.no_force)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
