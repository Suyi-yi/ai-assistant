/* AI 小助理 —— 前端逻辑（无框架、无 CDN） */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const api = () => window.pywebview.api;

window.__lastError = null;
window.addEventListener('error', (event) => {
  window.__lastError = `${event.message} @${event.filename}:${event.lineno}`;
});
window.addEventListener('unhandledrejection', (event) => {
  window.__lastError = 'rejection: ' + (event.reason && event.reason.message ? event.reason.message : event.reason);
});

const state = {
  config: {},
  tagSystem: {},
  emotions: {},
  personaPresets: {},
  sessions: [],
  session: null,
  refs: [],
  tree: [],
  started: false,
  skills: [],
  selectedSkills: [],
  prog: {},
  progRunning: false,
  provider: {},
  providerDraftId: "",
  usageTotal: { input: 0, output: 0, cached: 0, turns: 0 },
  expanded: new Set(),
  streaming: false,
  attachAlways: false,
  streamBubble: null,
  streamRaw: "",
  editingNote: null,
  currentNote: null,
};

/* ===== 小工具 ===== */

let toastTimer = null;
function toast(message, bad = false) {
  const node = $('toast');
  node.textContent = message;
  node.className = 'toast' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add('hidden'), 3200);
}

function askConfirm(title, body, okText = '确定') {
  return new Promise((resolve) => {
    $('confirmTitle').textContent = title;
    $('confirmBody').textContent = body;
    $('confirmYes').textContent = okText;
    const overlay = $('confirmOverlay');
    overlay.classList.remove('hidden');
    const finish = (value) => {
      overlay.classList.add('hidden');
      $('confirmYes').onclick = null;
      $('confirmNo').onclick = null;
      resolve(value);
    };
    $('confirmYes').onclick = () => finish(true);
    $('confirmNo').onclick = () => finish(false);
  });
}

function openOverlay(id) { $(id).classList.remove('hidden'); }
function closeOverlay(id) { $(id).classList.add('hidden'); }

document.addEventListener('click', (event) => {
  const closeBtn = event.target.closest('[data-close]');
  if (closeBtn) closeOverlay(closeBtn.dataset.close);
});

