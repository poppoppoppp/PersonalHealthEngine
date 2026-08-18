import asyncio
import base64
import hashlib
import json
import os
import random
import struct
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx


LOGIN_PREFIX = b"&&&START&&&"

CN_BASE_URL = "https://hlth.io.mi.com"

FITNESS_ENDPOINT = (
    "/app/v1/data/get_fitness_data_by_time"
)

SPORT_ENDPOINT = (
    "/app/v1/data/get_sport_records_by_time"
)

AUTH_ERROR_CODES = {
    401,
    403,
    -6,
    -10001,
}

AUTH_ERROR_MARKERS = (
    "authentication failed",
    "invalid credential",
    "invalid pass token",
    "invalid passtoken",
    "login required",
    "not logged in",
    "session expired",
    "unauthorized",
)


class XiaomiClientError(RuntimeError):
    pass


class XiaomiAuthenticationError(
    XiaomiClientError
):
    pass


class XiaomiProtocolError(
    XiaomiClientError
):
    pass


def _is_authentication_error(
    code: Any,
    message: str,
) -> bool:

    text = message.casefold()

    return (
        code in AUTH_ERROR_CODES
        or any(
            marker in text
            for marker in AUTH_ERROR_MARKERS
        )
    )


def _read_login_payload(
    text: str,
) -> dict[str, Any]:

    raw = text.encode("utf-8")

    if not raw.startswith(LOGIN_PREFIX):
        raise XiaomiAuthenticationError(
            "Unexpected Xiaomi login response."
        )

    try:
        return json.loads(
            raw[len(LOGIN_PREFIX):].decode(
                "utf-8"
            )
        )

    except Exception as exc:
        raise XiaomiAuthenticationError(
            "Invalid Xiaomi login payload."
        ) from exc


def _rc4_drop1024(
    key: bytes,
    payload: bytes,
) -> bytes:

    if not key:
        raise XiaomiProtocolError(
            "RC4 key is empty."
        )

    box = list(range(256))
    j = 0

    for i in range(256):
        j = (
            j
            + box[i]
            + key[i % len(key)]
        ) % 256

        box[i], box[j] = (
            box[j],
            box[i],
        )

    i = 0
    j = 0

    def next_byte() -> int:
        nonlocal i, j

        i = (i + 1) % 256
        j = (
            j + box[i]
        ) % 256

        box[i], box[j] = (
            box[j],
            box[i],
        )

        return box[
            (box[i] + box[j]) % 256
        ]

    # Xiaomi protocol discards the
    # first 1024 RC4 bytes.
    for _ in range(1024):
        next_byte()

    return bytes(
        value ^ next_byte()
        for value in payload
    )


def _generate_nonce() -> bytes:
    value = bytearray(
        os.urandom(8)
    )

    minute_epoch = int(
        datetime.now().timestamp()
        // 60
    )

    value.extend(
        struct.pack(
            ">I",
            minute_epoch,
        )
    )

    return bytes(value)


def _signed_nonce(
    ssecurity: bytes,
    nonce: bytes,
) -> bytes:

    return hashlib.sha256(
        ssecurity + nonce
    ).digest()


def _signature(
    method: str,
    path: str,
    values: dict[str, str],
    signed_nonce: bytes,
) -> str:

    base = (
        method
        + "&"
        + path
        + "&data="
        + values["data"]
    )

    rc4_hash = values.get(
        "rc4_hash__"
    )

    if rc4_hash:
        base += (
            "&rc4_hash__="
            + rc4_hash
        )

    base += (
        "&"
        + base64.b64encode(
            signed_nonce
        ).decode("ascii")
    )

    digest = hashlib.sha1(
        base.encode("utf-8")
    ).digest()

    return base64.b64encode(
        digest
    ).decode("ascii")


