"""Caller routes for contact assignments, dialling, and survey responses.

Names and numbers are encrypted in storage. Name images and dialling redirects
do not prevent a caller from copying contact details. See SECURITY.md."""

import io
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw, ImageFont

from app.common.crypto import decrypt_text, encrypt_text
from app.common.db import get_ops, init_pools, purge_expired_rows, put_ops
from app.common.email_auth import digest, request_code, verify_code
from app.common.security import (
    install_security,
    json_object_body,
    same_origin_write_request,
)
from app.common.survey import load_survey, read_survey, validate_answers
from app.common.util import fetchone_dict

logger = logging.getLogger(__name__)

PB_SESSION_COOKIE = "pb_session"
ASSIGNMENT_COOLDOWN_SECONDS = 20

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# Startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pools()
    purge_expired_rows()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
install_security(app)
app.mount("/assets", StaticFiles(directory="app/static"), name="shared")
app.mount(
    "/pb/static", StaticFiles(directory="app/phonebanking/static"), name="pb-static"
)
templates = Jinja2Templates(directory=["app/phonebanking/templates", "app/templates"])


# Helpers


def _security_headers(resp: Response) -> None:
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )


def _cookie_secure() -> bool:
    return True


def _same_origin(request: Request) -> None:
    if not same_origin_write_request(request):
        raise HTTPException(403, "Forbidden")


