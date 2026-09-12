/* 程序员模式 / 技能库 / 记忆库切换 / 助手规则 / 模型供应商 */

/* ===== 会话 ===== */

async function newSession(project) {
  const workspace = project ? project.path : "";
  state.session = await api().session_new("chat", workspace, state.prog.default_sandbox || "workspace-write");
  if (project) {
    state.session = await api().session_set_mode({
      session_id: state.session.id,
      mode: "prog",
      workspace: project.path,
    }).then((result) => (result.ok ? result.session : state.session));
    await api().session_rename(state.session.id, project.name);
    const loaded = await api().session_load(state.session.id);
    if (loaded.ok) state.session = loaded.session;
  }
  state.refs = [];
  state.selectedSkills = [];
  await refreshSessions();
  renderRefs();
  renderSkillChips();
  updateModeBar();
  switchTab("sessions");
}

async function setMode(mode) {
  if (!state.session) return;
  if (mode !== "chat" && !state.prog.ok) {
    toast(state.prog.error || "本机没找到 codex.exe", true);
    return;
  }
  if (mode !== "chat" && !state.prog.notice_shown) {
    const ok = await askConfirm(
      mode === "plan" ? "先说一下计划模式" : "先说一下天才程序员模式",
      mode === "plan"
        ? "它调用本机的 Codex，但强制只读：只调研、只出方案，不会改任何文件。\n\n" +
          "每跑一轮大概消耗 2 万到 20 万 token，比聊天模式贵不少。"
        : "它调用本机的 Codex，会真实读写文件、执行命令。\n\n" +
          "默认权限是「可改工作目录」，只影响选定的那个目录。\n" +
          "每跑一轮大概消耗 2 万到 20 万 token，比聊天模式贵不少。",
      "知道了"
    );
    if (!ok) return;
    state.prog.notice_shown = true;
    api().prog_notice_seen();
  }
  const result = await api().session_set_mode({
    session_id: state.session.id,
    mode,
    workspace: state.session.workspace || state.config.memory_root || state.dataDir || "",
    sandbox: state.session.sandbox || state.prog.default_sandbox,
  });
  if (!result.ok) {
    toast(result.error || "切换失败", true);
    return;
  }
  state.session = result.session;
  updateModeBar();
  renderMessages();
  const labels = { chat: "聊天", plan: "计划（只读，只出方案）", prog: "天才程序员" };
  toast("已切到「" + labels[mode] + "」，还是同一个对话");
}

async function pickWorkspace() {
  const result = await api().prog_pick_workspace();
  if (result.ok) return result.path;
  if (result.error === "cancelled") return null;
  const typed = window.prompt(
    "选择框打不开，直接把目录路径粘进来：",
    (state.session && state.session.workspace) || ""
  );
  return typed && typed.trim() ? typed.trim() : null;
}

/* ===== 程序员模式 ===== */

function sandboxLabel(key) {
  const found = (state.prog.sandboxes || []).find((item) => item.key === key);
  return found ? found.label : key;
}

function fillSandboxOptions() {
  const select = $("progSandbox");
  select.innerHTML = "";
  (state.prog.sandboxes || []).forEach((item) => {
    const option = el("option", "", item.label);
    option.value = item.key;
    select.appendChild(option);
  });
}

function updateModeBar() {
  const session = state.session || {};
  const mode = session.mode || "chat";
  const isProg = mode === "prog";
  const isPlan = mode === "plan";
  $("modeChatBtn").classList.toggle("active", mode === "chat");
  $("modePlanBtn").classList.toggle("active", isPlan);
  $("modeProgBtn").classList.toggle("active", isProg);
  $("progTools").classList.toggle("hidden", !(isProg || isPlan));
  if (!isProg && !isPlan) return;
  $("progWorkspaceBtn").textContent = session.workspace
    ? "📁 " + session.workspace
    : "📁 未选工作目录";
  $("progWorkspaceBtn").title = session.workspace || "点击选择";
  // 计划模式强制只读，权限下拉换成固定标识
  $("progSandbox").classList.toggle("hidden", isPlan);
  $("planBadge").classList.toggle("hidden", !isPlan);
  $("progSandbox").value = session.sandbox || state.prog.default_sandbox || "workspace-write";
  $("progStopBtn").disabled = !state.progRunning;
}

function showUsage(usage) {
  if (!usage || !usage.input_tokens) {
    $("progUsageChip").textContent = "—";
    return;
  }
  const input = usage.input_tokens || 0;
  const cached = usage.cached_input_tokens || 0;
  const output = usage.output_tokens || 0;
  $("progUsageChip").textContent = `↑${input.toLocaleString()} ↓${output.toLocaleString()}`;
  $("progUsageChip").title = `输入 ${input}（命中缓存 ${cached}），输出 ${output}`;
}

