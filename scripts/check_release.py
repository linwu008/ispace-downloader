"""Read-only deployed website checks. No accounts, device tasks, or cloud writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ["app.js", "experience.js", "v07.js", "style.css", "workspace.html", "en.json"]


def digest(value):
    return hashlib.sha256(value.replace(b"\r\n", b"\n")).hexdigest()


def verify(client, origin, production):
    expected = json.loads((ROOT / "cloud/package.json").read_text(encoding="utf-8"))["version"]
    health = client.get(origin + "/api/health")
    health.raise_for_status()
    assert health.json()["version"] == expected, "deployed website version differs"
    if production:
        assert health.json()["local"] is False, "production reports local mode"
    features = client.get(origin + "/api/features")
    features.raise_for_status()
    assert all(features.json()[k] is False for k in ["archive_enabled", "mail_enabled", "qa_enabled"]), "disabled service unexpectedly enabled"
    for path in ["/api/me", "/api/v07/activity", "/api/v07/archives"]:
        assert client.get(origin + path).status_code == 401, f"anonymous access not rejected: {path}"
    for asset in ASSETS:
        response = client.get(origin + "/" + asset)
        response.raise_for_status()
        assert digest(response.content) == digest((ROOT / "cloud/public" / asset).read_bytes()), f"asset differs: {asset}"
    for path, marker in [("/", "CourseNest"), ("/login.html", "public-site.js"), ("/help.html", "CourseNest")]:
        response = client.get(origin + path)
        response.raise_for_status()
        assert marker in response.text, f"page missing: {path}"
    assert "frame-ancestors 'none'" in health.headers.get("content-security-policy", ""), "security header missing"
    return expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:18810")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--wait", type=int, default=0, help="seconds to wait for deployment propagation")
    args = parser.parse_args()
    deadline = time.monotonic() + args.wait
    # Respect an explicitly configured proxy; local verification never uses it.
    with httpx.Client(timeout=20, follow_redirects=True, trust_env=args.production) as client:
        while True:
            try:
                version = verify(client, args.origin.rstrip("/"), args.production)
                print(f"PASS release {version}: matching assets, private API isolation, pages, disabled services.", flush=True)
                break
            except (httpx.HTTPError, AssertionError, ValueError, KeyError) as exc:
                if time.monotonic() >= deadline:
                    raise SystemExit(f"Release verification FAILED: {type(exc).__name__}: {exc}")
                print(f"Waiting for deployment: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(15)