def _require_pb_session(req: Request) -> dict:
    token = (req.cookies.get(PB_SESSION_COOKIE) or "").strip()
    if not token:
        raise HTTPException(401, "Not authenticated")

    conn = get_ops()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.session_token,
                           s.volunteer_email,
                           s.display_name,
                           EXTRACT(EPOCH FROM (now() - (
                               SELECT MAX(s2.assigned_at)
                               FROM pb_assignments s2
                               WHERE s2.volunteer_email = s.volunteer_email
                           )))::int AS secs_since_last
                    FROM pb_sessions s
                    JOIN pb_authorized_volunteers v ON lower(v.email)=lower(s.volunteer_email)
                      AND (v.expires_at IS NULL OR v.expires_at>now())
                    WHERE s.session_token = %s
                      AND s.expires_at > now()
                    """,
                    (token,),
                )
                sess = fetchone_dict(cur)
                if not sess:
                    raise HTTPException(401, "Session expired")
                return sess
    finally:
        put_ops(conn)


_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
_FONT_PATH = next((path for path in _FONT_CANDIDATES if os.path.exists(path)), None)


def _load_font(size: int):
    if _FONT_PATH:
        try:
            return ImageFont.truetype(_FONT_PATH, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _partition_name(words, line_count: int):
    if line_count == 1:
        yield (" ".join(words),)
        return

    max_split = len(words) - line_count + 1
    for split_at in range(1, max_split + 1):
        head = " ".join(words[:split_at])
        for tail in _partition_name(words[split_at:], line_count - 1):
            yield (head, *tail)


def _candidate_name_layouts(name: str):
    words = name.split()
    if not words:
        yield (name or "Unknown",)
        return

    max_lines = min(len(words), 3)
    for line_count in range(1, max_lines + 1):
        yield from _partition_name(words, line_count)


def _measure_name_block(draw, lines, font, spacing: int):
    bbox = draw.multiline_textbbox(
        (0, 0),
        "\n".join(lines),
        font=font,
        spacing=spacing,
        align="center",
    )
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _pick_name_layout(draw, name: str, max_width: int, max_height: int):
    best = None
    best_score = None

    for lines in _candidate_name_layouts(name):
        low, high = 24, 420
        layout = None

        while low <= high:
            size = (low + high) // 2
            font = _load_font(size)
            spacing = max(8, size // 6)
            text_width, text_height = _measure_name_block(draw, lines, font, spacing)

            if text_width <= max_width and text_height <= max_height:
                layout = (lines, font, spacing, text_width, text_height, size)
                low = size + 1
            else:
                high = size - 1

        if layout:
            _, _, _, text_width, text_height, size = layout
            width_ratio = text_width / max_width
            height_ratio = text_height / max_height
            score = (
                min(width_ratio, height_ratio),
                width_ratio * height_ratio,
                size,
                -len(lines),
            )
            if best is None or score > best_score:
                best = layout
                best_score = score

    if best is not None:
        lines, font, spacing, _, _, _ = best
        return lines, font, spacing

    fallback = _load_font(24)
    return (name or "Unknown",), fallback, 8


def _make_name_image(name: str) -> bytes:
    """Render name as a large PNG that fills the available image area."""
    width, height = 1400, 420
    padding_x, padding_y = 24, 20

    safe_name = " ".join((name or "").split()) or "Unknown"
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    lines, font, spacing = _pick_name_layout(
        draw,
        safe_name,
        max_width=width - (padding_x * 2),
        max_height=height - (padding_y * 2),
    )

    draw.multiline_text(
        (width // 2, height // 2),
        "\n".join(lines),
        fill=(15, 15, 15),
        anchor="mm",
        font=font,
        align="center",
        spacing=spacing,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _release_expired_assignments(cur) -> None:
    """Expire rows atomically before making their contacts available again."""
    cur.execute(
        "UPDATE pb_assignments SET active=false WHERE active AND expires_at<=now() RETURNING contact_id"
    )
    expired = [row[0] for row in cur.fetchall()]
    for contact_id in expired:
        cur.execute(
            "UPDATE pb_contacts SET status='available' WHERE contact_id=%s AND status='assigned'",
            (contact_id,),
        )


# Auth


@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse("/pb/login")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "caller"}


@app.get("/pb/login", response_class=HTMLResponse)
async def login_page(request: Request):
    resp = templates.TemplateResponse(request, "signin.html", {"is_admin": False})
    _security_headers(resp)
    return resp


@app.post("/pb/api/login/request")
async def api_login_request(request: Request, background_tasks: BackgroundTasks):
    body = await json_object_body(request)
    ip = str(request.client.host) if request.client else "unknown"
    return request_code(body.get("email"), "caller", ip, background_tasks)


@app.post("/pb/api/login/verify")
async def api_login_verify(request: Request):
    body = await json_object_body(request)
    if not body.get("code") or not body.get("challenge_id"):
        raise HTTPException(400, "Enter the code from your latest sign-in email.")
    ip = str(request.client.host) if request.client else "unknown"
    email = verify_code(body, "caller", ip)
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pb_authorized_volunteers WHERE lower(email)=%s AND (expires_at IS NULL OR expires_at>now())",
                (email,),
            )
            if not cur.fetchone():
                raise HTTPException(403, "Your access is no longer active.")
            cur.execute(
                "INSERT INTO pb_sessions(volunteer_email) VALUES (%s) RETURNING session_token",
                (email,),
            )
            session_token = str(cur.fetchone()[0])
    finally:
        put_ops(conn)
    response = Response(content='{"ok":true}', media_type="application/json")
    response.set_cookie(
        PB_SESSION_COOKIE,
        session_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=12 * 3600,
    )
    return response


@app.post("/pb/api/logout")
async def api_logout(request: Request):
    token = (request.cookies.get(PB_SESSION_COOKIE) or "").strip()
    if token:
        conn = get_ops()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM pb_sessions WHERE session_token = %s",
                        (token,),
                    )
        finally:
            put_ops(conn)
    resp = RedirectResponse("/pb/login", status_code=303)
    resp.delete_cookie(PB_SESSION_COOKIE)
    return resp


# Page


@app.get("/pb/call", response_class=HTMLResponse)
async def call_page(request: Request):
    sess = _require_pb_session(request)
    resp = templates.TemplateResponse(
        request,
        "call.html",
        {"volunteer_name": sess.get("display_name") or sess["volunteer_email"]},
    )
    _security_headers(resp)
    return resp


# Assignment API


@app.get("/pb/api/current")
async def api_current(request: Request):
    """Return the caller's current state without side effects.

    Returns one of:
      {"state": "assigned",  "assignment_id": "…"}
      {"state": "cooldown",  "wait_seconds": N}
      {"state": "idle"}
    """
    sess = _require_pb_session(request)

    conn = get_ops()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT assignment_id
                    FROM pb_assignments
                    WHERE session_token = %s
                      AND active = TRUE
                      AND expires_at > now()
                    """,
                    (sess["session_token"],),
                )
                asgn = fetchone_dict(cur)
    finally:
        put_ops(conn)

    if asgn:
        return {"state": "assigned", "assignment_id": str(asgn["assignment_id"])}

    secs = sess.get("secs_since_last")
    if secs is not None and int(secs) < ASSIGNMENT_COOLDOWN_SECONDS:
        return {
            "state": "cooldown",
            "wait_seconds": ASSIGNMENT_COOLDOWN_SECONDS - int(secs),
        }

    return {"state": "idle"}


