"""Read-only checks for corrupt HTTP content encoding on SPA routes and assets."""

from __future__ import annotations

import argparse
import gzip
import re
import subprocess
import tempfile
import zlib
from pathlib import Path
from urllib.parse import urlsplit

MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 27_0_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "CriOS/154.0.8037.55 Mobile/15E148 Safari/604.1"
)


def fetch(base_url: str, path: str, encoding: str, edge_ip: str | None) -> tuple[dict[str, str], bytes]:
    with tempfile.TemporaryDirectory(prefix="mmcat-http-check-") as directory:
        header_file, body_file = Path(directory) / "headers", Path(directory) / "body"
        command = [
            "curl", "--silent", "--show-error", "--fail", "--max-time", "20",
            "--user-agent", MOBILE_USER_AGENT,
            "--header", f"Accept-Encoding: {encoding}",
            "--dump-header", str(header_file), "--output", str(body_file),
        ]
        if edge_ip:
            url = urlsplit(base_url)
            port = url.port or (443 if url.scheme == "https" else 80)
            command += ["--resolve", f"{url.hostname}:{port}:{edge_ip}"]
        subprocess.run([*command, base_url.rstrip("/") + path], check=True, capture_output=True)
        # Ignore interim response headers (e.g. CONNECT or 103 Early Hints).
        block = header_file.read_text().strip().split("\n\n")[-1]
        headers = {
            name.lower(): value.strip()
            for line in block.splitlines()[1:]
            if ":" in line
            for name, value in [line.split(":", 1)]
        }
        body = body_file.read_bytes()
        if "content-length" in headers and int(headers["content-length"]) != len(body):
            raise ValueError("Content-Length differs from the actual response length")
        actual_encoding = headers.get("content-encoding", "identity").lower()
        if actual_encoding == "gzip":
            body = gzip.decompress(body)
        elif actual_encoding not in {"", "identity"}:
            raise ValueError(f"Unexpected Content-Encoding: {actual_encoding}")
        return headers, body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--edge-ip")
    args = parser.parse_args()
    if urlsplit(args.base_url).scheme not in {"http", "https"}:
        parser.error("base_url must use http or https")
    failures = 0
    assets: set[str] = set()
    for path in ("/", "/feed", "/posts", "/index.html"):
        for encoding in ("identity", "gzip"):
            try:
                headers, body = fetch(args.base_url, path, encoding, args.edge_ip)
                if "text/html" not in headers.get("content-type", "") or b'id="root"' not in body:
                    raise ValueError("Expected the SPA HTML entry document")
                assets.update(re.findall(r'(?:src|href)="(/assets/[^"\s]+\.(?:js|css))"', body.decode("utf-8")))
                print(f"PASS {path} accept={encoding} response={headers.get('content-encoding', 'identity')}")
            except (OSError, ValueError, EOFError, zlib.error, subprocess.SubprocessError) as exc:
                failures += 1
                print(f"FAIL {path} accept={encoding}: {exc}")
    if not assets:
        print("FAIL no JS/CSS assets found in the HTML")
        failures += 1
    for path in sorted(assets):
        try:
            headers, body = fetch(args.base_url, path, "gzip", args.edge_ip)
            if not body or "text/html" in headers.get("content-type", ""):
                raise ValueError("Expected a nonempty JS/CSS asset")
            print(f"PASS {path} response={headers.get('content-encoding', 'identity')}")
        except (OSError, ValueError, EOFError, zlib.error, subprocess.SubprocessError) as exc:
            failures += 1
            print(f"FAIL {path}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