function appendProgEvent(container, event) {
  if (!event || !container) return;
  if (event.kind === "status") {
    const line = el("div", "prog-status");
    line.appendChild(el("span", "ev-tag", "状态"));
    line.appendChild(el("span", "", event.text || ""));
    container.appendChild(line);
  } else if (event.kind === "command") {
    const details = el("details", "ev");
    const summary = el("summary");
    const ok = event.exit_code === 0 || event.exit_code === null;
    summary.appendChild(el("span", "ev-tag" + (ok ? " ok" : " bad"), "命令"));
    const short = (event.command || "").replace(/\s+/g, " ").slice(0, 110);
    summary.appendChild(el("span", "", short));
    details.appendChild(summary);
    const body = el("pre", "ev-body");
    body.textContent =
      "$ " + (event.command || "") + "\n\n" + (event.output || "（无输出）") +
      (event.exit_code !== null && event.exit_code !== undefined ? `\n\n[退出码 ${event.exit_code}]` : "");
    details.appendChild(body);
    container.appendChild(details);
  } else if (event.kind === "note") {
    const details = el("details", "ev slim");
    const summary = el("summary");
    summary.appendChild(el("span", "ev-tag", event.label || "步骤"));
    summary.appendChild(el("span", "", "查看详情"));
    details.appendChild(summary);
    const body = el("pre", "ev-body");
    body.textContent = event.text || "";
    details.appendChild(body);
    container.appendChild(details);
  } else if (event.kind === "error") {
    const details = el("details", "ev");
    const summary = el("summary");
    summary.appendChild(el("span", "ev-tag bad", "错误"));
    summary.appendChild(el("span", "", (event.text || "").slice(0, 110)));
    details.appendChild(summary);
    const body = el("pre", "ev-body");
    body.textContent = event.text || "";
    details.appendChild(body);
    container.appendChild(details);
  }
}

/* ===== 执行中的实时反馈 ===== */

function startProgLive(bubble) {
  const card = el("div", "prog-live");
  card.appendChild(el("span", "spinner"));
  const text = el("span", "prog-live-text", "正在启动 Codex…");
  const time = el("span", "prog-live-time", "0s");
  card.appendChild(text);
  card.appendChild(time);
  const stop = el("button", "btn tiny danger prog-live-stop", "停止");
  stop.title = "停掉这一轮";
  stop.onclick = () => progStop();
  card.appendChild(stop);
  bubble.appendChild(card);

  // 命令统一收进一个可折叠分组，默认收起，只有标题上的步数在动
  const group = el("details", "ev-group");
  group.open = false; // 默认收起：想看过程再点开
  const summary = el("summary");
  summary.appendChild(el("span", "ev-tag", "步骤"));
  const summaryText = el("span", "ev-group-text", "正在执行…");
  summary.appendChild(summaryText);
  group.appendChild(summary);
  const box = el("div", "ev-list");
  group.appendChild(box);
  bubble.appendChild(group);

  const startedAt = Date.now();
  const timer = setInterval(() => {
    if (time.isConnected) {
      time.textContent = Math.round((Date.now() - startedAt) / 1000) + "s";
    } else {
      clearInterval(timer);
    }
  }, 1000);
  state.progLive = { card, text, time, timer, group, summaryText, steps: 0 };
  state.progEventBox = box;
  state.progStarted = true;
}

function setProgLiveText(text) {
  if (state.progLive) state.progLive.text.textContent = text;
}

function stopProgLive() {
  if (!state.progLive) return;
  clearInterval(state.progLive.timer);
  if (state.progLive.card.isConnected) state.progLive.card.remove();
  state.progLive = null;
  state.progEventBox = null;
}

function describeEvent(event) {
  if (!event) return "";
  if (event.kind === "command") {
    const short = (event.command || "").replace(/\s+/g, " ").slice(0, 60);
    if (event.status === "in_progress") return "正在执行命令：" + short;
    const code = event.exit_code;
    return `命令已结束（退出码 ${code === null || code === undefined ? "?" : code}）：` + short;
  }
  if (event.kind === "message") return "正在整理回答…";
  if (event.kind === "note") return "正在改动文件…";
  if (event.kind === "status") return event.text || "";
  return "";
}

async function renderProgAnswer(bubble, message) {
  const text = stripEmotion((message && message.content) || "");
  if (text) {
    const label = el("div", "answer-label", "回答");
    bubble.appendChild(label);
    const answer = el("div", "prog-answer md");
    answer.innerHTML = await api().render_markdown(text);
    attachCopyButtons(answer);
    bubble.appendChild(answer);
  }
  if (message && message.usage) {
    const chip = el("div", "usage-chip");
    const input = message.usage.input || 0;
    chip.textContent = `本轮输入 ${input.toLocaleString()} token（命中缓存 ${(message.usage.cached || 0).toLocaleString()}），输出 ${(message.usage.output || 0).toLocaleString()}`;
    bubble.appendChild(chip);
  }
}

/** 历史回放：程序员模式的消息也按「折叠步骤 → 回答在最下」渲染。 */
async function renderProgMessage(message) {
  const row = el("div", "msg assistant");
  row.appendChild(el("div", "avatar", message.mode === "plan" ? "📋" : "⌨"));
  const bubble = el("div", "bubble prog-bubble");
  row.appendChild(bubble);
  $("messages").appendChild(row);
  await renderProgEvents(bubble, message);
}