@app.post("/pb/api/next")
async def api_next(request: Request):
    """Serialize allocations per caller across sessions, then lock the contact."""
    sess = _require_pb_session(request)
    token, email = sess["session_token"], sess["volunteer_email"]
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 771))", (email,)
            )
            _release_expired_assignments(cur)
            cur.execute(
                "SELECT assignment_id,session_token FROM pb_assignments WHERE volunteer_email=%s AND active FOR UPDATE",
                (email,),
            )
            existing = fetchone_dict(cur)
            if existing:
                if existing["session_token"] != token:
                    raise HTTPException(
                        409,
                        "You have an active call in another session. Finish it there, or wait for it to expire.",
                    )
                return {
                    "ok": True,
                    "assignment_id": str(existing["assignment_id"]),
                    "resumed": True,
                }
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM(now()-MAX(assigned_at))) FROM pb_assignments WHERE volunteer_email=%s",
                (email,),
            )
            seconds = cur.fetchone()[0]
            if seconds is not None and seconds < ASSIGNMENT_COOLDOWN_SECONDS:
                return {
                    "ok": False,
                    "cooldown": True,
                    "wait_seconds": ASSIGNMENT_COOLDOWN_SECONDS - int(seconds),
                }
            cur.execute(
                "SELECT contact_id FROM pb_contacts WHERE status='available' ORDER BY created_at,contact_id LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
            contact = fetchone_dict(cur)
            if not contact:
                return {"ok": False, "exhausted": True}
            cur.execute(
                "UPDATE pb_contacts SET status='assigned' WHERE contact_id=%s",
                (contact["contact_id"],),
            )
            cur.execute(
                "INSERT INTO pb_assignments(contact_id,session_token,volunteer_email) VALUES (%s,%s,%s) RETURNING assignment_id",
                (contact["contact_id"], token, email),
            )
            assignment_id = str(cur.fetchone()[0])
            cur.execute(
                "UPDATE pb_sessions SET last_assigned_at=now() WHERE session_token=%s",
                (token,),
            )
            return {"ok": True, "assignment_id": assignment_id, "resumed": False}
    finally:
        put_ops(conn)


# Name image


@app.get("/pb/api/name-image/{assignment_id}")
async def api_name_image(assignment_id: uuid.UUID, request: Request):
    """Return the contact's name as a PNG."""
    sess = _require_pb_session(request)

    conn = get_ops()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.name_cipher
                    FROM pb_assignments a
                    JOIN pb_contacts c ON c.contact_id = a.contact_id
                    WHERE a.assignment_id = %s
                      AND a.session_token = %s
                      AND a.active = TRUE
                      AND a.expires_at > now()
                    """,
                    (str(assignment_id), sess["session_token"]),
                )
                row = fetchone_dict(cur)
    finally:
        put_ops(conn)

    if not row:
        raise HTTPException(404, "Not found")

    try:
        name = decrypt_text(row["name_cipher"])
    except Exception:
        logger.error("Decrypt failed for assignment %s", assignment_id, exc_info=True)
        raise HTTPException(500, "Error")

    img_bytes = _make_name_image(name)

    return Response(
        content=img_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Pragma": "no-cache",
        },
    )


# Call redirect


@app.get("/pb/api/call/{assignment_id}")
async def api_initiate_call(assignment_id: uuid.UUID, request: Request):
    """Redirect to tel:+… — the phone number never appears in HTML."""
    sess = _require_pb_session(request)

    conn = get_ops()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.phone_cipher
                    FROM pb_assignments a
                    JOIN pb_contacts c ON c.contact_id = a.contact_id
                    WHERE a.assignment_id = %s
                      AND a.session_token = %s
                      AND a.active = TRUE
                      AND a.expires_at > now()
                    """,
                    (str(assignment_id), sess["session_token"]),
                )
                row = fetchone_dict(cur)
    finally:
        put_ops(conn)

    if not row:
        raise HTTPException(404, "Not found")

    try:
        phone = decrypt_text(row["phone_cipher"])
    except Exception:
        raise HTTPException(500, "Error")

    safe = "".join(c for c in phone if c in "0123456789+- ")
    return RedirectResponse(f"tel:{safe}", status_code=302)


