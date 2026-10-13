"""REST error envelope + whether the Python SDK keeps the request id. Creates nothing.

The token is read from the environment and only ever sent in the Authorization header.
Run: .venv/bin/python scripts/probe_rest_errors.py
"""

import asyncio
import json
import os

import httpx
from runta import ApiError, AsyncRunta

ZERO = "00000000-0000-0000-0000-000000000000"


def show(label: str, r: httpx.Response) -> None:
    try:
        body = r.json()
    except ValueError:
        body = {"_raw": r.text[:200]}
    rid = body.get("request_id") if isinstance(body, dict) else None
    print(json.dumps({"check": label, "status": r.status_code, "body": body, "header_x_request_id": r.headers.get("x-request-id"),
                      "envelope_request_id_matches_header": rid is not None and rid == r.headers.get("x-request-id")}))


async def main() -> None:
    async with AsyncRunta() as c:
        base = c._http.endpoint
    auth = {"Authorization": f"Bearer {os.environ['RUNTA_TOKEN']}"}
    with httpx.Client(base_url=base, timeout=30) as h:
        show("GET unknown runtime (expect 404)", h.get(f"/v2/runtimes/{ZERO}", headers=auth))
        show("GET with an invalid token (expect 401)", h.get("/v2/runtimes", headers={"Authorization": "Bearer invalid"}))
        show("POST malformed JSON (expect 400)", h.post("/v2/runtimes", headers={**auth, "Content-Type": "application/json"}, content=b"{"))
        show("GET malformed runtime id", h.get("/v2/runtimes/not-a-uuid", headers=auth))
    async with AsyncRunta() as c:
        try:
            await c.runtimes.get(ZERO)
        except ApiError as e:
            attrs = {k: v for k, v in vars(e).items()}
            print(json.dumps({"check": "Python SDK ApiError for the 404", "attributes": attrs, "str": str(e),
                              "mentions_request_id": "request" in (str(e) + json.dumps(attrs, default=str)).lower()}))


asyncio.run(main())
