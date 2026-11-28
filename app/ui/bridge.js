(function () {
  'use strict';

  var listeners = [];
  var queue = [];
  var ready = false;
  var flushing = false;

  var nativeBridge = window.chrome && window.chrome.webview;
  var nativePost = nativeBridge && nativeBridge.postMessage
    ? nativeBridge.postMessage.bind(nativeBridge)
    : null;

  function dispatch(message) {
    try { handleLocalMessage(message); } catch (e) { /* ignore */ }
    listeners.slice().forEach(function (cb) {
      try { cb({ data: message }); } catch (e) { /* ignore */ }
    });
  }

  // ---------------------------------------------------------------
  // 本机访问密码（原生校验，页面只负责显示）
  // ---------------------------------------------------------------
  function postCommand(message) {
    try { chrome.webview.postMessage(message); } catch (e) { /* ignore */ }
  }

  function b64(text) {
    try { return btoa(unescape(encodeURIComponent(String(text == null ? '' : text)))); }
    catch (e) { return ''; }
  }

  var lockArmed = true;  // 仅启动后的第一次状态查询会触发上锁

  function setCardState(locked) {    var status = document.getElementById('localPasswordStatus');
    if (status) {
      status.textContent = locked
        ? '已启用：进入「调试工具」与「小庆自用」需要输入查看密码，首页始终可用'
        : '未设置：任何人打开本程序都能直接使用';
      status.style.color = locked ? '#1a7f37' : '#8a6d3b';
    }
    var oldWrap = document.getElementById('localPasswordOldWrap');
    if (oldWrap) oldWrap.style.display = locked ? 'flex' : 'none';
    var clearBtn = document.getElementById('localPasswordClearBtn');
    if (clearBtn) clearBtn.style.display = locked ? '' : 'none';
  }

  function setHint(text, ok) {
    var hint = document.getElementById('localPasswordHint');
    if (!hint) return;
    hint.textContent = text || '';
    hint.style.color = ok ? '#1a7f37' : '#c0392b';
  }

  function handleLocalMessage(message) {
    var s = String(message || '');
    if (window.__xqMaintHandle && window.__xqMaintHandle(s)) { return true; }
    if (handleUpdateMessage(s)) { return true; }
    if (s.indexOf('remote_state|') === 0) {
      try { xrRender(JSON.parse(s.slice('remote_state|'.length))); } catch (e) { /* ignore */ }
      return true;
    }
    if (s.indexOf('remote_action|') === 0) {
      try { xrOnAction(JSON.parse(s.slice('remote_action|'.length))); } catch (e) { /* ignore */ }
      return true;
    }
    if (s.indexOf('my_library_notice|') === 0) {
      libNotice(s.slice('my_library_notice|'.length));
      return true;
    }
    if (s.indexOf('my_library|') === 0) {
      try { renderLibrary(JSON.parse(s.slice('my_library|'.length))); }
      catch (e) { libNotice('自建库数据解析失败'); }
      return true;
    }
    if (s.indexOf('local_password_lock_state|') === 0) {
      var locked = s.split('|')[1] === '1';
      libLocked = locked;
      setCardState(locked);
      lockArmed = false;   // 上锁由原生在首条命令后推送 auth_required 完成，这里只更新卡片
      if (window.xqLibActive) window.xqShowLibrary();
      return true;
    }
    if (s.indexOf('admin_gate_set_result|') === 0) {
      var gParts = s.split('|');
      var gOk = gParts[1] === 'success';
      setGateHint(gParts[2] || (gOk ? '入口密码已保存' : '保存失败'), gOk);
      if (gOk) {
        adminUnlocked = false;
        ['xqGateOld', 'xqGateNew', 'xqGateNew2'].forEach(function (id) {
          var el = document.getElementById(id);
          if (el) el.value = '';
        });
      }
      return true;
    }
    if (s.indexOf('admin_gate_result|') === 0) {
      var gateOk = s.split('|')[1] === '1';
      if (gateOk) {
        adminUnlocked = true;
        closeAdminGate();
        postCommand('open_admin_panel');
      } else {
        setAdminGateError('密码不正确，请联系小庆获取');
      }
      return true;
    }
    if (s.indexOf('password_result|') === 0) {
      var pwCode = s.split('|')[1];
      if (pwCode === '1') {
        libLocked = false;
        if (window.xqLibActive) window.xqShowLibrary();
      } else if (pwCode === '0') {
        libNotice('查看密码错误，请重试');
      } else if (pwCode === '2') {
        libNotice('尝试次数过多，请稍后再试');
      }
      return false;
    }
    if (s.indexOf('local_password_set_result|') === 0) {
      var parts = s.split('|');
      var okSet = parts[1] === 'success';
      setHint(parts[2] || (okSet ? '已保存' : '保存失败'), okSet);
      if (okSet) {
        var newInput = document.getElementById('localPasswordNew');
        var oldInput = document.getElementById('localPasswordOld');
        if (newInput) newInput.value = '';
        if (oldInput) oldInput.value = '';
      }
      postCommand('local_password_lock_state');
      return true;
    }
    if (s.indexOf('local_password_clear_result|') === 0) {
      var clearParts = s.split('|');
      var okClear = clearParts[1] === 'success';
      setHint(clearParts[2] || (okClear ? '已清除' : '清除失败'), okClear);
      if (okClear) {
        var oldIn = document.getElementById('localPasswordOld');
        var newIn = document.getElementById('localPasswordNew');
        if (oldIn) oldIn.value = '';
        if (newIn) newIn.value = '';
      }
      postCommand('local_password_lock_state');
      return true;
    }
    if (s.indexOf('dock_list|') === 0) {
      try { dockPayload = JSON.parse(s.slice('dock_list|'.length)); } catch (e) { dockPayload = null; }
      if (window.xqLibActive && libPayload) renderLibrary(null);
      return true;
    }
    if (s.indexOf('dock_notice|') === 0) {
      libNotice(s.slice('dock_notice|'.length));
      return true;
    }
    if (s.indexOf('autostart_state|') === 0) {
      var asOn = s.slice('autostart_state|'.length) === '1';
      var asBox = document.getElementById('xqAutostart');
      if (asBox) asBox.checked = asOn;
      var asHint = document.getElementById('xqAutostartHint');
      if (asHint) asHint.textContent = asOn
        ? '已开启：登录 Windows 后自动运行小庆工具箱。'
        : '已关闭：开机不会自动运行。';
      return true;
    }
    return false;
  }

  window.xqPasswordRefresh = function () { postCommand('local_password_lock_state'); };

  window.xqPasswordSave = function () {
    var oldEl = document.getElementById('localPasswordOld');
    var newEl = document.getElementById('localPasswordNew');
    var oldVal = oldEl ? oldEl.value : '';
    var newVal = newEl ? newEl.value : '';
    if (String(newVal).length < 4) { setHint('新密码至少 4 位', false); return; }
    if (String(newVal).length > 64) { setHint('新密码最多 64 位', false); return; }
    setHint('正在保存…', true);
    postCommand('local_password_set|' + b64(oldVal) + '|' + b64(newVal));
  };

  window.xqPasswordClear = function () {
    var oldEl = document.getElementById('localPasswordOld');
    var oldVal = oldEl ? oldEl.value : '';
    if (!window.confirm('确定清除本机访问密码？清除后打开程序不再需要密码。')) return;
    setHint('正在清除…', true);
    postCommand('local_password_clear|' + b64(oldVal));
  };

  function setGateHint(msg, ok) {
    var el = document.getElementById('xqGateHint');
    if (!el) return;
    el.textContent = msg || '';
    el.style.color = ok ? '#1f9d55' : '#e05a4a';
  }

  window.xqGateSave = function () {
    var oldEl = document.getElementById('xqGateOld');
    var newEl = document.getElementById('xqGateNew');
    var new2El = document.getElementById('xqGateNew2');
    var oldVal = oldEl ? oldEl.value : '';
    var newVal = newEl ? newEl.value : '';
    var new2Val = new2El ? new2El.value : '';
    if (String(newVal).length < 4) { setGateHint('新密码至少 4 位', false); return; }
    if (String(newVal).length > 64) { setGateHint('新密码最多 64 位', false); return; }
    if (newVal !== new2Val) { setGateHint('两次输入的新密码不一致', false); return; }
    setGateHint('正在保存…', true);
    postCommand('admin_gate_set|' + b64(oldVal) + '|' + b64(newVal));
  };

  // ---------------------------------------------------------------
  // 我的软件库（内容由网页后台维护，这里只读展示 + 下载）
  // ---------------------------------------------------------------
  function escHtml(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var libPayload = null;
  var dockPayload = null;   // 软件 Dock：固定文件夹扫描 + 手动添加 + 点击启动
  var libLocked = null;   // null=未知；true=需要输入查看密码
  var libExpanded = {};   // 项目卡片里「包含软件 / 链接」是否展开，默认收起
  var adminUnlocked = false;   // 本次会话是否已通过网页后台入口密码

  // 侧栏由原页面渲染，这里补「小庆自用」入口，并保持「软件下载 → 调试工具 → 小庆自用」的顺序
  function findNavByText(text) {
    var sidebar = document.getElementById('sidebar');
    if (!sidebar) return null;
    var items = sidebar.querySelectorAll('.nav-item');
    for (var i = 0; i < items.length; i++) {
      if (items[i].textContent.trim() === text) return items[i];
    }
    return null;
  }

  function moveAfter(sidebar, node, ref) {
    if (!node || !ref) return;
    if (node.previousElementSibling === ref) return;
    if (ref.nextSibling) sidebar.insertBefore(node, ref.nextSibling);
    else sidebar.appendChild(node);
  }

  function ensureLibraryNav() {
    var sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    var nav = document.getElementById('xqLibNav');
    var sw = findNavByText('软件下载');
    var dbg = findNavByText('调试工具');
    if (!nav) {
      nav = document.createElement('div');
      nav.className = 'nav-item';
      nav.id = 'xqLibNav';
      nav.textContent = '小庆自用';
      nav.addEventListener('click', function (e) {
        e.preventDefault();
        window.xqShowLibrary();
      });
      sidebar.appendChild(nav);
    }
    // 日常维护 紧跟 软件下载；调试工具 紧跟 日常维护；小庆自用 紧跟 调试工具
    var maint = document.getElementById('xqMaintNav');
    if (sw && sw.parentElement === sidebar && maint) moveAfter(sidebar, maint, sw);
    if (maint && maint.parentElement === sidebar && dbg) moveAfter(sidebar, dbg, maint);
    else if (sw && sw.parentElement === sidebar && dbg) moveAfter(sidebar, dbg, sw);
    if (dbg && dbg.parentElement === sidebar) moveAfter(sidebar, nav, dbg);
    else if (maint && maint.parentElement === sidebar) moveAfter(sidebar, nav, maint);
    else if (sw && sw.parentElement === sidebar) moveAfter(sidebar, nav, sw);
    else {
      var settings = findNavByText('全局设置');
      if (settings && settings.parentElement === sidebar) moveAfter(sidebar, nav, settings.previousElementSibling || null);
    }
  }

  function libNotice(text) {
    var box = document.getElementById('xqLibNotice');
    if (!box) {
      box = document.createElement('div');
      box.id = 'xqLibNotice';
      box.style.cssText = 'position:fixed;left:50%;bottom:36px;transform:translateX(-50%);' +
        'background:#1c2534;color:#fff;padding:9px 16px;border-radius:9px;font-size:13px;z-index:99999;' +
        'box-shadow:0 10px 30px rgba(0,0,0,.25);max-width:70%;text-align:center';
      document.body.appendChild(box);
    }
    box.textContent = text || '';
    box.style.display = text ? '' : 'none';
    clearTimeout(box._timer);
    if (text) box._timer = setTimeout(function () { box.style.display = 'none'; }, 4200);
  }

  var LIB_STYLE = {
    card: 'background:rgba(255,255,255,.55);' +
      'border:1px solid rgba(255,255,255,.8);border-radius:16px;padding:18px 20px;margin-bottom:14px;' +
      'box-shadow:0 6px 16px rgba(52,102,160,.10);backdrop-filter:blur(6px);',
    grid: 'display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;',
    row: 'display:flex;gap:12px;align-items:flex-start;padding:11px 0;border-top:1px dashed rgba(96,142,198,.22);',
    icon: 'width:44px;height:44px;border-radius:12px;background:linear-gradient(160deg,#ffffff,#e4efff);' +
      'border:1px solid rgba(255,255,255,.95);box-shadow:0 4px 10px rgba(46,98,160,.10);' +
      'display:flex;align-items:center;justify-content:center;font-weight:700;flex:none;overflow:hidden;font-size:15px;color:#245cae;',
    name: 'font-weight:700;font-size:14px;color:#16304d;',
    meta: 'font-size:12px;color:#5b7694;margin-top:2px;',
    btn: 'border:0;border-radius:10px;padding:7px 15px;font-size:12px;font-weight:700;cursor:pointer;' +
      'background:linear-gradient(180deg,#2f86f6,#1a63d8);color:#fff;box-shadow:0 6px 16px rgba(31,111,229,.24);',
    btnGhost: 'border:1px solid rgba(255,255,255,.95);border-radius:10px;padding:7px 15px;font-size:12px;cursor:pointer;' +
      'background:rgba(255,255,255,.85);color:#1c3a5a;box-shadow:0 4px 12px rgba(46,98,160,.10);'
  };
  var LIB_LIGHT = JSON.parse(JSON.stringify(LIB_STYLE));
  function libDark() { return document.documentElement.getAttribute('data-theme') === 'dark'; }
  function libMuted() { return libDark() ? '#b7c3d6' : '#6b7a90'; }
  function applyLibTheme() {
    if (libDark()) {
      LIB_STYLE.card = 'background:rgba(255,255,255,.07);' +
        'border:1px solid rgba(255,255,255,.14);border-radius:16px;padding:18px 20px;margin-bottom:14px;' +
        'box-shadow:0 6px 16px rgba(0,0,0,.35);backdrop-filter:blur(6px);';
      LIB_STYLE.row = 'display:flex;gap:12px;align-items:flex-start;padding:11px 0;border-top:1px dashed rgba(255,255,255,.16);';
      LIB_STYLE.icon = 'width:44px;height:44px;border-radius:12px;background:linear-gradient(160deg,#2b3648,#1d2634);' +
        'border:1px solid rgba(255,255,255,.16);box-shadow:0 4px 10px rgba(0,0,0,.3);' +
        'display:flex;align-items:center;justify-content:center;font-weight:700;flex:none;overflow:hidden;font-size:15px;color:#7cb8ff;';
      LIB_STYLE.name = 'font-weight:700;font-size:14px;color:#f2f6fc;';
      LIB_STYLE.meta = 'font-size:12px;color:#b7c3d6;margin-top:2px;';
      LIB_STYLE.btnGhost = 'border:1px solid rgba(255,255,255,.22);border-radius:10px;padding:7px 15px;font-size:12px;cursor:pointer;' +
        'background:rgba(255,255,255,.10);color:#e8eef7;box-shadow:none;';
    } else {
      Object.keys(LIB_LIGHT).forEach(function (k) { LIB_STYLE[k] = LIB_LIGHT[k]; });
    }
  }
  applyLibTheme();
  try {
    new MutationObserver(function () {
      applyLibTheme();
      if (window.xqLibActive) { if (libPayload) renderLibrary(); else if (window.xqShowLibrary) window.xqShowLibrary(); }
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  } catch (e) { /* ignore */ }

  function libIcon(item) {
    var icon = String(item.icon || '').trim();
    if (/^(https?:\/\/|data:image\/)/i.test(icon)) {
      return '<div style="' + LIB_STYLE.icon + '"><img src="' + escHtml(icon) +
        '" style="width:100%;height:100%;object-fit:cover" onerror="this.remove()"></div>';
    }
    var label = icon ? icon.slice(0, 2) : String(item.name || '?').slice(0, 1);
    return '<div style="' + LIB_STYLE.icon + '">' + escHtml(label) + '</div>';
  }

  function libSoftwareRow(item) {
    var meta = [];
    if (item.version) meta.push('v' + item.version);
    if (item.size) meta.push(item.size);
    if (item.category) meta.push(item.category);
    var note = item.note ? '<div style="' + LIB_STYLE.meta + '">' + escHtml(item.note) + '</div>' : '';
    var actions = item.url
      ? '<button style="' + LIB_STYLE.btn + '" data-lib-dl="' + escHtml(item.id) + '">下载</button>'
      : '<span style="' + LIB_STYLE.meta + '">无直链</span>';
    return '<div style="' + LIB_STYLE.row + '">' + libIcon(item) +
      '<div style="flex:1;min-width:0">' +
        '<div style="' + LIB_STYLE.name + '">' + escHtml(item.name) + '</div>' +
        (meta.length ? '<div style="' + LIB_STYLE.meta + '">' + escHtml(meta.join(' · ')) + '</div>' : '') +
        (item.intro ? '<div style="' + LIB_STYLE.meta + '">' + escHtml(item.intro) + '</div>' : '') +
        note +
      '</div>' +
      '<div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">' + actions + '</div>' +
    '</div>';
  }

  function renderLibrary(payload) {
    if (payload) libPayload = payload;
    var mount = document.getElementById('xqLibRoot');
    if (!mount || !libPayload) return;
    applyLibTheme();
    var software = (libPayload && libPayload.software) || [];
    var projects = (libPayload && libPayload.projects) || [];
    var byId = {};
    software.forEach(function (s) { byId[s.id] = s; });

    var head = '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px">' +
      '<span style="' + LIB_STYLE.meta + '">软件 ' + software.length + ' 条 · 项目 ' + projects.length + ' 个</span>' +
      '<div style="flex:1"></div>' +
      '<button style="' + LIB_STYLE.btn + '" data-lib-admin="1">打开网页后台</button>' +
      '<button style="' + LIB_STYLE.btnGhost + '" data-lib-refresh="1">刷新</button>' +
      '</div>';

    if (!software.length && !projects.length) {
      mount.innerHTML = head + '<div style="padding:16px 2px;color:' + libMuted() + ';font-size:13px">' +
        '自建库还是空的：点「打开网页后台」添加软件和项目，保存后回到这里点「刷新」。</div>' + dockHtml();
      bindLibraryButtons(mount, byId);
      bindDock(mount);
      return;
    }

    var html = head;

    projects.forEach(function (project) {
      var ownIds = project.softwareIds || [];
      var own = [];
      for (var i = 0; i < ownIds.length; i++) { if (byId[ownIds[i]]) own.push(byId[ownIds[i]]); }
      var links = project.links || [];
      var expanded = !!libExpanded[project.id];
      var summary = own.length ? ('包含 ' + own.length + ' 个软件') : '暂无软件';
      if (links.length) summary += ' · ' + links.length + ' 条链接';
      summary += ' · 点击标题' + (expanded ? '收起' : '展开');
      html += '<div style="' + LIB_STYLE.card + '" data-lib-card="' + escHtml(project.id) + '">' +
        '<div data-lib-toggle="' + escHtml(project.id) + '" style="display:flex;gap:12px;align-items:flex-start;cursor:pointer;user-select:none;border-radius:10px;padding:4px;margin:-4px">' +
          libIcon(project) +
          '<div style="flex:1;min-width:0">' +
            '<div style="font-size:15px;font-weight:700;display:flex;align-items:center;gap:6px">' +
              '<span>' + escHtml(project.name) + '</span>' +
              '<span data-lib-arrow="' + escHtml(project.id) + '" style="font-size:12px;color:#8a97a8;font-weight:400">' + (expanded ? '▾' : '▸') + '</span>' +
            '</div>' +
            ((project.tags || []).length
              ? '<div style="' + LIB_STYLE.meta + '">' + project.tags.map(function (t) { return '#' + escHtml(t); }).join(' ') + '</div>'
              : '') +
            (project.intro ? '<div style="' + LIB_STYLE.meta + '">' + escHtml(project.intro) + '</div>' : '') +
            (project.note ? '<div style="' + LIB_STYLE.meta + '">备注：' + escHtml(project.note) + '</div>' : '') +
            '<div data-lib-hint="' + escHtml(project.id) + '" style="' + LIB_STYLE.meta + '">' + escHtml(summary) + '</div>' +
          '</div>' +
        '</div>' +
        '<div data-lib-body="' + escHtml(project.id) + '" style="' + (expanded ? '' : 'display:none') + '">';

      if (own.length) {
        html += '<div style="margin-top:10px;font-size:12px;color:' + libMuted() + '">包含软件</div>';
        own.forEach(function (item) { html += libSoftwareRow(item); });
      }
      if (links.length) {
        html += '<div style="margin-top:10px;font-size:12px;color:' + libMuted() + '">附加链接</div>';
        links.forEach(function (link) {
          html += '<div style="' + LIB_STYLE.row + '">' +
            '<div style="flex:1;min-width:0"><div style="' + LIB_STYLE.name + '">' + escHtml(link.name) + '</div>' +
            '<div style="' + LIB_STYLE.meta + '">' + escHtml(link.url || '（无地址）') + '</div></div>' +
            (link.url ? '<button style="' + LIB_STYLE.btnGhost + '" data-lib-open="' + escHtml(link.url) + '">打开</button>' : '') +
          '</div>';
        });
      }
      html += '</div></div>';
    });

    var loose = software.filter(function (s) {
      return !projects.some(function (p) { return (p.softwareIds || []).indexOf(s.id) >= 0; });
    });
    if (loose.length) {
      html += '<div style="' + LIB_STYLE.card + '"><div style="font-size:15px;font-weight:700;margin-bottom:4px">未归属项目的软件</div>';
      loose.forEach(function (item) { html += libSoftwareRow(item); });
      html += '</div>';
    }

    mount.innerHTML = html + dockHtml();
    bindLibraryButtons(mount, byId);
    bindDock(mount);
  }

  function libStartDownload(item) {
    if (typeof window.simulateDownload !== 'function') {
      libNotice('当前版本不支持直接下载，请用“打开链接”');
      return;
    }
    try {
      // 存到下载目录下的“自建库”子文件夹：不与商店同名文件互相顶掉
      // 不把按钮交给前端改写，避免下载失败后按钮被清空成空白块
      window.simulateDownload(item.name, null, item.url, '自建库', {
        idPrefix: 'my_lib_', downloadOnly: true, preferSoftwareNameForFile: true
      });
    } catch (err) { libNotice('下载失败：' + err.message); }
  }

  function bindLibraryButtons(container, byId) {
    container.querySelectorAll('[data-lib-dl]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var item = byId[btn.getAttribute('data-lib-dl')];
        if (!item || !item.url) return;
        libStartDownload(item);
      });
    });
    container.querySelectorAll('[data-lib-open]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        try { window.open(btn.getAttribute('data-lib-open'), '_blank'); } catch (e) { /* ignore */ }
      });
    });
    container.querySelectorAll('[data-lib-toggle]').forEach(function (head) {
      head.addEventListener('click', function () {
        var pid = head.getAttribute('data-lib-toggle');
        var body = container.querySelector('[data-lib-body="' + pid + '"]');
        if (!body) return;
        var open = body.style.display === 'none';
        body.style.display = open ? '' : 'none';
        libExpanded[pid] = open;
        var arrow = head.querySelector('[data-lib-arrow]');
        if (arrow) arrow.textContent = open ? '▾' : '▸';
        var hint = head.querySelector('[data-lib-hint]');
        if (hint) hint.textContent = hint.textContent.replace(open ? '展开' : '收起', open ? '收起' : '展开');
      });
      head.addEventListener('mouseenter', function () {
        head.style.background = libDark() ? 'rgba(255,255,255,.10)' : '#f4f8ff';
        head.style.borderRadius = '10px';
      });
      head.addEventListener('mouseleave', function () { head.style.background = ''; });
    });
    var adminBtn = container.querySelector('[data-lib-admin]');
    if (adminBtn) adminBtn.addEventListener('click', function () {
      if (adminUnlocked) postCommand('open_admin_panel');
      else openAdminGate();
    });
    var refreshBtn = container.querySelector('[data-lib-refresh]');
    if (refreshBtn) refreshBtn.addEventListener('click', function () { postCommand('my_library'); });
  }

  /* ---- 软件 Dock：卡片下方空白处的启动栏 ---- */
  function ensureDockStyle() {
    if (document.getElementById('xqDockStyle')) return;
    var st = document.createElement('style');
    st.id = 'xqDockStyle';
    st.textContent =
      '.xqDockTile{position:relative;width:84px;padding:10px 4px 8px;border-radius:14px;text-align:center;' +
        'cursor:pointer;transition:transform .15s ease,background .15s ease,box-shadow .15s ease;user-select:none}' +
      '.xqDockTile:hover{transform:translateY(-4px)}' +
      ':root:not([data-theme="dark"]) .xqDockTile:hover{background:rgba(255,255,255,.7);box-shadow:0 8px 18px rgba(46,98,160,.16)}' +
      ':root[data-theme="dark"] .xqDockTile:hover{background:rgba(255,255,255,.10)}' +
      '.xqDockTile:active{transform:translateY(-1px) scale(.97)}' +
      '.xqDockName{font-size:11px;margin-top:6px;line-height:1.25;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      ':root:not([data-theme="dark"]) .xqDockName{color:#16304d}' +
      ':root[data-theme="dark"] .xqDockName{color:#e8eef7}' +
      '.xqDockDel{position:absolute;top:2px;right:4px;width:16px;height:16px;line-height:15px;border-radius:8px;' +
        'font-size:12px;color:#fff;background:rgba(120,140,165,.75);opacity:0;transition:opacity .12s}' +
      '.xqDockTile:hover .xqDockDel{opacity:1}' +
      '.xqDockDel:hover{background:#e05656}' +
      '.xqDockIco{width:42px;height:42px;margin:0 auto}' +
      '.xqDockIco .xqDockLetter{display:none}' +
      '.xqDockIco.noimg img{display:none}' +
      '.xqDockIco.noimg .xqDockLetter{display:flex}' +
      '.xqDockFolderInput{flex:1;min-width:160px;border-radius:9px;padding:6px 10px;font-size:12px;outline:none}' +
      ':root:not([data-theme="dark"]) .xqDockFolderInput{border:1px solid rgba(96,142,198,.35);background:rgba(255,255,255,.8);color:#16304d}' +
      ':root[data-theme="dark"] .xqDockFolderInput{border:1px solid rgba(255,255,255,.22);background:rgba(255,255,255,.08);color:#e8eef7}';
    document.head.appendChild(st);
  }

  function dockTile(item) {
    var path = escHtml(item.path || '');
    var letter = '<div class="xqDockLetter" style="' + LIB_STYLE.icon.replace('display:flex', '') + 'width:42px;height:42px;margin:0 auto">' +
      escHtml(String(item.name || '?').slice(0, 1)) + '</div>';
    var icon = '<div class="xqDockIco' + (item.icon ? '' : ' noimg') + '">' +
      (item.icon ? '<img src="' + escHtml(item.icon) + '" style="width:42px;height:42px;object-fit:contain;filter:drop-shadow(0 3px 6px rgba(20,40,80,.18))" onerror="this.parentNode.classList.add(\'noimg\')">' : '') +
      letter + '</div>';
    var miss = item.missing ? 'opacity:.45;' : '';
    return '<div class="xqDockTile" data-dock-run="' + path + '" style="' + miss + '" title="' + path + '">' +
      '<span class="xqDockDel" data-dock-del="' + path + '" title="从 Dock 移除">×</span>' +
      icon +
      '<div class="xqDockName">' + escHtml(item.name || '') + '</div>' +
    '</div>';
  }

  function dockHtml() {
    ensureDockStyle();
    var data = dockPayload || { folder: '', items: [] };
    var items = data.items || [];
    var tiles = items.length
      ? '<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:12px">' + items.map(dockTile).join('') + '</div>'
      : '<div style="margin-top:12px;font-size:12.5px;color:' + libMuted() + '">' +
        'Dock 还是空的：点「一键刷新」自动添加扫描文件夹里的所有软件，或点「添加软件」手动选择。</div>';
    return '<div style="' + LIB_STYLE.card + 'margin-top:18px">' +
      '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
        '<span style="font-size:15px;font-weight:700">软件 Dock</span>' +
        '<span style="' + LIB_STYLE.meta + '">' + items.length + ' 个 · 点击图标启动</span>' +
        '<div style="flex:1"></div>' +
        '<button style="' + LIB_STYLE.btn + '" data-dock-scan="1">一键刷新</button>' +
        '<button style="' + LIB_STYLE.btnGhost + '" data-dock-add="1">添加软件</button>' +
      '</div>' +
      '<div style="display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap">' +
        '<span style="' + LIB_STYLE.meta + 'flex:none">扫描文件夹</span>' +
        '<input id="xqDockFolder" class="xqDockFolderInput" type="text" spellcheck="false" value="' + escHtml(data.folder || '') + '" placeholder="例如 E:\\黑科技\\绿色版">' +
        '<button style="' + LIB_STYLE.btnGhost + '" data-dock-folder-save="1">保存</button>' +
      '</div>' +
      tiles +
    '</div>';
  }

  function bindDock(container) {
    var scan = container.querySelector('[data-dock-scan]');
    if (scan) scan.addEventListener('click', function () {
      libNotice('正在扫描文件夹…');
      postCommand('dock_scan');
    });
    var add = container.querySelector('[data-dock-add]');
    if (add) add.addEventListener('click', function () { postCommand('dock_add|'); });
    var save = container.querySelector('[data-dock-folder-save]');
    var input = container.querySelector('#xqDockFolder');
    function saveFolder() {
      if (!input) return;
      postCommand('dock_folder|' + b64(input.value || ''));
      libNotice('扫描文件夹已保存，点「一键刷新」生效');
    }
    if (save) save.addEventListener('click', saveFolder);
    if (input) input.addEventListener('keydown', function (e) { if (e.key === 'Enter') saveFolder(); });
    container.querySelectorAll('[data-dock-run]').forEach(function (tile) {
      tile.addEventListener('click', function () {
        postCommand('dock_launch|' + b64(tile.getAttribute('data-dock-run') || ''));
      });
    });
    container.querySelectorAll('[data-dock-del]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        postCommand('dock_remove|' + b64(btn.getAttribute('data-dock-del') || ''));
      });
    });
  }

  function closeAdminGate() {
    var ov = document.getElementById('xqAdminGate');
    if (ov && ov.parentNode) ov.parentNode.removeChild(ov);
  }
  function setAdminGateError(msg) {
    var el = document.getElementById('xqAdminGateErr');
    if (el) { el.textContent = msg || ''; el.style.display = msg ? 'block' : 'none'; }
  }
  function openAdminGate() {
    closeAdminGate();
    var dark = libDark();
    var cardBg = dark ? '#232a35' : '#ffffff';
    var fg = dark ? '#f2f6fc' : '#16304d';
    var sub = dark ? '#b7c3d6' : '#5b7694';
    var line = dark ? 'rgba(255,255,255,.22)' : 'rgba(96,142,198,.35)';
    var ov = document.createElement('div');
    ov.id = 'xqAdminGate';
    ov.style.cssText = 'position:fixed;inset:0;z-index:10006;display:flex;align-items:center;justify-content:center;' +
      'background:rgba(10,14,20,.45);backdrop-filter:blur(3px);';
    ov.innerHTML =
      '<div style="width:320px;padding:26px 26px 22px;border-radius:16px;background:' + cardBg + ';' +
        'border:1px solid ' + line + ';box-shadow:0 18px 48px rgba(0,0,0,.35);color:' + fg + '">' +
        '<div style="font-size:16px;font-weight:700;margin-bottom:6px">打开网页后台</div>' +
        '<div style="font-size:12px;color:' + sub + ';margin-bottom:16px">请输入入口密码（请联系小庆获取）</div>' +
        '<input id="xqAdminGatePwd" type="password" maxlength="64" autocomplete="off" spellcheck="false" ' +
          'placeholder="请联系小庆获取" style="width:100%;box-sizing:border-box;padding:9px 12px;font-size:14px;' +
          'border:1px solid ' + line + ';border-radius:10px;background:transparent;color:' + fg + ';outline:none">' +
        '<div id="xqAdminGateErr" style="display:none;font-size:12px;color:#e05a4a;margin-top:8px"></div>' +
        '<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:18px">' +
          '<button id="xqAdminGateCancel" style="border:1px solid ' + line + ';border-radius:10px;padding:7px 16px;' +
            'font-size:13px;cursor:pointer;background:transparent;color:' + sub + '">取消</button>' +
          '<button id="xqAdminGateOk" style="border:0;border-radius:10px;padding:7px 18px;font-size:13px;font-weight:700;' +
            'cursor:pointer;background:linear-gradient(180deg,#2f86f6,#1a63d8);color:#fff">进入后台</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(ov);
    var input = document.getElementById('xqAdminGatePwd');
    var submit = function () {
      var val = (input.value || '').trim();
      if (!val) { setAdminGateError('请输入密码'); return; }
      setAdminGateError('');
      postCommand('admin_gate_check|' + val);
    };
    document.getElementById('xqAdminGateOk').addEventListener('click', submit);
    document.getElementById('xqAdminGateCancel').addEventListener('click', closeAdminGate);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
    ov.addEventListener('click', function (e) { if (e.target === ov) closeAdminGate(); });
    setTimeout(function () { input.focus(); }, 30);
  }

  // ---------------------------------------------------------------
  // 自动更新：设置卡 + 新版本浮层 + 下载校验自替换
  // ---------------------------------------------------------------
  function setUpdHint(text, ok) {
    var el = document.getElementById('xqUpdHint');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = ok ? '#1a7f37' : '#c0392b';
  }
  function closeUpdDialog() {
    stopUpdPoll();
    var ov = document.getElementById('xqUpdDialog');
    if (ov) ov.parentNode.removeChild(ov);
  }
  var _updPollTimer = null;
  var _updBusy = false;
  function stopUpdPoll() {
    if (_updPollTimer) { clearInterval(_updPollTimer); _updPollTimer = null; }
    _updBusy = false;
  }
  function startUpdPoll() {
    stopUpdPoll();
    _updPollTimer = setInterval(function () {
      if (_updBusy) return;
      _updBusy = true;
      postCommand('update_progress');
      setTimeout(function () { _updBusy = false; }, 350);
    }, 260);
  }
  function setUpdProgress(pg) {
    var wrap = document.getElementById('xqUpdProgWrap');
    var bar = document.getElementById('xqUpdProgBar');
    var txt = document.getElementById('xqUpdProgText');
    if (!wrap || !bar || !txt) return;
    wrap.style.display = 'block';
    var pct = Math.max(0, Math.min(100, Number(pg.percent) || 0));
    if (pg.phase === 'verify' || pg.phase === 'done' || pg.phase === 'applying') pct = 100;
    bar.style.width = pct + '%';
    var mb = (Number(pg.got) || 0) / 1048576;
    var tot = (Number(pg.total) || 0) / 1048576;
    var spd = (Number(pg.speed) || 0) / 1048576;
    if (pg.phase === 'download') {
      txt.style.color = '';
      txt.textContent = '正在下载 ' + pct + '%' + (tot > 0.1 ? '（' + mb.toFixed(1) + ' / ' + tot.toFixed(1) + ' MB）' : '') +
        (spd > 0.05 ? ' · ' + spd.toFixed(2) + ' MB/s' : '');
    } else if (pg.phase === 'verify') {
      txt.textContent = '下载完成，正在校验 SHA256…';
    } else if (pg.phase === 'done') {
      txt.textContent = '校验通过，即将替换旧版本并重启…';
    } else if (pg.phase === 'applying') {
      txt.textContent = '正在替换旧版本，程序马上重启…';
    } else if (pg.phase === 'error') {
      txt.textContent = pg.msg || '更新失败';
      txt.style.color = '#c0392b';
      var okBtn = document.getElementById('xqUpdOk');
      if (okBtn) { okBtn.disabled = false; okBtn.textContent = '重试'; }
      stopUpdPoll();
    } else {
      txt.textContent = pg.msg || '准备下载…';
    }
  }
  function openUpdDialog(info) {
    closeUpdDialog();
    var dark = libDark();
    var cardBg = dark ? '#232a35' : '#ffffff';
    var fg = dark ? '#f2f6fc' : '#16304d';
    var sub = dark ? '#b7c3d6' : '#5b7694';
    var line = dark ? 'rgba(255,255,255,.22)' : 'rgba(96,142,198,.35)';
    var ov = document.createElement('div');
    ov.id = 'xqUpdDialog';
    ov.style.cssText = 'position:fixed;inset:0;z-index:10007;display:flex;align-items:center;justify-content:center;' +
      'background:rgba(10,14,20,.45);backdrop-filter:blur(3px);';
    ov.innerHTML =
      '<div style="width:340px;padding:26px 26px 22px;border-radius:16px;background:' + cardBg + ';' +
        'border:1px solid ' + line + ';box-shadow:0 18px 48px rgba(0,0,0,.35);color:' + fg + '">' +
        '<div style="font-size:16px;font-weight:700;margin-bottom:6px">发现新版本 v' + escHtml(info.version || '') + '</div>' +
        '<div style="font-size:12px;color:' + sub + ';margin-bottom:12px;line-height:1.6">' +
          (info.notes ? escHtml(info.notes) + '<br>' : '') + '下载完成后自动校验 SHA256，进度条走满即替换并重启；校验失败不会改动现有程序。</div>' +
        '<div id="xqUpdProgWrap" style="display:none;margin:0 0 14px">' +
          '<div style="height:6px;border-radius:4px;background:rgba(120,140,170,.25);overflow:hidden">' +
            '<div id="xqUpdProgBar" style="height:100%;width:0%;border-radius:4px;background:linear-gradient(90deg,#2f86f6,#4fb3ff);transition:width .2s"></div>' +
          '</div>' +
          '<div id="xqUpdProgText" style="font-size:12px;color:' + sub + ';margin-top:8px">准备下载…</div>' +
        '</div>' +
        '<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:6px">' +
          '<button id="xqUpdCancel" style="border:1px solid ' + line + ';border-radius:10px;padding:7px 16px;' +
            'font-size:13px;cursor:pointer;background:transparent;color:' + sub + '">稍后</button>' +
          '<button id="xqUpdOk" style="border:0;border-radius:10px;padding:7px 18px;font-size:13px;font-weight:700;' +
            'cursor:pointer;background:linear-gradient(180deg,#2f86f6,#1a63d8);color:#fff">立即更新</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(ov);
    document.getElementById('xqUpdOk').addEventListener('click', function () {
      var btn = document.getElementById('xqUpdOk');
      if (!btn || btn.disabled) return;
      btn.disabled = true;
      btn.textContent = '下载中…';
      setUpdHint('正在下载更新包…', true);
      setUpdProgress({ phase: 'download', percent: 0, msg: '正在连接下载线路…' });
      startUpdPoll();
      postCommand('update_download|' + encodeURIComponent(info.url || '') + '|' + (info.sha256 || '') + '|' + encodeURIComponent(info.version || ''));
    });
    document.getElementById('xqUpdCancel').addEventListener('click', closeUpdDialog);
  }
  function handleUpdateMessage(s) {
    if (s.indexOf('update_state|') === 0) {
      try {
        var st = JSON.parse(s.slice('update_state|'.length));
        var v = document.getElementById('xqUpdVer');
        if (v) v.textContent = '当前 v' + st.version;
        var b = document.getElementById('xqUpdBase');
        if (b && !b.value) b.value = st.base || '';
        var a = document.getElementById('xqUpdAuto');
        if (a) a.checked = !!st.auto;
        var sl = document.getElementById('xqUpdSilent');
        if (sl) sl.checked = !!st.silent;
      } catch (e) { /* ignore */ }
      return true;
    }
    if (s.indexOf('update_config_result|') === 0) {
      var cp = s.split('|');
      setUpdHint(cp[2] || (cp[1] === 'success' ? '已保存' : '保存失败'), cp[1] === 'success');
      return true;
    }
    if (s.indexOf('update_check_result|') === 0) {
      clearUpdCheckTimer();
      var rest = s.slice('update_check_result|'.length);
      var mode = rest.split('|')[0];
      var info = {};
      try { info = JSON.parse(rest.slice(mode.length + 1)); } catch (e) { /* ignore */ }
      if (info.status === 'available') {
        setUpdHint((info.msg || '发现新版本') + '，可立即更新', true);
        openUpdDialog(info);
      } else if (info.status === 'silent_ready' || info.status === 'silent_armed') {
        setUpdHint(info.msg || '新版本已就绪，退出后自动套用', true);
      } else if (info.status === 'latest') {
        setUpdHint(info.msg || '已是最新版本', true);
      } else if (info.status === 'nobase') {
        if (mode === 'manual') setUpdHint('请先填写更新源直链前缀并保存', false);
      } else {
        setUpdHint(info.msg || '检查更新失败', false);
      }
      return true;
    }
    if (s.indexOf('update_progress|') === 0) {
      var pg = {};
      try { pg = JSON.parse(s.slice('update_progress|'.length)); } catch (e) { return true; }
      setUpdProgress(pg);
      if (pg.phase === 'done' || pg.phase === 'applying') {
        setUpdHint(pg.msg || '校验通过，即将替换并重启…', true);
        var btnDone = document.getElementById('xqUpdOk');
        if (btnDone) { btnDone.disabled = true; btnDone.textContent = '即将重启…'; }
        var ccDone = document.getElementById('xqUpdCancel');
        if (ccDone) ccDone.style.display = 'none';
      } else if (pg.phase === 'error') {
        setUpdHint(pg.msg || '更新失败', false);
      }
      return true;
    }
    if (s.indexOf('update_apply_result|') === 0) {
      var ap = s.split('|');
      var okBtn = document.getElementById('xqUpdOk');
      if (ap[1] === 'started') {
        setUpdHint(ap[2] || '即将重启完成更新', true);
        if (okBtn) { okBtn.disabled = true; okBtn.textContent = '正在重启…'; }
        var cc = document.getElementById('xqUpdCancel');
        if (cc) cc.style.display = 'none';
      } else {
        setUpdHint(ap[2] || '更新失败', false);
        if (okBtn) { okBtn.disabled = false; okBtn.textContent = '立即更新'; }
      }
      return true;
    }
    return false;
  }
  // ---------------------------------------------------------------
  // 远程调试：把 X·Q Desk 的本机 ID / 密码 / 近期连接搬到调试页
  // ---------------------------------------------------------------
  var xrRoot = null;
  var xrData = null;
  var xrShowPwd = false;

  function xrFmtId(value) {
    var digits = String(value || '').replace(/\D/g, '');
    if (digits.length === 9) return digits.slice(0, 3) + ' ' + digits.slice(3, 6) + ' ' + digits.slice(6);
    return digits || '-- -- --';
  }

  function xrFmtTime(sec) {
    var n = Number(sec) || 0;
    if (n <= 0) return '';
    var d = new Date(n * 1000);
    if (isNaN(d.getTime())) return '';
    var mm = ('0' + (d.getMonth() + 1)).slice(-2);
    var dd = ('0' + d.getDate()).slice(-2);
    var hh = ('0' + d.getHours()).slice(-2);
    var mi = ('0' + d.getMinutes()).slice(-2);
    return mm + '-' + dd + ' ' + hh + ':' + mi;
  }

  function xrSetTip(el, text, kind) {
    if (!el) return;
    el.textContent = text || '';
    el.className = 'xr-tip' + (kind === 'ok' ? ' is-ok' : kind === 'bad' ? ' is-bad' : '');
  }

  function xrRenderRecent(rows) {
    var list = xrRoot && xrRoot.querySelector('#xrRecentList');
    if (!list) return;
    if (!rows || !rows.length) {
      list.innerHTML = '<div class="xr-empty">还没有连接记录。连过一次之后，这里会留着他那台机器的 ID 和缩略图。</div>';
      return;
    }
    var html = '';
    rows.forEach(function (row) {
      var id = String(row.id || '');
      var host = row.host || ('设备 ' + xrFmtId(id));
      var stamp = xrFmtTime(row.last);
      var thumbStyle = row.thumb ? " style=\"background-image:url('" + row.thumb + "')\"" : '';
      html += '<div class="xr-rc" data-peer="' + escHtml(id) + '" title="点击填入对端 ID">' +
        '<div class="xr-rc-thumb"' + thumbStyle + '></div>' +
        (stamp ? '<div class="xr-rc-off"><i></i>' + escHtml(stamp) + '</div>' : '') +
        '<div class="xr-rc-meta"><div class="xr-rc-host">' + escHtml(host) + '</div>' +
        '<div class="xr-rc-id">' + escHtml(id) + '</div></div>' +
        '<span class="xr-rc-go">→</span>' +
        '</div>';
    });
    list.innerHTML = html;
    list.querySelectorAll('.xr-rc').forEach(function (card) {
      card.addEventListener('click', function () {
        var peer = card.getAttribute('data-peer') || '';
        var input = xrRoot.querySelector('#xrPeerId');
        if (input) { input.value = peer; input.focus(); }
        xrSetTip(xrRoot.querySelector('#xrTip'), '已填入 ' + peer + '，点「连接」开始', '');
      });
    });
  }

  function xrRender(state) {
    if (!xrRoot) return;
    xrData = state || {};
    var self = xrData.self || {};
    var app = xrData.app || {};
    var cfg = xrData.cfg || {};
    var idEl = xrRoot.querySelector('#xrSelfId');
    if (idEl) idEl.textContent = xrFmtId(self.id);
    var pwdEl = xrRoot.querySelector('#xrSelfPwd');
    if (pwdEl) pwdEl.textContent = self.password ? (xrShowPwd ? self.password : '••••••') : '未设置';
    var dot = xrRoot.querySelector('#xrDot');
    var foot = xrRoot.querySelector('#xrFootState');
    var online = !!self.online;
    if (dot) dot.className = 'xr-dot' + (online ? '' : ' is-off');
    if (foot) {
      foot.textContent = online ? '已连接服务器'
        : (xrData.available ? 'X·Q Desk 在运行，但没连上服务器' : '未检测到 X·Q Desk');
    }
    var ver = xrRoot.querySelector('#xrFootVer');
    if (ver) ver.textContent = app.version ? 'v' + app.version : '';
    var portEl = xrRoot.querySelector('#xrIpPort');
    if (portEl) portEl.textContent = cfg.port || 52888;
    var ipEl = xrRoot.querySelector('#xrIpEnabled');
    if (ipEl) ipEl.textContent = cfg.ip_direct ? '已开启' : '未开启';
    var shEl = xrRoot.querySelector('#xrSelfHosted');
    if (shEl) shEl.textContent = cfg.self_hosted ? '已启用' : '未启用';
    if (!xrData.available) {
      xrSetTip(xrRoot.querySelector('#xrTip'), xrData.msg || '未检测到 X·Q Desk', 'bad');
    }
    xrRenderRecent(xrData.recent);
  }

  function xrOnAction(res) {
    if (!xrRoot) return;
    var isIp = res && res.target === 'ip';
    var tip = xrRoot.querySelector(isIp ? '#xrIpTip' : '#xrTip');
    var btn = xrRoot.querySelector('#xrConnectBtn');
    if (btn) { btn.disabled = false; btn.textContent = '连接'; }
    if (res && res.state) xrRender(res.state);
    if (res && res.ok) {
      xrSetTip(tip, res.msg || '已复制，去 X·Q Desk 里确认连接', 'ok');
    } else {
      xrSetTip(tip, (res && res.msg) || '操作失败', 'bad');
    }
  }

  function xrDoConnect() {
    if (!xrRoot) return;
    var input = xrRoot.querySelector('#xrPeerId');
    var btn = xrRoot.querySelector('#xrConnectBtn');
    var tip = xrRoot.querySelector('#xrTip');
    var peer = String((input && input.value) || '').replace(/\D/g, '');
    if (!peer) { xrSetTip(tip, '请输入对方设备的 ID', 'bad'); if (input) input.focus(); return; }
    if (peer.length < 6) { xrSetTip(tip, 'ID 位数不对，通常是 9 位数字', 'bad'); return; }
    if (btn) { btn.disabled = true; btn.textContent = '准备中…'; }
    xrSetTip(tip, '正在复制 ID 并打开 X·Q Desk…', '');
    postCommand('remote_connect|' + encodeURIComponent(peer));
  }

  window.xqRemoteMount = function (root) {
    xrRoot = root;
    var tabs = root.querySelectorAll('.xr-tab');
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        var mode = tab.getAttribute('data-xr-mode');
        tabs.forEach(function (other) {
          var on = other === tab;
          other.classList.toggle('is-active', on);
          other.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        root.querySelectorAll('[data-xr-pane]').forEach(function (pane) {
          pane.classList.toggle('is-hidden', pane.getAttribute('data-xr-pane') !== mode);
        });
      });
    });
    var tip = root.querySelector('#xrTip');
    var copyBtn = root.querySelector('#xrCopyId');
    if (copyBtn) copyBtn.addEventListener('click', function () {
      postCommand('remote_copy|id');
    });
    var refreshBtn = root.querySelector('#xrRefreshBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', function () {
      xrSetTip(tip, '正在重新读取 X·Q Desk…', '');
      postCommand('remote_state');
    });
    var eyeBtn = root.querySelector('#xrEyeBtn');
    if (eyeBtn) eyeBtn.addEventListener('click', function () {
      xrShowPwd = !xrShowPwd;
      xrRender(xrData || {});
    });
    var editBtn = root.querySelector('#xrEditBtn');
    if (editBtn) editBtn.addEventListener('click', function () {
      postCommand('remote_open');
      xrSetTip(tip, '已打开 X·Q Desk：密码在它的「设置 → 安全」里改，改完点刷新', '');
    });
    var connectBtn = root.querySelector('#xrConnectBtn');
    if (connectBtn) connectBtn.addEventListener('click', xrDoConnect);
    var peerInput = root.querySelector('#xrPeerId');
    if (peerInput) {
      peerInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') xrDoConnect();
      });
      peerInput.addEventListener('input', function () {
        var cleaned = String(peerInput.value || '').replace(/[^\d]/g, '').slice(0, 12);
        if (cleaned !== peerInput.value) peerInput.value = cleaned;
      });
    }
    ['#xrOpenBtn', '#xrIpOpenBtn', '#xrWallOpenBtn'].forEach(function (sel) {
      var btn = root.querySelector(sel);
      if (btn) btn.addEventListener('click', function () { postCommand('remote_open'); });
    });
    var ipBtn = root.querySelector('#xrIpConnectBtn');
    if (ipBtn) ipBtn.addEventListener('click', function () {
      var ipInput = root.querySelector('#xrIpInput');
      var ipTip = root.querySelector('#xrIpTip');
      var ip = String((ipInput && ipInput.value) || '').trim();
      if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)) {
        xrSetTip(ipTip, 'IP 格式不对，例如 192.168.1.23', 'bad');
        return;
      }
      xrSetTip(ipTip, '已复制 IP，正在打开 X·Q Desk…', '');
      postCommand('remote_clip|' + encodeURIComponent(ip));
    });
    postCommand('remote_state');
  };

  window.xqUpdRefresh = function () { postCommand('update_state'); };
  window.xqAutostartRefresh = function () { postCommand('autostart_state'); };
  window.xqAutostartSet = function (on) { postCommand('autostart_set|' + (on ? '1' : '0')); };
  window.xqUpdSave = function () {
    var b = document.getElementById('xqUpdBase');
    var a = document.getElementById('xqUpdAuto');
    var sl = document.getElementById('xqUpdSilent');
    postCommand('update_config|' + ((a && a.checked) ? '1' : '0') + '|' + encodeURIComponent(b ? b.value.trim() : '') + '|' + ((sl && sl.checked) ? '1' : '0'));
  };
  var _updCheckTimer = null;
  function clearUpdCheckTimer() {
    if (_updCheckTimer) { clearTimeout(_updCheckTimer); _updCheckTimer = null; }
  }
  window.xqUpdCheck = function () {
    setUpdHint('正在检查更新…', true);
    clearUpdCheckTimer();
    _updCheckTimer = setTimeout(function () {
      _updCheckTimer = null;
      setUpdHint('检查更新超时（15 秒无响应），请检查网络或更新源地址后重试', false);
    }, 15000);
    postCommand('update_check|manual');
  };

  function libLockPanel(container) {
    container.innerHTML =
      '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;padding-bottom:12%">' +
        '<svg style="width:32px;height:32px;color:#bbb;margin-bottom:12px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>' +
        '<p style="font-size:13px;color:#999;margin-bottom:20px;letter-spacing:1px">该栏目需要查看密码</p>' +
        '<input id="xqLibPwd" type="password" maxlength="64" autocomplete="off" spellcheck="false" ' +
          'style="width:260px;border:none;border-bottom:1px solid #ddd;background:transparent;padding:8px 12px;font-size:14px;text-align:center;outline:none" ' +
          'placeholder="请输入查看密码">' +
      '</div>';
    var input = document.getElementById('xqLibPwd');
    if (!input) return;
    var submit = function () {
      var val = String(input.value || '');
      if (!val) { libNotice('请输入查看密码'); return; }
      postCommand('check_password|' + val);
    };
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); submit(); }
    });
    input.addEventListener('input', function () {
      if (String(input.value || '').length >= 8) { submit(); }
    });
    setTimeout(function () { try { input.focus(); } catch (e) { /* ignore */ } }, 60);
  }

  window.xqShowLibrary = function () {
    window.xqLibActive = true;
    window.xqLibAfterUnlock = window.xqShowLibrary;
    var sidebar = document.getElementById('sidebar');
    if (sidebar) {
      Array.prototype.forEach.call(sidebar.querySelectorAll('.nav-item'), function (n) { n.classList.remove('active'); });
      var nav = document.getElementById('xqLibNav');
      if (nav) nav.classList.add('active');
    }
    var header = document.getElementById('pageHeaderWrap');
    if (header) header.style.display = '';
    var title = document.getElementById('pageTitle');
    if (title) title.textContent = '小庆自用';
    var sub = document.getElementById('pageSubtitle');
    if (sub) {
      sub.textContent = '自建库清单由网页后台维护，这里可以直接下载';
      sub.style.display = 'block';
    }
    ['categorySearchWrap', 'openFolderBtn', 'clearHistoryBtn'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    var container = document.getElementById('contentContainer');
    if (libLocked === true) {
      if (container) libLockPanel(container);
      return;
    }
    if (libLocked === null) {
      if (container) container.innerHTML = '<div id="xqLibRoot" style="color:' + libMuted() + ';font-size:13px">正在核对查看密码状态…</div>';
      postCommand('local_password_lock_state');
      return;
    }
    if (container) {
      container.innerHTML = '<div id="xqLibRoot" style="padding:2px 0 24px"><div style="color:' + libMuted() + ';font-size:13px">正在读取自建库…</div></div>';
    }
    if (libPayload) renderLibrary(null);
    else postCommand('my_library');
    postCommand('dock_list');
  };

  // 点其它侧栏项时退出“小庆自用”页面状态，避免解锁后误跳回
  document.addEventListener('click', function (e) {
    var item = e.target && e.target.closest ? e.target.closest('.nav-item') : null;
    if (item && item.id !== 'xqLibNav') {
      window.xqLibActive = false;
      window.xqLibAfterUnlock = null;
    }
  }, true);

  // 原生 -> 页面：Python 通过 evaluate_js 调用这个函数
  window.__wvPush = function (message) { dispatch(String(message)); };

  function apiReady() {
    return !!(window.pywebview && window.pywebview.api && window.pywebview.api.post_message);
  }

  function postToPython(message) {
    try {
      wakePoll();
      var p = window.pywebview.api.post_message(message);
      /* RPC 偶发拒绝（原生侧还没接好）时重排，避免启动竞态丢命令导致页面停在「正在读取」 */
      if (p && typeof p.catch === 'function') {
        p.catch(function () { requeue(message); });
      }
    } catch (e) {
      requeue(message);
    }
  }

  var retryCount = {};
  function requeue(message) {
    var n = (retryCount[message] || 0) + 1;
    retryCount[message] = n;
    if (n <= 5) {
      queue.push(message);
      setTimeout(flush, 150);
    }
  }

  function flush() {
    if (flushing || !apiReady()) return false;
    flushing = true;
    ready = true;
    /* 批量快照出队：postToPython 失败会把消息重新入队，
       若用 while(queue.length) 边出边进会在同步异常时死循环卡死界面 */
    var batch = queue.splice(0, queue.length);
    for (var i = 0; i < batch.length; i++) { postToPython(batch[i]); }
    flushing = false;
    return true;
  }

  var chrome = window.chrome || (window.chrome = {});
  var shim = chrome.webview && typeof chrome.webview.postMessage === 'function' ? chrome.webview : {};

  // 只拦截字符串（原程序的协议）；数组/其它类型是 pywebview 自身的调用，交给原生桥
  chrome.webview = {
    postMessage: function (message) {
      if (typeof message !== 'string') {
        if (nativePost) { return nativePost(message); }
        return undefined;
      }
      if (ready && apiReady()) { postToPython(message); } else { queue.push(message); flush(); }
      return undefined;
    },
    postMessageWithAdditionalObjects: shim.postMessageWithAdditionalObjects
      ? shim.postMessageWithAdditionalObjects.bind(shim)
      : (nativeBridge && nativeBridge.postMessageWithAdditionalObjects
        ? nativeBridge.postMessageWithAdditionalObjects.bind(nativeBridge)
        : undefined),
    addEventListener: function (type, cb) {
      if (type === 'message' && typeof cb === 'function') { listeners.push(cb); }
    },
    removeEventListener: function (type, cb) {
      var index = listeners.indexOf(cb);
      if (index >= 0) { listeners.splice(index, 1); }
    }
  };

  /* pywebview 偶发注入失败（window.pywebview.api 迟迟不出现）时，
     按原生 RPC 线协议自建一套最小 api，直接走 chrome.webview.postMessage，
     保证桥不会永久静默。正常注入完成会覆盖它，互不冲突。 */
  function installFallbackApi() {
    if (apiReady() || !nativePost) return;
    var pw = window.pywebview || (window.pywebview = {});
    pw._returnValuesCallbacks = pw._returnValuesCallbacks || {};
    pw.api = pw.api || {};
    function call(funcName, args) {
      var id = (Math.random() + '').substring(2);
      var promise = new Promise(function (resolve, reject) {
        var reg = pw._returnValuesCallbacks[funcName] || (pw._returnValuesCallbacks[funcName] = {});
        reg[id] = function (ret) {
          try { delete reg[id]; } catch (e) { /* ignore */ }
          if (ret && ret.isError) { reject(new Error(String(ret.value))); return; }
          try { resolve(JSON.parse(ret.value)); } catch (e) { resolve(ret && ret.value); }
        };
      });
      try { nativePost([funcName, JSON.stringify(args), id]); }
      catch (e) { setTimeout(function () { try { nativePost([funcName, JSON.stringify(args), id]); } catch (e2) { /* ignore */ } }, 200); }
      return promise;
    }
    pw.api.post_message = function (m) { return call('post_message', [m]); };
    pw.api.poll = function (n) { return call('poll', [n]); };
    flush();
    askLockState();
    wakePoll();
  }
  setTimeout(installFallbackApi, 1200);
  setTimeout(installFallbackApi, 3000);

  var timer = setInterval(function () { if (flush() && !queue.length) { clearInterval(timer); timer = 0; } }, 25);

  // 启动时问一次本机密码状态：已启用则通知页面进入上锁状态
  var lockAsked = false;
  function askLockState() {
    if (lockAsked || !apiReady()) return;
    lockAsked = true;
    setTimeout(function () { window.xqPasswordRefresh(); }, 300);
    setTimeout(function () { postCommand('update_state'); }, 500);
  }

  // 拉取原生队列（不使用 evaluate_js，避免跨线程调用界面线程）
  // 自适应节奏：有消息或刚发过命令时 70ms 快轮询；持续空闲降到 240ms，省掉无谓的 IPC
  var POLL_FAST = 70, POLL_IDLE = 240, POLL_IDLE_AFTER = 20;
  var pollDelay = POLL_FAST, pollIdle = 0, pollTimer = 0;

  function wakePoll() {
    pollIdle = 0;
    if (pollDelay !== POLL_FAST) {
      pollDelay = POLL_FAST;
      if (pollTimer) { clearTimeout(pollTimer); pollTimer = setTimeout(pollTick, POLL_FAST); }
    }
  }

  function pollTick() {
    pollTimer = 0;
    if (!apiReady() || typeof window.pywebview.api.poll !== 'function') {
      pollTimer = setTimeout(pollTick, POLL_FAST);
      return;
    }
    askLockState();
    var settled = false, guard = 0;
    function settle(count) {
      if (settled) return;
      settled = true;
      if (guard) { clearTimeout(guard); guard = 0; }
      if (count > 0) { pollIdle = 0; pollDelay = POLL_FAST; }
      else if (++pollIdle >= POLL_IDLE_AFTER) { pollDelay = POLL_IDLE; }
      pollTimer = setTimeout(pollTick, pollDelay);
    }
    // 兜底：万一某次 poll 的 promise 不落地，也要让轮询继续转
    guard = setTimeout(function () { settle(0); }, 1000);
    try {
      var pending = window.pywebview.api.poll(40);
      if (pending && typeof pending.then === 'function') {
        pending.then(function (items) {
          if (items && items.length) { items.forEach(dispatch); }
          settle(items ? items.length : 0);
        }, function () { settle(0); });
      } else {
        if (pending && pending.length) { pending.forEach(dispatch); }
        settle(pending ? pending.length : 0);
      }
    } catch (e) { settle(0); }
  }
  pollTimer = setTimeout(pollTick, POLL_FAST);

  window.addEventListener('pywebviewready', function () { flush(); askLockState(); wakePoll(); });
  document.addEventListener('DOMContentLoaded', flush);

  // 侧栏由页面自己渲染，这里轮询保证「小庆自用」始终紧跟在「软件下载」后面
  setInterval(function () { try { ensureLibraryNav(); } catch (e) { /* ignore */ } }, 800);
})();