# Submit outcome


@app.post("/pb/api/submit/{assignment_id}")
async def api_submit(assignment_id: uuid.UUID, request: Request):
    """One transaction closes the assignment and stores an encrypted answer snapshot."""
    sess = _require_pb_session(request)
    body = await json_object_body(request)
    outcome = body.get("outcome")
    comments = body.get("comments", "")
    if outcome not in ("answered", "not_answered", "refused"):
        raise HTTPException(400, "Choose a call outcome.")
    if not isinstance(comments, str) or len(comments) > 2000:
        raise HTTPException(400, "Notes must be at most 2,000 characters.")
    canonical = {
        "outcome": outcome,
        "comments": comments.strip(),
        "answers": body.get("answers", {}),
        "survey_version": body.get("survey_version"),
    }
    payload_hash = digest(
        "submission:"
        + str(assignment_id)
        + ":"
        + json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    )
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT contact_id,active,expires_at>now() AS fresh FROM pb_assignments WHERE assignment_id=%s AND session_token=%s FOR UPDATE",
                (str(assignment_id), sess["session_token"]),
            )
            assignment = fetchone_dict(cur)
            if not assignment:
                raise HTTPException(404, "Assignment not found.")
            cur.execute(
                "SELECT payload_sha256 FROM pb_call_results WHERE assignment_id=%s",
                (str(assignment_id),),
            )
            previous = cur.fetchone()
            if previous:
                if previous[0] != payload_hash:
                    raise HTTPException(
                        409, "This call was already saved with different answers."
                    )
                return {
                    "ok": True,
                    "duplicate": True,
                    "cooldown_seconds": ASSIGNMENT_COOLDOWN_SECONDS,
                }
            if not assignment["active"] or not assignment["fresh"]:
                raise HTTPException(
                    409, "This assignment expired. Request the next contact."
                )
            validation = {
                **body,
                "notes": comments,
                "contact_status": {
                    "answered": "answered",
                    "not_answered": "no_answer",
                    "refused": "refused",
                }[outcome],
            }
            _, snapshot = validate_answers(read_survey(cur), validation)
            cur.execute(
                "INSERT INTO pb_call_results(assignment_id,contact_id,volunteer_email,outcome,answers_cipher,payload_sha256) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    str(assignment_id),
                    assignment["contact_id"],
                    sess["volunteer_email"],
                    outcome,
                    encrypt_text(json.dumps(snapshot)),
                    payload_hash,
                ),
            )
            cur.execute(
                "UPDATE pb_assignments SET active=false,completed_at=now() WHERE assignment_id=%s",
                (str(assignment_id),),
            )
            cur.execute(
                "UPDATE pb_contacts SET status='done',call_count=call_count+1,last_outcome=%s,last_called_at=now() WHERE contact_id=%s",
                (outcome, assignment["contact_id"]),
            )
    finally:
        put_ops(conn)
    return {"ok": True, "cooldown_seconds": ASSIGNMENT_COOLDOWN_SECONDS}


@app.get("/pb/api/survey")
def caller_survey(request: Request):
    _require_pb_session(request)
    return load_survey()