function fmtTime(seconds) {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/* ===== 情绪 ===== */

const EMOTION_RE = /^\s*\[\s*emotion\s*:\s*([a-zA-Z_]+)\s*\]\s*/;

function stripEmotion(text) {
  return String(text || '').replace(EMOTION_RE, '');
}

function peekEmotion(text) {
  const match = String(text || '').match(EMOTION_RE);
  return match ? match[1].toLowerCase() : '';
}

function hexToRgba(hex, alpha) {
  const value = String(hex || '').replace('#', '');
  if (value.length !== 6) return `rgba(168,85,247,${alpha})`;
  const num = parseInt(value, 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}

function setMood(key) {
  const meta = state.emotions[key];
  const root = document.documentElement;
  if (!meta) {
    root.style.setProperty('--mood', 'rgba(168,85,247,.5)');
    root.style.setProperty('--mood-soft', 'rgba(168,85,247,.06)');
    $('moodEmoji').textContent = '🙂';
    $('moodLabel').textContent = '平静';
    return;
  }
  root.style.setProperty('--mood', meta.color);
  root.style.setProperty('--mood-soft', hexToRgba(meta.color, 0.08));
  $('moodEmoji').textContent = meta.emoji;
  $('moodLabel').textContent = meta.label;
  const box = $('moodBox');
  box.classList.remove('pulse');
  void box.offsetWidth;
  box.classList.add('pulse');
}

function applyAvatarMood(key) {
  const meta = state.emotions[key];
  if (!meta) return;
  const rows = document.querySelectorAll('.msg.assistant');
  const last = rows[rows.length - 1];
  if (last) last.querySelector('.avatar').textContent = meta.emoji;
}

/* ===== 启动 ===== */

async function init() {
  if (state.started) return;
  state.started = true;
  try {
    const info = await api().app_info();
    state.config = info.config || {};
    state.dataDir = info.data_dir || "";
    state.tagSystem = info.tag_system || {};
    state.emotions = info.emotions || {};
    state.personaPresets = info.persona_presets || {};
    $('assistantName').textContent = state.config.assistant_name || '小助理';
    $('modelChip').textContent = state.config.model || 'deepseek-chat';
    $('memoryChip').title = state.config.memory_root || '';
    renderMemoryStats(info.memory);

    await refreshSessions();
    await refreshTree();
    buildTagPicker();
    buildPresetPicker();
    if (window.initExtras) await window.initExtras();

    const setup = await api().setup_state();
    if (setup.needs_setup) openSetup(setup);
  } catch (error) {
    toast('启动出错：' + error, true);
  }
}

/* ===== 首次启动向导 ===== */

function openSetup(setup) {
  $('setupName').value = setup.assistant_name || '小助理';
  const memory = setup.memory || {};
  const hasMemory = !!memory.ok;
  $('setupMemory').value = hasMemory ? memory.root : (state.config.memory_root || '');
  $('setupCreate').checked = !hasMemory;
  $('setupCreateRow').style.display = hasMemory ? 'none' : 'flex';
  $('setupKey').value = '';
  $('setupHint').textContent = setup.has_key
    ? 'API Key 已就绪，只差记忆库目录。'
    : '每个人用自己的 DeepSeek Key。没有的话去 platform.deepseek.com 注册后创建。';
  $('setupHint').className = 'hint';
  openOverlay('setupOverlay');
}

async function finishSetup() {
  const memoryRoot = $('setupMemory').value.trim();
  if (!memoryRoot) { toast('先选一个记忆库目录', true); return; }
  const payload = {
    assistant_name: $('setupName').value.trim() || '小助理',
    memory_root: memoryRoot,
    api_key: $('setupKey').value.trim(),
    model: state.config.model || 'deepseek-chat',
    create_memory: $('setupCreate').checked,
  };
  const result = await api().setup_finish(payload);
  if (!result.ok) { toast(result.error || '保存失败', true); return; }
  state.config = result.config || state.config;
  $('assistantName').textContent = state.config.assistant_name;
  renderMemoryStats(result.memory);
  closeOverlay('setupOverlay');
  await refreshTree();
  toast('设置完成，可以开始聊了');
}

async function browseFolder(targetId, createId) {
  const button = event.target;
  button.disabled = true;
  const result = await api().pick_folder($(targetId).value.trim());
  button.disabled = false;
  if (result.ok) {
    $(targetId).value = result.path;
    if (createId) $(createId).checked = true;
  } else if (result.error !== 'cancelled') {
    toast('选择框打不开，直接把路径粘进输入框即可', true);
  }
}

/* ===== 会话 ===== */

async function refreshSessions() {
  state.sessions = await api().session_list();
  if (!state.sessions.length) {
    state.session = await api().session_new();
    state.sessions = await api().session_list();
  } else if (!state.session) {
    const loaded = await api().session_load(state.sessions[0].id);
    state.session = loaded.session || null;
  }
  renderSessionList();
  renderMessages();
}

function renderSessionList() {
  const list = $('sessionList');
  list.innerHTML = '';
  if (!state.sessions.length) {
    list.appendChild(el('div', 'empty', '还没有对话'));
    return;
  }
  state.sessions.forEach((session) => {
    const item = el('div', 'item' + (state.session && session.id === state.session.id ? ' active' : ''));
    item.appendChild(el('span', 'mode-icon', (session.mode || 'chat') === 'prog' ? '⌨' : '💬'));
    const title = el('div', 'item-title', session.title || '新对话');
    title.title = session.title || '';
    item.appendChild(title);
    const sub = el('span', 'item-sub', String(session.count || 0));
    item.appendChild(sub);

    const actions = el('div', 'item-actions');
    const rename = el('button', 'icon-btn', '✎');
    rename.title = '重命名';
    rename.onclick = async (event) => {
      event.stopPropagation();
      const name = window.prompt('重命名会话', session.title || '');
      if (!name) return;
      await api().session_rename(session.id, name);
      await refreshSessions();
    };
    const remove = el('button', 'icon-btn', '✕');
    remove.title = '删除';
    remove.onclick = async (event) => {
      event.stopPropagation();
      const ok = await askConfirm('删除会话', `删除「${session.title}」？记录不可恢复。`, '删除');
      if (!ok) return;
      await api().session_delete(session.id);
      if (state.session && state.session.id === session.id) state.session = null;
      state.sessions = await api().session_list();
      if (!state.sessions.length) {
        state.session = await api().session_new();
        state.sessions = await api().session_list();
      } else {
        const loaded = await api().session_load(state.sessions[0].id);
        state.session = loaded.session;
      }
      renderSessionList();
      renderMessages();
    };
    actions.appendChild(rename);
    actions.appendChild(remove);
    item.appendChild(actions);

    item.onclick = async () => {
      const loaded = await api().session_load(session.id);
      if (!loaded.ok) return;
      state.session = loaded.session;
      state.refs = [];
      renderSessionList();
      renderMessages();
      renderRefs();
    };
    list.appendChild(item);
  });
}

/* ===== 消息渲染 ===== */

function renderMessages() {
  const box = $('messages');
  box.innerHTML = '';
  const messages = (state.session && state.session.messages) || [];
  if (!messages.length) {
    const hint = el('div', 'empty');
    hint.innerHTML = '开始一段新对话。<br>左边「记忆库」里可以翻笔记、搜内容，还能选中笔记丢进对话当参考。';
    box.appendChild(hint);
    return;
  }
  let lastMode = null;
  messages.forEach((message) => {
    const mode = message.mode || (state.session.mode || 'chat');
    if (lastMode && mode !== lastMode) {
      const labels = { chat: '切回聊天模式', plan: '切到计划模式（只读）', prog: '切到天才程序员模式' };
      box.appendChild(el('div', 'mode-divider', labels[mode] || mode));
    }
    lastMode = mode;
    if (message.role === 'user') {
      appendBubble('user', message.content);
    } else if ((mode === 'prog' || mode === 'plan') && window.renderProgMessage) {
      window.renderProgMessage(message);
    } else {
      renderAssistant(stripEmotion(message.content), (bubble) => {
        if (window.renderProgEvents) window.renderProgEvents(bubble, message);
      });
    }
  });
  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant');
  const mood = lastAssistant ? peekEmotion(lastAssistant.content) : '';
  setMood(mood);
  applyAvatarMood(mood);
  if (window.updateUsageBar) updateUsageBar();
  scrollToBottom();
}

function appendBubble(role, text) {
  const row = el('div', 'msg ' + role);
  const avatar = el('div', 'avatar', role === 'user' ? '我' : '✦');
  const bubble = el('div', 'bubble');
  bubble.textContent = text;
  row.appendChild(avatar);
  row.appendChild(bubble);
  $('messages').appendChild(row);
  scrollToBottom();
  return bubble;
}

function renderAssistant(raw, after) {
  const row = el('div', 'msg assistant');
  const avatar = el('div', 'avatar', '✦');
  const bubble = el('div', 'bubble');
  row.appendChild(avatar);
  row.appendChild(bubble);
  $('messages').appendChild(row);
  fillAssistantBubble(bubble, raw).then(() => {
    if (after) after(bubble);
  });
  return bubble;
}

async function fillAssistantBubble(bubble, raw) {
  const html = await api().render_markdown(raw);
  bubble.classList.add('md');
  bubble.style.whiteSpace = 'normal';
  bubble.innerHTML = html;
  attachCopyButtons(bubble);
}

function attachCopyButtons(scope) {
  scope.querySelectorAll('.copy-btn').forEach((button) => {
    button.onclick = () => {
      const code = button.closest('.code-block').querySelector('code');
      navigator.clipboard.writeText(code.innerText).then(() => toast('代码已复制'));
    };
  });
}

function scrollToBottom() {
  const box = $('messages');
  box.scrollTop = box.scrollHeight;
}

/* ===== 发送 ===== */

async function sendMessage() {
  const input = $('input');
  const text = input.value.trim();
  if (!text || state.streaming) return;
  if (!state.session) {
    state.session = await api().session_new('chat');
    await refreshSessions();
  }
  if ((state.session.mode || 'chat') === 'prog') {
    if (window.progSend) window.progSend(text);
    return;
  }
  input.value = '';
  input.style.height = 'auto';

  appendBubble('user', text);

  const row = el('div', 'msg assistant');
  row.appendChild(el('div', 'avatar', '✦'));
  const bubble = el('div', 'bubble');
  const thinking = el('div', 'thinking');
  thinking.innerHTML = '<i></i><i></i><i></i>';
  bubble.appendChild(thinking);
  row.appendChild(bubble);
  $('messages').appendChild(row);
  scrollToBottom();

  state.streaming = true;
  state.streamBubble = bubble;
  state.streamRaw = '';
  $('sendBtn').disabled = true;

  const result = await api().chat_send({
    text,
    session_id: state.session.id,
    refs: state.refs.slice(),
    force_retrieve: state.attachAlways,
  });
  if (!result.ok) {
    state.streaming = false;
    $('sendBtn').disabled = false;
    bubble.innerHTML = '';
    bubble.classList.add('err');
    bubble.textContent = result.error || '发送失败';
  }
}

/* ===== 记忆库 ===== */

async function refreshTree() {
  const result = await api().memo_tree();
  const box = $('memoTree');
  box.innerHTML = '';
  if (!result.ok) {
    const empty = el('div', 'empty');
    empty.innerHTML = (result.error || '记忆库不可用') + '<br>到设置里改一下路径。';
    box.appendChild(empty);
    return;
  }
  state.tree = result.tree || [];
  (result.tree || []).forEach((node) => {
    if (node.type === 'dir') state.expanded.add(node.path);
  });
  renderTreeNodes(state.tree, box);
  if (!state.tree.length) box.appendChild(el('div', 'empty', '记忆库里还没有笔记'));
}

function renderTreeNodes(nodes, container) {
  nodes.forEach((node) => {
    if (node.type === 'dir') {
      const row = el('div', 'tree-row dir');
      const open = state.expanded.has(node.path);
      row.appendChild(el('span', 'tree-caret', open ? '▼' : '▶'));
      row.appendChild(el('span', '', '📁 ' + node.name));
      if (node.readonly) row.appendChild(el('span', 'item-sub', '只读'));
      row.onclick = () => {
        if (open) state.expanded.delete(node.path); else state.expanded.add(node.path);
        const box = $('memoTree');
        box.innerHTML = '';
        renderTreeNodes(state.tree, box);
      };
      container.appendChild(row);
      if (open && node.children && node.children.length) {
        const children = el('div', 'tree-children');
        renderTreeNodes(node.children, children);
        container.appendChild(children);
      }
    } else {
      const row = el('div', 'tree-row' + (state.currentNote === node.path ? ' active' : '') + (node.readonly ? ' readonly' : ''));
      row.appendChild(el('span', 'tree-caret', ''));
      row.appendChild(el('span', '', '📄 ' + node.name.replace(/\.md$/, '')));
      row.title = node.path;
      row.onclick = () => openNote(node.path);
      container.appendChild(row);
    }
  });
}

const runSearch = debounce(async () => {
  const query = $('searchInput').value.trim();
  const box = $('memoTree');
  if (!query) { await refreshTree(); return; }
  const results = await api().memo_search(query, 40);
  box.innerHTML = '';
  if (!results.length) {
    box.appendChild(el('div', 'empty', '没找到相关笔记'));
    return;
  }
  results.forEach((hit) => {
    const item = el('div', 'item');
    const wrap = el('div');
    wrap.style.flex = '1';
    wrap.style.minWidth = '0';
    const title = el('div', 'item-title', hit.title);
    const excerpt = el('div', 'item-sub', hit.excerpt);
    excerpt.style.display = 'block';
    excerpt.style.whiteSpace = 'normal';
    excerpt.style.lineHeight = '1.5';
    wrap.appendChild(title);
    wrap.appendChild(excerpt);
    item.appendChild(wrap);
    item.onclick = () => openNote(hit.path);
    box.appendChild(item);
  });
}, 260);

async function openNote(path) {
  const note = await api().memo_read(path);
  if (!note.ok) { toast(note.error || '打不开这篇笔记', true); return; }
  state.currentNote = path;
  $('noteTitle').textContent = note.title || note.name;
  $('noteTitle').title = path;
  const html = await api().render_markdown(note.content);
  $('noteView').innerHTML = html;
  attachCopyButtons($('noteView'));
  $('noteEditor').value = note.content;
  $('noteView').classList.remove('hidden');
  $('noteEdit').classList.add('hidden');
  $('noteEditBtn').textContent = '编辑';
  $('noteDeleteBtn').style.display = note.readonly ? 'none' : '';
  $('noteEditBtn').style.display = note.readonly ? 'none' : '';
  $('noteHint').textContent = note.readonly ? '07-附件 与 .obsidian 是只读的' : '';
  openOverlay('noteOverlay');
  const box = $('memoTree');
  box.innerHTML = '';
  renderTreeNodes(state.tree, box);
}

async function saveNote() {
  if (!state.currentNote) return;
  const content = $('noteEditor').value;
  const result = await api().memo_save(state.currentNote, content);
  if (!result.ok) { toast(result.error || '保存失败', true); return; }
  toast(result.created ? '已创建' : '已保存，原文件已备份');
  $('noteHint').textContent = result.backup ? '备份：' + result.backup : '';
  await openNote(state.currentNote);
  await refreshTree();
}

async function deleteNote() {
  if (!state.currentNote) return;
  const ok = await askConfirm(
    '删除笔记',
    `把「${state.currentNote}」移到回收站？\n文件会留在 data/backups/trash 里，可以手动找回。`,
    '移入回收站'
  );
  if (!ok) return;
  const result = await api().memo_delete(state.currentNote);
  if (!result.ok) { toast(result.error || '删除失败', true); return; }
  toast('已移入回收站');
  closeOverlay('noteOverlay');
  state.currentNote = null;
  await refreshTree();
}

function addCurrentNoteToRefs() {
  if (!state.currentNote) return;
  if (state.refs.includes(state.currentNote)) { toast('已经在参考列表里了'); return; }
  state.refs.push(state.currentNote);
  renderRefs();
  toast('已加入对话参考');
  switchTab('sessions');
}

async function renderRefs() {
  const box = $('refsList');
  box.innerHTML = '';
  if (!state.refs.length) {
    box.appendChild(el('div', 'empty', '还没引用笔记'));
    return;
  }
  for (const path of state.refs) {
    const note = await api().memo_read(path);
    const item = el('div', 'ref');
    const name = el('b', '', note.ok ? note.title : path);
    item.appendChild(name);
    item.appendChild(el('span', '', path));
    const remove = el('button', 'icon-btn', '✕');
    remove.style.cssFloat = 'right';
    remove.onclick = () => {
      state.refs = state.refs.filter((p) => p !== path);
      renderRefs();
    };
    item.appendChild(remove);
    box.appendChild(item);
  }
}

function renderMemoryStats(stats) {
  const box = $('memoryStats');
  if (!box) return;
  box.innerHTML = '';
  if (!stats || !stats.ok) {
    box.appendChild(el('div', '', '记忆库不可用'));
    return;
  }
  box.innerHTML = `<div>笔记数：<span>${stats.count}</span></div><div>路径：<span style="word-break:break-all">${stats.root}</span></div>`;
}

async function askMemory() {
  const query = $('searchInput').value.trim();
  if (!query) { toast('先在搜索框里写个关键词', true); return; }
  const hits = await api().memo_search(query, 4);
  if (!hits.length) { toast('没搜到相关笔记', true); return; }
  state.refs = hits.map((hit) => hit.path);
  renderRefs();
  switchTab('sessions');
  const input = $('input');
  input.value = `结合我引用的这几篇笔记，说说「${query}」这件事现在到什么程度了？`;
  sendMessage();
}

/* ===== 新建笔记 ===== */

function buildTagPicker() {
  const box = $('tagPicker');
  box.innerHTML = '';
  Object.entries(state.tagSystem || {}).forEach(([group, names]) => {
    names.forEach((name) => {
      const tag = `${group}/${name}`;
      const chip = el('button', 'tag-opt', tag);
      chip.dataset.tag = tag;
      chip.onclick = () => chip.classList.toggle('on');
      box.appendChild(chip);
    });
  });
  const dirSelect = $('newNoteDir');
  dirSelect.innerHTML = '';
  ['00-收件箱', '01-项目', '02-知识/编程', '03-思考', '04-复盘', '05-目标'].forEach((dir) => {
    const option = el('option', '', dir);
    option.value = dir;
    dirSelect.appendChild(option);
  });
  dirSelect.onchange = syncKindField;
  syncKindField();
}

function syncKindField() {
  const isReview = $('newNoteDir').value.startsWith('04-复盘');
  $('kindField').style.display = isReview ? 'flex' : 'none';
}

function buildPresetPicker() {
  const select = $('cfgPreset');
  select.innerHTML = '';
  const none = el('option', '', '只用手写人设');
  none.value = '';
  select.appendChild(none);
  Object.keys(state.personaPresets || {}).forEach((name) => {
    const option = el('option', '', name);
    option.value = name;
    select.appendChild(option);
  });
  select.onchange = () => {
    const preset = state.personaPresets[select.value];
    if (preset) $('cfgPersona').value = preset;
  };
}

async function createNote() {
  const dir = $('newNoteDir').value;
  const title = $('newNoteTitle').value.trim();
  if (!title) { toast('写个标题', true); return; }
  const tags = Array.from(document.querySelectorAll('.tag-opt.on')).map((node) => node.dataset.tag);
  const payload = {
    dir,
    title,
    tags,
    content: $('newNoteContent').value,
    kind: dir.startsWith('04-复盘') ? $('newNoteKind').value : '',
  };
  const result = await api().memo_create(payload);
  if (!result.ok) { toast(result.error || '创建失败', true); return; }
  toast('已创建：' + result.path);
  closeOverlay('newNoteOverlay');
  $('newNoteTitle').value = '';
  $('newNoteContent').value = '';
  document.querySelectorAll('.tag-opt.on').forEach((node) => node.classList.remove('on'));
  await refreshTree();
  await openNote(result.path);
}

/* ===== AI 写入确认卡 ===== */

function renderWriteCard(bubble, write) {
  const card = el('div', 'write-card');
  card.appendChild(el('h4', '', '写入申请：' + (write.title || '')));
  card.appendChild(el('div', 'meta', `目录：${write.dir || '00-收件箱'}`));
  if (Array.isArray(write.tags) && write.tags.length) {
    const tags = el('div', 'tags');
    write.tags.forEach((tag) => tags.appendChild(el('span', 'tag', tag)));
    card.appendChild(tags);
  }
  card.appendChild(el('pre', '', (write.content || '').slice(0, 400)));
  const row = el('div', 'note-save-row');
  const confirm = el('button', 'btn primary tiny', '写入记忆库');
  const cancel = el('button', 'btn ghost tiny', '不要');
  confirm.onclick = async () => {
    const result = await api().memo_create(write);
    if (!result.ok) { toast(result.error || '写入失败', true); return; }
    toast('已写入 ' + result.path);
    row.innerHTML = '';
    row.appendChild(el('small', 'hint ok', '已写入：' + result.path + '（记忆库总览需要手动同步）'));
    await refreshTree();
  };
  cancel.onclick = () => {
    row.innerHTML = '';
    row.appendChild(el('small', 'hint', '已忽略'));
  };
  row.appendChild(confirm);
  row.appendChild(cancel);
  card.appendChild(row);
  bubble.appendChild(card);
}

/* ===== 设置 ===== */

function openSettings() {
  const config = state.config;
  $('cfgName').value = config.assistant_name || '';
  $('cfgPreset').value = config.persona_preset || '';
  $('cfgPersona').value = config.persona || '';
  $('cfgMemory').value = config.memory_root || '';
  $('cfgKey').value = '';
  $('cfgModel').value = config.model || 'deepseek-chat';
  $('cfgTemp').value = config.temperature ?? 0.7;
  $('cfgMaxNotes').value = config.max_context_notes ?? 4;
  $('cfgBase').value = config.base_url || 'https://api.deepseek.com';
  $('cfgCodex').value = config.codex_path || '';
  $('cfgUpdateSource').value = config.update_source || '';
  $('keyStatus').textContent = config.has_key ? `已配置：${config.api_key_masked}` : '还没配置 API Key';
  $('keyStatus').className = 'hint ' + (config.has_key ? 'ok' : 'bad');
  if (window.fillSettingsExtras) window.fillSettingsExtras();
  openOverlay('settingsOverlay');
}

async function saveSettings() {
  const patch = {
    assistant_name: $('cfgName').value.trim(),
    persona_preset: $('cfgPreset').value,
    persona: $('cfgPersona').value.trim(),
    memory_root: $('cfgMemory').value.trim(),
    model: $('cfgModel').value.trim(),
    temperature: parseFloat($('cfgTemp').value),
    max_context_notes: parseInt($('cfgMaxNotes').value, 10) || 4,
    base_url: $('cfgBase').value.trim(),
    codex_path: $('cfgCodex').value.trim(),
    update_source: $('cfgUpdateSource').value.trim(),
  };
  const key = $('cfgKey').value.trim();
  if (key) patch.api_key = key;
  const result = await api().settings_set(patch);
  if (!result.ok) { toast('保存失败', true); return; }
  state.config = result.config;
  $('assistantName').textContent = state.config.assistant_name;
  $('modelChip').textContent = state.config.model;
  renderMemoryStats(result.memory);
  closeOverlay('settingsOverlay');
  await refreshTree();
  toast('设置已保存');
}

/* ===== 前后端事件桥 ===== */

window.__aiAssistant = {
  onReady() { init(); },
  onStart() { /* 占位：流式已经开始 */ },
  onDelta(payload) {
    if (!state.streamBubble) return;
    if (!state.streamRaw) state.streamBubble.innerHTML = '';
    state.streamRaw += payload.text || '';
    const mood = peekEmotion(state.streamRaw);
    if (mood) { setMood(mood); applyAvatarMood(mood); }
    state.streamBubble.classList.remove('md');
    state.streamBubble.style.whiteSpace = 'pre-wrap';
    state.streamBubble.innerHTML = '';
    state.streamBubble.appendChild(document.createTextNode(stripEmotion(state.streamRaw)));
    state.streamBubble.appendChild(el('span', 'cursor'));
    scrollToBottom();
  },
  async onDone(payload) {
    state.streaming = false;
    $('sendBtn').disabled = false;
    const bubble = state.streamBubble;
    state.streamBubble = null;
    state.streamRaw = '';
    if (!bubble) return;
    bubble.innerHTML = '';
    const mood = payload.emotion || peekEmotion(payload.content || '');
    setMood(mood);
    applyAvatarMood(mood);
    await fillAssistantBubble(bubble, payload.clean || payload.content || '');
    (payload.writes || []).forEach((write) => renderWriteCard(bubble, write));
    if (payload.session) state.session = payload.session;
    if (payload.list) { state.sessions = payload.list; renderSessionList(); }
    if (window.updateUsageBar) updateUsageBar(payload);
    scrollToBottom();
  },
  onError(payload) {
    state.streaming = false;
    $('sendBtn').disabled = false;
    const bubble = state.streamBubble;
    state.streamBubble = null;
    state.streamRaw = '';
    if (bubble) {
      bubble.innerHTML = '';
      bubble.classList.add('err');
      bubble.textContent = payload.message || '出错了';
    } else {
      toast(payload.message || '出错了', true);
    }
    scrollToBottom();
  },
  onRefs(payload) {
    state.refs = (payload.refs || []).map((ref) => ref.path);
    renderRefs();
  },
  onSession(payload) {
    if (payload.session) state.session = payload.session;
    if (payload.list) { state.sessions = payload.list; renderSessionList(); }
  },
};

/* ===== 交互绑定 ===== */

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.tab === name);
  });
  $('paneSessions').classList.toggle('hidden', name !== 'sessions');
  $('paneMemory').classList.toggle('hidden', name !== 'memory');
  $('paneSkills').classList.toggle('hidden', name !== 'skills');
  $('paneProjects').classList.toggle('hidden', name !== 'projects');
  if (name === 'skills' && window.loadSkills) window.loadSkills();
  if (name === 'projects' && window.loadProjects) window.loadProjects();
}

