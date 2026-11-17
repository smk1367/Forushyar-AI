from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.models.customer import Customer
from app.models.telephony_call import TelephonyCall

log = logging.getLogger("forushyar.telephony")

CDR_FIELDS = [
    "AcctId", "accountcode", "src", "dst", "dcontext", "clid", "channel",
    "dstchannel", "lastapp", "lastdata", "start", "answer", "end", "duration",
    "billsec", "disposition", "amaflags", "uniqueid", "userfield", "channel_ext",
    "dstchannel_ext", "service", "caller_name", "recordfiles", "dstanswer",
    "chanext", "dstchanext", "session", "action_owner", "action_type",
    "src_trunk_name", "dst_trunk_name",
]
MISSED_DISPOSITIONS = {
    "NO ANSWER", "NOANSWER", "BUSY", "FAILED", "CANCELLED", "CHANUNAVAIL",
    "CONGESTION", "UNANSWERED", "REJECTED",
}


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _digits(value: Any) -> str:
    return "".join(character for character in _text(value) if character.isdigit())


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _segments(item: dict[str, Any]) -> list[dict[str, Any]]:
    main = item.get("main_cdr")
    if not isinstance(main, dict):
        return [item]
    segments = [main]
    keys = sorted(
        (key for key, value in item.items() if key.startswith("sub_cdr_") and isinstance(value, dict)),
        key=lambda key: int(key.rsplit("_", 1)[-1]) if key.rsplit("_", 1)[-1].isdigit() else 999999,
    )
    segments.extend(item[key] for key in keys)
    return segments


def _first_value(items: list[dict[str, Any]], *keys: str) -> str:
    for item in items:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return _text(value)
    return ""


def _xml_value(node: ET.Element) -> Any:
    children = list(node)
    if not children:
        return (node.text or "").strip()
    result: dict[str, Any] = {}
    for child in children:
        value = _xml_value(child)
        if child.tag in result:
            previous = result[child.tag]
            result[child.tag] = previous + [value] if isinstance(previous, list) else [previous, value]
        else:
            result[child.tag] = value
    return result


def _extract_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, dict):
            return
        keys = {str(key).lower() for key in node}
        if "main_cdr" in keys or ({"src", "dst"} <= keys and ({"uniqueid", "acctid", "cdr"} & keys)):
            records.append(node)
            return
        for child in node.values():
            visit(child)

    visit(value)
    return records