async function renderProgEvents(bubble, message) {
  if (!message || !message.events || !message.events.length) return;
  const commands = message.events.filter((event) => event.kind === "command");
  const others = message.events.filter((event) => event.kind !== "command");
  if (commands.length || others.length) {
    const group = el("details", "ev-group");
    const summary = el("summary");
    summary.appendChild(el("span", "ev-tag", "步骤"));
    const label = commands.length ? `执行了 ${commands.length} 步，点击展开` : "过程，点击展开";
    summary.appendChild(el("span", "ev-group-text", label));
    group.appendChild(summary);
    const box = el("div", "ev-list");
    message.events.forEach((event) => appendProgEvent(box, event));
    group.appendChild(box);
    bubble.appendChild(group);
  }
  await renderProgAnswer(bubble, message);
  if (message.mode === "plan") addPlanExecuteButton(bubble);
}

/** 计划跑完之后，给一个「按这个计划执行」的入口，切到执行模式并带上方案。 */
function addPlanExecuteButton(bubble) {
  const row = el("div", "note-save-row");
  const button = el("button", "btn primary tiny", "按这个计划执行");
  button.title = "切到天才程序员模式，把这份计划交给它去执行";
  button.onclick = async () => {
    await setMode("prog");
    $("input").value = "按上面的方案开始执行。";
    sendMessage();
  };
  row.appendChild(button);
  row.appendChild(el("small", "hint", "会切到天才程序员模式，权限恢复成你选的那档"));
  bubble.appendChild(row);
}

async function progSend(text) {
  if (state.progRunning) {
    toast("上一轮还在跑，先点停止或等它结束", true);
    return;
  }
  if (!state.session.workspace) {
    const workspace = await pickWorkspace();
    if (!workspace) {
      toast("程序员模式必须先选一个工作目录", true);
      return;
    }
    const updated = await api().prog_configure({ session_id: state.session.id, workspace });
    if (updated.ok && updated.session) state.session = updated.session;
    updateModeBar();
  }
  const input = $("input");
  input.value = "";
  input.style.height = "auto";

  const mentions = state.selectedSkills.map((name) => "$" + name).join(" ");
  const prompt = mentions ? `${mentions}\n\n${text}` : text;

  appendBubble("user", prompt);
  const row = el("div", "msg assistant");
  const runIsPlan = (state.session.mode || "chat") === "plan";
  row.appendChild(el("div", "avatar", runIsPlan ? "📋" : "⌨"));
  const bubble = el("div", "bubble");
  row.appendChild(bubble);
  $("messages").appendChild(row);
  startProgLive(bubble);
  scrollToBottom();

  state.progRunning = true;
  state.progBubble = bubble;
  $("sendBtn").disabled = true;
  $("progStopBtn").disabled = false;

  const result = await api().prog_start({
    session_id: state.session.id,
    text: prompt,
    workspace: state.session.workspace,
    sandbox: state.session.sandbox,
    extra_dirs: state.session.extra_dirs || [],
  });
  if (!result.ok) {
    stopProgLive();
    state.progRunning = false;
    $("sendBtn").disabled = false;
    $("progStopBtn").disabled = true;
    bubble.innerHTML = "";
    bubble.classList.add("err");
    bubble.textContent = result.error || "启动失败";
  }
}

async function progStop() {
  if (!state.session) return { ok: false };
  const result = await api().prog_stop(state.session.id);
  if (!result.ok) toast(result.error || "没有在跑的任务", true);
  return result;
}

/* ===== 技能库 ===== */

async function loadSkills(force) {
  if (state.skills.length && !force) {
    renderSkillList();
    return;
  }
  const result = await api().skills_list(!!force);
  state.skills = result.skills || [];
  $("skillCount").textContent = state.skills.length + " 个";
  renderSkillList();
}

function skillBadgeClass(skill) {
  if (skill.source === "个人") return "badge personal";
  if (skill.source === "系统") return "badge system";
  return "badge";
}

function renderSkillList() {
  const box = $("skillList");
  const query = ($("skillSearch").value || "").trim().toLowerCase();
  box.innerHTML = "";
  const items = state.skills.filter((skill) => {
    if (!query) return true;
    return (
      skill.name.toLowerCase().includes(query) ||
      (skill.display_name || "").toLowerCase().includes(query) ||
      (skill.description || "").toLowerCase().includes(query) ||
      (skill.short || "").toLowerCase().includes(query)
    );
  });
  if (!items.length) {
    box.appendChild(el("div", "empty", state.skills.length ? "没找到匹配的技能" : "还没扫描"));
    return;
  }
  items.forEach((skill) => {
    const item = el("div", "item skill-item");
    item.appendChild(el("span", skillBadgeClass(skill), skill.source));
    const wrap = el("div");
    wrap.style.flex = "1";
    wrap.style.minWidth = "0";
    const title = el("div", "item-title", skill.display_name || skill.name);
    const sub = el("div", "item-sub", skill.short || skill.name);
    sub.style.display = "block";
    sub.style.whiteSpace = "normal";
    sub.style.lineHeight = "1.5";
    wrap.appendChild(title);
    wrap.appendChild(sub);
    item.appendChild(wrap);
    if (skill.huge) item.appendChild(el("span", "badge huge", "巨大"));
    item.onclick = () => openSkill(skill.name);
    box.appendChild(item);
  });
}

