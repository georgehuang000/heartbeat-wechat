(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  let state = null;
  let selectedId = null;
  let selectedChoiceText = '';
  let choicesSignature = '';
  let draftDirty = false;
  let draftDismissed = false;
  let currentContact = null;
  let polling = false;
  let actionCount = 0;
  let toastTimer;
  let disconnectedCount = 0;
  let visualBusy = false;
  let visualSetup = null;
  let visualPreview = null;
  let visualDrag = null;
  let visualRetryMode = 'capture';
  let preferVisualHint = false;
  const busy = new Set();
  const sessionToken = new URLSearchParams(location.hash.slice(1)).get('token') || '';

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }

  async function dispatch(method, payload = {}) {
    if (window.pywebview?.api?.dispatch) {
      const result = await window.pywebview.api.dispatch(method, payload);
      return typeof result === 'string' ? JSON.parse(result) : result;
    }
    const response = await fetch(`/api/${method}`, {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Galchat-Token': sessionToken}, body: JSON.stringify(payload),
    });
    if (!response.ok) {
      let detail;
      try { const body = await response.json(); detail = body.error || body.detail; } catch (_) { /* A proxy may return HTML. */ }
      throw new Error(detail || `服务暂时无法连接（${response.status}）`);
    }
    return response.json();
  }

  function toast(message, error = false) {
    clearTimeout(toastTimer);
    $('toast').textContent = message;
    $('toast').className = `toast${error ? ' error' : ''}`;
    $('toast').hidden = false;
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, error ? 6500 : 3500);
  }

  function unpack(result) {
    if (!result || result.ok === false) throw new Error(result?.error || '这一步没有完成，请稍后再试。');
    return result.state || (result.connection || result.mode ? result : null);
  }

  async function action(method, payload = {}, options = {}) {
    if (busy.has(method)) return null;
    busy.add(method);
    actionCount++;
    const button = options.button;
    const before = button?.innerHTML;
    if (button) { button.disabled = true; if (options.loading) button.textContent = options.loading; }
    if (method === 'generate') updateGenerateActions();
    try {
      const result = await dispatch(method, payload);
      if (result?.ok === false && result.state && typeof result.state === 'object') render(result.state);
      const next = unpack(result);
      if (next) render(next);
      if (options.success) toast(options.success);
      return result;
    } catch (error) {
      toast(error.message || '连接出现了问题，请稍后重试。', true);
      if (options.onError) options.onError(error);
      return null;
    } finally {
      busy.delete(method);
      actionCount--;
      if (button) { button.disabled = false; button.innerHTML = before; }
      if (method === 'generate') updateGenerateActions();
      if (button?.id === 'fill-button' || button?.id === 'copy-button') updateDraftActions();
    }
  }

  function isSelf(message) { return ['me', 'self', 'user', 'outgoing'].includes(message.role); }
  function latestIncoming(messages) {
    const relevant = messages.filter((m) => m.text && !['system', 'notice'].includes(m.role));
    return relevant.filter((m) => !isSelf(m)).at(-1) || relevant.at(-1);
  }

  function displayTime(value) {
    if (!value) return '';
    if (typeof value === 'string' && /^\d{1,2}:\d{2}$/.test(value)) return value;
    const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', hour12: false});
  }

  function setText(id, value) { $(id).textContent = value; }
  function needsApiKey() { return state?.settings?.api_key_set !== true; }
  function isGenerating() { return ['generating', 'loading', 'starting', 'running'].includes(state?.generation?.status); }
  function updateGenerateActions() {
    const disabled = needsApiKey() || isGenerating() || busy.has('generate') || !!state?.generation_blocked_reason;
    $('generate-button').disabled = disabled;
    $('intent-submit').disabled = disabled;
  }

  function render(next) {
    const previousDraft = state?.draft;
    state = next;
    disconnectedCount = 0;
    const connection = state.connection || {};
    const generation = state.generation || {};
    const demo = state.mode === 'demo';
    const contactKey = `${state.mode || ''}:${state.contact?.id || state.contact?.name || ''}`;
    if (currentContact !== null && currentContact !== contactKey) {
      selectedId = null; selectedChoiceText = ''; draftDirty = false; draftDismissed = false;
      $('draft').value = ''; $('draft-section').hidden = true;
      $('intent').value = '';
    }
    currentContact = contactKey;
    const connected = !!connection.can_read || ['connected', 'ready'].includes(connection.status);
    $('connection-banner').className = `connection-banner${connected ? ' connected' : connection.status === 'error' ? ' error' : ''}`;
    $('demo-banner').hidden = !demo;
    setText('connection-copy', demo ? '先熟悉一下选项框，再接上真实聊天' : connection.message || (connected ? '微信已连接' : '等待微信连接'));
    $('connect-button').hidden = connected || demo;
    $('connect-button').disabled = busy.has('connect');
    setText('visual-open', !connected && (connection.status === 'error' || preferVisualHint) ? '读不到？截图读取 ↗' : '截图读取 ↗');
    $('visual-open').disabled = visualBusy;
    const name = state.contact?.name || (demo ? '体验会话' : '等一句开场白');
    setText('conversation-heading', name);
    setText('contact-avatar', state.contact?.name ? [...name][0] : '↗');
    setText('contact-detail', state.contact ? (demo ? '试着选一句，让剧情继续' : '当前微信会话') : '打开微信，选中你想聊天的人');
    $('live-pill').className = `live-pill${state.paused ? ' paused' : ''}`;
    $('live-pill').innerHTML = `<span></span>${state.paused ? '已暂停' : demo ? '体验中' : connected ? '正在捕捉' : '等待连接'}`;
    const latest = latestIncoming(Array.isArray(state.messages) ? state.messages : []);
    setText('latest-message', latest?.text || (state.contact ? '等待对方的新消息…' : '聊天最难的，有时候就是下一句。\n让我们一起接下去。'));
    setText('message-time', latest ? `${isSelf(latest) ? '你 · ' : ''}${displayTime(latest.timestamp)}` : '');

    const missingKey = needsApiKey();
    const loading = !missingKey && isGenerating();
    let note = '';
    let noteClass = '';
    if (missingKey) { note = '还差一个 DeepSeek API Key，填好就能开始。'; noteClass = ' warning'; }
    else if (state.generation_blocked_reason) { note = state.generation_blocked_reason; noteClass = ' warning'; }
    else if (generation.error) { note = generation.error; noteClass = ' warning'; }
    else if (loading) { note = 'DeepSeek 正在接住这句话，想几个不同的走向…'; noteClass = ' loading'; }
    else if (['unconfigured', 'not_configured', 'unavailable', 'missing_key'].includes(generation.status)) { note = 'DeepSeek 还没接好，可以到设置里测试连接。'; noteClass = ' warning'; }
    else if (state.paused) { note = '已暂停自动捕捉，你仍可以手动换一组选项。'; }
    else if (generation.source === 'demo' || demo) { note = '虚构聊天场景 · 回复选项由 DeepSeek 生成'; }
    $('generation-note').className = `generation-note${noteClass}`;
    $('generation-note').hidden = !note;
    setText('generation-message', note);
    $('generation-settings').hidden = !missingKey && (!!state.generation_blocked_reason || (!generation.error && !['unconfigured', 'not_configured', 'unavailable', 'missing_key'].includes(generation.status)));
    updateGenerateActions();
    if (selectedId !== null && !(state.choices || []).some((choice) => String(choice.id) === String(selectedId) && choice.text === selectedChoiceText)) {
      selectedId = null;
      selectedChoiceText = '';
    }
    const signature = JSON.stringify([state.choices || [], selectedId, loading, !!state.contact, demo, generation.status, missingKey, state.generation_blocked_reason]);
    if (signature !== choicesSignature) { choicesSignature = signature; renderChoices(loading); }
    const hasDraft = typeof state.draft === 'string' && state.draft.length > 0;
    if (previousDraft && !hasDraft && !draftDirty) {
      $('draft').value = '';
      $('draft-section').hidden = true;
      selectedId = null;
    }
    if (!draftDirty && !draftDismissed && hasDraft) {
      $('draft').value = state.draft;
      $('draft-section').hidden = false;
    }
    updateDraftActions();
    setText('fill-button', demo ? '体验草稿' : '填入微信 ↗');
    setText('draft-note', state.draft_stale ? '聊天有更新，请重新选一句；这份草稿仍可复制' : demo ? '体验模式，可复制试试；连接微信后可填入' : state.can_fill_draft === false ? '这句可以复制；连接当前微信会话后再填入' : '填入后，由你按下发送');
    setText('pause-icon', state.paused ? '▷' : 'Ⅱ');
    setText('pause-copy', state.paused ? '继续捕捉' : '暂停捕捉');
    $('pause-button').setAttribute('aria-pressed', String(!!state.paused));
    setText('memory-count', `剧情存档 · ${state.history?.count || 0} 条`);
    if ($('history-dialog').open) renderHistory();
  }

  function renderChoices(loading) {
    const choices = needsApiKey() ? [] : state.choices || [];
    if (state.generation_blocked_reason) {
      $('choices').innerHTML = `<div class="empty-state"><div class="empty-art" aria-hidden="true">↗</div><h3>这句先由你来接</h3><p>${escapeHTML(state.generation_blocked_reason)}</p></div>`;
    } else if (choices.length) {
      $('choices').innerHTML = choices.map((choice, i) => `<article class="choice-card${String(selectedId) === String(choice.id) ? ' selected' : ''}"><button class="choice-main" data-choice="${escapeHTML(choice.id)}" aria-pressed="${String(selectedId) === String(choice.id)}" ${loading ? 'disabled' : ''}><span class="choice-num">${String(i + 1).padStart(2, '0')}</span><span class="choice-content"><span class="choice-label">${escapeHTML(choice.label || ['接个梗', '聊下去', '直球一点'][i] || '另一个走向')}</span><span class="choice-text">${escapeHTML(choice.text)}</span></span><span class="choice-arrow" aria-hidden="true">↗</span></button>${choice.hint ? `<button class="choice-hint-toggle" data-hint="hint-${i}" aria-expanded="false" aria-controls="hint-${i}">为什么这样接 ＋</button><p class="choice-hint" id="hint-${i}" hidden>${escapeHTML(choice.hint)}</p>` : ''}</article>`).join('');
    } else if (loading) {
      $('choices').innerHTML = Array.from({length: 3}, () => '<div class="skeleton-card" aria-hidden="true"><div class="skeleton-line"></div><div class="skeleton-line"></div><div class="skeleton-line"></div></div>').join('');
    } else {
      const configured = !needsApiKey() && !['unconfigured', 'not_configured', 'unavailable', 'missing_key'].includes(state.generation?.status);
      const title = !configured ? '先接上 DeepSeek' : state.contact ? '你的下一句，准备中' : '下一句，还没开始';
      const copy = !configured ? '填好 API Key，<br>下一句就能变成几个不同的选项。' : state.contact ? '读懂你们的聊天，<br>再一起找找新的方向。' : '连接微信后，新的消息会在这里<br>变成几个可以选择的方向。';
      const button = !configured ? '<button class="button primary" data-empty="settings">填写 DeepSeek API Key ↗</button>' : state.contact ? '<button class="button secondary" data-empty="generate">生成我的下一句</button>' : '<button class="button secondary" data-empty="demo">先玩一下演示</button>';
      $('choices').innerHTML = `<div class="empty-state"><div class="empty-art" aria-hidden="true">↗</div><h3>${title}</h3><p>${copy}</p>${button}</div>`;
    }
  }

  async function choose(id) {
    const choice = (state?.choices || []).find((c) => String(c.id) === String(id));
    if (!choice) return;
    const oldId = selectedId;
    const oldChoiceText = selectedChoiceText;
    const oldDraftDirty = draftDirty;
    const oldDraftDismissed = draftDismissed;
    const oldDraftValue = $('draft').value;
    const oldDraftHidden = $('draft-section').hidden;
    const requestedContact = currentContact;
    selectedId = choice.id; selectedChoiceText = choice.text; draftDirty = false; draftDismissed = false;
    const result = await action('choose', {id: choice.id});
    if (!result) {
      if (requestedContact === currentContact) {
        selectedId = oldId; selectedChoiceText = oldChoiceText;
        draftDirty = oldDraftDirty; draftDismissed = oldDraftDismissed;
        if (oldDraftDirty) {
          $('draft').value = oldDraftValue;
          $('draft-section').hidden = oldDraftHidden;
          updateDraftActions();
        }
      }
      return;
    }
    $('draft').value = result.state?.draft || choice.text;
    $('draft-section').hidden = false;
    renderChoices(false);
    $('draft-section').scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest'});
  }

  function renderHistory() {
    const history = state?.history || {};
    setText('history-total', `${history.count || 0} 条消息${state?.contact?.name ? ` · ${state.contact.name}` : ''}`);
    const coverage = history.coverage;
    setText('history-coverage', history.error ? `这次读取未完成：${history.error}` : typeof coverage === 'string' ? coverage : coverage?.description || (coverage?.start || coverage?.end ? `${coverage.start || '未知起点'} — ${coverage.end || '最近读取'}` : history.count ? '当前已读取并保存的聊天范围' : '还没有读取历史'));
    $('load-history').disabled = !state?.connection?.can_read || state?.mode === 'demo' || busy.has('load_history');
    const rawMemories = state?.memories || history.memories || [];
    const memories = Array.isArray(rawMemories) ? rawMemories : [];
    $('memory-list').innerHTML = memories.length ? memories.map((memory) => {
      const content = typeof memory === 'string' ? memory : memory.text || memory.content || memory.summary || memory.fact || '';
      const attribution = typeof memory === 'object' ? [memory.role ? (isSelf(memory) ? '我' : '对方') : '', memory.timestamp ? String(memory.timestamp) : '', memory.source || ''].filter(Boolean).join(' · ') : '';
      return `<div class="memory-item">${escapeHTML(content)}${attribution ? `<small>${escapeHTML(attribution)}</small>` : ''}</div>`;
    }).join('') : '<p class="memory-empty">存档会作为生成时的参考。读过的消息范围会如实显示；没聊过的事，不会当成共同经历。</p>';
  }

  function openHistory() {
    renderHistory();
    $('history-contact').value = state?.contact?.name || '';
    $('history-contact').readOnly = !!state?.contact;
    $('history-dialog').showModal();
  }

  function openSettings() {
    const settings = state?.settings || {};
    $('base-url').value = settings.base_url || 'https://api.deepseek.com';
    $('model-name').value = settings.model || 'deepseek-flash';
    $('api-key').value = '';
    setText('key-status', settings.api_key_set ? '已保存，留空保留' : '尚未配置');
    $('auto-generate').checked = settings.auto_generate !== false;
    $('follow-window').checked = settings.follow_window !== false;
    setText('model-test-result', '');
    $('settings-dialog').showModal();
    if (!settings.api_key_set) $('api-key').focus();
  }

  function settingsPayload() {
    return {base_url: $('base-url').value.trim(), model: $('model-name').value.trim(), api_key: $('api-key').value, auto_generate: $('auto-generate').checked, follow_window: $('follow-window').checked};
  }

  function generate() {
    if (needsApiKey()) { openSettings(); return; }
    if (state?.generation_blocked_reason) { toast(state.generation_blocked_reason); return; }
    if (isGenerating() || busy.has('generate')) return;
    return action('generate', {intent: $('intent').value.trim()}, {button: $('generate-button'), loading: '想一想…'});
  }
  function updateDraftActions() {
    const hasText = !!$('draft').value.trim();
    const canFill = typeof state?.can_fill_draft === 'boolean' ? state.can_fill_draft : !!(selectedId !== null && state?.connection?.can_fill);
    $('fill-button').disabled = !hasText || !canFill || state?.mode === 'demo' || !!state?.draft_stale || busy.has('fill_draft');
    $('copy-button').disabled = !hasText || busy.has('copy_draft');
  }
  async function enterDemo() {
    const result = await action('demo', {enabled: true});
    if (result) $('settings-dialog').close();
  }

  function visualStatus(message = '', error = false) {
    setText('visual-status', message);
    $('visual-status').hidden = !message;
    $('visual-status').className = `visual-status${error ? ' error' : visualBusy ? ' loading' : ''}`;
  }

  function updateVisualControls() {
    ['visual-open', 'visual-settings-open', 'visual-recalibrate', 'visual-zoom', 'visual-redraw', 'visual-recapture', 'visual-preview-refresh', 'visual-preview-redraw', 'visual-retry', 'visual-recovery-redraw'].forEach((id) => { $(id).disabled = visualBusy; });
    $('visual-save').disabled = visualBusy || !visualSetup?.loaded || !visualSetup.regions.every(Boolean) || !$('visual-account').value.trim();
    $('visual-bind').disabled = visualBusy || !visualPreview || !$('visual-contact').value.trim();
    $('visual-account').disabled = visualBusy;
    $('visual-contact').disabled = visualBusy;
    $('visual-stage').setAttribute('aria-disabled', String(visualBusy));
    document.querySelectorAll('[data-visual-step]').forEach((button) => {
      const step = Number(button.dataset.visualStep);
      button.disabled = visualBusy || !visualSetup?.loaded || (step > 0 && !visualSetup.regions[step - 1]);
      button.classList.toggle('complete', !!visualSetup?.regions[step]);
      if (visualSetup?.step === step) button.setAttribute('aria-current', 'step');
      else button.removeAttribute('aria-current');
    });
  }

  async function visualAction(method, payload, progress) {
    if (visualBusy) return null;
    visualBusy = true;
    $('visual-recovery').hidden = true;
    updateVisualControls();
    visualStatus(progress);
    try {
      const result = await action(method, payload, {onError: (error) => visualStatus(error.message, true)});
      if (result) visualStatus();
      else $('visual-recovery').hidden = false;
      return result;
    } finally {
      visualBusy = false;
      updateVisualControls();
    }
  }

  async function openVisual(recalibrate = false) {
    if (visualBusy) return;
    if ($('settings-dialog').open) $('settings-dialog').close();
    if (!$('visual-dialog').open) $('visual-dialog').showModal();
    $('visual-setup').hidden = true;
    $('visual-preview').hidden = true;
    if (state?.settings?.visual_config && !recalibrate) await previewVisual();
    else await captureVisual();
  }

  async function captureVisual() {
    if (visualBusy) return;
    visualRetryMode = 'capture';
    setText('visual-title', '先框好聊天的位置');
    setText('visual-intro', '拖动鼠标框出三个位置。当前整张窗口截图只在本机预览。');
    $('visual-preview').hidden = true;
    $('visual-setup').hidden = true;
    visualPreview = null;
    const result = await visualAction('visual_setup_capture', {}, '正在截取微信窗口，稍等一下…');
    if (!result || !$('visual-dialog').open) return;
    const imageURL = result.image_data_url;
    const width = Number(result.width);
    const height = Number(result.height);
    if (!/^data:image\/(png|jpeg|webp);base64,/i.test(imageURL || '') || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
      visualStatus('没有拿到完整的窗口截图，请重新截图。', true);
      $('visual-recovery').hidden = false;
      return;
    }
    visualSetup = {width, height, regions: [null, null, null], step: 0, loaded: false};
    visualDrag = null;
    $('visual-account').value = result.account_name || state?.settings?.visual_config?.account_name || '';
    $('visual-stage').classList.remove('zoomed');
    setText('visual-zoom', '放大查看 ＋');
    $('visual-image').onload = () => {
      if (!visualSetup || !$('visual-dialog').open) return;
      if ($('visual-image').naturalWidth !== width || $('visual-image').naturalHeight !== height) {
        visualStatus('截图尺寸发生了变化，请重新截图后框选。', true);
        $('visual-recovery').hidden = false;
        return;
      }
      visualSetup.loaded = true;
      updateVisualControls();
    };
    $('visual-image').onerror = () => {
      visualStatus('这张截图没有显示出来，请重新截图。', true);
      $('visual-recovery').hidden = false;
    };
    $('visual-image').src = imageURL;
    $('visual-setup').hidden = false;
    renderVisualBoxes();
    updateVisualStep();
  }

  function updateVisualStep() {
    const instructions = [
      '在图上拖出一个框，选中完整的右侧聊天区，包含标题和输入框。',
      '在聊天区里面，框住顶部的联系人标题。尽量只包含名字这一行。',
      '最后框住下面可以打字的输入框，仍然保持在聊天区里面。',
      '三个位置都框好了。检查一下，保存后就可以识别这段聊天。',
    ];
    setText('visual-step-instruction', instructions[visualSetup?.step ?? 0]);
    updateVisualControls();
  }

  function renderVisualBoxes() {
    if (!visualSetup) { $('visual-boxes').innerHTML = ''; return; }
    const boxes = visualSetup.regions.map((region, index) => ({region, index, drawing: false}));
    if (visualDrag?.region) boxes.push({region: visualDrag.region, index: visualSetup.step, drawing: true});
    const labels = ['聊天区', '聊天标题', '输入框'];
    $('visual-boxes').innerHTML = boxes.filter((box) => box.region).map(({region, index, drawing}) => {
      const [left, top, right, bottom] = region;
      return `<div class="visual-box box-${index}${drawing ? ' drawing' : ''}" style="left:${left / visualSetup.width * 100}%;top:${top / visualSetup.height * 100}%;width:${(right - left) / visualSetup.width * 100}%;height:${(bottom - top) / visualSetup.height * 100}%"><span>${labels[index]}</span></div>`;
    }).join('');
  }

  function visualPoint(event) {
    const bounds = $('visual-stage').getBoundingClientRect();
    return [
      Math.round(Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)) * visualSetup.width),
      Math.round(Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)) * visualSetup.height),
    ];
  }

  function updateVisualDrag(event) {
    if (!visualDrag || event.pointerId !== visualDrag.pointerId) return;
    const point = visualPoint(event);
    visualDrag.region = [Math.min(visualDrag.start[0], point[0]), Math.min(visualDrag.start[1], point[1]), Math.max(visualDrag.start[0], point[0]), Math.max(visualDrag.start[1], point[1])];
    renderVisualBoxes();
  }

  async function saveVisualSetup() {
    if (visualBusy || !visualSetup?.loaded || !visualSetup.regions.every(Boolean)) return;
    const accountName = $('visual-account').value.trim();
    if (!accountName) { $('visual-account').focus(); return; }
    visualRetryMode = 'save';
    const result = await visualAction('visual_setup_save', {
      region: visualSetup.regions[0], title_region: visualSetup.regions[1], input_region: visualSetup.regions[2],
      width: visualSetup.width, height: visualSetup.height, account_name: accountName,
    }, '正在记住这几个位置…');
    if (result && $('visual-dialog').open) await previewVisual();
  }

  async function previewVisual() {
    if (visualBusy) return;
    visualRetryMode = 'preview';
    setText('visual-title', '核对一下这段聊天');
    setText('visual-intro', '只识别已框选的聊天区。先确认联系人，避免把相似的名字存错。');
    $('visual-setup').hidden = true;
    $('visual-preview').hidden = true;
    visualPreview = null;
    const result = await visualAction('visual_preview', {}, '正在识别框选的聊天区域…');
    if (!result || !$('visual-dialog').open) return;
    const preview = result.preview;
    if (!preview || !Array.isArray(preview.messages)) {
      visualStatus('这次没有读到完整的聊天内容，可以重新识别或调整框选位置。', true);
      $('visual-recovery').hidden = false;
      return;
    }
    visualPreview = preview;
    $('visual-contact').value = preview.contact_name || '';
    const warnings = (Array.isArray(preview.warnings) ? preview.warnings : preview.warnings ? [preview.warnings] : []).map((warning) => typeof warning === 'string' ? warning : warning?.message || warning?.text || '有内容需要核对');
    if (typeof preview.confidence === 'number' && preview.confidence < 0.7) warnings.push('这次有些内容不够清楚，建议核对后再连接。');
    $('visual-warnings').innerHTML = warnings.map((warning) => `<p>${escapeHTML(warning)}</p>`).join('');
    $('visual-warnings').hidden = !warnings.length;
    const messages = preview.messages.filter((message) => message && typeof message === 'object').slice(-6);
    $('visual-preview-messages').innerHTML = messages.length ? messages.map((message) => {
      const text = message.text || ({image: '〔图片消息〕', voice: '〔语音消息〕', video: '〔视频消息〕'}[message.kind] || '〔这条内容暂时没有读到文字〕');
      const speaker = isSelf(message) ? '我说' : message.role === 'other' ? '对方说' : message.role === 'system' ? '系统提示' : '发言方待核对';
      return `<div class="visual-message${isSelf(message) ? ' self' : ''}"><strong>${speaker}</strong><p>${escapeHTML(text)}</p>${message.truncated ? '<small>这条消息在画面中没有显示完整</small>' : ''}</div>`;
    }).join('') : '<p class="field-hint">当前画面还没有可读的聊天消息。</p>';
    setText('visual-input-preview', preview.input_text ? `微信输入框已有内容：\n${preview.input_text}` : '微信输入框当前为空。');
    $('visual-input-preview').hidden = false;
    $('visual-preview').hidden = false;
    updateVisualControls();
    $('visual-contact').focus();
  }

  async function bindVisual() {
    if (visualBusy || !visualPreview) return;
    const contactName = $('visual-contact').value.trim();
    if (!contactName) { $('visual-contact').focus(); return; }
    visualRetryMode = 'preview';
    const result = await visualAction('visual_bind', {contact_name: contactName}, '正在连接这位联系人的聊天…');
    if (result) {
      $('visual-dialog').close();
      toast(`已连接 ${contactName}，下一句准备接话了`);
    }
  }

  $('visual-open').addEventListener('click', () => openVisual());
  $('visual-settings-open').addEventListener('click', () => openVisual());
  $('visual-recalibrate').addEventListener('click', () => openVisual(true));
  $('visual-recapture').addEventListener('click', captureVisual);
  $('visual-preview-redraw').addEventListener('click', captureVisual);
  $('visual-recovery-redraw').addEventListener('click', captureVisual);
  $('visual-preview-refresh').addEventListener('click', previewVisual);
  $('visual-retry').addEventListener('click', () => {
    if (visualRetryMode === 'preview') previewVisual();
    else if (visualRetryMode === 'save') saveVisualSetup();
    else captureVisual();
  });
  $('visual-save').addEventListener('click', saveVisualSetup);
  $('visual-bind').addEventListener('click', bindVisual);
  $('visual-account').addEventListener('input', updateVisualControls);
  $('visual-contact').addEventListener('input', updateVisualControls);
  $('visual-zoom').addEventListener('click', () => {
    const zoomed = $('visual-stage').classList.toggle('zoomed');
    setText('visual-zoom', zoomed ? '适应窗口 −' : '放大查看 ＋');
  });
  $('visual-redraw').addEventListener('click', () => {
    if (visualBusy || !visualSetup) return;
    visualSetup.regions = [null, null, null]; visualSetup.step = 0; visualDrag = null;
    visualStatus(); renderVisualBoxes(); updateVisualStep();
  });
  document.querySelectorAll('[data-visual-step]').forEach((button) => button.addEventListener('click', () => {
    if (visualBusy || !visualSetup?.loaded) return;
    visualSetup.step = Number(button.dataset.visualStep); visualDrag = null;
    visualStatus(); renderVisualBoxes(); updateVisualStep();
  }));
  $('visual-stage').addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || visualBusy || !visualSetup?.loaded || visualSetup.step > 2) return;
    event.preventDefault();
    visualDrag = {pointerId: event.pointerId, start: visualPoint(event), region: null};
    $('visual-stage').setPointerCapture(event.pointerId);
  });
  $('visual-stage').addEventListener('pointermove', updateVisualDrag);
  $('visual-stage').addEventListener('pointerup', (event) => {
    if (!visualDrag || event.pointerId !== visualDrag.pointerId) return;
    updateVisualDrag(event);
    const region = visualDrag.region;
    visualDrag = null;
    $('visual-stage').releasePointerCapture(event.pointerId);
    const inside = visualSetup.regions[0];
    if (!region || region[2] - region[0] < 6 || region[3] - region[1] < 6) visualStatus('拖动鼠标框出一个区域，再松开就好。', true);
    else if (visualSetup.step > 0 && (!inside || region[0] < inside[0] || region[1] < inside[1] || region[2] > inside[2] || region[3] > inside[3])) visualStatus('标题和输入框需要在第一步的聊天区里面，请重新框一下。', true);
    else {
      if (visualSetup.step === 0) visualSetup.regions = [null, null, null];
      visualSetup.regions[visualSetup.step] = region;
      visualSetup.step = Math.min(3, visualSetup.step + 1);
      visualStatus();
    }
    renderVisualBoxes(); updateVisualStep();
  });
  $('visual-stage').addEventListener('pointercancel', () => { visualDrag = null; renderVisualBoxes(); });
  $('visual-dialog').addEventListener('close', () => {
    visualDrag = null; visualSetup = null; visualPreview = null;
    $('visual-image').onload = null; $('visual-image').onerror = null;
    $('visual-image').removeAttribute('src');
    $('visual-boxes').innerHTML = ''; $('visual-preview-messages').innerHTML = '';
    $('visual-input-preview').textContent = ''; $('visual-contact').value = '';
  });
  $('connect-button').addEventListener('click', () => action('connect', {}, {button: $('connect-button'), loading: '连接中…', onError: () => {
    preferVisualHint = true;
    setText('visual-open', '读不到？截图读取 ↗');
  }}));
  $('demo-exit').addEventListener('click', () => action('demo', {enabled: false}));
  $('generate-button').addEventListener('click', generate);
  $('intent-submit').addEventListener('click', generate);
  $('intent').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); generate(); } });
  $('choices').addEventListener('click', (event) => {
    const choice = event.target.closest('[data-choice]');
    if (choice) { choose(choice.dataset.choice); return; }
    const hint = event.target.closest('[data-hint]');
    if (hint) {
      const panel = $(hint.dataset.hint); panel.hidden = !panel.hidden;
      hint.setAttribute('aria-expanded', String(!panel.hidden));
      hint.textContent = panel.hidden ? '为什么这样接 ＋' : '收起提示 −';
      return;
    }
    const empty = event.target.closest('[data-empty]');
    if (empty?.dataset.empty === 'demo' || event.target.closest('#empty-demo')) enterDemo();
    if (empty?.dataset.empty === 'generate') generate();
    if (empty?.dataset.empty === 'settings') openSettings();
  });
  $('draft').addEventListener('input', () => { draftDirty = true; updateDraftActions(); });
  $('draft-close').addEventListener('click', () => { $('draft-section').hidden = true; draftDismissed = true; });
  $('copy-button').addEventListener('click', () => {
    const text = $('draft').value.trim();
    if (!text) return;
    action('copy_draft', {text}, {button: $('copy-button'), loading: '复制中…', success: '已复制，切回微信就可以粘贴了'});
  });
  $('fill-button').addEventListener('click', () => {
    const text = $('draft').value.trim();
    if (!text || state?.mode === 'demo') return;
    action('fill_draft', {text}, {button: $('fill-button'), loading: '填入中…', success: '已填入微信，确认后由你发送'});
  });
  $('pause-button').addEventListener('click', () => action('set_paused', {paused: !state?.paused}));
  $('history-open').addEventListener('click', openHistory);
  $('memory-open').addEventListener('click', openHistory);
  $('settings-open').addEventListener('click', openSettings);
  $('generation-settings').addEventListener('click', openSettings);
  $('settings-demo').addEventListener('click', enterDemo);
  document.querySelectorAll('.close-dialog').forEach((button) => button.addEventListener('click', () => button.closest('dialog').close()));
  document.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('click', (event) => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } }));
  $('load-history').addEventListener('click', () => action('load_history', {pages: 5}, {button: $('load-history'), loading: '正在读取这段剧情…', success: '已更新可以读取的聊天记录'}));
  $('import-history').addEventListener('click', async () => {
    const text = $('history-text').value.trim();
    const contactName = $('history-contact').value.trim();
    if (!text) { $('history-text').focus(); toast('先粘贴一段聊天记录吧。'); return; }
    if (!contactName) { $('history-contact').focus(); toast('填个联系人名称，让剧情存到对的人下面。'); return; }
    const result = await action('import_history', {text, contact_name: contactName}, {button: $('import-history'), loading: '保存中…', success: '这段剧情，记住了'});
    if (result) $('history-text').value = '';
  });
  $('settings-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const result = await action('save_settings', settingsPayload(), {button: event.submitter, loading: '保存中…', success: '设置已保存'});
    $('api-key').value = '';
    if (result) $('settings-dialog').close();
  });
  $('test-model').addEventListener('click', async () => {
    if (!$('settings-form').reportValidity()) return;
    if (!$('api-key').value.trim() && !state?.settings?.api_key_set) {
      setText('model-test-result', '先填入 DeepSeek API Key，再测试连接。');
      $('api-key').focus();
      return;
    }
    setText('model-test-result', '正在保存配置，并让 DeepSeek 生成一组测试选项…');
    const saved = await action('save_settings', settingsPayload());
    $('api-key').value = '';
    if (!saved) { setText('model-test-result', '配置没有保存成功，请检查输入。'); return; }
    const result = await action('test_model', {}, {button: $('test-model'), loading: '测试中…', onError: (error) => setText('model-test-result', error.message)});
    if (result) setText('model-test-result', result.message || 'DeepSeek 连接正常，可以开始生成下一句。');
  });
  document.addEventListener('keydown', (event) => {
    if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey || document.querySelector('dialog[open]') || document.activeElement?.matches('input,textarea,[contenteditable="true"]')) return;
    if (/^[123]$/.test(event.key) && !needsApiKey() && !state?.generation_blocked_reason && !['generating', 'loading', 'starting', 'running'].includes(state?.generation?.status)) {
      const choice = state?.choices?.[Number(event.key) - 1];
      if (choice) { event.preventDefault(); choose(choice.id); }
    }
  });

  async function poll() {
    if (polling || actionCount) return;
    if (location.protocol === 'file:' && !window.pywebview?.api?.dispatch) return;
    polling = true;
    try {
      const result = await dispatch('get_state');
      const next = unpack(result);
      if (next && !actionCount) render(next);
    } catch (_) {
      disconnectedCount++;
      if (disconnectedCount >= 2 || !state) {
        $('connection-banner').className = 'connection-banner error';
        setText('connection-copy', '还没连上本地服务，请通过“启动下一句”打开。');
        $('connect-button').hidden = false;
      }
    } finally { polling = false; }
  }
  window.addEventListener('pywebviewready', poll);
  poll();
  setInterval(poll, 1500);
})();
