#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def post_json(
    base_url: str, secret: str, path: str, payload: dict, timeout_seconds: float
):
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method="POST")
    req.add_header("X-Internal-Secret", secret)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:600]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc


def parse_args():
    parser = argparse.ArgumentParser(description="Run internal agent API")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--user-id", default="")
    parser.add_argument("--mode", choices=["ask", "agent"], default="agent")
    parser.add_argument(
        "--runtime",
        choices=["builtin", "openclaw"],
        default=os.getenv("INTERNAL_AGENT_RUNTIME", "openclaw"),
    )
    parser.add_argument("--session-id", default="")
    parser.add_argument("--referenced-file", action="append", default=[])
    parser.add_argument("--output-json", default="")
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("INTERNAL_AGENT_TIMEOUT_SECONDS", "360")),
        help="HTTP timeout (seconds), default 360",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    base_url = os.getenv("INTERNAL_API_BASE_URL", "http://localhost:3000")
    secret = os.getenv("INTERNAL_API_SECRET", "")
    if not secret:
        print("ERROR: INTERNAL_API_SECRET is required", file=sys.stderr)
        sys.exit(1)

    payload = {
        "projectId": args.project_id,
        "userId": args.user_id,
        "message": args.message,
        "mode": args.mode,
        "runtime": args.runtime,
        "referencedFiles": args.referenced_file,
    }
    if args.session_id:
        payload["sessionId"] = args.session_id

    try:
        status, data = post_json(
            base_url,
            secret,
            "/api/internal/agent/run",
            payload,
            timeout_seconds=args.timeout,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"[{status}] /api/internal/agent/run")
    print(json.dumps(data, ensure_ascii=False, indent=2))

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
            fp.write("\n")

    if not data.get("success", False):
        sys.exit(3)


if __name__ == "__main__":
    main()
