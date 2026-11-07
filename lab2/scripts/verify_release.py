import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


direct_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_json(url: str, payload: dict | None = None) -> dict:
    request = urllib.request.Request(url)
    if payload is not None:
        request.data = json.dumps(payload).encode()
        request.add_header("Content-Type", "application/json")
    with direct_http.open(request, timeout=5) as response:
        return json.load(response)


def wait_for_readiness(base_url: str) -> dict:
    deadline = time.monotonic() + 30
    last_error = "readiness did not report ready"
    while time.monotonic() < deadline:
        try:
            ready = read_json(f"{base_url}/readyz")
            if ready.get("status") == "ready":
                return ready
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = str(error)
        time.sleep(0.5)
    raise RuntimeError(f"service did not become ready: {last_error}")


def verify(base_url: str, expected_metadata: dict | None = None) -> int:
    base_url = base_url.rstrip("/")
    ready = wait_for_readiness(base_url)
    meta = read_json(f"{base_url}/meta")
    for field, expected in (expected_metadata or {}).items():
        if expected is not None and meta.get(field) != expected:
            raise RuntimeError(f"/meta {field}: expected {expected!r}, got {meta.get(field)!r}")

    search = read_json(f"{base_url}/v1/search", {
        "query": "как вернуть предыдущую модель после плохого релиза",
        "limit": 3,
    })
    versions = (ready["index_version"], meta["index_version"], search["index_version"])
    if not versions[0] or len(set(versions)) != 1:
        raise RuntimeError("index versions from /readyz, /meta and /v1/search differ")

    print(json.dumps({"ready": ready, "meta": meta, "search": search}, ensure_ascii=False, indent=2))
    results = search["results"]
    top_result = results[0]["id"] if results else None
    if top_result != "doc-rollback":
        print(f"unexpected ranking: expected doc-rollback, got {top_result!r}", file=sys.stderr)
        return 2
    return 0


@contextmanager
def forward_service(namespace: str, release: str, port: int):
    local_port = str(port) if port else ""
    with TemporaryDirectory() as temp_dir:
        log_path = Path(temp_dir) / "port-forward.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen([
                "kubectl", "-n", namespace, "port-forward", "--address=127.0.0.1",
                f"service/{release}-mlops-search", f"{local_port}:80",
            ], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while process.poll() is None and time.monotonic() < deadline:
                match = re.search(r"^Forwarding from 127\.0\.0\.1:(\d+) ->", log_path.read_text(), re.MULTILINE)
                if match:
                    yield f"http://127.0.0.1:{match.group(1)}"
                    return
                time.sleep(0.2)
            raise RuntimeError(f"kubectl port-forward did not start: {log_path.read_text().strip()}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--namespace", default="mlops-students")
    parser.add_argument("--port", type=int, default=0, help="local port, 0 for an available port")
    parser.add_argument("--expect-digest")
    parser.add_argument("--expect-environment")
    parser.add_argument("--expect-build-revision")
    parser.add_argument("--expect-ranking-mode", choices=["normal", "reverse"], default="normal")
    args = parser.parse_args()

    with forward_service(args.namespace, args.release, args.port) as base_url:
        return verify(base_url, {
            "image_digest": args.expect_digest,
            "environment": args.expect_environment,
            "build_revision": args.expect_build_revision,
            "ranking_mode": args.expect_ranking_mode,
        })


if __name__ == "__main__":
    raise SystemExit(main())