async function openSkill(name) {
  const detail = await api().skills_read(name);
  if (!detail.ok) {
    toast(detail.error || "打不开这个技能", true);
    return;
  }
  state.currentSkill = name;
  $("skillTitle").textContent = detail.display_name || detail.name;
  $("skillMeta").innerHTML = "";
  const lines = [
    ["标识", "$" + detail.name],
    ["来源", detail.source],
    ["大小", Math.round((detail.bytes || 0) / 1024) + " KB"],
    ["路径", detail.path],
  ];
  lines.forEach(([key, value]) => {
    const row = el("div");
    row.appendChild(el("b", "", key + "："));
    row.appendChild(document.createTextNode(value));
    $("skillMeta").appendChild(row);
  });
  if (detail.truncated) {
    $("skillMeta").appendChild(el("div", "hint", "内容太长，这里只显示前 6 万字"));
  }
  const html = await api().render_markdown(detail.content);
  $("skillView").innerHTML = html;
  attachCopyButtons($("skillView"));
  openOverlay("skillOverlay");
}

async function useSkill(name) {
  if (!state.selectedSkills.includes(name)) state.selectedSkills.push(name);
  renderSkillChips();
  const skill = state.skills.find((item) => item.name === name);
  if ((state.session && state.session.mode) === "prog") {
    toast("已加入，发送时会用 $" + name + " 触发");
  } else {
    toast("已加入，这一轮会把技能的说明注入给模型");
  }
  if (skill && skill.default_prompt && !$("input").value.trim()) {
    $("input").value = skill.default_prompt;
  }
}

function renderSkillChips() {
  const box = $("skillChips");
  box.innerHTML = "";
  box.classList.toggle("hidden", !state.selectedSkills.length);
  state.selectedSkills.forEach((name) => {
    const chip = el("span", "skill-chip");
    chip.appendChild(el("span", "", "$" + name));
    const remove = el("button", "", "✕");
    remove.onclick = () => {
      state.selectedSkills = state.selectedSkills.filter((item) => item !== name);
      renderSkillChips();
    };
    chip.appendChild(remove);
    box.appendChild(chip);
  });
}

/* ===== 记忆库切换 ===== */

async function openMemorySwitcher() {
  const info = await api().memory_recent();
  $("memoryCurrent").innerHTML = "";
  const stats = info.memory || {};
  const current = el("div");
  current.appendChild(el("b", "", "当前："));
  current.appendChild(document.createTextNode((info.current || "未设置") + `　${stats.count || 0} 篇笔记`));
  $("memoryCurrent").appendChild(current);
  $("memoryPathInput").value = info.current || "";
  $("memoryCreateCheck").checked = false;
  const box = $("memoryRecentList");
  box.innerHTML = "";
  (info.recent || []).forEach((path) => {
    const item = el("div", "ref");
    item.appendChild(el("b", "", path.split(/[\\/]/).pop() || path));
    item.appendChild(el("span", "", path));
    item.onclick = () => {
      $("memoryPathInput").value = path;
    };
    box.appendChild(item);
  });
  if (!(info.recent || []).length) box.appendChild(el("div", "empty", "还没有切换记录"));
  $("memoryHint").textContent = "";
  openOverlay("memoryOverlay");
}

async function switchMemory() {
  const root = $("memoryPathInput").value.trim();
  const result = await api().memory_switch({ root, create: $("memoryCreateCheck").checked });
  if (!result.ok) {
    $("memoryHint").textContent = result.error || "切换失败";
    $("memoryHint").className = "hint bad";
    return;
  }
  state.config = result.config;
  state.refs = [];
  renderRefs();
  renderMemoryStats(result.memory);
  await refreshTree();
  closeOverlay("memoryOverlay");
  toast("已切换到 " + result.root + "，共 " + (result.memory.count || 0) + " 篇笔记");
}

/* ===== 助手规则 ===== */

async function openRules() {
  const result = await api().rules_get();
  $("rulesEditor").value = result.content || "";
  const extra = result.bank_rules
    ? "（当前记忆库里还有一份专属规则，会叠在这份后面）"
    : "";
  $("rulesHint").textContent = "保存在 " + result.path + "。改完保存立刻生效。" + extra;
  openOverlay("rulesOverlay");
}

async function saveRules() {
  const result = await api().rules_save($("rulesEditor").value);
  if (!result.ok) {
    toast(result.error || "保存失败", true);
    return;
  }
  closeOverlay("rulesOverlay");
  toast("规则已保存，下一轮对话生效");
}

/* ===== 模型供应商 ===== */

async function openProviders() {
  const [list, presets] = await Promise.all([api().provider_list(), api().provider_presets()]);
  const select = $("providerPreset");
  select.innerHTML = "";
  const blank = el("option", "", "— 选一个预设 —");
  blank.value = "";
  select.appendChild(blank);
  (presets.presets || []).forEach((preset, index) => {
    const option = el("option", "", preset.name);
    option.value = String(index);
    select.appendChild(option);
  });
  select.dataset.presets = JSON.stringify(presets.presets || []);
  renderProviderList(list.providers || [], list.active || "");
  const active = (list.providers || []).find((item) => item.id === list.active);
  if (active) fillProviderForm(active);
  openOverlay("providerOverlay");
}

