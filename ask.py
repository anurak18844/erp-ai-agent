"""Ask the running ERP AI Agent and print a human-readable answer in the terminal."""
from __future__ import annotations

import argparse
import sys

from runner_bootstrap import ensure_project_venv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask ERP AI Agent from the command line")
    parser.add_argument("question", nargs="+", help="Natural-language question")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="FastAPI base URL")
    parser.add_argument("--show-request-id", action="store_true")
    return parser.parse_args()


def main() -> int:
    ensure_project_venv(__file__)
    import httpx

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    question = " ".join(args.question).strip()
    try:
        response = httpx.post(
            f"{args.url.rstrip('/')}/chat",
            json={"message": question},
            timeout=120.0,
        )
        response.raise_for_status()
    except httpx.ConnectError:
        print("เชื่อมต่อ API ไม่ได้ กรุณาเปิด Server ก่อนด้วย: python run_server.py")
        return 2
    except httpx.HTTPStatusError as exc:
        print(f"API ตอบกลับด้วย HTTP {exc.response.status_code}")
        try:
            print(exc.response.json())
        except ValueError:
            print(exc.response.text)
        return 1

    payload = response.json()
    answer = str(payload.get("answer", "ไม่มีคำตอบจาก API"))
    print("\nคำตอบ")
    print("-" * 72)
    print(answer)
    print("-" * 72)
    if args.show_request_id:
        request_id = (payload.get("debug") or {}).get("request_id")
        if request_id:
            print(f"request_id: {request_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