document.querySelectorAll('.tab').forEach((tab) => {
  tab.onclick = () => switchTab(tab.dataset.tab);
});

$('newSessionBtn').onclick = () => newSession();

$('sendBtn').onclick = sendMessage;
$('attachBtn').onclick = () => {
  state.attachAlways = !state.attachAlways;
  $('attachBtn').classList.toggle('on', state.attachAlways);
  toast(state.attachAlways ? '这一轮会带上记忆库笔记' : '已改为按需自动检索');
};
$('input').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});
$('input').addEventListener('input', (event) => {
  const node = event.target;
  node.style.height = 'auto';
  node.style.height = Math.min(node.scrollHeight, 170) + 'px';
});

$('searchInput').addEventListener('input', runSearch);
$('askMemoryBtn').onclick = askMemory;
$('newNoteBtn').onclick = () => openOverlay('newNoteOverlay');
$('createNoteBtn').onclick = createNote;

$('noteEditBtn').onclick = () => {
  const editing = !$('noteEdit').classList.contains('hidden');
  if (editing) {
    $('noteEdit').classList.add('hidden');
    $('noteView').classList.remove('hidden');
    $('noteEditBtn').textContent = '编辑';
  } else {
    $('noteEdit').classList.remove('hidden');
    $('noteView').classList.add('hidden');
    $('noteEditBtn').textContent = '看预览';
  }
};
$('noteSaveBtn').onclick = saveNote;
$('noteCancelBtn').onclick = () => {
  $('noteEdit').classList.add('hidden');
  $('noteView').classList.remove('hidden');
  $('noteEditBtn').textContent = '编辑';
};
$('noteDeleteBtn').onclick = deleteNote;
$('noteRefBtn').onclick = addCurrentNoteToRefs;

