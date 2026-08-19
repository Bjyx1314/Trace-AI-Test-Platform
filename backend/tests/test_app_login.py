import asyncio
from types import SimpleNamespace


def test_build_goals_requires_tenant_for_tenant_apps():
    from app.services.runners.app_login import _build_goals

    recipe = SimpleNamespace(env_steps="", restart_after_env=False, needs_tenant=True)
    goals = _build_goals(recipe, env="", phone="10000000000", code="768235", tenant="测试租户")

    assert goals[-1][0].startswith("登录已完成。查看首页【左上角】显示的租户")
    assert goals[-1][1] is False


def test_run_login_checks_tenant_even_when_already_home(monkeypatch):
    import app.services.runners.app_login as al

    recipe = SimpleNamespace(env_steps="", restart_after_env=False, needs_tenant=True)
    calls = {"ensure": 0}

    async def _fake_load_recipe(platform_key, label):
        return recipe

    async def _fake_reached_home(d, provider, tries=3, wait=2.0):
        return True

    async def _fake_ensure_tenant(d, dev_w, dev_h, provider, recipe_obj, tenant, phone, code):
        calls["ensure"] += 1
        assert tenant == "目标租户"
        return False, "未切到目标租户"

    async def _fake_drain(d, rounds=4):
        return None

    monkeypatch.setattr(al, "_load_recipe", _fake_load_recipe)
    monkeypatch.setattr(al, "_reached_home", _fake_reached_home)
    monkeypatch.setattr(al, "_ensure_tenant", _fake_ensure_tenant)
    monkeypatch.setattr(al, "_drain_native_popups", _fake_drain)

    async def _run():
        ok, msg = await al.run_login(
            object(), 1080, 2400, object(), "pkg.demo",
            platform_key="Android App", label="Android App", env="", phone="10000000000", tenant="目标租户",
        )
        assert ok is False
        assert "租户不正确" in msg
        assert calls["ensure"] == 1

    asyncio.run(_run())
