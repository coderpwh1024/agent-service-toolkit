"""Send validated mail through the current user's authenticated service endpoint."""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    base = os.environ.get("AGENT_SERVICE_URL", "").rstrip("/")
    token = os.environ.get("AGENT_USER_ACCESS_TOKEN", "")
    parsed = urlsplit(base)
    secure = parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    )
    if (
        not secure
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not token
    ):
        print("Configure a trusted HTTPS service URL and current-user token.", file=sys.stderr)
        return 2
    try:
        payload = json.loads(sys.stdin.read(100000))
        if not isinstance(payload, dict) or set(payload) != {"request_id", "subject", "body"}:
            raise ValueError("Unexpected input fields")
        UUID(payload["request_id"])
        if not isinstance(payload["subject"], str) or not isinstance(payload["body"], str):
            raise ValueError("Subject and body must be text")
    except (ValueError, TypeError, AttributeError):
        print("Provide JSON with request_id (UUID), subject and body.", file=sys.stderr)
        return 2
    request = Request(
        base + "/skills/email/send",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            result = json.load(response)
        status = result["data"]["status"]
        if status not in {"sent", "duplicate", "unknown"}:
            raise ValueError("Invalid result")
    except HTTPError as exc:
        print(
            f"Email service rejected request (HTTP {exc.code}); do not retry automatically.",
            file=sys.stderr,
        )
        return 1
    except (URLError, OSError, ValueError, KeyError, TypeError):
        print("Delivery is unconfirmed; check your inbox before retrying.", file=sys.stderr)
        return 1
    print(json.dumps({"status": status}))
    return 0 if status in {"sent", "duplicate"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
