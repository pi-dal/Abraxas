#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def post_json(base_url: str, secret: str, path: str, payload: dict):
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method="POST")
    req.add_header("X-Internal-Secret", secret)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc


def require_env():
    base_url = os.getenv("INTERNAL_API_BASE_URL", "http://localhost:3000")
    secret = os.getenv("INTERNAL_API_SECRET", "")
    if not secret:
        print("ERROR: INTERNAL_API_SECRET is required", file=sys.stderr)
        sys.exit(1)
    return base_url, secret


def print_result(endpoint: str, status: int, data: dict):
    print(f"[{status}] {endpoint}")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def load_text_content(args):
    if args.content:
        return args.content
    if args.content_file:
        with open(args.content_file, "r", encoding="utf-8") as fp:
            return fp.read()
    raise ValueError("Either --content or --content-file is required")


def cmd_list_projects(args):
    base_url, secret = require_env()
    payload = {"ownerId": args.owner_id, "limit": args.limit}
    if args.search:
        payload["search"] = args.search
    status, data = post_json(base_url, secret, "/api/internal/projects/list", payload)
    print_result("/api/internal/projects/list", status, data)


def cmd_list_files(args):
    base_url, secret = require_env()
    payload = {
        "projectId": args.project_id,
        "directory": args.directory,
        "recursive": args.recursive,
    }
    if args.pattern:
        payload["pattern"] = args.pattern
    status, data = post_json(base_url, secret, "/api/internal/files/list", payload)
    print_result("/api/internal/files/list", status, data)


def cmd_read_file(args):
    base_url, secret = require_env()
    payload = {
        "projectId": args.project_id,
        "filePath": args.file_path,
        "source": args.source,
    }
    if args.user_id:
        payload["userId"] = args.user_id
    if args.start_line is not None:
        payload["startLine"] = args.start_line
    if args.end_line is not None:
        payload["endLine"] = args.end_line

    status, data = post_json(base_url, secret, "/api/internal/files/read", payload)
    print_result("/api/internal/files/read", status, data)


def cmd_edit_file(args):
    base_url, secret = require_env()
    payload = {
        "projectId": args.project_id,
        "filePath": args.file_path,
        "content": load_text_content(args),
        "syncYjs": args.sync_yjs,
        "strictYjs": not args.best_effort_yjs,
    }
    status, data = post_json(base_url, secret, "/api/internal/files/edit", payload)
    print_result("/api/internal/files/edit", status, data)


def build_parser():
    parser = argparse.ArgumentParser(description="Internal API CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_projects = sub.add_parser("list-projects")
    p_projects.add_argument("--owner-id", required=True)
    p_projects.add_argument("--search", default="")
    p_projects.add_argument("--limit", type=int, default=20)
    p_projects.set_defaults(func=cmd_list_projects)

    p_files = sub.add_parser("list-files")
    p_files.add_argument("--project-id", required=True)
    p_files.add_argument("--directory", default="")
    p_files.add_argument("--recursive", dest="recursive", action="store_true", default=True)
    p_files.add_argument("--no-recursive", dest="recursive", action="store_false")
    p_files.add_argument("--pattern", default="")
    p_files.set_defaults(func=cmd_list_files)

    p_read = sub.add_parser("read-file")
    p_read.add_argument("--project-id", required=True)
    p_read.add_argument("--file-path", required=True)
    p_read.add_argument("--user-id", default="")
    p_read.add_argument(
        "--source",
        choices=["effective", "storage"],
        default="effective",
        help="Read source: effective (Yjs + pending edits) or storage (deterministic)",
    )
    p_read.add_argument("--start-line", type=int)
    p_read.add_argument("--end-line", type=int)
    p_read.set_defaults(func=cmd_read_file)

    p_edit = sub.add_parser("edit-file")
    p_edit.add_argument("--project-id", required=True)
    p_edit.add_argument("--file-path", required=True)
    p_edit.add_argument("--content", default="")
    p_edit.add_argument("--content-file", default="")
    p_edit.add_argument(
        "--sync-yjs",
        dest="sync_yjs",
        action="store_true",
        help="Sync edited content to ws-server Yjs document (default)",
    )
    p_edit.add_argument(
        "--no-sync-yjs",
        dest="sync_yjs",
        action="store_false",
        help="Write storage only without ws-server Yjs sync",
    )
    p_edit.add_argument(
        "--best-effort-yjs",
        action="store_true",
        default=False,
        help="Do not fail request when Yjs sync fails",
    )
    p_edit.set_defaults(sync_yjs=True)
    p_edit.set_defaults(func=cmd_edit_file)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
