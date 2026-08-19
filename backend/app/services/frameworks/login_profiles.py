"""Config-driven interface login profiles.

The open-source edition does not ship business-specific app ids, login routes,
or account field names. Integrators can provide profiles through environment
variables when they want API runners to login through an external framework.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time

logger = logging.getLogger(__name__)


def _load_json_env(name: str) -> dict:
    raw = os.environ.get(name) or "{}"
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        logger.warning("Invalid %s JSON, ignoring", name)
        return {}


# Expected shape:
# {
#   "orders": {
#     "mode": "password",
#     "login_key": "orders_login",
#     "appid": "10000",
#     "fields": {"username": "username", "password": "password"}
#   }
# }
_PROFILES: dict[str, dict] = _load_json_env("INTERFACE_LOGIN_PROFILES_JSON")
_PLATFORM_ALIAS: dict[str, str] = _load_json_env("INTERFACE_PLATFORM_ALIAS_JSON")


def profile_for(service: str | None) -> dict | None:
    """Return a configured login profile for a service or platform alias."""
    if not service:
        return None
    key = str(service).strip()
    return _PROFILES.get(key) or _PROFILES.get(_PLATFORM_ALIAS.get(key, ""))


def _der_len(b: bytes, i: int) -> tuple[int, int]:
    n = b[i]
    i += 1
    if n < 0x80:
        return n, i
    k = n & 0x7F
    return int.from_bytes(b[i:i + k], "big"), i + k


def _rsa_pub(der: bytes) -> tuple[int, int]:
    i = 0
    if der[i] != 0x30:
        raise ValueError("public key is not a DER sequence")
    _, i = _der_len(der, i + 1)
    if der[i] != 0x30:
        raise ValueError("public key is missing algorithm identifier")
    alg_len, i = _der_len(der, i + 1)
    i += alg_len
    if der[i] != 0x03:
        raise ValueError("public key is missing bit string")
    _, i = _der_len(der, i + 1)
    i += 1
    if der[i] != 0x30:
        raise ValueError("public key inner structure is invalid")
    _, i = _der_len(der, i + 1)
    if der[i] != 0x02:
        raise ValueError("public key is missing modulus")
    n_len, i = _der_len(der, i + 1)
    n = int.from_bytes(der[i:i + n_len], "big")
    i += n_len
    if der[i] != 0x02:
        raise ValueError("public key is missing exponent")
    e_len, i = _der_len(der, i + 1)
    return n, int.from_bytes(der[i:i + e_len], "big")


def rsa_encrypt(plain: str, pub_b64: str) -> str:
    """PKCS#1 v1.5 public-key encryption, returned as base64."""
    n, e = _rsa_pub(base64.b64decode(pub_b64))
    k = (n.bit_length() + 7) // 8
    msg = plain.encode()
    if len(msg) > k - 11:
        raise ValueError("text is too long for the public key")
    pad = bytearray()
    while len(pad) < k - 3 - len(msg):
        pad += bytes(c for c in os.urandom(k) if c != 0)
    em = b"\x00\x02" + bytes(pad[:k - 3 - len(msg)]) + b"\x00" + msg
    c = pow(int.from_bytes(em, "big"), e, n)
    return base64.b64encode(c.to_bytes(k, "big")).decode()


_TTL = 600
_cache: dict[tuple, tuple[dict, float]] = {}


async def login_headers(
    service: str,
    env: str = "test",
    *,
    appid: str | None = None,
    account: dict | None = None,
    account_profile: str | None = None,
    timeout: int = 30,
) -> dict:
    """Login through a configured external framework and return auth headers."""
    import httpx
    from app.services.frameworks.interface_env import account as _account
    from app.services.frameworks.interface_env import login_url as _login_url
    from app.services.frameworks.interface_env import resolve_service_base_url as _resolve

    prof = profile_for(service)
    if not prof:
        raise ValueError(f"No login profile is configured for {service!r}")

    app_id = str(appid or prof.get("appid") or "")
    host = _resolve(service, env) or _resolve(_PLATFORM_ALIAS.get(str(service), ""), env)
    path = _login_url(prof.get("login_key") or "", env)
    if not host or not path:
        raise ValueError(f"{service} is missing host or login path in {env}")

    base_acc = _account(env, account_profile) if account_profile else _account(env)
    acc = {**(base_acc or {}), **(account or {})}
    fields = prof.get("fields") or {}
    cache_identity = next((acc.get(v) for v in fields.values()), service)
    key = (service, app_id, str(cache_identity), env)
    hit = _cache.get(key)
    if hit and hit[1] > time.monotonic():
        return dict(hit[0])

    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        if prof.get("mode") == "rsa_password":
            pk_path = _login_url(prof.get("public_key_login_key") or "public_key", env)
            if not pk_path:
                raise ValueError("rsa_password profile is missing public key endpoint")
            pk = (await client.post(host.rstrip("/") + pk_path, headers={"X-Auth-AppId": app_id})).json().get("data")
            if not pk:
                raise ValueError(f"{service} public key request failed")
            payload = {
                out_name: rsa_encrypt(str(acc.get(in_name) or ""), pk) if out_name == "password" else acc.get(in_name)
                for out_name, in_name in fields.items()
            }
        else:
            payload = {out_name: acc.get(in_name) for out_name, in_name in fields.items()}

        resp = await client.post(
            host.rstrip("/") + path,
            json=payload,
            headers={"Content-Type": "application/json", "X-Auth-AppId": app_id},
        )
        data = (resp.json() or {}).get("data") or {}

    token = data.get("token") or data.get("ticket")
    if not token:
        raise ValueError(f"{service} login did not return a token: {str(resp.text)[:160]}")
    headers = {"X-Auth-Token": token, "X-Auth-AppId": app_id}
    _cache[key] = (dict(headers), time.monotonic() + _TTL)
    return dict(headers)
