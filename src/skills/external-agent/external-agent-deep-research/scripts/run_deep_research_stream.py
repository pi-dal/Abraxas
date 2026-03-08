#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def build_request(args):
    payload = {
        "query": args.query,
        "arxiv_papers": args.arxiv_papers,
        "web_pages": args.web_pages,
        "structured": args.structured,
        "max_iterations": args.max_iterations,
    }
    if args.project_id:
        payload["projectId"] = args.project_id
    return payload


def emit_event(event_name, data_lines, out_fp):
    if event_name is None:
        return

    data_text = "\n".join(data_lines).strip()
    data_obj = None
    if data_text:
        try:
            data_obj = json.loads(data_text)
        except json.JSONDecodeError:
            data_obj = {"raw": data_text}

    record = {"event": event_name, "data": data_obj}
    print(json.dumps(record, ensure_ascii=False))
    if out_fp is not None:
        out_fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_sse_stream(response, output_jsonl_path=""):
    event_name = None
    data_lines = []

    out_fp = None
    if output_jsonl_path:
        out_fp = open(output_jsonl_path, "w", encoding="utf-8")

    try:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")

            if not line:
                emit_event(event_name, data_lines, out_fp)
                event_name = None
                data_lines = []
                continue

            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                if event_name is None:
                    event_name = "message"
                data_lines.append(line.split(":", 1)[1].strip())
                continue

        # Flush tail event when stream ends without trailing blank line
        emit_event(event_name, data_lines, out_fp)
    finally:
        if out_fp is not None:
            out_fp.close()


def main():
    parser = argparse.ArgumentParser(description="Run deep research stream")
    parser.add_argument("--query", required=True)
    parser.add_argument("--arxiv-papers", type=int, default=10)
    parser.add_argument("--web-pages", type=int, default=10)
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--project-id", default="")
    parser.add_argument("--structured", action="store_true", default=False)
    parser.add_argument("--output-jsonl", default="")
    args = parser.parse_args()

    base_url = os.getenv("AI_SERVER_BASE_URL", "http://localhost:8000")
    url = f"{base_url.rstrip('/')}/api/deep-research/stream"

    payload = build_request(args)
    req_data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url=url, data=req_data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")

    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            parse_sse_stream(response, args.output_jsonl)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"ERROR: HTTP {exc.code}: {body[:600]}", file=sys.stderr)
        sys.exit(2)
    except urllib.error.URLError as exc:
        print(f"ERROR: network failure: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
