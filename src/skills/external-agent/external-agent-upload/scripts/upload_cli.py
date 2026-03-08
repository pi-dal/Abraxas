#!/usr/bin/env python3
import argparse
import base64
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:600]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc


def read_base64(path: str) -> str:
    with open(path, "rb") as fp:
        return base64.b64encode(fp.read()).decode("ascii")


def require_env():
    base_url = os.getenv("INTERNAL_API_BASE_URL", "http://localhost:3000")
    secret = os.getenv("INTERNAL_API_SECRET", "")
    if not secret:
        print("ERROR: INTERNAL_API_SECRET is required", file=sys.stderr)
        sys.exit(1)
    return base_url, secret


def cmd_upload_file(args):
    base_url, secret = require_env()
    if not os.path.exists(args.source):
        raise ValueError(f"Source file does not exist: {args.source}")

    payload = {
        "projectId": args.project_id,
        "filePath": args.file_path,
        "contentBase64": read_base64(args.source),
        "overwrite": not args.no_overwrite,
        "syncYjs": args.sync_yjs,
        "strictYjs": not args.best_effort_yjs,
    }
    status, data = post_json(base_url, secret, "/api/internal/files/upload", payload)
    print(f"[{status}] /api/internal/files/upload")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_import_project(args):
    base_url, secret = require_env()
    if not os.path.exists(args.source):
        raise ValueError(f"Source file does not exist: {args.source}")

    payload = {
        "fileBase64": read_base64(args.source),
        "fileName": args.file_name or os.path.basename(args.source),
        "ownerId": args.owner_id,
    }
    if args.name:
        payload["name"] = args.name
    if args.description:
        payload["description"] = args.description

    status, data = post_json(
        base_url,
        secret,
        "/api/internal/projects/import/upload",
        payload,
    )
    print(f"[{status}] /api/internal/projects/import/upload")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="Internal upload CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_upload = sub.add_parser("upload-file")
    p_upload.add_argument("--project-id", required=True)
    p_upload.add_argument("--file-path", required=True)
    p_upload.add_argument("--source", required=True)
    p_upload.add_argument("--no-overwrite", action="store_true", default=False)
    p_upload.add_argument(
        "--sync-yjs",
        dest="sync_yjs",
        action="store_true",
        help="Sync text-like upload to ws-server Yjs document (default)",
    )
    p_upload.add_argument(
        "--no-sync-yjs",
        dest="sync_yjs",
        action="store_false",
        help="Upload to storage only without ws-server Yjs sync",
    )
    p_upload.add_argument(
        "--best-effort-yjs",
        action="store_true",
        default=False,
        help="Do not fail request when Yjs sync fails",
    )
    p_upload.set_defaults(sync_yjs=True)
    p_upload.set_defaults(func=cmd_upload_file)

    p_import = sub.add_parser("import-project")
    p_import.add_argument("--owner-id", required=True)
    p_import.add_argument("--source", required=True)
    p_import.add_argument("--file-name", default="")
    p_import.add_argument("--name", default="")
    p_import.add_argument("--description", default="")
    p_import.set_defaults(func=cmd_import_project)

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
