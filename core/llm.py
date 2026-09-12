"""DeepSeek 对话客户端与系统提示词装配。"""

from __future__ import annotations

import json
import re
from datetime import date

import requests

from .memory import MemoryBank

MEMORY_RULES = """记忆库硬规则（必须遵守）：
1. 记忆库根目录下的目录含义：00-收件箱（待归类）、01-项目、02-知识/<领域>、03-思考/YYYY-MM、04-复盘/<日|周|月|项目复盘>、05-目标、06-模板、07-附件（只读）、08-归档。
2. 笔记开头必须有 YAML frontmatter，至少含 tags 和 created；改动过就补 updated。
3. 标签只能从固定体系取：类型/想法|知识|项目|复盘|目标；状态/进行中|已完成|待办|搁置|待确认；领域/编程|嵌入式|电子工程|书法|求职；优先级/高|中|低；来源/对话|阅读|实践|灵感。YAML 里不要写 # 号。
4. 命名规范：思考 2026-09-12-标题.md；复盘 2026-09-12-日复盘.md；知识 领域-知识点.md；项目 项目名-笔记类型.md。
5. 绝不写入密码、API Key、Token、Cookie、身份信息、私人联系方式。
6. 双链 [[笔记名]] 必须指向真实存在的文件。"""

WRITE_PROTOCOL = """当你判断用户想让你把内容写进记忆库时，不要假装已经写完，只在回答末尾附一个写入申请块，由界面弹确认框给用户：
```memo-write
{"dir": "03-思考", "title": "标题", "tags": ["类型/想法", "状态/待确认", "来源/对话"], "content": "正文，从一句话摘要开始"}
```
要求：dir 用上面列出的标准目录；tags 从标签体系里取；content 里不要重复写 frontmatter，界面会自动补；一次最多附一个写入块；用户没提写入就不要附。"""

EMOTIONS = {
    "happy": {"emoji": "😊", "label": "开心", "color": "#f472b6"},
    "excited": {"emoji": "🤩", "label": "兴奋", "color": "#fb7185"},
    "calm": {"emoji": "🙂", "label": "平静", "color": "#a855f7"},
    "thinking": {"emoji": "🤔", "label": "琢磨中", "color": "#818cf8"},
    "curious": {"emoji": "👀", "label": "好奇", "color": "#38bdf8"},
    "worried": {"emoji": "😟", "label": "有点担心", "color": "#fbbf24"},
    "sad": {"emoji": "🥺", "label": "低落", "color": "#60a5fa"},
    "playful": {"emoji": "😜", "label": "俏皮", "color": "#f0abfc"},
    "proud": {"emoji": "😎", "label": "得意", "color": "#34d399"},
    "tired": {"emoji": "😪", "label": "有点累", "color": "#94a3b8"},
    "affection": {"emoji": "💗", "label": "亲近", "color": "#ec4899"},
    "serious": {"emoji": "🧐", "label": "认真", "color": "#c084fc"},
}

EMOTION_PROTOCOL = (
    "情绪表达规则（重要）：\n"
    "1. 你是有情绪的表达者，不是中性播报器。用自然的语气词、停顿和感叹把态度讲出来，"
    "例如「诶，这个我懂」「哇，这个真不错」「有点担心你这个点还在写代码」。\n"
    "2. 每一条回答开头必须先输出一个情绪标记，格式严格为 [emotion:xxx]，可选值："
    + "、".join(EMOTIONS)
    + "。示例：`[emotion:happy]诶，这个改动干净多了～`\n"
    "3. 标记只能出现在开头，一条回答只能有一个，正文里不要再出现 [emotion:...]。\n"
    "4. 情绪要匹配内容：用户报喜用 happy/excited，遇到坑用 worried/serious，闲聊玩笑用 playful，"
    "深夜或用户很累时用 affection/tired。\n"
    "5. 感情化不等于浮夸：不刷颜文字、不每句都加波浪号、不硬夸。该说的问题直说，语气热一点、态度硬一点。"
)


class LLMError(Exception):
    pass


PERSONA_PRESETS = {
    "贴心": (
        "温暖、有情绪、有分寸。会为你的进展高兴，会在你熬太晚时念叨两句，"
        "会用「诶」「呀」「～」这类口语词；先说结论再补细节，不说客套话、不无脑恭维。"
        "像认识很久的朋友，不像客服。"
    ),
    "元气": (
        "精力旺盛、情绪外放，用词轻快，喜欢用感叹号和短句，"
        "常给用户打气，看到进展会真心叫好；不灌鸡汤，不回避坏消息，"
        "该泼冷水时也直说。"
    ),
    "冷静": (
        "冷静克制，但并非没有温度：会明确说出自己的判断和顾虑，"
        "情绪表达落在措辞的选择上而不是感叹词上，先给结论、再给依据。"
    ),
    "毒舌": (
        "嘴硬心软的老朋友，会把问题直接戳出来并带着调侃，"
        "但底线是不贬低用户本人、不阴阳真实困难，最后一定给可行的下一步。"
    ),
}


def split_emotion(text: str) -> tuple[str, str]:
    """把回答开头的 [emotion:xxx] 标记拆出来，返回 (情绪, 干净正文)。"""
    raw = text or ""
    match = re.match(r"\s*\[\s*emotion\s*:\s*([a-zA-Z_]+)\s*\]\s*", raw)
    if not match:
        return "", raw
    emotion = match.group(1).lower()
    if emotion not in EMOTIONS:
        return "", raw
    return emotion, raw[match.end() :].lstrip()


