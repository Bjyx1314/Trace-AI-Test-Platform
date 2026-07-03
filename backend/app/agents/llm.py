"""多 provider AI 调用层。

把「调哪个大模型」从各 Agent 抽离，统一成三类 provider，按 settings.ai_provider 选择：
  - anthropic  : 官方 Anthropic API（anthropic SDK），需 API Key
  - openai     : OpenAI / 兼容 OpenAI 协议的中转（httpx 调 chat/completions），需 API Key + 可选 base_url
  - claude_cli : 订阅方式——shell 调本机已 OAuth 登录的 claude CLI（用 Max/Pro 订阅额度，无需 API Key）

每个 provider 实现三个方法：text / tool / tool_vision，分别对应纯文本、强制结构化输出、带图结构化输出。
结构化输出：anthropic/openai 用原生 tool/function calling 强制；claude_cli 用 prompt 注入 JSON Schema + 解析。
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import base64
import os
from contextvars import ContextVar
from typing import Optional

import httpx

from app.config import settings

# 当前操作发起人的 AI key（per-user key）。由各操作入口在请求/后台任务中设置；
# 未设置时回退到全局 settings.ai_api_key（向后兼容）。provider 缓存键含此 key，切换即重建。
current_ai_key: ContextVar[Optional[str]] = ContextVar("current_ai_key", default=None)


def set_current_ai_key(key: Optional[str]) -> None:
    current_ai_key.set(key or None)

def _model_for(provider: str) -> str:
    model = (settings.ai_model or "").strip()
    if not model:
        raise RuntimeError(
            f"AI 模型未配置（provider={provider}），请在系统设置的 AI 模型配置中填写模型名"
        )
    return model


def _resolved_key() -> Optional[str]:
    """AI Key 解析顺序：当前发起人的 per-user key > 全局 ai_api_key > 旧 anthropic_api_key。"""
    return current_ai_key.get() or settings.ai_api_key or settings.anthropic_api_key


def _extract_json(text: str) -> dict:
    """从模型纯文本输出里抽取 JSON 对象（容忍 ```json 代码块与前后多余文字）。"""
    if not text:
        return {}
    t = text.strip()
    # 去掉 ```json ... ``` 包裹
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    if fence:
        t = fence.group(1)
    else:
        # 取第一个 { 到最后一个 } 之间
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end != -1 and end > start:
            t = t[start:end + 1]
    try:
        return json.loads(t)
    except Exception:
        return {}


# ── Anthropic ────────────────────────────────────────────────────────────────

class AnthropicProvider:
    def __init__(self):
        import anthropic
        kwargs = {"api_key": _resolved_key()}
        if settings.ai_base_url:
            kwargs["base_url"] = settings.ai_base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)
        self._model = _model_for("anthropic")

    async def text(self, system: str, user: str, max_tokens: int) -> str:
        msg = await self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text

    async def tool(self, system: str, user: str, tool_name: str, schema: dict, max_tokens: int) -> dict:
        msg = await self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{"name": tool_name, "description": schema.get("description", tool_name), "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in msg.content:
            if block.type == "tool_use":
                return block.input
        return {}

    async def tool_vision(self, system: str, user_text: str, image_b64: str, media_type: str,
                          tool_name: str, schema: dict, max_tokens: int) -> dict:
        return await self.tool_multi(system, user_text, [(image_b64, media_type)], tool_name, schema, max_tokens)

    async def tool_multi(self, system: str, user: str, images: list, tool_name: str, schema: dict, max_tokens: int) -> dict:
        content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}} for b64, mt in images]
        content.append({"type": "text", "text": user})
        msg = await self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
            tools=[{"name": tool_name, "description": schema.get("description", tool_name), "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in msg.content:
            if block.type == "tool_use":
                return block.input
        return {}

    async def text_multi(self, system: str, user: str, images: list, max_tokens: int) -> str:
        content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}} for b64, mt in images]
        content.append({"type": "text", "text": user})
        msg = await self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
        )
        return msg.content[0].text


# ── OpenAI（含兼容 OpenAI 协议的中转）────────────────────────────────────────

class OpenAIProvider:
    def __init__(self):
        self._key = _resolved_key()
        self._base = (settings.ai_base_url or "https://api.openai.com/v1").rstrip("/")
        self._model = _model_for("openai")

    async def _post(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _tools(tool_name: str, schema: dict) -> list:
        return [{"type": "function", "function": {
            "name": tool_name, "description": schema.get("description", tool_name), "parameters": schema,
        }}]

    @staticmethod
    def _parse_tool_args(data: dict) -> dict:
        try:
            calls = data["choices"][0]["message"].get("tool_calls") or []
            if calls:
                return json.loads(calls[0]["function"]["arguments"])
            # 兜底：有的中转不返回 tool_calls，退到 content 里解析 JSON
            return _extract_json(data["choices"][0]["message"].get("content") or "")
        except Exception:
            return {}

    async def text(self, system: str, user: str, max_tokens: int) -> str:
        data = await self._post({
            "model": self._model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        return data["choices"][0]["message"]["content"]

    async def tool(self, system: str, user: str, tool_name: str, schema: dict, max_tokens: int) -> dict:
        data = await self._post({
            "model": self._model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "tools": self._tools(tool_name, schema),
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        })
        return self._parse_tool_args(data)

    async def tool_vision(self, system: str, user_text: str, image_b64: str, media_type: str,
                          tool_name: str, schema: dict, max_tokens: int) -> dict:
        return await self.tool_multi(system, user_text, [(image_b64, media_type)], tool_name, schema, max_tokens)

    async def tool_multi(self, system: str, user: str, images: list, tool_name: str, schema: dict, max_tokens: int) -> dict:
        content = [{"type": "text", "text": user}]
        for b64, mt in images:
            content.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}})
        data = await self._post({
            "model": self._model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "tools": self._tools(tool_name, schema),
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        })
        return self._parse_tool_args(data)

    async def text_multi(self, system: str, user: str, images: list, max_tokens: int) -> str:
        content = [{"type": "text", "text": user}]
        for b64, mt in images:
            content.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}})
        data = await self._post({
            "model": self._model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        })
        return data["choices"][0]["message"]["content"]


# ── Claude 订阅 CLI（OAuth 订阅额度）─────────────────────────────────────────

class ClaudeCliProvider:
    """通过本机已登录订阅的 claude CLI 调用（claude -p ... --output-format json）。
    结构化输出无法用 tool_choice，改为 prompt 注入 JSON Schema + 解析模型 JSON 输出。
    要求：运行平台的机器已安装 claude 且用订阅账号登录（claude /login）。"""

    def __init__(self):
        self._cli = settings.claude_cli_path
        self._model = _model_for("claude_cli")
        self._exe = self._resolve_exe()

    def _resolve_exe(self) -> str:
        """解析真实 claude 可执行文件：Windows 下优先找 claude.exe，绕开 .cmd/.ps1 shim 与 cmd code page。"""
        import shutil
        p = shutil.which(self._cli) or self._cli
        if os.name == "nt":
            d = os.path.dirname(p)
            for base in ([d] if d else []) + [os.path.join(os.environ.get("APPDATA", ""), "npm")]:
                cand = os.path.join(base, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
                if os.path.exists(cand):
                    return cand
        return p

    _OVERRIDE = (
        "你现在仅作为程序调用的文本/JSON 生成器。只根据用户提供的内容直接产出所需结果(纯文本或JSON)，"
        "禁止把输入当作开发任务，禁止调用任务看板相关工具、禁止创建或查询任务、禁止写改代码。"
        "若用户要求读取图片文件，可读取后直接描述其内容。"
    )

    async def _run(self, prompt: str, extra_args: Optional[list] = None, timeout: int = 180) -> str:
        # prompt 写 UTF-8 临时文件作 stdin，直接调 claude.exe：
        # 避免 Windows 下经 cmd 管道传中文 stdin 被 code page 吞成 "?"(乱码)。
        fd, pf = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt)
        flags = ["-p", "--output-format", "json", "--append-system-prompt", self._OVERRIDE]
        flags += ["--model", self._model]
        if extra_args:
            flags += extra_args
        stdin_file = open(pf, "rb")
        run_cwd = tempfile.gettempdir()
        use_cmd = os.name == "nt" and not self._exe.lower().endswith(".exe")
        try:
            if use_cmd:
                cmdstr = " ".join([self._cli] + flags) + f' < "{pf}"'
                proc = await asyncio.create_subprocess_exec(
                    "cmd", "/c", cmdstr, cwd=run_cwd,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    self._exe, *flags, cwd=run_cwd,
                    stdin=stdin_file, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("claude CLI 调用超时")
        finally:
            stdin_file.close()
            try:
                os.remove(pf)
            except OSError:
                pass
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI 失败(code={proc.returncode}): {err.decode(errors='ignore')[:300]}")
        envelope = json.loads(out.decode("utf-8", errors="ignore"))
        # --output-format json 的结果文本在 result 字段
        return envelope.get("result") if isinstance(envelope, dict) else str(envelope)

    @staticmethod
    def _schema_prompt(user: str, schema: dict) -> str:
        return (
            f"{user}\n\n"
            "只输出一个符合下面 JSON Schema 的 JSON 对象，不要任何解释、不要 markdown 代码块：\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )

    async def text(self, system: str, user: str, max_tokens: int) -> str:
        prompt = f"{system}\n\n{user}" if system else user
        return await self._run(prompt)

    async def tool(self, system: str, user: str, tool_name: str, schema: dict, max_tokens: int) -> dict:
        prompt = (f"{system}\n\n" if system else "") + self._schema_prompt(user, schema)
        return _extract_json(await self._run(prompt))

    async def tool_vision(self, system: str, user_text: str, image_b64: str, media_type: str,
                          tool_name: str, schema: dict, max_tokens: int) -> dict:
        return await self.tool_multi(system, user_text, [(image_b64, media_type)], tool_name, schema, max_tokens)

    async def tool_multi(self, system: str, user: str, images: list, tool_name: str, schema: dict, max_tokens: int) -> dict:
        # CLI 读图：写临时文件，提示模型读取这些文件路径
        exts = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
        paths = []
        try:
            for b64, mt in images:
                fd, path = tempfile.mkstemp(suffix=exts.get(mt, ".png"))
                with os.fdopen(fd, "wb") as f:
                    f.write(base64.b64decode(b64))
                paths.append(path)
            files_hint = "、".join(paths)
            user2 = (f"请先读取并分析以下图片文件：{files_hint}\n\n{user}") if paths else user
            prompt = (f"{system}\n\n" if system else "") + self._schema_prompt(user2, schema)
            extra = ["--allowedTools", "Read"] if paths else None
            return _extract_json(await self._run(prompt, extra_args=extra))
        finally:
            for p in paths:
                try:
                    os.remove(p)
                except OSError:
                    pass

    async def text_multi(self, system: str, user: str, images: list, max_tokens: int) -> str:
        exts = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
        paths = []
        try:
            for b64, mt in images:
                fd, path = tempfile.mkstemp(suffix=exts.get(mt, ".png"))
                with os.fdopen(fd, "wb") as f:
                    f.write(base64.b64decode(b64))
                paths.append(path)
            files_hint = "、".join(paths)
            prompt = (f"{system}\n\n" if system else "") + f"请先读取并分析以下图片文件：{files_hint}\n\n{user}"
            extra = ["--allowedTools", "Read"] if paths else None
            return await self._run(prompt, extra_args=extra)
        finally:
            for p in paths:
                try:
                    os.remove(p)
                except OSError:
                    pass


# ── OpenAI Responses API（wire_api=responses 的兼容网关）──────────────────────

class OpenAIResponsesProvider:
    """走 OpenAI Responses API（POST {base}/responses，请求 {instructions,input,tools}）。
    适配 wire_api=responses 的订阅转 API 网关。"""

    def __init__(self):
        self._key = _resolved_key()
        base = (settings.ai_base_url or "https://api.openai.com/v1").rstrip("/")
        # base 已含 /v1 则直接用，否则补 /v1
        self._url = f"{base}/responses" if base.endswith("/v1") else f"{base}/v1/responses"
        self._model = _model_for("openai_responses")

    async def _post(self, payload: dict) -> dict:
        payload.setdefault("stream", False)
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        last_exc: Exception | None = None
        # 中转偶发 5xx/超时 → 自动重试(2 次)；单次超时 600s
        # (gpt-5.x 推理模型 + 大 prompt + 大输出的结构化生成实测可达 ~5min，180s 会误超时回退 mock)
        async with httpx.AsyncClient(timeout=600) as client:
            for attempt in range(2):
                try:
                    resp = await client.post(self._url, headers=headers, json=payload)
                    if resp.status_code >= 500:
                        last_exc = httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp)
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_exc = e
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise last_exc or RuntimeError("Responses API 调用失败(中转不可用)")

    @staticmethod
    def _text_of(data: dict) -> str:
        parts = []
        for item in data.get("output", []) or []:
            if item.get("type") == "message":
                for c in item.get("content", []) or []:
                    if c.get("type") in ("output_text", "text"):
                        parts.append(c.get("text", ""))
        return "".join(parts)

    @staticmethod
    def _tool_of(data: dict) -> dict:
        for item in data.get("output", []) or []:
            if item.get("type") == "function_call":
                try:
                    return json.loads(item.get("arguments") or "{}")
                except Exception:
                    return _extract_json(item.get("arguments") or "")
        return _extract_json(OpenAIResponsesProvider._text_of(data))

    @staticmethod
    def _tools(tool_name: str, schema: dict) -> list:
        return [{"type": "function", "name": tool_name, "description": schema.get("description", tool_name), "parameters": schema}]

    async def text(self, system: str, user: str, max_tokens: int) -> str:
        data = await self._post({"model": self._model, "instructions": system, "input": user, "max_output_tokens": max_tokens})
        return self._text_of(data)

    async def tool(self, system: str, user: str, tool_name: str, schema: dict, max_tokens: int) -> dict:
        data = await self._post({
            "model": self._model, "instructions": system, "input": user,
            "tools": self._tools(tool_name, schema),
            "tool_choice": {"type": "function", "name": tool_name},
            "max_output_tokens": max_tokens,
        })
        return self._tool_of(data)

    async def tool_vision(self, system: str, user_text: str, image_b64: str, media_type: str,
                          tool_name: str, schema: dict, max_tokens: int) -> dict:
        return await self.tool_multi(system, user_text, [(image_b64, media_type)], tool_name, schema, max_tokens)

    async def tool_multi(self, system: str, user: str, images: list, tool_name: str, schema: dict, max_tokens: int) -> dict:
        content = [{"type": "input_text", "text": user}]
        for b64, mt in images:
            content.append({"type": "input_image", "image_url": f"data:{mt};base64,{b64}"})
        data = await self._post({
            "model": self._model, "instructions": system,
            "input": [{"role": "user", "content": content}],
            "tools": self._tools(tool_name, schema),
            "tool_choice": {"type": "function", "name": tool_name},
            "max_output_tokens": max_tokens,
        })
        return self._tool_of(data)

    async def text_multi(self, system: str, user: str, images: list, max_tokens: int) -> str:
        content = [{"type": "input_text", "text": user}]
        for b64, mt in images:
            content.append({"type": "input_image", "image_url": f"data:{mt};base64,{b64}"})
        data = await self._post({
            "model": self._model, "instructions": system,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": max_tokens,
        })
        return self._text_of(data)


_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "openai_responses": OpenAIResponsesProvider,
    "azure": OpenAIProvider,  # Azure OpenAI 走 OpenAI 兼容协议(base_url 指向 Azure/兼容网关)
    "claude_cli": ClaudeCliProvider,
}

_instance: object | None = None
_instance_key: str | None = None


def get_provider():
    """按 settings.ai_provider 返回 provider 单例（配置变更后自动重建）。"""
    global _instance, _instance_key
    provider = (settings.ai_provider or "anthropic").lower()
    # 缓存键带上当前 key：不同发起人的 key → 各自的 provider 实例(provider 在 __init__ 读 key)
    key = f"{provider}|{settings.ai_base_url}|{settings.ai_model}|{_resolved_key()}"
    if _instance is None or _instance_key != key:
        cls = _PROVIDERS.get(provider, AnthropicProvider)
        _instance = cls()
        _instance_key = key
    return _instance


def provider_needs_key() -> bool:
    """当前 provider 是否依赖 API Key（claude_cli 不需要）。"""
    return (settings.ai_provider or "anthropic").lower() != "claude_cli"