$('settingsBtn').onclick = openSettings;
$('saveSettingsBtn').onclick = saveSettings;
$('importKeyBtn').onclick = async () => {
  const result = await api().import_key_from_codex();
  if (!result.ok) { toast(result.error || '导入失败', true); return; }
  $('cfgKey').value = result.api_key;
  toast('已从 Codex 配置读取，点保存生效');
};
$('memoryChip').onclick = async () => {
  const result = await api().memo_open('');
  if (!result.ok) toast(result.error || '打不开', true);
};

$('toggleContextBtn').onclick = () => {
  const layout = document.querySelector('.layout');
  layout.classList.toggle('context-collapsed');
  $('toggleContextBtn').textContent = layout.classList.contains('context-collapsed') ? '展开' : '收起';
};

$('setupBrowseBtn').onclick = () => browseFolder('setupMemory', 'setupCreate');
$('setupCreateBankBtn').onclick = () => { $('setupCreate').checked = true; toast('会按标准目录建一个空白记忆库'); };
$('setupFinishBtn').onclick = finishSetup;

window.addEventListener('pywebviewready', init);
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => { if (!state.config.assistant_name) init(); }, 900);
});

document.addEventListener('click', (event) => {
  const link = event.target.closest('.md a');
  if (link && link.href) {
    event.preventDefault();
    api().open_external(link.href);
  }
});
