from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Annotation


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class HypothesisClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, query: dict[str, str | int] | None = None,
                 body: dict | None = None) -> dict:
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        payload = json.dumps(body).encode() if body is not None else None
        request = Request(url, data=payload, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.hypothesis.v1+json")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("reason")
            except Exception:
                detail = None
            raise ApiError(detail or f"Hypothesis API returned HTTP {exc.code}", exc.code) from exc
        except (URLError, TimeoutError) as exc:
            raise ApiError(f"Unable to reach Hypothesis API: {exc.reason if isinstance(exc, URLError) else exc}") from exc

    def profile_userid(self) -> str:
        userid = self._request("GET", "/profile").get("userid")
        if not userid:
            raise ApiError("APP_TOKEN did not authenticate a Hypothesis user")
        return userid

    def search_created(self, userid: str, start_iso: str, end_iso: str) -> list[Annotation]:
        rows: list[Annotation] = []
        start_time = datetime.fromisoformat(start_iso)
        end_time = datetime.fromisoformat(end_iso)
        cursor = (datetime.fromisoformat(start_iso) - timedelta(microseconds=1)).isoformat()
        while True:
            page = self._request("GET", "/search", {
                "user": userid,
                "sort": "created",
                "order": "asc",
                "limit": 200,
                "search_after": cursor,
            }).get("rows", [])
            if not page:
                break
            reached_end = False
            for raw in page:
                created = raw.get("created", "")
                try:
                    created_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except (TypeError, ValueError) as exc:
                    raise ApiError(f"annotation {raw.get('id', '<unknown>')} has an invalid created timestamp") from exc
                if created_time >= end_time:
                    reached_end = True
                    break
                if created_time >= start_time:
                    rows.append(Annotation.from_api(raw))
            if reached_end or len(page) < 200:
                break
            next_cursor = page[-1].get("created")
            if not next_cursor or next_cursor == cursor:
                raise ApiError("Hypothesis pagination cursor did not advance")
            cursor = next_cursor
        return rows

    def get_annotation(self, annotation_id: str) -> Annotation | None:
        try:
            return Annotation.from_api(self._request("GET", f"/annotations/{annotation_id}"))
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def patch_text(self, annotation_id: str, text: str) -> Annotation:
        return Annotation.from_api(self._request("PATCH", f"/annotations/{annotation_id}", body={"text": text}))