function renderProviderList(providers, activeId) {
  state.providers = providers;
  const box = $("providerList");
  box.innerHTML = "";
  providers.forEach((provider) => {
    const row = el("div", "provider-row" + (provider.id === activeId ? " on" : ""));
    const wrap = el("div");
    wrap.style.flex = "1";
    wrap.appendChild(el("div", "pname", provider.name + (provider.id === activeId ? "（当前）" : "")));
    wrap.appendChild(
      el("div", "pmeta", `${provider.model || "未填模型"} · ${provider.base_url || "未填地址"} · ${provider.has_key ? "已配 Key" : "无 Key"}`)
    );
    row.appendChild(wrap);
    const use = el("button", "btn tiny ghost", provider.id === activeId ? "使用中" : "切到它");
    use.onclick = async (event) => {
      event.stopPropagation();
      if (provider.id === activeId) return;
      const result = await api().provider_activate(provider.id);
      if (!result.ok) {
        toast("切换失败", true);
        return;
      }
      state.config = result.config;
      updateProviderChip();
      await openProviders();
      toast("已切到 " + provider.name);
    };
    row.appendChild(use);
    row.onclick = () => fillProviderForm(provider);
    box.appendChild(row);
  });
}

function fillProviderForm(provider) {
  state.providerDraftId = provider.id || "";
  $("providerName").value = provider.name || "";
  $("providerBase").value = provider.base_url || "";
  $("providerModel").value = provider.model || "";
  $("providerKey").value = "";
  $("providerKey").placeholder = provider.has_key
    ? "已配置 " + (provider.api_key_masked || "") + "，留空表示不改"
    : "粘贴这个供应商的 API Key";
  $("providerModelList").innerHTML = "";
  $("providerHint").textContent = "";
}

async function saveProvider() {
  const payload = {
    id: state.providerDraftId,
    name: $("providerName").value.trim(),
    base_url: $("providerBase").value.trim(),
    model: $("providerModel").value.trim(),
    api_key: $("providerKey").value.trim(),
  };
  if (!payload.base_url) {
    toast("接口地址不能为空", true);
    return;
  }
  const result = await api().provider_save(payload);
  if (!result.ok) {
    toast("保存失败", true);
    return;
  }
  state.config = result.config;
  state.providerDraftId = result.id || "";
  updateProviderChip();
  await openProviders();
  toast("已保存");
}

function updateProviderChip() {
  const providers = state.config.providers || [];
  const active = providers.find((item) => item.id === state.config.active_provider) || providers[0];
  if (!active) return;
  $("modelChip").textContent = `${active.name} · ${active.model || "未填模型"}`;
  $("modelChip").title = active.base_url || "";
}

/* ===== 底部流量条 ===== */

function formatTokens(value) {
  const number = Number(value) || 0;
  if (number >= 1000000) return (number / 1000000).toFixed(2) + "M";
  if (number >= 1000) return (number / 1000).toFixed(1) + "k";
  return String(number);
}

const USD_TO_CNY = 7.1;

/** 兜底估算：老消息没存 cost_usd 时用高峰价上限估一下。 */
function estimateCost(usage) {
  const cached = Math.min(usage.cached || 0, usage.input || 0);
  const miss = Math.max(0, (usage.input || 0) - cached);
  return ((miss / 1e6) * 0.3 + (cached / 1e6) * 0.006 + ((usage.output || 0) / 1e6) * 1.2) * USD_TO_CNY;
}

function usageCost(usage) {
  if (!usage) return 0;
  if (typeof usage.cost_usd === "number") return usage.cost_usd * USD_TO_CNY;
  return estimateCost(usage);
}

function updateUsageBar(payload) {
  if (payload && payload.total) state.usageTotal = payload.total;
  const total = state.usageTotal || { input: 0, output: 0, cached: 0, turns: 0 };
  const messages = (state.session && state.session.messages) || [];
  const used = messages.filter((item) => item.usage && (item.usage.input || item.usage.output));
  const session = used.reduce(
    (acc, item) => {
      acc.input += item.usage.input || 0;
      acc.output += item.usage.output || 0;
      acc.cached += item.usage.cached || 0;
      acc.cost += usageCost(item.usage);
      return acc;
    },
    { input: 0, output: 0, cached: 0, cost: 0 }
  );
  const last = used.length ? used[used.length - 1].usage : null;

  const lastEl = $("sbLast");
  lastEl.textContent = last
    ? `本轮 输入 ${formatTokens(last.input)}（缓存命中 ${formatTokens(last.cached)}）输出 ${formatTokens(last.output)}`
    : "本轮 —";
  lastEl.title = last
    ? `输入 ${last.input} token，其中 ${last.cached} 命中缓存；输出 ${last.output} token`
    : "";
  $("sbSession").textContent = `本会话 输入 ${formatTokens(session.input)} 输出 ${formatTokens(session.output)}`;
  $("sbTotal").textContent = `累计 ${total.turns} 轮 输入 ${formatTokens(total.input)} 输出 ${formatTokens(total.output)}`;

  const totalCost = typeof total.cost_usd === "number" ? total.cost_usd * USD_TO_CNY : 0;
  $("sbCost").textContent = `累计花费 ¥${totalCost.toFixed(2)}`
    + (session.cost ? ` ｜ 本会话 ¥${session.cost.toFixed(3)}` : "");
  $("sbCost").title =
    "按 DeepSeek Flash 现价逐轮累加：低谷时段是高峰价的一半（高峰=UTC 周一~周五 01-04、06-10 点）。\n" +
    "token 数取自接口每次返回的 usage，与官网用量口径一致。";
}