class XiaomiHealthClient:

    def __init__(
        self,
        user_id: str,
        pass_token: str,
        region: str = "cn",
        timeout_seconds: float = 20.0,
        retries: int = 3,
        max_pages: int = 200,
    ):

        self.user_id = user_id
        self.pass_token = pass_token
        self.region = region

        self.timeout_seconds = (
            timeout_seconds
        )

        self.retries = retries
        self.max_pages = max_pages

        self._client: (
            httpx.AsyncClient | None
        ) = None

        self._ssecurity = b""
        self._cookies = ""
        self._connected = False

    @property
    def connected(self) -> bool:
        return (
            self._connected
            and self._client is not None
        )

    def _base_url(self) -> str:

        if self.region in ("", "cn"):
            return CN_BASE_URL

        return (
            f"https://{self.region}"
            ".hlth.io.mi.com"
        )

    def _request_timezone(
        self,
    ) -> timezone:

        if self.region in ("", "cn"):
            return timezone(
                timedelta(hours=8)
            )

        return timezone.utc

    def _date_range_to_timestamps(
        self,
        start_date: str,
        end_date: str,
    ) -> tuple[int, int]:

        tz = self._request_timezone()

        start = datetime.fromisoformat(
            start_date
        ).replace(
            tzinfo=tz
        )

        end = datetime.fromisoformat(
            end_date + "T23:59:59"
        ).replace(
            tzinfo=tz
        )

        return (
            int(start.timestamp()),
            int(end.timestamp()),
        )

    async def connect(self) -> None:

        await self.close()

        self._client = httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
        )

        try:
            await self._login()
            self._connected = True

        except Exception:
            self._connected = False
            await self.close()
            raise

    async def close(self) -> None:

        if self._client is not None:
            await self._client.aclose()

        self._client = None
        self._connected = False

    async def _login(self) -> None:

        if self._client is None:
            raise XiaomiClientError(
                "HTTP client not initialized."
            )

        login_url = (
            "https://account.xiaomi.com/"
            "pass/serviceLogin"
            "?_json=true"
            "&sid=miothealth"
        )

        response = await self._client.get(
            login_url,
            headers={
                "Cookie": (
                    f"userId={self.user_id}; "
                    f"passToken={self.pass_token}"
                )
            },
        )

        response.raise_for_status()

        payload = _read_login_payload(
            response.text
        )

        required = (
            "passToken",
            "userId",
            "ssecurity",
            "location",
        )

        missing = [
            key
            for key in required
            if not payload.get(key)
        ]

        if missing:
            raise XiaomiAuthenticationError(
                "Xiaomi did not return a "
                "complete authenticated session."
            )

        try:
            self.pass_token = str(
                payload["passToken"]
            )

            self.user_id = str(
                payload["userId"]
            )

            self._ssecurity = (
                base64.b64decode(
                    payload["ssecurity"]
                )
            )

        except Exception as exc:
            raise XiaomiAuthenticationError(
                "Invalid Xiaomi session data."
            ) from exc

        redirect = await self._client.get(
            payload["location"]
        )

        redirect.raise_for_status()

        cookie_parts = []

        for value in (
            redirect.headers.get_list(
                "set-cookie"
            )
        ):
            cookie_parts.append(
                value.split(";", 1)[0]
            )

        if not cookie_parts:
            raise XiaomiAuthenticationError(
                "Xiaomi session cookies "
                "were not returned."
            )

        self._cookies = "; ".join(
            cookie_parts
        )

    async def _encrypted_post(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:

        if self._client is None:
            raise XiaomiClientError(
                "HTTP client not initialized."
            )

        last_error: (
            Exception | None
        ) = None

        for attempt in range(
            self.retries
        ):

            try:
                form = {
                    "data": json.dumps(
                        payload,
                        separators=(",", ":"),
                    )
                }

                nonce = _generate_nonce()

                signed = _signed_nonce(
                    self._ssecurity,
                    nonce,
                )

                form["rc4_hash__"] = (
                    _signature(
                        "POST",
                        endpoint,
                        form,
                        signed,
                    )
                )

                encrypted = {}

                for key, value in (
                    form.items()
                ):
                    encrypted[key] = (
                        base64.b64encode(
                            _rc4_drop1024(
                                signed,
                                value.encode(
                                    "utf-8"
                                ),
                            )
                        ).decode("ascii")
                    )

                encrypted[
                    "signature"
                ] = _signature(
                    "POST",
                    endpoint,
                    encrypted,
                    signed,
                )

                encrypted["_nonce"] = (
                    base64.b64encode(
                        nonce
                    ).decode("ascii")
                )

                response = (
                    await self._client.post(
                        self._base_url()
                        + endpoint,
                        headers={
                            "Cookie":
                                self._cookies,
                            "Content-Type":
                                "application/"
                                "x-www-form-urlencoded",
                        },
                        content=urlencode(
                            encrypted
                        ),
                    )
                )

                response.raise_for_status()

                try:
                    encrypted_body = (
                        base64.b64decode(
                            response.text
                        )
                    )

                    plaintext = (
                        _rc4_drop1024(
                            signed,
                            encrypted_body,
                        )
                    )

                    body = json.loads(
                        plaintext.decode(
                            "utf-8"
                        )
                    )

                except Exception as exc:
                    raise XiaomiProtocolError(
                        "Unable to decode Xiaomi "
                        "API response."
                    ) from exc

                code = body.get("code")

                if code != 0:
                    message = str(
                        body.get(
                            "message",
                            "Unknown Xiaomi API "
                            "error.",
                        )
                    )

                    if (
                        _is_authentication_error(
                            code,
                            message,
                        )
                    ):
                        raise (
                            XiaomiAuthenticationError(
                                message
                            )
                        )

                    raise XiaomiClientError(
                        message
                    )

                result = body.get(
                    "result",
                    {},
                )

                if not isinstance(
                    result,
                    dict,
                ):
                    raise XiaomiProtocolError(
                        "Unexpected Xiaomi "
                        "result structure."
                    )

                return result

            except Exception as exc:
                last_error = exc

                auth_error = isinstance(
                    exc,
                    XiaomiAuthenticationError,
                )

                retryable = isinstance(
                    exc,
                    (
                        httpx.TimeoutException,
                        httpx.NetworkError,
                    ),
                )

                if isinstance(
                    exc,
                    httpx.HTTPStatusError,
                ):
                    status = (
                        exc.response.status_code
                    )

                    auth_error = (
                        status in (401, 403)
                    )

                    retryable = (
                        status == 429
                        or status >= 500
                    )

                if auth_error:
                    self._connected = False

                    if (
                        attempt
                        < self.retries - 1
                    ):
                        try:
                            await self._login()
                            self._connected = True
                            retryable = True

                        except Exception as login_exc:
                            last_error = (
                                login_exc
                            )
                            retryable = False

                if (
                    attempt
                    >= self.retries - 1
                    or not retryable
                ):
                    break

                delay = min(
                    4.0,
                    0.5 * (2 ** attempt),
                )

                delay += (
                    random.random()
                    * 0.1
                )

                await asyncio.sleep(
                    delay
                )

        if isinstance(
            last_error,
            XiaomiAuthenticationError,
        ):
            raise last_error

        raise XiaomiClientError(
            "Xiaomi request failed."
        ) from last_error

    async def fetch_fitness_key(
        self,
        key: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:

        start_time, end_time = (
            self._date_range_to_timestamps(
                start_date,
                end_date,
            )
        )

        records = []
        next_key = None
        seen_cursors = set()

        for page in range(
            1,
            self.max_pages + 1,
        ):

            payload = {
                "start_time": start_time,
                "end_time": end_time,
                "key": key,
            }

            if next_key is not None:
                payload[
                    "next_key"
                ] = next_key

            result = (
                await self._encrypted_post(
                    FITNESS_ENDPOINT,
                    payload,
                )
            )

            page_records = result.get(
                "data_list",
                [],
            )

            if not isinstance(
                page_records,
                list,
            ):
                raise XiaomiProtocolError(
                    "fitness data_list is "
                    "not a list."
                )

            records.extend(
                page_records
            )

            has_more = bool(
                result.get("has_more")
            )

            cursor = result.get(
                "next_key"
            )

            if not has_more or not cursor:
                return records

            cursor = str(cursor)

            if cursor in seen_cursors:
                raise XiaomiProtocolError(
                    "Fitness pagination "
                    "cursor loop detected."
                )

            seen_cursors.add(
                cursor
            )

            next_key = cursor

        raise XiaomiProtocolError(
            "Fitness pagination exceeded "
            "safety limit."
        )

    async def fetch_sport_records(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:

        start_time, end_time = (
            self._date_range_to_timestamps(
                start_date,
                end_date,
            )
        )

        records = []
        next_key = None
        seen_cursors = set()

        for page in range(
            1,
            self.max_pages + 1,
        ):

            payload = {
                "start_time": start_time,
                "end_time": end_time,
                "limit": 50,
            }

            if next_key is not None:
                payload[
                    "next_key"
                ] = next_key

            result = (
                await self._encrypted_post(
                    SPORT_ENDPOINT,
                    payload,
                )
            )

            page_records = result.get(
                "sport_records",
                [],
            )

            if not isinstance(
                page_records,
                list,
            ):
                raise XiaomiProtocolError(
                    "sport_records is "
                    "not a list."
                )

            records.extend(
                page_records
            )

            has_more = bool(
                result.get("has_more")
            )

            cursor = result.get(
                "next_key"
            )

            if not has_more or not cursor:
                return records

            cursor = str(cursor)

            if cursor in seen_cursors:
                raise XiaomiProtocolError(
                    "Sport pagination cursor "
                    "loop detected."
                )

            seen_cursors.add(
                cursor
            )

            next_key = cursor

        raise XiaomiProtocolError(
            "Sport pagination exceeded "
            "safety limit."
        )