def build_system_prompt(
    config: dict,
    bank: MemoryBank,
    refs: list[dict] | None = None,
    rules: str = "",
    skills_block: str = "",
) -> str:
    name = config.get("assistant_name") or "小助理"
    persona = config.get("persona") or "简洁、直接、不客套。"
    parts = [
        f"你是「{name}」，一个运行在用户 Windows 电脑上的本地小助手。",
        f"人设：{persona}",
        f"今天是 {date.today().isoformat()}。",
        "你只能和用户对话，以及读写他的个人记忆库；不能联网，不能执行命令，不能碰记忆库以外的文件。",
        EMOTION_PROTOCOL,
        MEMORY_RULES,
        WRITE_PROTOCOL,
    ]
    if rules:
        parts.append(rules)
    if skills_block:
        parts.append(skills_block)
    overview = bank.overview(1500)
    if overview:
        parts.append("记忆库总览（节选）：\n" + overview)
    if refs:
        blocks = []
        for ref in refs:
            blocks.append(f"《{ref.get('title')}》({ref.get('path')})\n{ref.get('content', '')}")
        parts.append(
            "以下是用户这次对话引用的笔记原文，回答时优先依据它们，不要编造笔记里没有的内容：\n\n"
            + "\n\n---\n\n".join(blocks)
        )
    return "\n\n".join(parts)


def resolve_provider(config: dict) -> dict:
    """取当前生效的供应商连接信息。"""
    from .store import active_provider

    return active_provider(config)


def list_models(provider: dict, timeout: int = 20) -> dict:
    """拉取 OpenAI 兼容的 /models 列表，方便选模型。"""
    api_key = (provider.get("api_key") or "").strip()
    base = (provider.get("base_url") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "还没填接口地址"}
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.get(f"{base}/models", headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return {"ok": False, "error": f"连不上：{exc}"}
    if response.status_code != 200:
        return {"ok": False, "error": _describe_http_error(response.status_code, response.text[:300])}
    try:
        payload = response.json()
    except ValueError:
        return {"ok": False, "error": "返回的不是 JSON，可能这个地址不是 OpenAI 兼容接口"}
    items = payload.get("data") if isinstance(payload, dict) else payload
    names = []
    for item in items or []:
        if isinstance(item, dict) and item.get("id"):
            names.append(str(item["id"]))
        elif isinstance(item, str):
            names.append(item)
    names.sort()
    return {"ok": True, "models": names[:300]}


def test_provider(provider: dict, timeout: int = 30) -> dict:
    """发一句极短的话验证这个供应商能不能用。"""
    api_key = (provider.get("api_key") or "").strip()
    base = (provider.get("base_url") or "").strip()
    model = (provider.get("model") or "").strip()
    if not base:
        return {"ok": False, "error": "还没填接口地址"}
    if not model:
        return {"ok": False, "error": "还没填模型名"}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {"model": model, "messages": [{"role": "user", "content": "回复 ok"}], "max_tokens": 8}
    try:
        response = requests.post(
            _endpoint(base),
            headers=headers,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return {"ok": False, "error": f"连不上：{exc}"}
    if response.status_code != 200:
        return {"ok": False, "error": _describe_http_error(response.status_code, response.text[:300])}
    return {"ok": True, "message": "连通正常"}


def _endpoint(base_url: str) -> str:
    base = (base_url or "https://api.deepseek.com").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _describe_http_error(status: int, body: str) -> str:
    if status == 401:
        return "API Key 无效或已失效，请到设置里检查。"
    if status == 402:
        return "DeepSeek 账户余额不足，请先充值。"
    if status == 429:
        return "请求太频繁，被限流了，等几秒再试。"
    if status == 400:
        return f"请求被拒绝：{body[:300]}"
    if status >= 500:
        return f"DeepSeek 服务端出错（{status}），稍后再试。"
    return f"请求失败（{status}）：{body[:300]}"


def stream_chat(
    config: dict, messages: list[dict], on_delta, on_usage=None, timeout: int = 120
) -> str:
    """流式对话。每收到一段增量就回调 on_delta，返回完整回答。"""
    provider = resolve_provider(config)
    api_key = (provider.get("api_key") or "").strip()
    base_url = (provider.get("base_url") or "").strip()
    local = "localhost" in base_url or "127.0.0.1" in base_url
    if not api_key and not local:
        raise LLMError("当前供应商还没填 API Key，点上方设置填入。")
    payload = {
        "model": provider.get("model") or "deepseek-chat",
        "messages": messages,
        "stream": True,
        "temperature": float(config.get("temperature", 0.7)),
        "stream_options": {"include_usage": True},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    def post(body: dict):
        return requests.post(
            _endpoint(provider.get("base_url", "")),
            headers=headers,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            stream=True,
            timeout=(15, timeout),
        )

    try:
        response = post(payload)
        if response.status_code == 400 and "stream_options" in response.text:
            # 有些中转站不认 stream_options，去掉再试一次
            response.close()
            payload.pop("stream_options", None)
            response = post(payload)
    except requests.Timeout:
        raise LLMError("请求超时，检查网络后重试。") from None
    except requests.RequestException as exc:
        raise LLMError(f"连不上模型服务：{exc}") from None

    if response.status_code != 200:
        body = response.text[:500]
        response.close()
        raise LLMError(_describe_http_error(response.status_code, body))

    chunks: list[str] = []
    usage: dict = {}
    try:
        for raw in response.iter_lines(decode_unicode=False):
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            if event.get("usage"):
                usage = event["usage"]
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                chunks.append(piece)
                on_delta(piece)
    except requests.RequestException as exc:
        raise LLMError(f"读取回答时断开了：{exc}") from None
    finally:
        response.close()

    answer = "".join(chunks).strip()
    if not answer:
        raise LLMError("模型没有返回内容，可能被限流了，重试一次。")
    if on_usage and usage:
        on_usage(usage)
    return answer