/* ===== 初始化与事件绑定 ===== */

window.initExtras = async function () {
  state.prog = await api().prog_available();
  fillSandboxOptions();
  updateModeBar();
  const list = await api().provider_list();
  state.providers = list.providers || [];
  updateProviderChip();
  const usage = await api().usage_stats();
  state.usageTotal = usage.total || state.usageTotal;
  updateUsageBar();
  // 启动后后台静默检查更新，失败不打扰
  checkUpdate(false).catch(function () {});
  if (!state.prog.ok) {
    $("modeProgBtn").disabled = true;
    $("modeProgBtn").title = state.prog.error || "";
    $("modePlanBtn").disabled = true;
    $("modePlanBtn").title = state.prog.error || "";
  } else {
    $("modeProgBtn").title = state.prog.version || "";
    $("modePlanBtn").title = state.prog.version || "";
  }
};

window.fillSettingsExtras = function () {
  updateProviderChip();
};

window.loadSkills = loadSkills;

/* ===== 项目 ===== */

/* ===== 检查更新 ===== */

async function checkUpdate(force) {
  const result = await api().update_check(!!force);
  if (!result.ok) {
    if (force) toast(result.error || "检查更新失败", true);
    return null;
  }
  if (!result.configured) {
    if (force) toast("还没填更新源（右上角设置里填）", true);
    return null;
  }
  state.updateInfo = result;
  $("updateChip").classList.toggle("hidden", !result.has_update);
  if (result.has_update) {
    $("updateChip").textContent = `⬆ 有新版本 v${result.version}`;
    if (force) openUpdateDialog();
  } else if (force) {
    toast(`已经是最新版（v${result.current}）`);
  }
  return result;
}

function openUpdateDialog() {
  const info = state.updateInfo;
  if (!info || !info.has_update) return;
  $("updateTitle").textContent = `发现新版本 v${info.version}`;
  $("updateMeta").innerHTML = "";
  [
    ["当前版本", "v" + info.current],
    ["最新版本", "v" + info.version],
    ["更新包", info.url],
  ].forEach(([key, value]) => {
    const row = el("div");
    row.appendChild(el("b", "", key + "："));
    row.appendChild(document.createTextNode(value));
    $("updateMeta").appendChild(row);
  });
  api().render_markdown(info.notes || "（作者没写更新说明）").then((html) => {
    $("updateNotes").innerHTML = html;
  });
  openOverlay("updateOverlay");
}

async function applyUpdate() {
  const button = $("updateApplyBtn");
  button.disabled = true;
  button.textContent = "正在下载…";
  const downloaded = await api().update_download();
  if (!downloaded.ok) {
    button.disabled = false;
    button.textContent = "下载并更新";
    toast(downloaded.error || "下载失败", true);
    return;
  }
  button.textContent = "正在安装，程序会自动重开…";
  const applied = await api().update_apply(downloaded.zip);
  if (!applied.ok) {
    button.disabled = false;
    button.textContent = "下载并更新";
    toast(applied.error || "安装失败", true);
    return;
  }
  toast("正在覆盖安装，稍等几秒会自动重新打开");
  setTimeout(() => api().close_app(), 1200);
}


async function loadProjects() {
  const result = await api().project_list();
  state.projects = result.projects || [];
  const box = $("projectList");
  box.innerHTML = "";
  if (!state.projects.length) {
    box.appendChild(
      el("div", "empty", "还没有项目。\n一个项目 = 记忆库 01-项目 里的一篇笔记 + 一个工作目录。")
    );
    return;
  }
  state.projects.forEach((project) => {
    const item = el("div", "item skill-item");
    const wrap = el("div");
    wrap.style.flex = "1";
    wrap.style.minWidth = "0";
    wrap.appendChild(el("div", "item-title", project.name));
    const sub = el("div", "item-sub", project.has_path ? project.path : "还没设工作目录，点「开工」时会问你");
    sub.style.display = "block";
    sub.style.whiteSpace = "normal";
    sub.style.lineHeight = "1.5";
    wrap.appendChild(sub);
    item.appendChild(wrap);
    if (project.has_path && !project.exists) item.appendChild(el("span", "badge huge", "目录不在"));
    const open = el("button", "btn tiny ghost", "开工");
    open.onclick = async (event) => {
      event.stopPropagation();
      if (!project.path) {
        const typed = window.prompt("这个项目还没设工作目录，把路径粘进来：", "");
        if (!typed || !typed.trim()) return;
        const saved = await api().project_set_path({ note: project.note, path: typed.trim() });
        if (!saved.ok) {
          toast(saved.error || "保存失败", true);
          return;
        }
        project.path = typed.trim();
        project.has_path = true;
      }
      await newSession(project);
    };
    item.appendChild(open);
    item.onclick = () => openNote(project.note);
    box.appendChild(item);
  });
}

