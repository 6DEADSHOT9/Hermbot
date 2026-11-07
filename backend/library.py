"""Owner file library. Objects and versions are immutable; sharing is explicit per version."""

import base64
import binascii
import csv
import hashlib
import http.client
import io
from itertools import islice
import ipaddress
import socket
import ssl
import threading
import re
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import quote, urljoin, urlsplit
from uuid import UUID, uuid4
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import Field

from . import db, registry
from .models import AttachmentRequest, StrictModel

MAX_BYTES = 10_000_000
MAX_FETCH_SECONDS = 12
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEXT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "text/x-python",
    "text/javascript",
    "text/html",
}
FILE_TYPES = TEXT_TYPES | {"application/pdf", "image/png", "image/jpeg", "image/webp", DOCX, XLSX}
COLUMNS = "id,task_id,bot_id,conversation_id,name,content_type,sha256,size,family_id,version,previous_id,source,source_url,created_at"


class Upload(AttachmentRequest):
    conversation_id: UUID | None = None
    previous_id: UUID | None = None


class LinkRequest(StrictModel):
    url: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    previous_id: UUID | None = None


class ShareRequest(StrictModel):
    conversation_id: UUID


def filename(name):
    if (
        not name
        or name in (".", "..")
        or len(name) > 160
        or PurePosixPath(name).name != name
        or "\\" in name
        or any(ord(c) < 32 for c in name)
    ):
        raise HTTPException(422, "Use a plain filename with at most 160 characters")
    return name


def decode_upload(body):
    filename(body.name)
    if body.content_type not in FILE_TYPES:
        raise HTTPException(422, "Unsupported file type. Use text/code, PDF, PNG/JPEG/WebP, DOCX or XLSX")
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Invalid file encoding")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Each library file is limited to 10 MB")
    validate_bytes(data, body.content_type)
    return data


def validate_bytes(data, content_type):
    signatures = {
        "application/pdf": (b"%PDF-",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/webp": (b"RIFF",),
        DOCX: (b"PK",),
        XLSX: (b"PK",),
    }
    if content_type in signatures and not any(data.startswith(s) for s in signatures[content_type]):
        raise HTTPException(422, "File content does not match its declared type")
    if content_type == "image/webp" and data[8:12] != b"WEBP":
        raise HTTPException(422, "Invalid WebP file")
    if content_type in TEXT_TYPES:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(422, "Text files must use UTF-8 encoding")


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag in ("p", "div", "br", "h1", "h2", "h3", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(text)


def extract(data, content_type):
    if content_type in TEXT_TYPES:
        value = data.decode("utf-8")
        if content_type == "text/html":
            parser = PageText()
            parser.feed(value)
            value = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
        return value[:200_000]
    if content_type in (DOCX, XLSX):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = (
                    ["word/document.xml"]
                    if content_type == DOCX
                    else [
                        n
                        for n in archive.namelist()
                        if n == "xl/sharedStrings.xml" or re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
                    ][:20]
                )
                result = []
                total_size = 0
                for name in names:
                    info = archive.getinfo(name)
                    total_size += info.file_size
                    if total_size > 2_000_000:
                        raise HTTPException(413, "Document preview exceeds the decompression limit")
                    xml = archive.read(info)
                    normalized_xml = xml.replace(b"\x00", b"").upper()
                    if b"<!DOCTYPE" in normalized_xml or b"<!ENTITY" in normalized_xml:
                        raise HTTPException(422, "Document XML cannot contain entity declarations")
                    tree = ElementTree.fromstring(xml)
                    result.append(" ".join(tree.itertext()))
                return "\n".join(result)[:200_000]
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError):
            raise HTTPException(422, "Invalid office document")
    return ""


def public_address(url):
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(422, "Invalid URL port")
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in (None, 80, 443)
    ):
        raise HTTPException(422, "Use a public HTTP or HTTPS URL without credentials or a custom port")
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except socket.gaierror:
        raise HTTPException(422, "The link hostname could not be resolved")
    ips = {row[4][0] for row in addresses}
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise HTTPException(422, "Links must resolve only to public Internet addresses")
    return parsed, sorted(ips)[0], port or (443 if parsed.scheme == "https" else 80)


