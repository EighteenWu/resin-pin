from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ResinError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"resin api {status}: {message}")
        self.status = status
        self.message = message


class ResinClient:
    def __init__(self, base_url: str, admin_token: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.timeout = timeout

    def _request(self, method: str, path: str, query: dict[str, Any] | None = None, body: Any = None) -> Any:
        url = self.base_url + path
        if query:
            filtered = {k: v for k, v in query.items() if v is not None}
            if filtered:
                url += "?" + urllib.parse.urlencode(filtered, doseq=True)
        data = None
        headers = {"Accept": "application/json"}
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            message = payload
            try:
                parsed = json.loads(payload)
                message = parsed.get("error", {}).get("message") or payload
            except json.JSONDecodeError:
                pass
            raise ResinError(exc.code, message) from exc

    def list_all(self, path: str, query: dict[str, Any] | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        params = dict(query or {})
        while True:
            params["limit"] = limit
            params["offset"] = offset
            data = self._request("GET", path, params) or {}
            batch = data.get("items") or []
            items.extend(batch)
            total = int(data.get("total") or len(items))
            if not batch or offset + len(batch) >= total:
                break
            offset += len(batch)
        return items

    def list_nodes(self, region: str | None = None, enabled: bool | None = True) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if region:
            query["region"] = region
        if enabled is not None:
            query["enabled"] = str(enabled).lower()
        return self.list_all("/api/v1/nodes", query)

    def list_platforms(self) -> list[dict[str, Any]]:
        return self.list_all("/api/v1/platforms")

    def create_platform(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/platforms", body=body)

    def update_platform(self, platform_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/api/v1/platforms/{platform_id}", body=body)

    def delete_platform(self, platform_id: str) -> None:
        self._request("DELETE", f"/api/v1/platforms/{platform_id}")
