"""Local HTTP boundary and current, database-backed authorization."""

import asyncio
import hashlib
import secrets
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from connectorforge.config import Settings
from connectorforge.database import Database
from connectorforge.models import AccessToken, Audit, LoginCode
from connectorforge.problems import Problem, problem_response

Role = Literal["admin", "operator", "viewer"]
PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"read", "invoke", "evaluate", "events:retry", "manage"}),
    "operator": frozenset({"read", "invoke", "evaluate", "events:retry"}),
    "viewer": frozenset({"read"}),
}
COOKIE = "cf_session"
BODY_TIMEOUT_SECONDS = 5.0
router = APIRouter(prefix="/api/v1", tags=["access"])


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_login_code(db: Database, role: Role) -> str:
    code = "cf_login_" + secrets.token_urlsafe(24)
    with db.transaction() as session:
        session.execute(delete(LoginCode).where(LoginCode.role == role))
        session.add(LoginCode(digest=digest(code), role=role, expires_at=time.time() + 600))
    return code


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    scopes: frozenset[str]
    connectors: frozenset[str]
    operations: frozenset[str]
    kind: str
    csrf: str | None


def authorize(
    db: Database,
    token: str,
    permission: str,
    connector: str | None = None,
    operation: str | None = None,
) -> Principal:
    with db.transaction() as session:
        row = session.scalar(select(AccessToken).where(AccessToken.digest == digest(token)))
        if row is None or row.revoked or row.expires_at <= time.time():
            raise Problem(401, "unauthorized", "Sign in with a current credential.")
        if permission not in row.scopes or permission not in PERMISSIONS.get(row.role, frozenset()):
            raise Problem(403, "forbidden", "This credential does not grant the requested action.")
        if connector and "*" not in row.connectors and connector not in row.connectors:
            raise Problem(403, "connector_scope", "This connector is outside the credential scope.")
        if operation and "*" not in row.operations and operation not in row.operations:
            raise Problem(403, "operation_scope", "This operation is outside the credential scope.")
        return Principal(
            row.id,
            row.role,
            frozenset(row.scopes),
            frozenset(row.connectors),
            frozenset(row.operations),
            row.kind,
            row.csrf,
        )


def request_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization:
        if not authorization.startswith("Bearer "):
            raise Problem(401, "unauthorized", "Use a bearer credential.")
        return authorization[7:]
    return request.cookies.get(COOKIE, "")


def require(
    request: Request,
    permission: str = "read",
    connector: str | None = None,
    operation: str | None = None,
) -> Principal:
    principal = authorize(
        request.app.state.db, request_token(request), permission, connector, operation
    )
    if principal.kind == "session" and request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("x-csrf-token", "")
        if not principal.csrf or not secrets.compare_digest(supplied, principal.csrf):
            raise Problem(403, "csrf", "A current CSRF token is required.")
    return principal


