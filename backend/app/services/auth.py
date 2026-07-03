"""认证服务：验证外部 SSO token，管理平台用户角色，签发平台 JWT。"""
from __future__ import annotations
import time
import uuid
import hmac
import hashlib
import json
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.models import PlatformUser
from app.services.password import verify_password


# ── 简易 JWT（不依赖额外库，HS256 手工实现）──────────────────────────────────

def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


def create_platform_jwt(user_id: str, role: str, name: str | None, email: str | None, uid: str | None = None) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "sub": user_id,
        "uid": uid or user_id,  # 平台用户主键(PlatformUser.id)，用于自我校验等
        "role": role,
        "name": name or "",
        "email": email or "",
        "iat": int(time.time()),
        "exp": int(time.time()) + 86400 * 7,  # 7天有效
    }).encode())
    sig_input = f"{header}.{payload}".encode()
    sig = _b64url(hmac.new(settings.jwt_secret.encode(), sig_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def verify_platform_jwt(token: str) -> dict | None:
    """验证平台 JWT，返回 payload 或 None。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, sig = parts
        sig_input = f"{header}.{payload}".encode()
        expected = _b64url(hmac.new(settings.jwt_secret.encode(), sig_input, hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64url_decode(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


# ── 外部 SSO token 验证 ──────────────────────────────────────────────────────

async def verify_external_sso_token(token: str, base_url: str | None = None) -> dict | None:
    """
    调用外部 SSO API 验证 token，返回用户信息 {user_id, email, name}。
    base_url 可由后台配置；不传则使用 config.external_task_api_url。
    """
    if settings.mock_mode:
        # mock：token 直接作为 user_id，name 固定
        return {"user_id": token or "mock-user-001", "email": f"{token or 'mock'}@mock.com",
                "name": "Mock用户", "username": token or "mock-user-001"}

    api_base = (base_url or settings.external_task_api_url or "").rstrip("/")
    if not api_base:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                # SSO 专用换票接口：返回扁平 {id,username,name,email}。
                # （网页用的 /api/auth/me 返回 {user:{...}} 且只认 cookie，不可用于券换票。）
                f"{api_base}/api/auth/sso/userinfo",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "user_id": str(data.get("id") or data.get("user_id") or ""),
                    "email": data.get("email") or "",
                    "name": data.get("name") or data.get("username") or "",
                    # 外部 username 仅作姓名无法转拼音时的账号回退值。
                    "username": data.get("username") or "",
                }
    except Exception:
        pass

    # 券无效、过期或外部 SSO 不通时拒绝，不降级为 mock 用户。
    # 不再降级为 mock 用户——否则任何 token 都能登录，等于绕过 SSO。
    return None


# ── 用户查找/创建 ──────────────────────────────────────────────────────────────

async def ensure_default_admin(db: AsyncSession) -> None:
    """首次启动创建本地管理员；已有账号不会被覆盖。"""
    import logging
    from app.services.password import hash_password
    if not settings.default_admin_password:
        logging.getLogger(__name__).warning(
            "未设置 DEFAULT_ADMIN_PASSWORD，跳过默认管理员创建。"
        )
        return
    existing = (await db.execute(
        select(PlatformUser).where(PlatformUser.username == settings.default_admin_username)
    )).scalar_one_or_none()
    if existing:
        return
    db.add(PlatformUser(
        id=str(uuid.uuid4()),
        username=settings.default_admin_username,
        password_hash=hash_password(settings.default_admin_password),
        name="管理员",
        role="admin",
        is_active=True,
        auth_source="local",
    ))
    await db.commit()
    logging.getLogger(__name__).warning(
        "已创建默认本地管理员账号 '%s'，请尽快登录后修改密码。", settings.default_admin_username
    )


async def get_user_by_username(db: AsyncSession, username: str) -> PlatformUser | None:
    result = await db.execute(select(PlatformUser).where(PlatformUser.username == username))
    return result.scalar_one_or_none()


class AuthError(Exception):
    """登录失败，message 可直接展示给用户。"""


async def authenticate_local_user(db: AsyncSession, username: str, password: str) -> PlatformUser:
    """本地账号密码登录校验。失败抛 AuthError（中文）。"""
    user = await get_user_by_username(db, username)
    if not user or not verify_password(password, user.password_hash):
        raise AuthError("账号或密码错误")
    if not user.is_active:
        raise AuthError("账号已被禁用，请联系管理员")
    # 后续登录不自动改角色：以用户管理里设置的为准
    return user


async def _pick_username(db: AsyncSession, desired: str, self_id: str | None) -> str | None:
    """返回可落库的 username（账号唯一约束保护）：
    无人占用 / 本人占用 → 返回 desired；被【别的账号】占用 → 返回 None（不抢占，避免唯一冲突，调用方保持原值）。"""
    desired = (desired or "").strip()
    if not desired:
        return None
    owner = (await db.execute(
        select(PlatformUser).where(PlatformUser.username == desired)
    )).scalar_one_or_none()
    if owner is None or (self_id and owner.id == self_id):
        return desired
    return None


async def _account_from_name(
    db: AsyncSession, name: str, fallback: str, self_id: str | None,
) -> str | None:
    """账号 = 姓名拼音；拼音取不到则回退外部 SSO username。
    重名拼音冲突时追加序号，本人已占用则保持不变。"""
    from app.services.pinyin_util import name_to_pinyin

    base = name_to_pinyin(name) or (fallback or "").strip()
    if not base:
        return None
    cand = base
    i = 1
    while True:
        owner = (await db.execute(
            select(PlatformUser).where(PlatformUser.username == cand)
        )).scalar_one_or_none()
        if owner is None or (self_id and owner.id == self_id):
            return cand
        i += 1
        cand = f"{base}{i}"


async def _maybe_assign_ai_key(db: AsyncSession, user: PlatformUser) -> None:
    """登录时按姓名自动补 AI key：从 app_settings 的种子映射 ai_key_seed_json({姓名:key}) 取。

    仅在用户当前没有 key 时生效；种子由管理员预先灌入（不入库到 git）。
    """
    if getattr(user, "ai_api_key", None) or not user.name:
        return
    from app.models import AppSetting

    row = (
        await db.execute(select(AppSetting).where(AppSetting.key == "ai_key_seed_json"))
    ).scalar_one_or_none()
    if not row or not row.value:
        return
    try:
        mapping = json.loads(row.value)
    except Exception:
        return
    key = mapping.get(user.name)
    if key:
        user.ai_api_key = key


async def get_or_create_platform_user(
    db: AsyncSession, external_user_id: str, email: str, name: str, username: str = "",
) -> PlatformUser:
    """查找平台用户，不存在则自动创建（默认 role=user）。

    姓名(name)以外部 SSO 的 name 为准；账号(username)统一取姓名拼音，
    而非外部系统工号，重名拼音追加序号。
    """
    result = await db.execute(
        select(PlatformUser).where(PlatformUser.external_user_id == external_user_id)
    )
    user = result.scalar_one_or_none()
    if user:
        # 更新姓名/邮箱/账号（以外部 SSO 为准；账号唯一冲突时保持原值）
        user.email = email or user.email
        user.name = name or user.name
        # 账号统一为姓名拼音，而非外部系统工号
        acct = await _account_from_name(db, user.name, username, user.id)
        if acct:
            user.username = acct
        # 后续登录不自动改角色：以用户管理里设置的为准
        await _maybe_assign_ai_key(db, user)
        await db.commit()
        await db.refresh(user)
        return user

    # 外部 SSO 首次登录一律创建为普通用户，管理员需在用户管理中显式授权。
    user = PlatformUser(
        id=str(uuid.uuid4()),
        external_user_id=external_user_id,
        email=email,
        name=name,
        username=await _account_from_name(db, name, username, None),
        role="user",
    )
    await _maybe_assign_ai_key(db, user)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
