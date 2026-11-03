"""Pairs the deck with an IKEA DIRIGERA hub and saves the access token (never printed).

Run on the Pi: python3 ~/pi-deck/scripts/dirigera_pair.py <hub-ip>
Then press the action button on the bottom of the hub within 60 s.

Writes ~/.config/pi-deck/dirigera.json (mode 600): host, token, and the hub certificate's SHA-256, which the
deck pins (the hub's certificate is self-signed). Stdlib only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PORT = 8443
CONFIG = Path.home() / ".config" / "pi-deck" / "dirigera.json"
CLIENT_NAME = "pi-deck"
WAIT_S = 90


def insecure_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def certificate_sha256(host: str) -> str:
    with socket.create_connection((host, PORT), timeout=5) as raw:
        with insecure_context().wrap_socket(raw, server_hostname=host) as tls:
            return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()


def call(url: str, data: dict | None = None) -> dict:
    """GET without data, form-encoded POST with it."""
    if data is None:
        request = urllib.request.Request(url)
    else:
        request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, context=insecure_context(), timeout=10) as response:
        return json.load(response)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: dirigera_pair.py <hub-ip>   (the hub's address, e.g. from your router or the IKEA Home app)")
    host = sys.argv[1]
    base = f"https://{host}:{PORT}/v1"
    fingerprint = certificate_sha256(host)

    verifier = "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~") for _ in range(128))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urllib.parse.urlencode({"audience": "homesmart.local", "response_type": "code",
                                    "code_challenge": challenge, "code_challenge_method": "S256"})
    code = call(f"{base}/oauth/authorize?{query}")["code"]
    print(f"Press the action button on the hub now (waiting up to {WAIT_S} s)...", flush=True)

    deadline = time.monotonic() + WAIT_S
    while True:
        try:
            token = call(f"{base}/oauth/token", {"code": code, "name": CLIENT_NAME,
                                                 "grant_type": "authorization_code", "code_verifier": verifier})["access_token"]
            break
        except urllib.error.HTTPError:
            if time.monotonic() > deadline:
                sys.exit("No button press seen; run the script again.")
            time.sleep(1)

    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"host": host, "token": token, "cert_sha256": fingerprint}, indent=2))
    os.chmod(CONFIG, 0o600)
    print(f"Paired. Saved to {CONFIG} (token not shown). Hub certificate SHA-256: {fingerprint[:16]}…")


if __name__ == "__main__":
    main()