class SecurityBoundary:
    """Reject hostile origins and oversized requests before parsing request bodies."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        origins = {self.settings.origin, f"http://localhost:{self.settings.port}"}
        if self.settings.development:
            origins.add("http://127.0.0.1:5173")
        error: tuple[int, str, str] | None = None
        hosts = {f"127.0.0.1:{self.settings.port}", f"localhost:{self.settings.port}"}
        if headers.get("host") not in hosts:
            error = (400, "invalid_host", "Use the configured loopback address.")
        elif "origin" in headers and headers["origin"] not in origins:
            error = (403, "invalid_origin", "This origin is not allowed.")
        body = bytearray()
        deadline = time.monotonic() + BODY_TIMEOUT_SECONDS
        if error is None:
            while True:
                try:
                    message = await asyncio.wait_for(receive(), timeout=deadline - time.monotonic())
                except TimeoutError:
                    error = (408, "request_timeout", "Request body exceeded its time budget.")
                    break
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 2 * 1024 * 1024:
                    error = (413, "request_too_large", "Request body exceeds 2 MiB.")
                    break
                if not message.get("more_body", False):
                    break
        if error:
            await problem_response(*error)(scope, receive, send)
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def secured_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"cache-control", b"no-store"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; "
                            b"style-src 'self' 'unsafe-inline'; object-src 'none'; "
                            b"base-uri 'none'; "
                            b"frame-ancestors 'none'; form-action 'self'",
                        ),
                    ]
                )
            await send(message)

        await self.app(scope, replay, secured_send)


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=128)


class KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    role: Role = "operator"
    scopes: list[str] = Field(default_factory=lambda: ["read", "invoke"], max_length=5)
    connectors: list[str] = Field(default_factory=lambda: ["*"], max_length=100)
    operations: list[str] = Field(default_factory=lambda: ["*"], max_length=100)
    hours: int = Field(default=24, ge=1, le=24)


@router.post("/auth/login")
def login(data: LoginInput, request: Request, response: Response) -> dict[str, str]:
    now = time.time()
    attempts: deque[float] = request.app.state.login_attempts
    while attempts and attempts[0] < now - 60:
        attempts.popleft()
    if len(attempts) >= 10:
        raise Problem(429, "login_throttled", "Wait a minute before trying again.")
    attempts.append(now)
    db: Database = request.app.state.db
    with db.transaction() as session:
        # DELETE ... RETURNING makes consumption atomic across simultaneous logins.
        code = session.execute(
            delete(LoginCode)
            .where(
                LoginCode.digest == digest(data.code),
                LoginCode.expires_at > now,
            )
            .returning(LoginCode.role)
        ).scalar_one_or_none()
        if code is None:
            raise Problem(401, "invalid_login", "The login code has expired or was already used.")
        token, csrf = "cf_session_" + secrets.token_urlsafe(32), secrets.token_hex(32)
        session.add(
            AccessToken(
                digest=digest(token),
                kind="session",
                name="Local session",
                role=code,
                scopes=sorted(PERMISSIONS[code]),
                connectors=["*"],
                operations=["*"],
                csrf=csrf,
                expires_at=now + 1800,
            )
        )
        session.add(Audit(actor=code, action="auth.login", resource="workspace"))
    response.set_cookie(COOKIE, token, max_age=1800, httponly=True, samesite="strict", secure=False)
    return {"role": code, "csrf": csrf}


@router.get("/auth/me")
def me(request: Request) -> dict[str, str | None]:
    principal = require(request)
    return {"role": principal.role, "csrf": principal.csrf}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    principal = require(request)
    with request.app.state.db.transaction() as session:
        session.get(AccessToken, principal.id).revoked = True
        session.add(Audit(actor=principal.id, action="auth.logout", resource="workspace"))
    response.delete_cookie(COOKIE)


@router.post("/keys", status_code=201)
def create_key(data: KeyInput, request: Request) -> dict[str, str]:
    principal = require(request, "manage")
    if not data.scopes or not set(data.scopes) <= PERMISSIONS[data.role]:
        raise Problem(422, "invalid_scopes", "Scopes must belong to the selected role.")
    token = "cf_key_" + secrets.token_urlsafe(32)
    with request.app.state.db.transaction() as session:
        row = AccessToken(
            digest=digest(token),
            kind="api",
            name=data.name,
            role=data.role,
            scopes=data.scopes,
            connectors=data.connectors,
            operations=data.operations,
            expires_at=time.time() + data.hours * 3600,
        )
        session.add(row)
        session.flush()
        session.add(Audit(actor=principal.id, action="key.created", resource=row.id))
        key_id = row.id
    return {"id": key_id, "token": token}


@router.get("/keys")
def list_keys(request: Request) -> list[dict[str, str | float | bool]]:
    require(request, "manage")
    with request.app.state.db.transaction() as session:
        return [
            {
                "id": row.id,
                "name": row.name,
                "role": row.role,
                "expires_at": row.expires_at,
                "revoked": row.revoked,
                "expired": row.expires_at <= time.time(),
            }
            for row in session.scalars(select(AccessToken).where(AccessToken.kind == "api"))
        ]


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(key_id: str, request: Request) -> None:
    principal = require(request, "manage")
    with request.app.state.db.transaction() as session:
        row = session.get(AccessToken, key_id)
        if row is None or row.kind != "api":
            raise Problem(404, "not_found", "No such API credential.")
        row.revoked = True
        session.add(Audit(actor=principal.id, action="key.revoked", resource=key_id))