function projectBaseDir() {
  const root = state.config.memory_root || "";
  const parts = root.split(/[\\/]/);
  parts.pop();
  return parts.join("\\");
}

function openProjectDialog() {
  $("projectName").value = "";
  $("projectPath").value = "";
  $("projectCreateDir").checked = true;
  $("projectHint").textContent = "";
  $("projectPath").dataset.auto = "1";
  openOverlay("projectOverlay");
}

async function createProject() {
  const name = $("projectName").value.trim();
  if (!name) {
    $("projectHint").textContent = "先写个项目名";
    $("projectHint").className = "hint bad";
    return;
  }
  const result = await api().project_create({
    name,
    path: $("projectPath").value.trim(),
    create_dir: $("projectCreateDir").checked,
  });
  if (!result.ok) {
    $("projectHint").textContent = result.error || "创建失败";
    $("projectHint").className = "hint bad";
    return;
  }
  closeOverlay("projectOverlay");
  toast("项目已创建：" + result.note);
  await loadProjects();
  await refreshTree();
}

window.loadProjects = loadProjects;
window.renderProgEvents = renderProgEvents;
window.renderProgMessage = renderProgMessage;
window.progSend = progSend;
window.progStop = progStop;

window.__aiAssistant = window.__aiAssistant || {};
window.__aiAssistant.onProgEvent = function (payload) {
  const bubble = state.progBubble;
  if (!bubble) return;
  const hint = describeEvent(payload.event);
  if (hint) setProgLiveText(hint);
  if (payload.event && payload.event.kind === "command" && state.progLive) {
    state.progLive.steps += 1;
    state.progLive.summaryText.textContent = `已执行 ${state.progLive.steps} 步…`;
  }
  appendProgEvent(state.progEventBox || bubble, payload.event);
  scrollToBottom();
};

window.__aiAssistant.onProgDone = async function (payload) {
  stopProgLive();
  state.progRunning = false;
  $("sendBtn").disabled = false;
  $("progStopBtn").disabled = true;
  const bubble = state.progBubble;
  state.progBubble = null;
  if (payload.session) state.session = payload.session;
  if (payload.list) {
    state.sessions = payload.list;
    renderSessionList();
  }
  const messages = (payload.session && payload.session.messages) || [];
  const last = [...messages].reverse().find((item) => item.role === "assistant");
  const sameSession = !payload.session_id || (state.session && state.session.id === payload.session_id);
  if (bubble && bubble.isConnected && sameSession && last) {
    bubble.innerHTML = "";
    bubble.classList.remove("err");
    bubble.classList.add("prog-bubble");
    await renderProgEvents(bubble, last);
  } else if (sameSession) {
    // 跑的过程中切过模式/重绘过界面，原来的气泡已经不在了：整屏重画，结果不会丢
    renderMessages();
  }
  showUsage(payload.usage);
  scrollToBottom();
};

window.__aiAssistant.onProgError = async function (payload) {
  stopProgLive();
  state.progRunning = false;
  $("sendBtn").disabled = false;
  $("progStopBtn").disabled = true;
  const bubble = state.progBubble;
  state.progBubble = null;
  if (payload.session) state.session = payload.session;
  if (payload.list) {
    state.sessions = payload.list;
    renderSessionList();
  }
  const sameSession = !payload.session_id || (state.session && state.session.id === payload.session_id);
  if (bubble && bubble.isConnected && sameSession) {
    bubble.innerHTML = "";
    bubble.classList.add("err");
    const note = el("div", "", payload.message || "执行失败");
    bubble.appendChild(note);
    const messages = (payload.session && payload.session.messages) || [];
    const last = [...messages].reverse().find((item) => item.role === "assistant");
    if (last) renderProgEvents(bubble, last);
  } else if (sameSession) {
    renderMessages();
  }
  toast(payload.stopped ? "已停止" : payload.message || "执行失败", !payload.stopped);
  scrollToBottom();
};