def parse_cdr_csv(raw: bytes | str) -> list[dict[str, Any]]:
    """Parse Grandstream UCM Real-time Output CSV rows."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = text.replace("\x00", "").strip()
    if not text:
        return []
    try:
        reader = csv.reader(io.StringIO(text), delimiter=",", quotechar="'", skipinitialspace=False)
        records = []
        for row in reader:
            if not row:
                continue
            values = [str(value).strip() for value in row]
            record = {
                field: values[index] if index < len(values) else ""
                for index, field in enumerate(CDR_FIELDS)
            }
            if len(values) > len(CDR_FIELDS):
                record["_extra"] = values[len(CDR_FIELDS):]
            if any(record.get(key) for key in ("src", "dst", "uniqueid", "AcctId")):
                records.append(record)
        return records
    except csv.Error:
        log.warning("Unable to parse UCM real-time CDR CSV payload")
        return []


def parse_cdr_payload(raw: bytes, content_type: str = "") -> list[dict[str, Any]]:
    """Decode JSON, XML or the CSV format used by UCM TCP real-time output."""
    text = raw.decode("utf-8", errors="replace").replace("\x00", "").strip()
    if not text:
        return []

    try:
        value = json.loads(text)
        records = _extract_records(value)
        if records:
            return records
        if isinstance(value, dict):
            cdr_root = value.get("cdr_root")
            if isinstance(cdr_root, dict):
                return [cdr_root]
            if isinstance(cdr_root, list):
                return [item for item in cdr_root if isinstance(item, dict)]
    except (json.JSONDecodeError, TypeError):
        pass

    if "xml" in (content_type or "").lower() or text.startswith("<"):
        try:
            value = _xml_value(ET.fromstring(text))
            records = _extract_records(value)
            if records:
                return records
            if isinstance(value, dict):
                return [value]
        except ET.ParseError:
            pass

    return parse_cdr_csv(text)


def normalize_recording_references(value: Any) -> list[dict[str, str]]:
    """Extract file references while rejecting traversal and path separators."""
    candidates: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, list):
            for nested in item:
                collect(nested)
        elif isinstance(item, dict):
            if item.get("filedir") and item.get("filename"):
                candidates.append(_text(item["filedir"]) + "/" + _text(item["filename"]))
            else:
                for key in ("filename", "recordfile", "recordfiles", "file", "files"):
                    if key in item:
                        collect(item[key])
        elif item not in (None, ""):
            raw_item = _text(item)
            try:
                parsed_item = json.loads(raw_item)
            except (json.JSONDecodeError, TypeError):
                candidates.extend(line.strip() for line in raw_item.splitlines() if line.strip())
            else:
                if isinstance(parsed_item, (list, dict)):
                    collect(parsed_item)
                else:
                    candidates.append(raw_item)

    collect(value)

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in candidates:
        directory = "monitor"
        filename = raw
        if "filedir=" in raw and "filename=" in raw:
            query = parse_qs(urlparse(raw).query)
            directory = query.get("filedir", ["monitor"])[0]
            filename = query.get("filename", [""])[0]
        elif "@" in raw:
            parts = [part.strip(" /") for part in raw.split("@") if part.strip(" /")]
            if len(parts) >= 2:
                directory, filename = parts[0], parts[-1]
        elif "/" in raw or "\\" in raw:
            parts = re.split(r"[/\\]+", raw.strip("/\\"))
            if len(parts) >= 2:
                directory, filename = parts[-2], parts[-1]

        directory, filename = _text(directory), _text(filename)
        invalid = (
            not directory or not filename
            or directory in {".", ".."} or filename in {".", ".."}
            or ".." in directory or ".." in filename
            or any(character in directory + filename for character in ("/", "\\", "\x00", "\r", "\n"))
        )
        if invalid:
            continue
        key = (directory, filename)
        if key not in seen:
            seen.add(key)
            result.append({"filedir": directory, "filename": filename})
    return result


def normalize_cdr(item: dict[str, Any]) -> dict[str, Any]:
    segments = _segments(item)
    userfields = {_text(segment.get("userfield")).lower() for segment in segments}
    source_trunk = _first_value(segments, "src_trunk_name")
    destination_trunk = _first_value(segments, "dst_trunk_name")
    context = _first_value(segments, "dcontext").lower()

    if "inbound" in userfields or (source_trunk and not destination_trunk) or "ext-did" in context:
        direction = "inbound"
    elif "external" in userfields or (destination_trunk and not source_trunk) or "outbound-route" in context:
        direction = "outbound"
    else:
        direction = "internal"

    main = _as_dict(item.get("main_cdr")) or item
    source = _text(main.get("src"))
    destination = _text(main.get("dst"))
    customer_number = source if direction == "inbound" else destination if direction == "outbound" else ""

    extension = ""
    if direction == "inbound":
        extension = _first_value(segments, "dstchannel_ext", "dstchanext")
        if not extension and destination.isdigit() and len(destination) <= 8:
            extension = destination
    elif direction == "outbound":
        extension = _first_value(segments, "channel_ext", "chanext")
        if not extension and source.isdigit() and len(source) <= 8:
            extension = source

    dispositions = [_text(segment.get("disposition")) for segment in segments if _text(segment.get("disposition"))]
    disposition = "ANSWERED" if any(value.upper() in {"ANSWERED", "ANSWER"} for value in dispositions) else (
        dispositions[-1].upper() if dispositions else "UNKNOWN"
    )
    if disposition in {"ANSWERED", "ANSWER"}:
        call_status = "answered"
    elif direction == "inbound" and disposition in MISSED_DISPOSITIONS:
        call_status = "missed"
    elif disposition in MISSED_DISPOSITIONS:
        call_status = "no_answer"
    else:
        call_status = disposition.lower().replace(" ", "_")

    unique_id = (
        _text(item.get("cdr"))
        or _text(main.get("session"))
        or _text(main.get("uniqueid"))
        or _first_value(segments, "uniqueid", "AcctId")
    )
    if not unique_id:
        raw = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        unique_id = "sha256:" + hashlib.sha256(raw).hexdigest()

    def integer(value: Any) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError, OverflowError):
            return 0

    recording_values = [segment["recordfiles"] for segment in segments if segment.get("recordfiles") not in (None, "")]
    return {
        "ucm_unique_id": unique_id[:255],
        "source_number": source[:80],
        "destination_number": destination[:80],
        "customer_number": customer_number[:80],
        "caller_name": _first_value(segments, "caller_name", "callername", "clid", "cid_name")[:200],
        "direction": direction,
        "extension": extension[:24],
        "status": call_status[:24],
        "disposition": disposition[:80],
        "start_time": _text(main.get("start") or main.get("start_time"))[:80],
        "answer_time": _text(main.get("answer") or main.get("answer_time"))[:80],
        "end_time": _text(main.get("end") or main.get("end_time"))[:80],
        "duration_seconds": integer(main.get("duration")),
        "billsec_seconds": integer(main.get("billsec")),
        "src_trunk": _first_value(segments, "src_trunk_name")[:120],
        "dst_trunk": _first_value(list(reversed(segments)), "dst_trunk_name")[:120],
        "channel": _first_value(segments, "channel")[:240],
        "dst_channel": _first_value(list(reversed(segments)), "dstchannel", "dst_channel")[:240],
        "recordings": normalize_recording_references(recording_values),
        "raw_cdr": item,
    }


class GrandstreamTelephonyRuntime:
    """Async Grandstream UCM client, CDR sync and optional TCP receiver."""

    def __init__(self, settings: Settings, session_factory: async_sessionmaker):
        self.settings = settings
        self.session_factory = session_factory
        self.http: httpx.AsyncClient | None = None
        self._api_cookie = ""
        self._api_cookie_expires = 0.0
        self._api_lock = asyncio.Lock()
        self._live_calls: list[dict[str, Any]] = []
        self._live_updated_at: str | None = None
        self._last_sync_at: str | None = None
        self._last_sync_error: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._tcp_server: asyncio.AbstractServer | None = None

    @property
    def api_configured(self) -> bool:
        return bool(self.settings.ucm_enabled and self.settings.ucm_api_url
                    and self.settings.ucm_api_username and self.settings.ucm_api_password)

    @property
    def cdr_configured(self) -> bool:
        return bool(self.settings.ucm_enabled and self.settings.ucm_cdr_url
                    and self.settings.ucm_cdr_username and self.settings.ucm_cdr_password)

    @property
    def recording_configured(self) -> bool:
        return bool(self.settings.ucm_enabled and self.settings.ucm_recording_url
                    and self.settings.ucm_cdr_username and self.settings.ucm_cdr_password)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.settings.ucm_enabled),
            "api_configured": self.api_configured,
            "cdr_configured": self.cdr_configured,
            "recording_configured": self.recording_configured,
            "tcp_listener_enabled": bool(self.settings.ucm_tcp_listener_enabled),
            "live_calls": len(self._live_calls),
            "live_updated_at": self._live_updated_at,
            "last_sync_at": self._last_sync_at,
            "last_sync_error": self._last_sync_error,
        }

    def live_calls(self) -> list[dict[str, Any]]:
        return [dict(call) for call in self._live_calls]

    async def start(self) -> None:
        if not self.settings.ucm_enabled:
            log.info("Grandstream UCM integration is disabled")
            return
        self.http = httpx.AsyncClient(
            verify=self.settings.ucm_verify_tls,
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        if self.cdr_configured:
            self._tasks.append(asyncio.create_task(self._sync_loop(), name="ucm-cdr-sync"))
        if self.api_configured:
            self._tasks.append(asyncio.create_task(self._live_loop(), name="ucm-live-calls"))
        if self.settings.ucm_tcp_listener_enabled:
            allowed_ips = self.settings.ucm_tcp_allowed_ips
            if not allowed_ips:
                log.error("UCM TCP listener disabled because UCM_TCP_ALLOWED_IPS is empty")
            else:
                try:
                    self._tcp_server = await asyncio.start_server(
                        self._handle_tcp_client,
                        host=self.settings.ucm_tcp_host,
                        port=self.settings.ucm_tcp_port,
                        limit=1024 * 1024,
                    )
                    log.info("UCM TCP CDR listener started on %s:%s", self.settings.ucm_tcp_host, self.settings.ucm_tcp_port)
                except OSError:
                    log.exception("Unable to start UCM TCP CDR listener")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._tcp_server is not None:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
            self._tcp_server = None
        if self.http is not None:
            await self.http.aclose()
            self.http = None

    def _client(self) -> httpx.AsyncClient:
        if self.http is None:
            raise RuntimeError("Grandstream UCM integration is not running")
        return self.http

    async def _post_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client().post(self.settings.ucm_api_url, json={"request": payload})
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("Unexpected Grandstream API response")
        return body

    async def _ensure_api_cookie(self) -> str:
        if not self.api_configured:
            raise RuntimeError("Grandstream HTTPS API credentials are not configured")
        if self._api_cookie and time.monotonic() < self._api_cookie_expires:
            return self._api_cookie
        async with self._api_lock:
            if self._api_cookie and time.monotonic() < self._api_cookie_expires:
                return self._api_cookie
            challenge_response = await self._post_api({
                "action": "challenge",
                "user": self.settings.ucm_api_username,
                "version": "1.0",
            })
            challenge = _text(_as_dict(challenge_response.get("response")).get("challenge"))
            if not challenge:
                raise RuntimeError("Grandstream API challenge was not returned")
            token = hashlib.md5((challenge + self.settings.ucm_api_password).encode("utf-8")).hexdigest()
            login_response = await self._post_api({
                "action": "login",
                "token": token,
                "user": self.settings.ucm_api_username,
            })
            if int(login_response.get("status", -1)) != 0:
                raise RuntimeError("Grandstream API login was rejected")
            cookie = _text(_as_dict(login_response.get("response")).get("cookie"))
            if not cookie:
                raise RuntimeError("Grandstream API did not return a session cookie")
            self._api_cookie = cookie
            self._api_cookie_expires = time.monotonic() + 240
            return cookie

    async def api_action(self, action: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(2):
            cookie = await self._ensure_api_cookie()
            body = {"action": action, **(parameters or {}), "cookie": cookie}
            response = await self._post_api(body)
            code = int(response.get("status", -999))
            if code == 0:
                return response
            if attempt == 0:
                self._api_cookie = ""
                self._api_cookie_expires = 0.0
                continue
            raise RuntimeError("Grandstream API action failed: " + action + " (status " + str(code) + ")")
        raise RuntimeError("Grandstream API action failed")

    async def originate_call(self, target: str, caller_extension: str, target_type: str) -> dict[str, Any]:
        if target_type == "EXTENSION":
            return await self.api_action("dialExtension", {"callee": target, "caller": caller_extension})
        if target_type == "OUTBOUND":
            return await self.api_action("dialOutbound", {"outbound": target, "caller": caller_extension})
        raise ValueError("Unsupported call target type")

    async def sync_once(self) -> dict[str, int]:
        if not self.cdr_configured:
            raise RuntimeError("Grandstream CDR URL or Digest credentials are not configured")
        auth = httpx.DigestAuth(self.settings.ucm_cdr_username, self.settings.ucm_cdr_password)
        records: list[dict[str, Any]] = []
        offset = 0
        for _ in range(self.settings.ucm_cdr_max_pages):
            response = await self._client().get(
                self.settings.ucm_cdr_url,
                params={
                    "format": "json",
                    "numRecords": self.settings.ucm_cdr_page_size,
                    "offset": offset,
                },
                auth=auth,
            )
            response.raise_for_status()
            body = response.json()
            page = body.get("cdr_root", body) if isinstance(body, dict) else body
            if isinstance(page, dict):
                page = [page]
            if not isinstance(page, list) or not page:
                break
            records.extend(item for item in page if isinstance(item, dict))
            offset += len(page)
            if len(page) < self.settings.ucm_cdr_page_size:
                break
        inserted = await self.ingest_records(records)
        self._last_sync_at = datetime.now(timezone.utc).isoformat()
        self._last_sync_error = None
        return {"received": len(records), "upserted": inserted}

    async def ingest_records(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        processed = 0
        async with self.session_factory() as session:
            for record in records:
                normalized = normalize_cdr(record)
                number = _digits(normalized["customer_number"])
                customer_id = None
                if number:
                    customer_id = await session.scalar(
                        select(Customer.id)
                        .where(func.regexp_replace(Customer.phone, r"\D", "", "g") == number)
                        .limit(1)
                    )
                now = datetime.now(timezone.utc)
                values = {
                    "id": uuid4(),
                    **normalized,
                    "customer_id": customer_id,
                    "synced_at": now,
                    "updated_at": now,
                }
                statement = pg_insert(TelephonyCall).values(**values)
                update_fields = {
                    field: getattr(statement.excluded, field)
                    for field in (
                        "customer_id", "source_number", "destination_number", "customer_number",
                        "caller_name", "direction", "extension", "status", "disposition",
                        "start_time", "answer_time", "end_time", "duration_seconds",
                        "billsec_seconds", "src_trunk", "dst_trunk", "channel", "dst_channel",
                        "recordings", "raw_cdr", "synced_at", "updated_at",
                    )
                }
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[TelephonyCall.ucm_unique_id],
                        set_=update_fields,
                    )
                )
                processed += 1
            await session.commit()
        return processed

    async def fetch_recording(self, filedir: str, filename: str) -> tuple[bytes, str]:
        if not self.recording_configured:
            raise RuntimeError("Grandstream recording API is not configured")
        safe = normalize_recording_references([filedir + "/" + filename])
        if not safe or safe[0] != {"filedir": filedir, "filename": filename}:
            raise ValueError("Invalid recording reference")
        auth = httpx.DigestAuth(self.settings.ucm_cdr_username, self.settings.ucm_cdr_password)
        response = await self._client().get(
            self.settings.ucm_recording_url,
            params={"filedir": filedir, "filename": filename},
            auth=auth,
        )
        response.raise_for_status()
        if len(response.content) > 50 * 1024 * 1024:
            raise RuntimeError("Recording exceeds the 50 MiB download limit")
        media_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        if media_type in {"application/octet-stream", "binary/octet-stream"} and filename.lower().endswith(".wav"):
            media_type = "audio/wav"
        return response.content, media_type

    async def _sync_loop(self) -> None:
        await asyncio.sleep(2)
        while True:
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_sync_error = type(exc).__name__
                log.warning("Grandstream CDR synchronization failed: %s", type(exc).__name__)
            await asyncio.sleep(self.settings.ucm_sync_interval_seconds)

    async def _live_loop(self) -> None:
        while True:
            try:
                response_unbridged = await self.api_action("listUnBridgedChannels")
                response_bridged = await self.api_action("listBridgedChannels")
                unbridged = _as_dict(response_unbridged.get("response")).get("channel") or []
                bridged = _as_dict(response_bridged.get("response")).get("channel") or []
                if isinstance(unbridged, dict):
                    unbridged = [unbridged]
                if isinstance(bridged, dict):
                    bridged = [bridged]
                calls = []
                channel_states = [(channel, False) for channel in (unbridged or [])]
                channel_states.extend((channel, True) for channel in (bridged or []))
                for channel, is_bridged in channel_states:
                    if not isinstance(channel, dict):
                        continue
                    caller = _text(channel.get("callernum") or channel.get("callerid1") or channel.get("callerid2"))
                    connected = _text(channel.get("connectednum") or channel.get("callerid2"))
                    inbound = bool(channel.get("inbound_trunk_name")) or _text(channel.get("channel")).lower().startswith("trunk")
                    channel_name = _text(channel.get("channel"))
                    destination_channel = _text(channel.get("dstchannel") or channel.get("dst_channel"))
                    extension = _text(
                        channel.get("channel_ext") or channel.get("chanext")
                        or channel.get("dstchannel_ext") or channel.get("dstchanext")
                    )
                    if not extension:
                        for candidate in (caller, connected):
                            digits = _digits(candidate)
                            if 2 <= len(digits) <= 8:
                                extension = digits
                                break
                    if not extension:
                        for candidate_channel in (channel_name, destination_channel):
                            match = re.search(r"(?:PJSIP|SIP|Local)/([0-9]{2,8})(?:-|@)", candidate_channel, re.I)
                            if match:
                                extension = match.group(1)
                                break
                    calls.append({
                        "call_key": _text(channel.get("uniqueid") or channel.get("uniqueid1") or channel.get("bridge_id") or channel_name or (caller + ":" + connected)),
                        "caller_number": caller,
                        "connected_number": connected,
                        "caller_name": _text(channel.get("callername") or channel.get("name1")),
                        "direction": "inbound" if inbound else "outbound",
                        "extension": extension,
                        "state": _text(channel.get("state")) or ("Up" if is_bridged else "Ringing"),
                        "channel": channel_name,
                        "bridged": is_bridged,
                    })
                self._live_calls = calls
                self._live_updated_at = datetime.now(timezone.utc).isoformat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("Grandstream live channel polling failed: %s", type(exc).__name__)
            await asyncio.sleep(self.settings.ucm_live_poll_seconds)

    async def _handle_tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else ""
        try:
            if peer_ip not in set(self.settings.ucm_tcp_allowed_ips):
                log.warning("Rejected UCM TCP CDR connection from an unapproved IP")
                writer.write(b"DENIED\r\n")
                await writer.drain()
                return
            chunks: list[bytes] = []
            total = 0
            while total < 1024 * 1024:
                try:
                    chunk = await asyncio.wait_for(
                        reader.read(min(65536, 1024 * 1024 - total)), timeout=5
                    )
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if b"\n" in chunk:
                    break
            raw = b"".join(chunks)
            records = parse_cdr_csv(raw)
            if not records:
                writer.write(b"INVALID\r\n")
            else:
                await self.ingest_records(records)
                writer.write(b"OK\r\n")
            await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Grandstream TCP CDR payload processing failed")
            try:
                writer.write(b"ERROR\r\n")
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