def fetch_link(url):
    # DNS is checked at each redirect, and the TCP connection uses that validated numeric IP.
    # TLS still verifies the original hostname. Environment proxy settings cannot bypass the check.
    for _ in range(5):
        parsed, address, port = public_address(url)
        conn = http.client.HTTPConnection(parsed.hostname, port, timeout=MAX_FETCH_SECONDS)
        timer = None
        expired = threading.Event()
        try:
            conn.sock = socket.create_connection((address, port), timeout=MAX_FETCH_SECONDS)
            if parsed.scheme == "https":
                conn.sock = ssl.create_default_context().wrap_socket(
                    conn.sock, server_hostname=parsed.hostname
                )
            # Socket timeouts alone allow an endless response that keeps dripping
            # bytes. Interrupt the actual socket on a wall-clock deadline, including
            # header parsing, and reject partial content received at that deadline.
            def interrupt(sock=conn.sock, elapsed=expired):
                elapsed.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            timer = threading.Timer(MAX_FETCH_SECONDS, interrupt)
            timer.daemon = True
            timer.start()
            conn.request(
                "GET",
                (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                headers={"User-Agent": "Hermbot/0.3", "Accept-Encoding": "identity"},
            )
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                destination = response.getheader("Location")
                if not destination:
                    raise HTTPException(422, "The link returned an invalid redirect")
                url = urljoin(url, destination)
                continue
            if response.status != 200:
                raise HTTPException(422, f"The link returned HTTP {response.status}")
            if response.getheader("Content-Encoding", "identity").lower() not in ("identity", ""):
                raise HTTPException(422, "Compressed HTTP responses are not supported for link import")
            content_type = response.getheader("Content-Type", "text/plain").split(";")[0].strip().lower()
            if content_type not in FILE_TYPES:
                raise HTTPException(422, "This link does not contain a supported document or image")
            data = response.read(MAX_BYTES + 1)
            if expired.is_set():
                raise HTTPException(502, "The public link response exceeded its download deadline")
            if len(data) > MAX_BYTES:
                raise HTTPException(413, "The linked file exceeds 10 MB")
            validate_bytes(data, content_type)
            name = PurePosixPath(parsed.path).name or "linked-page"
            name = re.sub(r"[^a-zA-Z0-9._-]", "-", name)[:150] or "linked-page"
            if content_type == "text/html":
                data, content_type, name = extract(data, content_type).encode(), "text/plain", name + ".txt"
            return filename(name), data, content_type, url
        except (OSError, http.client.HTTPException):
            raise HTTPException(502, "The public link could not be downloaded; retry or upload the file")
        finally:
            if timer is not None:
                timer.cancel()
            conn.close()
    raise HTTPException(422, "The link redirected too many times")


def visible_in_conversation(conn, artifact_id, conversation_id):
    return conn.execute(
        "SELECT a.* FROM artifacts a WHERE a.id=%s AND (a.conversation_id=%s OR EXISTS(SELECT 1 FROM library_shares s WHERE s.artifact_id=a.id AND s.conversation_id=%s))",
        (artifact_id, conversation_id, conversation_id),
    ).fetchone()


def save(
    conn,
    store,
    *,
    name,
    data,
    content_type,
    conversation_id=None,
    previous_id=None,
    task=None,
    artifact_id=None,
    source="upload",
    source_url=None,
):
    filename(name)
    if (
        conversation_id
        and not conn.execute("SELECT 1 FROM conversations WHERE id=%s", (conversation_id,)).fetchone()
    ):
        raise HTTPException(404, "Conversation not found")
    preview_text = extract(data, content_type)
    artifact_id = artifact_id or uuid4()
    existing = conn.execute("SELECT * FROM artifacts WHERE id=%s", (artifact_id,)).fetchone()
    if existing:
        return existing
    previous = None
    if previous_id:
        previous = conn.execute("SELECT * FROM artifacts WHERE id=%s", (previous_id,)).fetchone()
        if not previous:
            raise HTTPException(404, "Previous version not found")
        if task and not visible_in_conversation(conn, previous_id, task["conversation_id"]):
            raise HTTPException(403, "Previous version is outside this conversation")
        conversation_id = previous["conversation_id"]
    elif task:
        # Named outputs in one conversation form a version family. A lock covers concurrent first publications.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(conversation_id) + ":" + name,)
        )
        previous = conn.execute(
            "SELECT * FROM artifacts WHERE conversation_id=%s AND name=%s ORDER BY created_at DESC,id DESC LIMIT 1",
            (conversation_id, name),
        ).fetchone()
    family_id, version = artifact_id, 1
    if previous:
        family_id = previous["family_id"]
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(family_id),))
        head = conn.execute(
            "SELECT * FROM artifacts WHERE family_id=%s ORDER BY version DESC LIMIT 1", (family_id,)
        ).fetchone()
        if previous_id and head["id"] != previous_id:
            raise HTTPException(409, "A newer version exists. Refresh before adding another version")
        previous, version = head, head["version"] + 1
    sha = hashlib.sha256(data).hexdigest()
    object_key = f"library/{family_id}/{artifact_id}"
    store.client.put_object(
        Bucket=store.bucket, Key=object_key, Body=data, ContentType=content_type, Metadata={"sha256": sha}
    )
    return conn.execute(
        "INSERT INTO artifacts(id,task_id,bot_id,conversation_id,name,content_type,object_key,sha256,size,family_id,version,previous_id,source,source_url,preview_text) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (
            artifact_id,
            task["id"] if task else None,
            task["bot_id"] if task else None,
            conversation_id,
            name,
            content_type,
            object_key,
            sha,
            len(data),
            family_id,
            version,
            previous["id"] if previous else None,
            source,
            source_url,
            preview_text,
        ),
    ).fetchone()


