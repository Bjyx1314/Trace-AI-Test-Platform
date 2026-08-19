"""认证服务：验证 external task system token，管理平台用户角色，签发平台 JWT。"""
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


# ── external task system token 验证（现阶段 mock）──────────────────────────────────────

def _external_task_user_id(data: dict) -> str:
    """从 SSO userinfo 响应中取平台用于绑定用户的外部 id。

    subkey 是当前稳定身份口径；保留 id/user_id 作为旧接口兜底。
    """
    return str(
        data.get("subkey")
        or data.get("subKey")
        or data.get("sub_key")
        or data.get("id")
        or data.get("user_id")
        or ""
    ).strip()


def _external_task_legacy_user_ids(data: dict) -> list[str]:
    primary = _external_task_user_id(data)
    ids: list[str] = []
    for key in ("id", "user_id"):
        value = str(data.get(key) or "").strip()
        if value and value != primary and value not in ids:
            ids.append(value)
    return ids

async def verify_external_task_token(token: str, base_url: str | None = None) -> dict | None:
    """
    调用 external task system API 验证 token，返回用户信息 {user_id, email, name}。
    base_url = SSO 对接认证地址(后台可配,见 services/app_settings.resolve_external_task_url);
    不传则回落 config.external_task_api_url。token 无效返回 None。
    """
    if settings.mock_mode:
        # mock：token 直接作为 user_id，name 固定
        return {"user_id": token or "mock-user-001", "email": f"{token or 'mock'}@mock.com",
                "name": "Mock用户", "username": token or "mock-user-001"}

    api_base = (base_url or settings.external_task_api_url).rstrip("/")
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
                user_id = _external_task_user_id(data)
                if not user_id:
                    return None
                return {
                    "user_id": user_id,
                    "email": data.get("email") or "",
                    "name": data.get("name") or data.get("username") or "",
                    # 账号：以 external task system 的 username 为准，落库到平台 username 字段
                    "username": data.get("username") or "",
                    "legacy_user_ids": _external_task_legacy_user_ids(data),
                }
    except Exception:
        pass

    # 券无效/过期或 external task system 不通：拒绝（返回 None → verify 接口 401）。
    # 不再降级为 mock 用户——否则任何 token 都能登录，等于绕过 SSO。
    return None


# ── 用户查找/创建 ──────────────────────────────────────────────────────────────

# 内置管理员：按姓名命中即固定为 admin（登录时生效，不会被降级）。
BUILTIN_ADMIN_NAMES = {"张三"}


def _is_builtin_admin(name: str | None, email: str | None = None) -> bool:
    return bool(name and name.strip() in BUILTIN_ADMIN_NAMES)


async def ensure_default_admin(db: AsyncSession) -> None:
    """首次启动若不存在任何本地管理员，创建默认 admin，保证 external task system 不可用时也能登录。"""
    import logging
    from app.services.password import hash_password
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
    """账号 = 姓名拼音(如 张三→zhangsan)；拼音取不到则回退 external task system username。
    重名拼音冲突时追加序号(zhangsan2…)，本人已占用则保持不变。"""
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


def _legacy_sso_match_keys(name: str, username: str, email: str) -> list[tuple[str, str]]:
    """历史用户缺外部 id 时的补偿匹配键，按更可靠的账号/邮箱优先。"""
    from app.services.pinyin_util import name_to_pinyin

    keys: list[tuple[str, str]] = []
    for field, value in (
        ("username", (username or "").strip()),
        ("username", name_to_pinyin(name) or ""),
        ("email", (email or "").strip()),
    ):
        if value and (field, value) not in keys:
            keys.append((field, value))
    return keys


async def _find_legacy_sso_user(
    db: AsyncSession, *, email: str, name: str, username: str,
) -> PlatformUser | None:
    """找历史上没有 external_task_user_id 的同一人账号，用于登录时回填 subkey。

    只匹配 external id 为空的记录，不抢占已经绑定过其它 SSO 身份的账号。
    """
    for field, value in _legacy_sso_match_keys(name, username, email):
        col = getattr(PlatformUser, field)
        rows = (await db.execute(
            select(PlatformUser).where(
                PlatformUser.external_task_user_id.is_(None),
                col == value,
            )
        )).scalars().all()
        if len(rows) == 1:
            return rows[0]

    # 姓名可能重名，只有唯一命中时才兜底。
    clean_name = (name or "").strip()
    if clean_name:
        rows = (await db.execute(
            select(PlatformUser).where(
                PlatformUser.external_task_user_id.is_(None),
                PlatformUser.name == clean_name,
            )
        )).scalars().all()
        if len(rows) == 1:
            return rows[0]
    return None


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
    db: AsyncSession, external_task_user_id: str, email: str, name: str, username: str = "",
    legacy_user_ids: list[str] | None = None,
) -> PlatformUser:
    """查找平台用户，不存在则自动创建（默认 role=user）。

    姓名(name)以 external task system 的 name 为准；账号(username)统一取姓名拼音(如 zhangsan)，
    而非 external task system 工号，重名拼音追加序号。
    """
    result = await db.execute(
        select(PlatformUser).where(PlatformUser.external_task_user_id == external_task_user_id)
    )
    user = result.scalar_one_or_none()
    if user is None and legacy_user_ids:
        user = (await db.execute(
            select(PlatformUser).where(PlatformUser.external_task_user_id.in_(legacy_user_ids))
        )).scalar_one_or_none()
        if user is not None:
            user.external_task_user_id = external_task_user_id
    if user is None and external_task_user_id:
        user = await _find_legacy_sso_user(db, email=email, name=name, username=username)
        if user is not None:
            user.external_task_user_id = external_task_user_id
    if user:
        # 更新姓名/邮箱/账号（以 external task system 为准；账号唯一冲突时保持原值）
        user.email = email or user.email
        user.name = name or user.name
        # 账号统一为姓名拼音(zhangsan)，而非 external task system 工号
        acct = await _account_from_name(db, user.name, username, user.id)
        if acct:
            user.username = acct
        # 后续登录不自动改角色：以用户管理里设置的为准
        await _maybe_assign_ai_key(db, user)
        await db.commit()
        await db.refresh(user)
        return user

    # 初次登录(创建账号)才自动定角色：内置管理员名单→admin，其余默认普通用户
    user = PlatformUser(
        id=str(uuid.uuid4()),
        external_task_user_id=external_task_user_id,
        email=email,
        name=name,
        username=await _account_from_name(db, name, username, None),
        role="admin" if _is_builtin_admin(name, email) else "user",
    )
    await _maybe_assign_ai_key(db, user)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
