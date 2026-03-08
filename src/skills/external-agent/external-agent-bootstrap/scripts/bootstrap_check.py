#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def call_get(base_url: str, secret: str, path: str):
    url = f"{base_url.rstrip('/')}{path}"
    req = urllib.request.Request(url=url, method="GET")
    req.add_header("X-Internal-Secret", secret)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {path}: {body[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {path}: {exc}") from exc


def parse_args():
    parser = argparse.ArgumentParser(description="Check internal API connectivity")
    parser.add_argument(
        "--base-url",
        default=os.getenv("INTERNAL_API_BASE_URL", "http://localhost:3000"),
    )
    parser.add_argument("--strict", action="store_true", default=False)
    return parser.parse_args()


def main():
    args = parse_args()
    base_url = args.base_url
    secret = os.getenv("INTERNAL_API_SECRET", "")

    if not secret:
        print("ERROR: INTERNAL_API_SECRET is required", file=sys.stderr)
        sys.exit(1)

    checks = [
        "/api/internal/capabilities",
        "/api/internal/chat-config",
    ]

    failed = False
    for path in checks:
        try:
            status, data = call_get(base_url, secret, path)
            print(f"[{status}] {path}")
            print(json.dumps(data, ensure_ascii=False, indent=2))
        except RuntimeError as exc:
            failed = True
            print(f"[ERROR] {path}: {exc}", file=sys.stderr)

    if failed and args.strict:
        sys.exit(2)


if __name__ == "__main__":
    main()