$("modeChatBtn").onclick = () => setMode("chat");
$("modePlanBtn").onclick = () => setMode("plan");
$("modeProgBtn").onclick = () => setMode("prog");
$("skillSearch").addEventListener("input", debounce(renderSkillList, 200));
$("skillReloadBtn").onclick = async () => {
  await loadSkills(true);
  toast("已重新扫描，共 " + state.skills.length + " 个技能");
};
$("skillUseBtn").onclick = () => {
  if (state.currentSkill) useSkill(state.currentSkill);
};
$("skillOpenDirBtn").onclick = async () => {
  const detail = await api().skills_read(state.currentSkill || "");
  if (!detail.ok) return;
  const dir = (detail.path || "").replace(/[\\/][^\\/]*$/, "");
  if (!dir) return;
  const result = await api().open_path(dir);
  if (!result.ok) toast(result.error || "打不开这个目录", true);
};
$("progWorkspaceBtn").onclick = async () => {
  if (!state.session || state.session.mode !== "prog") return;
  const workspace = await pickWorkspace();
  if (!workspace) return;
  const result = await api().prog_configure({ session_id: state.session.id, workspace });
  if (!result.ok) {
    toast(result.error || "设置失败", true);
    return;
  }
  state.session = result.session;
  updateModeBar();
};
$("progSandbox").onchange = async () => {
  if (!state.session || state.session.mode !== "prog") return;
  const next = $("progSandbox").value;
  if (next === "danger-full-access") {
    const ok = await askConfirm(
      "确认开完全访问？",
      "这个档位下 Codex 可以改动整台电脑上任意位置的文件，不再限制在工作目录里。\n只在你清楚要干什么的时候用。",
      "我明白，开"
    );
    if (!ok) {
      updateModeBar();
      return;
    }
  }
  const result = await api().prog_configure({ session_id: state.session.id, sandbox: next });
  if (result.ok) {
    state.session = result.session;
    state.config = result.config || state.config;
    toast("权限： " + sandboxLabel(next));
  }
};
$("progStopBtn").onclick = async () => {
  progStop();
};
$("memoryChip").onclick = openMemorySwitcher;
$("memoryBrowseBtn").onclick = async () => {
  const result = await api().pick_folder($("memoryPathInput").value.trim());
  if (result.ok) {
    $("memoryPathInput").value = result.path;
    $("memoryCreateCheck").checked = true;
  } else if (result.error !== "cancelled") {
    toast("选择框打不开，直接粘贴路径即可", true);
  }
};
$("memorySwitchBtn").onclick = switchMemory;
$("modelChip").onclick = openProviders;
$("updateChip").onclick = openUpdateDialog;
$("updateApplyBtn").onclick = applyUpdate;
$("checkUpdateBtn").onclick = async () => {
  const source = $("cfgUpdateSource").value.trim();
  if (source !== (state.config.update_source || "")) {
    const saved = await api().settings_set({ update_source: source });
    if (saved.ok) state.config = saved.config;
  }
  await checkUpdate(true);
};
$("newProjectBtn").onclick = openProjectDialog;
$("projectCreateBtn").onclick = createProject;
$("projectBrowseBtn").onclick = async () => {
  const result = await api().pick_folder($("projectPath").value.trim());
  if (result.ok) {
    $("projectPath").value = result.path;
    $("projectPath").dataset.auto = "0";
  } else if (result.error !== "cancelled") {
    toast("选择框打不开，直接粘路径即可", true);
  }
};
$("projectName").addEventListener("input", () => {
  const name = $("projectName").value.trim();
  if ($("projectPath").dataset.auto !== "1") return;
  const base = projectBaseDir();
  $("projectPath").value = name && base ? base + "\\" + name : "";
});
$("openRulesBtn").onclick = openRules;
$("openProvidersBtn").onclick = openProviders;
$("rulesSaveBtn").onclick = saveRules;
$("rulesResetBtn").onclick = async () => {
  const ok = await askConfirm("恢复默认", "把你改过的规则替换回默认模板？", "恢复");
  if (!ok) return;
  const result = await api().rules_reset();
  $("rulesEditor").value = result.content || "";
  toast("已恢复默认模板");
};
$("providerPreset").onchange = (event) => {
  const presets = JSON.parse(event.target.dataset.presets || "[]");
  const preset = presets[Number(event.target.value)];
  if (!preset) return;
  $("providerName").value = preset.name;
  $("providerBase").value = preset.base_url;
  $("providerModel").value = preset.model;
  state.providerDraftId = "";
};
$("providerNewBtn").onclick = () => fillProviderForm({ id: "", name: "", base_url: "", model: "" });
$("providerSaveBtn").onclick = saveProvider;
$("providerDeleteBtn").onclick = async () => {
  if (!state.providerDraftId) {
    toast("先从上面选一个要删的", true);
    return;
  }
  const ok = await askConfirm("删除供应商", "删掉这条配置？Key 也一起没了。", "删除");
  if (!ok) return;
  const result = await api().provider_delete(state.providerDraftId);
  if (!result.ok) {
    toast(result.error || "删除失败", true);
    return;
  }
  state.config = result.config;
  updateProviderChip();
  await openProviders();
  toast("已删除");
};
$("providerTestBtn").onclick = async () => {
  if (!state.providerDraftId) {
    toast("先保存这条配置再测", true);
    return;
  }
  $("providerHint").textContent = "正在测试…";
  const result = await api().provider_test(state.providerDraftId);
  $("providerHint").textContent = result.ok ? "连通正常" : "失败：" + (result.error || "");
  $("providerHint").className = "hint " + (result.ok ? "ok" : "bad");
};
$("providerModelsBtn").onclick = async () => {
  if (!state.providerDraftId) {
    toast("先保存这条配置再拉取", true);
    return;
  }
  $("providerHint").textContent = "正在拉取模型列表…";
  const result = await api().provider_models(state.providerDraftId);
  const box = $("providerModelList");
  box.innerHTML = "";
  if (!result.ok) {
    $("providerHint").textContent = "拉取失败：" + (result.error || "");
    $("providerHint").className = "hint bad";
    return;
  }
  $("providerHint").textContent = `共 ${result.models.length} 个模型，点一个填进去`;
  $("providerHint").className = "hint";
  result.models.slice(0, 60).forEach((name) => {
    const chip = el("button", "tag-opt", name);
    chip.onclick = () => {
      $("providerModel").value = name;
    };
    box.appendChild(chip);
  });
};
