from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import httpx

from .config import BridgeConfig


@dataclass(slots=True)
class ShoreRequestError(Exception):
    message: str
    request_id: str
    status_code: int | None = None

    def __str__(self) -> str:
        if self.status_code is None:
            return f"{self.message} (request_id={self.request_id})"
        return f"{self.message} (status={self.status_code}, request_id={self.request_id})"


class ShoreClient:
    def __init__(self, settings: BridgeConfig, *, version: str) -> None:
        self.settings = settings
        self.version = version
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._user_agent = f"astrbot-plugin-shore-bridge/{version}"

    async def open(self) -> None:
        if self._client is not None:
            return
        async with self._lock:
            if self._client is not None:
                return
            self._client = httpx.AsyncClient(
                base_url=self.settings.service_base_url,
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=50,
                ),
                follow_redirects=True,
            )

    async def close(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            client = self._client
            self._client = None
            await client.aclose()

    async def health(self, *, request_id: str) -> dict[str, Any]:
        data = await self._request_json(
            "GET",
            "/health",
            request_id=request_id,
            read_timeout=self.settings.command_read_timeout_seconds,
        )
        return data if isinstance(data, dict) else {}

    async def recall(self, payload: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        data = await self._request_json(
            "POST",
            "/v1/context/recall",
            request_id=request_id,
            json_body=payload,
            read_timeout=self.settings.recall_read_timeout_seconds,
        )
        return data if isinstance(data, dict) else {}

    async def write_turn(self, payload: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        data = await self._request_json(
            "POST",
            "/v1/events/turn",
            request_id=request_id,
            json_body=payload,
            read_timeout=self.settings.writeback_read_timeout_seconds,
        )
        return data if isinstance(data, dict) else {}

    async def create_memory(self, payload: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        data = await self._request_json(
            "POST",
            "/v1/memories",
            request_id=request_id,
            json_body=payload,
            read_timeout=self.settings.command_read_timeout_seconds,
        )
        return data if isinstance(data, dict) else {}

    async def update_memory(
        self,
        memory_id: int,
        payload: dict[str, Any],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        data = await self._request_json(
            "PATCH",
            f"/v1/memories/{memory_id}",
            request_id=request_id,
            json_body=payload,
            read_timeout=self.settings.command_read_timeout_seconds,
        )
        return data if isinstance(data, dict) else {}

    async def list_memories(
        self,
        params: dict[str, Any],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        data = await self._request_json(
            "GET",
            "/v1/memories",
            request_id=request_id,
            params=params,
            read_timeout=self.settings.command_read_timeout_seconds,
        )
        return data if isinstance(data, dict) else {}

    async def get_agent_state(self, agent_id: str, *, request_id: str) -> dict[str, Any]:
        data = await self._request_json(
            "GET",
            f"/v1/agents/{agent_id}/state",
            request_id=request_id,
            read_timeout=self.settings.command_read_timeout_seconds,
        )
        return data if isinstance(data, dict) else {}

    def websocket_url(self) -> str:
        parsed = urlparse(self.settings.service_base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = urlencode({"api_key": self.settings.api_key}) if self.settings.api_key else ""
        return urlunparse((scheme, parsed.netloc, "/v1/events", "", query, ""))

    def websocket_headers(self) -> dict[str, str]:
        return self._build_headers(request_id="shore-events", include_json=False)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        request_id: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        read_timeout: float,
    ) -> Any:
        client = await self._get_client()
        headers = self._build_headers(request_id=request_id, include_json=json_body is not None)
        timeout = httpx.Timeout(
            connect=self.settings.connect_timeout_seconds,
            read=read_timeout,
            write=read_timeout,
            pool=self.settings.connect_timeout_seconds,
        )
        try:
            response = await client.request(
                method,
                path,
                json=json_body,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:400]
            raise ShoreRequestError(
                message=f"shore request failed: {body or exc.response.reason_phrase}",
                request_id=request_id,
                status_code=exc.response.status_code,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ShoreRequestError(
                message="shore request timed out",
                request_id=request_id,
            ) from exc
        except httpx.RequestError as exc:
            raise ShoreRequestError(
                message=f"shore request error: {exc}",
                request_id=request_id,
            ) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise ShoreRequestError(
                message="shore returned invalid json",
                request_id=request_id,
                status_code=response.status_code,
            ) from exc

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            await self.open()
        assert self._client is not None
        return self._client

    def _build_headers(self, *, request_id: str, include_json: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
            "x-request-id": request_id,
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        api_key = self.settings.api_key.strip()
        if not api_key:
            return headers
        if self.settings.api_key_mode in {"both", "bearer"}:
            headers["Authorization"] = f"Bearer {api_key}"
        if self.settings.api_key_mode in {"both", "x-api-key"}:
            headers["x-api-key"] = api_key
        return headers
