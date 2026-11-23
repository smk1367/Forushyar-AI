"""Check the configured UCM API once without starting background workers."""

import asyncio

import httpx
from pydantic import ValidationError


async def check(runtime) -> int:
    if not runtime.api_configured:
        print("SKIP: UCM integration is disabled or API settings are incomplete.")
        return 2

    for action in ("listUnBridgedChannels", "listBridgedChannels"):
        try:
            result = await runtime.api_action(action)
        except httpx.HTTPStatusError as error:
            print(f"FAIL: {action}: HTTP {error.response.status_code}")
            return 1
        except (httpx.HTTPError, RuntimeError, ValueError, TypeError) as error:
            print(f"FAIL: {action}: {type(error).__name__}")
            return 1

        payload = result.get("response")
        channels = payload.get("channel") if isinstance(payload, dict) else None
        if isinstance(channels, dict):
            channels = [channels]
        if not isinstance(channels, list) or not all(isinstance(c, dict) for c in channels):
            print(f"FAIL: {action}: unexpected channel response")
            return 1
        print(f"OK: {action}: {len(channels)} channels")
    return 0


async def main() -> int:
    try:
        from app.core.config import settings
    except ValidationError:
        print("FAIL: invalid application settings")
        return 2

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services.telephony.grandstream import GrandstreamTelephonyRuntime

    runtime = GrandstreamTelephonyRuntime(settings, async_sessionmaker())
    if not runtime.api_configured:
        return await check(runtime)
    try:
        async with httpx.AsyncClient(
            verify=settings.ucm_verify_tls, timeout=httpx.Timeout(20.0, connect=5.0)
        ) as runtime.http:
            return await check(runtime)
    except (ImportError, OSError, ValueError):
        print("FAIL: invalid HTTP client configuration; check TLS and proxy settings")
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