def projection(row):
    return {key: row[key] for key in COLUMNS.split(",")}


def file_response(store, artifact, download=False):
    data = store.read_bytes(artifact)
    disposition = "attachment" if download else "inline"
    # Chromium's built-in PDF viewer cannot load inside a sandboxed document. A fixed
    # application/pdf type plus nosniff keeps the browser on its PDF renderer; other
    # uploaded types retain the restrictive sandbox, including all text/HTML downloads.
    policy = (
        "frame-ancestors 'self'"
        if artifact["content_type"] == "application/pdf"
        else "sandbox; default-src 'none'"
    )
    return Response(
        data,
        media_type=artifact["content_type"],
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(artifact['name'])}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": policy,
        },
    )


def routes(owner, get_store):
    router = APIRouter(dependencies=[Depends(owner)])

    @router.get("/api/v1/library")
    def listing(
        q: str = Query(default="", max_length=200),
        conversation_id: UUID | None = None,
        offset: int = Query(default=0, ge=0),
        all_versions: bool = False,
    ):
        with db.connection() as conn:
            rows = conn.execute(
                f"SELECT {COLUMNS} FROM artifacts a WHERE (%s::uuid IS NULL OR conversation_id=%s OR EXISTS(SELECT 1 FROM library_shares s WHERE s.artifact_id=a.id AND s.conversation_id=%s)) AND (name ILIKE %s OR preview_text ILIKE %s) AND (%s OR NOT EXISTS(SELECT 1 FROM artifacts newer WHERE newer.family_id=a.family_id AND newer.version>a.version AND (%s::uuid IS NULL OR newer.conversation_id=%s OR EXISTS(SELECT 1 FROM library_shares ns WHERE ns.artifact_id=newer.id AND ns.conversation_id=%s)))) ORDER BY created_at DESC,id LIMIT 51 OFFSET %s",
                (
                    conversation_id,
                    conversation_id,
                    conversation_id,
                    "%" + q + "%",
                    "%" + q + "%",
                    all_versions,
                    conversation_id,
                    conversation_id,
                    conversation_id,
                    offset,
                ),
            ).fetchall()
            return {"items": rows[:50], "has_more": len(rows) > 50}

    @router.post("/api/v1/library", status_code=201)
    def upload(body: Upload, key: str = Header(alias="Idempotency-Key")):
        data = decode_upload(body)
        with db.connection() as conn:
            return registry.mutation(
                conn,
                key,
                {"action": "library-upload", "body": body.model_dump()},
                lambda: projection(
                    save(
                        conn,
                        get_store(),
                        name=body.name,
                        data=data,
                        content_type=body.content_type,
                        conversation_id=body.conversation_id,
                        previous_id=body.previous_id,
                    )
                ),
            )

    @router.post("/api/v1/library/links", status_code=201)
    def ingest(body: LinkRequest, key: str = Header(alias="Idempotency-Key")):
        with db.connection() as conn:

            def work():
                name, data, content_type, final_url = fetch_link(body.url)
                return projection(
                    save(
                        conn,
                        get_store(),
                        name=name,
                        data=data,
                        content_type=content_type,
                        conversation_id=body.conversation_id,
                        previous_id=body.previous_id,
                        source="link",
                        source_url=final_url,
                    )
                )

            return registry.mutation(conn, key, {"action": "library-link", "body": body.model_dump()}, work)

    @router.get("/api/v1/library/{artifact_id}")
    def detail(artifact_id: UUID):
        with db.connection() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id=%s", (artifact_id,)).fetchone()
            if not row:
                raise HTTPException(404, "File not found")
            text = row["preview_text"] or extract(get_store().read_bytes(row), row["content_type"])
            table = None
            if row["content_type"] == "text/csv":
                try:
                    table = [r[:30] for r in islice(csv.reader(io.StringIO(text)), 201)]
                except csv.Error:
                    pass  # Large fields still have a bounded text preview and the original download.
            return {
                **projection(row),
                "preview_text": text,
                "table": table,
                "versions": conn.execute(
                    f"SELECT {COLUMNS} FROM artifacts WHERE family_id=%s ORDER BY version DESC",
                    (row["family_id"],),
                ).fetchall(),
                "shares": conn.execute(
                    "SELECT conversation_id,created_at FROM library_shares WHERE artifact_id=%s",
                    (artifact_id,),
                ).fetchall(),
                "task_access": conn.execute(
                    "SELECT task_id FROM artifact_access WHERE artifact_id=%s", (artifact_id,)
                ).fetchall(),
            }

    @router.post("/api/v1/library/{artifact_id}/shares")
    def share(artifact_id: UUID, body: ShareRequest):
        with db.connection() as conn:
            if (
                not conn.execute("SELECT 1 FROM artifacts WHERE id=%s", (artifact_id,)).fetchone()
                or not conn.execute(
                    "SELECT 1 FROM conversations WHERE id=%s", (body.conversation_id,)
                ).fetchone()
            ):
                raise HTTPException(404, "File or conversation not found")
            conn.execute(
                "INSERT INTO library_shares(artifact_id,conversation_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (artifact_id, body.conversation_id),
            )
            db.event(
                conn,
                "library.shared",
                conversation_id=body.conversation_id,
                data={"artifact_id": str(artifact_id)},
            )
            return {"shared": True}

    @router.delete("/api/v1/library/{artifact_id}/shares/{conversation_id}")
    def revoke(artifact_id: UUID, conversation_id: UUID):
        with db.connection() as conn:
            conn.execute(
                "DELETE FROM library_shares WHERE artifact_id=%s AND conversation_id=%s",
                (artifact_id, conversation_id),
            )
            return {
                "revoked": True,
                "note": "New task admission is blocked; existing task copies and explicit task grants remain attached to their admitted work.",
            }

    @router.get("/api/v1/history/search")
    def history(q: str = Query(min_length=2, max_length=200), offset: int = Query(default=0, ge=0)):
        with db.connection() as conn:
            return {
                "items": conn.execute(
                    "SELECT id,conversation_id,author,role,content,task_id,created_at FROM messages WHERE content ILIKE %s ORDER BY created_at DESC LIMIT 50 OFFSET %s",
                    ("%" + q + "%", offset),
                ).fetchall()
            }

    return router