/* 顶部彩色高亮滚动字幕：每日励志短句循环 */
(function () {
  var QUOTES = [
    '今天多练一分钟，明天少慌一整场',
    '慢一点没关系，别停就好',
    '把小事做透，运气自然找上门',
    '先完成，再完美',
    '你不是没天赋，只是还没练够',
    '稳住手上的活，胜过想一万遍',
    '每天进步一点点，年底吓自己一跳',
    '工具是死的，手艺是活的',
    '听得清细节的人，才调得出好声音',
    '别怕重来，怕的是不敢开始',
    '现在的枯燥，是以后的底气',
    '把标准提上去，路自然宽起来',
    '情绪稳住，手上就稳',
    '别跟别人比快，跟自己比准',
    '重复到不用想，才算真会',
    '先解决眼前这一步，下一步自然清楚',
    '认真做事的人，运气都不会太差',
    '能力是练出来的，不是等出来的',
    '今天的难，是明天的轻松',
    '把活儿干漂亮，话就不用多',
    '专注一件事，胜过忙十件半成品',
    '手艺人的底气，来自每一次较真',
    '只要方向对，慢也是前进',
    '你熬过的夜，都在给你攒本事',
    '不将就，才配得上好结果',
    '把问题拆小，就没有过不去的坎',
    '肯下笨功夫的人，最后都赢了',
    '每天学一点，久了就是差距',
    '手上功夫越硬，心里越不慌',
    '别等状态好，先干起来状态就来了',
    '稳住节奏，别被别人的进度带跑',
    '把今天做好，就是最好的规划'
  ];
  var SEP = '   ✦   ';
  var marqueeEl = null, trackEl = null, chunkA = null, chunkB = null;
  var offset = 0;
  var pos = 0, chunkWidth = 0, lastTs = 0, rafId = null;
  var SPEED = 52; // px / 秒

  function dayIndex() {
    var d = new Date();
    var n = d.getFullYear() * 372 + (d.getMonth() + 1) * 31 + d.getDate();
    return n % QUOTES.length;
  }

  function build() {
    if (!marqueeEl || !chunkA || !chunkB) return;
    var text = '';
    for (var i = 0; i < 4; i++) {
      var q = QUOTES[(dayIndex() + offset + i) % QUOTES.length];
      text += (i ? SEP : '') + q;
    }
    text += SEP;
    chunkA.textContent = text;
    chunkB.textContent = text;
    var width = chunkA.getBoundingClientRect().width;
    if (width > 0) chunkWidth = width;
  }

  function step(ts) {
    rafId = window.requestAnimationFrame(step);
    if (!chunkA || !trackEl) return;
    if (!lastTs) { lastTs = ts; return; }
    var dt = Math.min(150, ts - lastTs) / 1000;
    lastTs = ts;
    if (!chunkWidth) {
      chunkWidth = chunkA.getBoundingClientRect().width || 0;
      if (!chunkWidth) return;
    }
    pos -= SPEED * dt;
    while (pos <= -chunkWidth) pos += chunkWidth;
    trackEl.style.transform = 'translate3d(' + Math.round(pos) + 'px, 0, 0)';
  }

  function init() {
    marqueeEl = document.getElementById('xqMarquee');
    trackEl = document.getElementById('xqMarqueeTrack');
    chunkA = document.getElementById('xqMarqueeChunkA');
    chunkB = document.getElementById('xqMarqueeChunkB');
    if (!marqueeEl || !chunkA || !chunkB) return;
    build();
    if (window.requestAnimationFrame && !rafId) rafId = window.requestAnimationFrame(step);
    // 每 3 分钟换一批句子，滚动不间断
    setInterval(function () {
      offset = (offset + 4) % QUOTES.length;
      build();
    }, 180000);
    var resizeTimer = null;
    window.addEventListener('resize', function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(build, 260);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

/* 侧栏音乐组件：跟随 Windows 系统媒体会话，显示封面/曲名/歌词并控制播放 */
(function () {
  var panel = null;
  var timer = null;
  var lastKey = '';
  var lastPlaying = null;

  var ICON_NOTE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l10-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="16" cy="16" r="3"/></svg>';
  // 小表情人（纯 SVG 自绘，白色圆脸，播放时浮动/眨眼/张嘴唱歌，暂停静止）
  var PET_SVG = '<svg class="xq-mu-face" data-face viewBox="0 0 48 48" aria-hidden="true">' +
    '<defs><radialGradient id="xqPetG" cx="46%" cy="28%" r="76%">' +
      '<stop offset="0%" stop-color="#ffffff"/><stop offset="68%" stop-color="#eaf3ff"/><stop offset="100%" stop-color="#c6dcfa"/>' +
    '</radialGradient></defs>' +
    '<circle cx="24" cy="26" r="15.6" fill="url(#xqPetG)" stroke="rgba(118,166,228,.55)" stroke-width="1.1"/>' +
    '<circle class="xq-pet-blush" cx="14.6" cy="30.6" r="2.9" fill="#ffb0c4" opacity=".55"/>' +
    '<circle class="xq-pet-blush" cx="33.4" cy="30.6" r="2.9" fill="#ffb0c4" opacity=".55"/>' +
    '<g class="xq-pet-eye">' +
      '<ellipse cx="18.8" cy="24.4" rx="2.5" ry="3.1" fill="#2f4c6e"/>' +
      '<ellipse cx="29.2" cy="24.4" rx="2.5" ry="3.1" fill="#2f4c6e"/>' +
      '<circle cx="17.9" cy="23.3" r=".85" fill="#fff" opacity=".9"/>' +
      '<circle cx="28.3" cy="23.3" r=".85" fill="#fff" opacity=".9"/>' +
    '</g>' +
    '<path class="xq-pet-mouth" d="M19.8 31.6q4.2 3 8.4 0" stroke="#2f4c6e" stroke-width="1.6" stroke-linecap="round" fill="none"/>' +
    '<g class="xq-pet-note">' +
      '<path d="M35.4 12.6v-5.6l5.2-1.3v4.1" stroke="#7fb2ef" stroke-width="1.3" fill="none" stroke-linecap="round"/>' +
      '<circle cx="33.5" cy="13.4" r="2.2" fill="#7fb2ef"/><circle cx="38.7" cy="10.4" r="2.2" fill="#7fb2ef"/>' +
    '</g>' +
  '</svg>';
  var ICON_PREV = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h2v14H7zM19 5v14l-9-7z"/></svg>';
  var ICON_NEXT = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M15 5h2v14h-2zM5 5l9 7-9 7z"/></svg>';
  var ICON_PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5l11 7-11 7z"/></svg>';
  var ICON_PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h3.4v14H7zM13.6 5H17v14h-3.4z"/></svg>';

  function panelHtml() {
    return '<div class="xq-mu-head"><span>音乐</span><span class="xq-mu-src" data-src></span></div>' +
      '<div class="xq-mu-main">' +
        '<div class="xq-mu-art" data-art><span class="xq-mu-cover" data-coverbg></span><span class="xq-mu-note">' + ICON_NOTE + '</span></div>' +
        '<div class="xq-mu-meta">' +
          '<div class="xq-mu-title" data-title>等待系统音乐</div>' +
          '<div class="xq-mu-artist" data-artist>开始播放后显示曲名与封面</div>' +
        '</div>' +
        '<span class="xq-mu-pet" data-pet>' + PET_SVG + '</span>' +
      '</div>' +
      '<div class="xq-mu-lyric" data-lyric></div>' +
      '<div class="xq-mu-bar"><i data-progress></i></div>' +
      '<div class="xq-mu-ctrl">' +
        '<button type="button" data-act="prev" title="上一曲">' + ICON_PREV + '</button>' +
        '<button type="button" data-act="playpause" class="is-primary" title="播放/暂停">' + ICON_PLAY + '</button>' +
        '<button type="button" data-act="next" title="下一曲">' + ICON_NEXT + '</button>' +
      '</div>' +
      '<div class="xq-mu-foot" data-foot>暂无音乐播放</div>';
  }

  function ensurePanel() {
    var sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    var existing = document.getElementById('xqMusic');
    if (existing && existing.parentElement === sidebar) { panel = existing; return; }
    if (existing && existing.parentElement) existing.parentElement.removeChild(existing);
    var el = document.createElement('div');
    el.className = 'xq-music';
    el.id = 'xqMusic';
    el.innerHTML = panelHtml();
    el.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('button[data-act]') : null;
      if (!btn || btn.disabled) return;
      e.preventDefault();
      try { window.chrome.webview.postMessage('music_control|' + btn.getAttribute('data-act')); } catch (err) { /* ignore */ }
      setTimeout(request, 220);
    });
    var sep = sidebar.querySelector('.nav-item.nav-separator');
    if (sep && sep.parentElement === sidebar) sidebar.insertBefore(el, sep);
    else sidebar.appendChild(el);
    panel = el;
    lastKey = '';
    lastPlaying = null;
  }

  function q(sel) { return panel ? panel.querySelector(sel) : null; }

  function fmt(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function apply(d) {
    if (!panel || !d) return;
    var active = !!d.active;
    panel.classList.toggle('is-active', active);

    var title = q('[data-title]'), artist = q('[data-artist]');
    if (title) {
      title.textContent = active ? (d.title || '未知曲目') : '等待系统音乐';
      title.title = active ? (d.title || '') : '';
    }
    if (artist) {
      artist.textContent = active ? (d.artist || d.album || '未知歌手') : '播放后显示曲名与封面';
      artist.title = active ? (d.artist || '') : '';
    }

    var art = q('[data-art]');
    if (art) {
      var key = active ? (d.cover || '') : '';
      if (key !== lastKey) {
        lastKey = key;
        var coverEl = q('[data-coverbg]');
        if (key) {
          if (coverEl) coverEl.style.backgroundImage = 'url("' + key + '")';
          art.classList.add('has-cover');
        } else {
          if (coverEl) coverEl.style.backgroundImage = '';
          art.classList.remove('has-cover');
        }
      }
    }

    var playing = d.status === 'playing';
    panel.classList.toggle('is-playing', active && playing);
    var playBtn = q('button[data-act="playpause"]');
    if (playBtn && playing !== lastPlaying) {
      lastPlaying = playing;
      playBtn.innerHTML = playing ? ICON_PAUSE : ICON_PLAY;
    }
    if (playBtn) playBtn.disabled = !active || !(d.canPlay || d.canPause);
    var prevBtn = q('button[data-act="prev"]'), nextBtn = q('button[data-act="next"]');
    if (prevBtn) prevBtn.disabled = !active || !d.canPrev;
    if (nextBtn) nextBtn.disabled = !active || !d.canNext;

    var lyric = q('[data-lyric]');
    if (lyric) {
      var text = '';
      if (!active) text = '';
      else if (d.lyric) text = d.lyric;
      else if (d.lyricState === 'loading') text = '歌词加载中…';
      else if (d.lyricState === 'none') text = '暂无歌词';
      lyric.textContent = text;
      lyric.title = text;
      lyric.style.display = text ? '' : 'none';
    }

    var bar = q('[data-progress]');
    if (bar) {
      var pct = (d.duration > 0) ? Math.max(0, Math.min(100, d.position / d.duration * 100)) : 0;
      bar.style.width = pct.toFixed(2) + '%';
    }

    var src = q('[data-src]');
    if (src) src.textContent = active ? (playing ? '播放中' : '已暂停') : '';

    var foot = q('[data-foot]');
    if (foot) {
      foot.textContent = active
        ? (fmt(d.position) + ' / ' + (d.duration > 0 ? fmt(d.duration) : '--:--'))
        : '暂无音乐播放';
    }
  }

  function request() {
    ensurePanel();
    if (!panel) return;
    try { window.chrome.webview.postMessage('music_snapshot'); } catch (e) { /* ignore */ }
  }

  function onMessage(event) {
    var s = String((event && event.data) || '');
    if (s.indexOf('music|') !== 0) return;
    var data = null;
    try { data = JSON.parse(s.slice('music|'.length)); } catch (e) { return; }
    apply(data);
  }

  function init() {
    ensurePanel();
    try { window.chrome.webview.addEventListener('message', onMessage); } catch (e) { /* ignore */ }
    if (timer) clearInterval(timer);
    timer = setInterval(function () {
      if (document.hidden) return;
      request();
    }, 1200);
    setInterval(ensurePanel, 700);
    setTimeout(request, 600);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();/* 侧栏天气块：Open-Meteo 免费接口，当前天气 + 7 天预报，城市可换、30 分钟自动刷新 */
(function () {
  'use strict';
  var CITIES = {
    '北京': [39.9042, 116.4074], '上海': [31.2304, 121.4737], '广州': [23.1291, 113.2644],
    '深圳': [22.5431, 114.0579], '成都': [30.5728, 104.0668], '杭州': [30.2741, 120.1551],
    '武汉': [30.5928, 114.3055], '西安': [34.3416, 108.9398], '哈尔滨': [45.8038, 126.5349],
    '乌鲁木齐': [43.8256, 87.6168], '大连': [38.9140, 121.6147], '苏州': [31.3041, 120.5954],
    '青岛': [36.0671, 120.3826], '重庆': [29.5630, 106.5516], '南京': [32.0603, 118.7969]
  };
  var WD = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  var card = null;

  function cfg() { try { return JSON.parse(localStorage.getItem('xq_wx_cfg') || '{}'); } catch (e) { return {}; } }
  function saveCfg(c) { try { localStorage.setItem('xq_wx_cfg', JSON.stringify(c)); } catch (e) { /* ignore */ } }
  function city() { var c = cfg(); return CITIES[c.city] ? c.city : '北京'; }
  function coords() {
    var c = cfg();
    if (c.custom && c.custom.lat != null && c.custom.lon != null) return [c.custom.lat, c.custom.lon];
    return CITIES[c.city] || CITIES['北京'];
  }
  function cityName() {
    var c = cfg();
    if (c.custom && c.custom.name) return c.custom.name;
    return city();
  }
  function locate(done) {
    var urls = ['https://ipapi.co/json/', 'https://ipwho.is/'];
    var i = 0;
    function next() {
      if (i >= urls.length) { done(null); return; }
      fetch(urls[i++]).then(function (r) { return r.json(); }).then(function (j) {
        if (!j || j.latitude == null || j.longitude == null) { next(); return; }
        done({ name: j.city || j.region || j.country_name || '当地', lat: j.latitude, lon: j.longitude });
      }).catch(next);
    }
    next();
  }
  function syncCity() {
    var name = cityName();
    set('xqWxCityName', name);
    var btn = document.getElementById('xqWxCityBtn');
    if (btn) btn.title = '当前城市：' + name + '（点击更换）';
    set('xqWxGeo', cfg().custom && cfg().custom.auto ? 'IP 定位' : '');
    var chips = document.querySelectorAll('#xqWxPresets .xq-wx-chip');
    for (var i = 0; i < chips.length; i++) {
      chips[i].classList.toggle('is-on', chips[i].getAttribute('data-city') === name);
    }
  }

  function wmo(code, day) {
    var c = Number(code);
    if (c === 0) return day ? ['sun', '晴'] : ['moon', '晴'];
    if (c === 1) return day ? ['cloudsun', '基本晴'] : ['cloudmoon', '基本晴'];
    if (c === 2) return day ? ['cloudsun', '多云'] : ['cloudmoon', '多云'];
    if (c === 3) return ['cloud', '阴'];
    if (c === 45 || c === 48) return ['fog', '雾'];
    if (c >= 51 && c <= 57) return ['rain', '毛毛雨'];
    if (c >= 61 && c <= 67) return ['rain', '雨'];
    if (c >= 71 && c <= 77) return ['snow', '雪'];
    if (c >= 80 && c <= 82) return ['rain', '阵雨'];
    if (c === 85 || c === 86) return ['snow', '阵雪'];
    if (c >= 95) return ['storm', '雷阵雨'];
    return ['cloud', '阴'];
  }
  function icon(kind) {
    var open = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">';
    var sun = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';
    var moon = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>';
    var cloud = '<path d="M17.5 19a4.5 4.5 0 0 0 .4-9 6 6 0 0 0-11.6 1.6A3.5 3.5 0 0 0 7 19z"/>';
    var body = {
      sun: sun, moon: moon, cloud: cloud,
      cloudsun: '<circle cx="8" cy="8" r="3"/><path d="M8 2v1.5M2 8h1.5M3.8 3.8l1 1M12.2 3.8l-1 1"/><path d="M17.5 20a4 4 0 0 0 .4-8 5.5 5.5 0 0 0-10.6 1.4A3.2 3.2 0 0 0 8 20z"/>',
      cloudmoon: '<path d="M20 10.5A6 6 0 0 1 13.5 4a6 6 0 0 0-4.4 8.6"/><path d="M17.5 20a4 4 0 0 0 .4-8 5.5 5.5 0 0 0-10.6 1.4A3.2 3.2 0 0 0 8 20z"/>',
      fog: cloud + '<path d="M5 21h14M7 23h10"/>',
      rain: cloud + '<path d="M8 21v2M12 21v2M16 21v2"/>',
      snow: cloud + '<path d="M8 21h.01M12 22h.01M16 21h.01"/>',
      storm: cloud + '<path d="M12 19l-2 4h3l-2 4"/>'
    }[kind] || cloud;
    return open + body + '</svg>';
  }

  function applyCity(name, lat, lon) {
    var c = cfg();
    c.auto = false;
    c.custom = lat != null ? { name: name, lat: lat, lon: lon, auto: false } : null;
    c.city = lat != null ? '' : name;
    saveCfg(c);
    syncCity();
    closeCityPanel();
    set('xqWxCond', '加载中…');
    load();
  }

  function closeCityPanel() {
    var panel = document.getElementById('xqWxCityPanel');
    if (panel) panel.hidden = true;
  }

  // 地名库对「大连」这类简称只给同名小地方（福建/海南…），补搜「大连市」才能拿到辽宁大连。
  // 多个变体并发查询后合并，按人口降序，人最多的一般就是用户要的那个城市。
  function searchCities(q, done) {
    var variants = [q];
    if (!/市$/.test(q)) variants.push(q + '市');
    var rows = [];
    var pending = variants.length;
    var finished = false;
    function finish() {
      if (finished) return;
      finished = true;
      var seen = {}, list = [];
      rows.forEach(function (it) {
        if (!it || it.latitude == null) return;
        var key = it.name + '|' + (it.admin1 || '');
        if (seen[key]) return;
        seen[key] = 1;
        list.push(it);
      });
      list.sort(function (a, b) { return (b.population || 0) - (a.population || 0); });
      done(list.slice(0, 8));
    }
    variants.forEach(function (v) {
      var url = 'https://geocoding-api.open-meteo.com/v1/search?name=' + encodeURIComponent(v) +
        '&count=8&language=zh&format=json&countryCode=CN';
      fetch(url).then(function (r) { return r.json(); }).then(function (d) {
        (d && d.results || []).forEach(function (it) { rows.push(it); });
      }).catch(function () { /* 单个变体失败不影响其它 */ }).then(function () {
        pending -= 1;
        if (pending <= 0) finish();
      });
    });
  }

  function bindCityControls(el) {
    var btn = el.querySelector('#xqWxCityBtn');
    var panel = el.querySelector('#xqWxCityPanel');
    var input = el.querySelector('#xqWxSearch');
    var results = el.querySelector('#xqWxResults');
    var timer = null;

    if (btn && panel) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        panel.hidden = !panel.hidden;
        if (!panel.hidden && input) { input.value = ''; results.innerHTML = ''; setTimeout(function () { input.focus(); }, 0); }
      });
    }
    if (panel) panel.addEventListener('click', function (e) { e.stopPropagation(); });
    if (!window.__xqWxDocBound) {
      document.addEventListener('click', closeCityPanel);
      window.__xqWxDocBound = true;
    }

    el.querySelector('#xqWxPresets').addEventListener('click', function (e) {
      var chip = e.target.closest('.xq-wx-chip');
      if (chip) applyCity(chip.getAttribute('data-city'), null, null);
    });

    el.querySelector('#xqWxLocate').addEventListener('click', function () {
      var c = cfg();
      c.auto = true; c.custom = null; saveCfg(c);
      set('xqWxCond', '定位中…');
      closeCityPanel();
      locate(function (g) {
        var c2 = cfg();
        if (g) { c2.custom = { name: g.name, lat: g.lat, lon: g.lon, auto: true }; }
        else { c2.auto = false; c2.city = '北京'; }
        saveCfg(c2); syncCity(); load();
      });
    });

    if (input) {
      input.addEventListener('input', function () {
        var q = this.value.trim();
        if (timer) clearTimeout(timer);
        if (q.length < 1) { results.innerHTML = ''; return; }
        timer = setTimeout(function () {
          results.innerHTML = '<div class="xq-wx-tip">搜索中…</div>';
          searchCities(q, function (list) {
            if (!list.length) { results.innerHTML = '<div class="xq-wx-tip">没找到这个城市</div>'; return; }
            results.innerHTML = list.map(function (it, i) {
              var sub = [it.admin1, it.country].filter(Boolean).join(' · ');
              var pop = it.population ? (it.population >= 10000 ? Math.round(it.population / 10000) + ' 万人' : '') : '';
              return '<button type="button" class="xq-wx-hit" data-i="' + i + '">' +
                '<b>' + it.name + '</b><span>' + sub + '</span>' +
                (pop ? '<span class="pop">' + pop + '</span>' : '') +
                (i === 0 ? '<span class="best">推荐</span>' : '') +
                '</button>';
            }).join('');
            results.__list = list;
          });
        }, 400);
      });
    }

    if (results) {
      results.addEventListener('click', function (e) {
        var hit = e.target.closest('.xq-wx-hit');
        if (!hit || !results.__list) return;
        var it = results.__list[Number(hit.getAttribute('data-i'))];
        if (it) applyCity(it.name, it.latitude, it.longitude);
      });
    }
  }

  var ICON_FEEL = '<svg viewBox="0 0 24 24" fill="none" stroke="#f0932b" stroke-width="1.7" stroke-linecap="round"><path d="M14 14.76V4a2 2 0 0 0-4 0v10.76a4 4 0 1 0 4 0z"/><path d="M12 8v6" stroke="#f0932b"/></svg>';
  var ICON_HUM = '<svg viewBox="0 0 24 24" fill="none" stroke="#3d9df3" stroke-width="1.7" stroke-linecap="round"><path d="M12 3.2c3.2 4 5.2 6.4 5.2 9.2a5.2 5.2 0 0 1-10.4 0c0-2.8 2-5.2 5.2-9.2z"/></svg>';
  var ICON_WIND = '<svg viewBox="0 0 24 24" fill="none" stroke="#2bb3c0" stroke-width="1.7" stroke-linecap="round"><path d="M4 9h9a2.5 2.5 0 1 0-2.5-3"/><path d="M4 14h13a2.5 2.5 0 1 1-2.5 3"/><path d="M4 19h6"/></svg>';
  var ICON_SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="#e8b02a" stroke-width="1.7" stroke-linecap="round"><path d="M3 17h4a2 2 0 0 0 0-4H5"/><path d="M10 5v2M6.4 6.4l1.4 1.4M3 9h1.4"/><circle cx="10" cy="10" r="5" stroke="#e8b02a"/><path d="M17 19h4"/></svg>';
  var ICON_GEO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="11" r="7"/><path d="M5 11h14"/><path d="M12 4c2 2.2 3 4.4 3 7s-1 4.8-3 7c-2-2.2-3-4.4-3-7s1-4.8 3-7z"/></svg>';

  function skeleton() {
    var presets = Object.keys(CITIES).map(function (k) {
      return '<button type="button" class="xq-wx-chip" data-city="' + k + '">' + k + '</button>';
    }).join('');
    return '<div class="xq-wx-head">' +
        '<span class="t"><b>当地</b> 天气</span>' +
        '<span class="xq-wx-date" id="xqWxDate"></span>' +
        '<span class="xq-wx-hacts"><button type="button" class="xq-wx-refresh" id="xqWxRefresh">刷新</button>' +
        '<button type="button" class="xq-wx-gear" id="xqWxGear" title="天气设置">⋯</button></span>' +
      '</div>' +
      '<div class="xq-wx-hero">' +
        '<div class="xq-wx-herometa">' +
          '<span class="xq-wx-ico" id="xqWxIco"></span>' +
          '<div class="xq-wx-herotext">' +
            '<div class="xq-wx-temp" id="xqWxTemp">--°</div>' +
            '<div class="xq-wx-cond" id="xqWxCond">加载中…</div>' +
            '<div class="xq-wx-citywrap">' +
              '<button type="button" class="xq-wx-citybtn" id="xqWxCityBtn" title="点击更换城市">' +
                '<span class="gico">' + ICON_GEO + '</span><span id="xqWxCityName">北京</span>' +
                '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>' +
              '</button>' +
              '<div class="xq-wx-citypanel" id="xqWxCityPanel" hidden>' +
                '<div class="xq-wx-searchrow">' +
                  '<input type="text" id="xqWxSearch" placeholder="输入城市名，如 大连 / 苏州" autocomplete="off">' +
                  '<button type="button" class="xq-wx-locbtn" id="xqWxLocate" title="重新自动定位">自动定位</button>' +
                '</div>' +
                '<div class="xq-wx-results" id="xqWxResults"></div>' +
                '<div class="xq-wx-presets" id="xqWxPresets">' + presets + '</div>' +
              '</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div class="xq-wx-tiles">' +
          '<div class="xq-wx-tile"><span class="ic">' + ICON_FEEL + '</span><span class="lb">体感</span><b id="xqWxFeel">--</b></div>' +
          '<div class="xq-wx-tile"><span class="ic">' + ICON_HUM + '</span><span class="lb">湿度</span><b id="xqWxHum">--</b></div>' +
          '<div class="xq-wx-tile"><span class="ic">' + ICON_WIND + '</span><span class="lb">风速</span><b id="xqWxWind">--</b></div>' +
          '<div class="xq-wx-tile"><span class="ic">' + ICON_SUN + '</span><span class="lb">日出 / 日落</span><b id="xqWxSun">--</b></div>' +
        '</div>' +
      '</div>' +
      '<div class="xq-wx-week">' +
        '<div class="xq-wx-weekdays" id="xqWxWeekHi"></div>' +
        '<svg class="xq-wx-curve" id="xqWxCurve" viewBox="0 0 196 26" preserveAspectRatio="none"></svg>' +
        '<div class="xq-wx-weekdays" id="xqWxWeekLo"></div>' +
      '</div>' +
      '<div class="xq-wx-err" id="xqWxErr" style="display:none"></div>' +
      '<div class="xq-wx-foot">Open-Meteo</div>';
  }

  function set(id, text) { var el = document.getElementById(id); if (el) el.textContent = text; }
  function tickClock() {
    var n = new Date();
    var p = function (x) { return (x < 10 ? '0' : '') + x; };
    set('xqWxDate', (n.getMonth() + 1) + '月' + n.getDate() + '日 ' + WD[n.getDay()] + ' ' + p(n.getHours()) + ':' + p(n.getMinutes()));
  }

  function render(d) {
    if (!d || !d.current || !d.daily) return;
    var cur = d.current, day = d.daily;
    var w = wmo(cur.weather_code, cur.is_day === 1);
    var ico = document.getElementById('xqWxIco');
    if (ico) ico.innerHTML = icon(w[0]);
    set('xqWxTemp', Math.round(cur.temperature_2m) + '°');
    var hi0 = day.temperature_2m_max && day.temperature_2m_max[0];
    var lo0 = day.temperature_2m_min && day.temperature_2m_min[0];
    set('xqWxCond', w[1] + (hi0 != null && lo0 != null ? ' ' + Math.round(hi0) + '° / ' + Math.round(lo0) + '°' : ''));
    set('xqWxFeel', Math.round(cur.apparent_temperature) + '°');
    set('xqWxHum', Math.round(cur.relative_humidity_2m) + '%');
    set('xqWxWind', Math.round(cur.wind_speed_10m) + ' km/h');
    var sr = (day.sunrise && day.sunrise[0] || '').slice(11, 16);
    var ss = (day.sunset && day.sunset[0] || '').slice(11, 16);
    set('xqWxSun', (sr || '--') + ' / ' + (ss || '--'));
    var n = Math.min(7, (day.time || []).length);
    var hiHtml = '', loHtml = '', his = [], los = [];
    for (var i = 0; i < n; i++) {
      var dt = new Date(day.time[i] + 'T00:00:00');
      var lab = i === 0 ? '今天' : (i === 1 ? '明天' : WD[dt.getDay()]);
      var dw = wmo(day.weather_code[i], 1);
      var hi = Math.round(day.temperature_2m_max[i]), lo = Math.round(day.temperature_2m_min[i]);
      his.push(hi); los.push(lo);
      hiHtml += '<div class="xq-wx-day"><span>' + lab + '</span><span class="d">' + (dt.getMonth() + 1) + '/' + dt.getDate() + '</span>' + icon(dw[0]) + '<span class="hi">' + hi + '°</span></div>';
      loHtml += '<div class="xq-wx-day"><span class="lo">' + lo + '°</span></div>';
    }
    var wh = document.getElementById('xqWxWeekHi'); if (wh) wh.innerHTML = hiHtml;
    var wl = document.getElementById('xqWxWeekLo'); if (wl) wl.innerHTML = loHtml;
    var cv = document.getElementById('xqWxCurve');
    if (cv && n > 1) {
      var all = his.concat(los);
      var mn = Math.min.apply(null, all), mx = Math.max.apply(null, all);
      var span = Math.max(1, mx - mn);
      var y = function (t) { return 23 - ((t - mn) / span) * 20; };
      var x = function (i) { return 14 + i * (168 / (n - 1)); };
      var pts = function (arr) { return arr.map(function (t, i) { return x(i).toFixed(1) + ',' + y(t).toFixed(1); }).join(' '); };
      var dots = function (arr, color) {
        return arr.map(function (t, i) { return '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(t).toFixed(1) + '" r="2" fill="' + color + '"/>'; }).join('');
      };
      cv.innerHTML = '<polyline points="' + pts(his) + '" fill="none" stroke="#f5a623" stroke-width="1.6"/>' +
        '<polyline points="' + pts(los) + '" fill="none" stroke="#4d9dff" stroke-width="1.6"/>' +
        dots(his, '#f5a623') + dots(los, '#4d9dff');
    }
    try { localStorage.setItem('xq_wx_cache', JSON.stringify(d)); } catch (e) { /* ignore */ }
    syncCity();
  }

  function renderCache() {
    try { render(JSON.parse(localStorage.getItem('xq_wx_cache') || 'null')); } catch (e) { /* ignore */ }
  }
  function setErr(msg) {
    var el = document.getElementById('xqWxErr');
    if (el) { el.textContent = msg || ''; el.style.display = msg ? 'block' : 'none'; }
  }
  function load() {
    var ll = coords();
    var url = 'https://api.open-meteo.com/v1/forecast?latitude=' + ll[0] + '&longitude=' + ll[1] +
      '&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,is_day' +
      '&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset&timezone=auto&forecast_days=7';
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      setErr('');
      render(d);
    }).catch(function () {
      setErr('天气获取失败，显示上次缓存');
      renderCache();
    });
  }

  function ensure() {
    var host = document.getElementById('contentContainer');
    var info = document.querySelector('.system-info-box');
    if (!host || !info || !host.contains(info)) {
      if (card && card.parentNode) card.parentNode.removeChild(card);
      card = null;
      return;
    }
    var wrap = info.parentElement;
    if (!wrap.classList.contains('xq-ov-split')) {
      wrap = document.createElement('div');
      wrap.className = 'xq-ov-split';
      info.parentNode.insertBefore(wrap, info);
      wrap.appendChild(info);
    }
    if (card && card.parentElement === wrap) return;
    if (!card) {
      var el = document.createElement('div');
      el.className = 'xq-wx xq-wx-embed';
      el.id = 'xqWx';
      el.innerHTML = skeleton();
      card = el;
      el.querySelector('#xqWxRefresh').addEventListener('click', load);
      var gear = el.querySelector('#xqWxGear');
      if (gear) gear.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var p = document.getElementById('xqWxCityPanel');
        if (p) p.hidden = !p.hidden;
      });
      bindCityControls(el);
      syncCity();
      tickClock();
      renderCache();
      if (cfg().custom && cfg().custom.lat != null) {
        load();
      } else {
        set('xqWxCond', '定位中…');
        locate(function (g) {
          var c = cfg();
          if (g) { c.custom = { name: g.name, lat: g.lat, lon: g.lon, auto: true }; }
          else { c.auto = false; c.city = '北京'; }
          saveCfg(c); syncCity(); load();
        });
      }
    }
    wrap.appendChild(card);
    tickClock();
    renderCache();
  }

  function init() {
    ensure();
    setInterval(ensure, 900);
    setInterval(tickClock, 30000);
    setInterval(load, 1800000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
