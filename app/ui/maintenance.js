/* 日常维护：快速清理 / 各项占用 / 软件卸载
 * 页面与数据由原生维护模块提供（app/native/maintenance.py），这里只负责界面。
 */
(function () {
  var MAINT = {
    active: false, tab: 'clean', admin: false, items: null,
    checked: {}, scan: null,
    stats: null, apps: null, appsLoaded: false, sysVisible: false,
    sel: '', busy: false, pending: null, timer: 0, logText: '', keyword: '', memMsg: '', memArmed: false
  };

  function post(msg) {
    try { window.chrome.webview.postMessage(msg); } catch (e) { /* ignore */ }
  }

  function esc(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function notice(text) {
    var box = document.getElementById('xqMaintNotice');
    if (!box) {
      box = document.createElement('div');
      box.id = 'xqMaintNotice';
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

  function size(bytes) {
    bytes = Number(bytes) || 0;
    if (bytes <= 0) return '0 B';
    var units = ['B', 'KB', 'MB', 'GB', 'TB'];
    var i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i += 1; }
    return (bytes >= 100 ? bytes.toFixed(0) : bytes.toFixed(1)) + ' ' + units[i];
  }

  function findNav(text) {
    var sidebar = document.getElementById('sidebar');
    if (!sidebar) return null;
    var items = sidebar.querySelectorAll('.nav-item');
    for (var i = 0; i < items.length; i++) {
      if (items[i].textContent.trim() === text) return items[i];
    }
    return null;
  }

  function moveAfter(sidebar, node, ref) {
    if (!node || !ref || !sidebar) return;
    if (node.previousElementSibling === ref) return;
    if (ref.nextSibling) sidebar.insertBefore(node, ref.nextSibling);
    else sidebar.appendChild(node);
  }

  function ensureNav() {
    var sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    var nav = document.getElementById('xqMaintNav');
    if (!nav) {
      nav = document.createElement('div');
      nav.className = 'nav-item';
      nav.id = 'xqMaintNav';
      nav.textContent = '日常维护';
      nav.addEventListener('click', function (e) {
        e.preventDefault();
        show();
      });
      sidebar.appendChild(nav);
    }
    var sw = findNav('软件下载');
    var dbg = findNav('调试工具');
    var lib = document.getElementById('xqLibNav');
    if (sw && sw.parentElement === sidebar) moveAfter(sidebar, nav, sw);
    if (dbg && dbg.parentElement === sidebar && nav.parentElement === sidebar) moveAfter(sidebar, dbg, nav);
    if (lib && lib.parentElement === sidebar && dbg && dbg.parentElement === sidebar) moveAfter(sidebar, lib, dbg);
  }

  function bodyEl() { return document.getElementById('xqMaintBody'); }

  function show() {
    MAINT.active = true;
    window.xqMaintActive = true;
    window.xqShowMaintenance = show;
    var sidebar = document.getElementById('sidebar');
    if (sidebar) {
      Array.prototype.forEach.call(sidebar.querySelectorAll('.nav-item'), function (n) { n.classList.remove('active'); });
      var nav = document.getElementById('xqMaintNav');
      if (nav) nav.classList.add('active');
    }
    var header = document.getElementById('pageHeaderWrap');
    if (header) header.style.display = '';
    var title = document.getElementById('pageTitle');
    if (title) title.textContent = '日常维护';
    var sub = document.getElementById('pageSubtitle');
    if (sub) {
      sub.textContent = '快速清理、资源占用与软件卸载，少翻长页。';
      sub.style.display = 'block';
    }
    ['categorySearchWrap', 'openFolderBtn', 'clearHistoryBtn'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    var container = document.getElementById('contentContainer');
    if (!container) return;
    container.innerHTML = '<div class="xq-maint" id="xqMaintRoot">' +
      '<div class="m-tabs" id="xqMaintTabs"></div>' +
      '<div id="xqMaintBody"><div class="m-card"><div class="m-meta">正在准备…</div></div></div>' +
      '</div>';
    renderTabs();
    switchTab(MAINT.tab || 'clean');
    if (!MAINT.items) post('maint_items');
    startTimer();
  }

  function renderTabs() {
    var box = document.getElementById('xqMaintTabs');
    if (!box) return;
    var tabs = [['clean', '快速清理'], ['usage', '各项占用'], ['apps', '软件卸载']];
    box.innerHTML = tabs.map(function (t) {
      return '<button class="m-tab' + (MAINT.tab === t[0] ? ' active' : '') + '" data-mtab="' + t[0] + '">' + t[1] + '</button>';
    }).join('');
    box.querySelectorAll('[data-mtab]').forEach(function (btn) {
      btn.addEventListener('click', function () { switchTab(btn.getAttribute('data-mtab')); });
    });
  }

  function switchTab(tab) {
    MAINT.tab = tab;
    var box = document.getElementById('xqMaintTabs');
    if (box) {
      Array.prototype.forEach.call(box.querySelectorAll('[data-mtab]'), function (btn) {
        btn.className = 'm-tab' + (btn.getAttribute('data-mtab') === tab ? ' active' : '');
      });
    }
    if (tab === 'clean') renderClean();
    else if (tab === 'usage') { renderUsage(); post('maint_stats'); }
    else { renderApps(); if (!MAINT.appsLoaded) { post('maint_apps|1'); } }
  }

  function startTimer() {
    if (MAINT.timer) clearInterval(MAINT.timer);
    MAINT.timer = setInterval(function () {
      if (!MAINT.active || document.hidden) return;
      if (MAINT.tab === 'usage') post('maint_stats');
    }, 2000);
  }

  /* ------------------------- 快速清理 ------------------------- */
  function renderClean() {
    var body = bodyEl();
    if (!body) return;
    if (!MAINT.items) {
      body.innerHTML = '<div class="m-card"><div class="m-meta">正在读取清理项…</div></div>';
      return;
    }
    var cells = MAINT.items.map(function (it) {
      var disabled = it.admin && !MAINT.admin;
      var checked = MAINT.checked[it.id] !== undefined ? MAINT.checked[it.id] : !!it.default;
      var scan = MAINT.scan && MAINT.scan[it.id];
      var line = scan ? '<br><b style="color:var(--color-accent)">约 ' + size(scan.size) +
        (scan.count ? ' · ' + scan.count + ' 项' : '') + '</b>' : '';
      return '<label class="m-item' + (disabled ? ' disabled' : '') + '" title="' + esc(it.desc) + '">' +
        '<input type="checkbox" data-mitem="' + esc(it.id) + '"' + (checked ? ' checked' : '') + (disabled ? ' disabled' : '') + '>' +
        '<span style="flex:1;min-width:0"><span>' + esc(it.name) + '</span>' +
        '<div class="m-desc">' + esc(it.desc) + (disabled ? '<br>需要管理员权限，以管理员重新打开后可用' : '') + line + '</div>' +
        '</span></label>';
    }).join('');
    body.innerHTML = '<div class="m-card">' +
      '<div class="m-title">快速清理</div>' +
      '<div class="m-meta" style="margin-top:4px">勾选后点「扫描」预估可清理容量；清理完成后在这里显示实际释放结果。</div>' +
      '<div class="m-grid m-scroll" style="margin-top:14px">' + cells + '</div>' +
      '<div class="m-actions">' +
        '<button class="m-btn ghost" id="mScanBtn" style="flex:1">扫描</button>' +
        '<button class="m-btn" id="mCleanBtn" style="flex:1">清理</button>' +
      '</div>' +
      '<div class="m-meta" id="mCleanStatus" style="margin-top:12px">' +
        (MAINT.scan ? '上次扫描：' + (MAINT.scan.__msg || '') : '还没有扫描过。') +
      '</div>' +
      '<div class="m-meta" id="mCleanDetail"></div>' +
    '</div>';

    body.querySelectorAll('[data-mitem]').forEach(function (cb) {
      cb.addEventListener('change', function () { MAINT.checked[cb.getAttribute('data-mitem')] = cb.checked; });
    });
    var scanBtn = document.getElementById('mScanBtn');
    if (scanBtn) scanBtn.addEventListener('click', doScan);
    var cleanBtn = document.getElementById('mCleanBtn');
    if (cleanBtn) cleanBtn.addEventListener('click', doClean);
    if (MAINT.lastClean) {
      var det = document.getElementById('mCleanDetail');
      if (det) det.innerHTML = MAINT.lastClean;
    }
  }

  function checkedIds() {
    var ids = [];
    (MAINT.items || []).forEach(function (it) {
      var disabled = it.admin && !MAINT.admin;
      var on = MAINT.checked[it.id] !== undefined ? MAINT.checked[it.id] : !!it.default;
      if (on && !disabled) ids.push(it.id);
    });
    return ids;
  }

  function doScan() {
    var ids = checkedIds();
    if (!ids.length) { notice('先勾选要清理的项目'); return; }
    var st = document.getElementById('mCleanStatus');
    if (st) st.textContent = '正在扫描（占用中的文件会自动跳过）…';
    post('maint_scan|' + ids.join(','));
  }

  function doClean() {
    var ids = checkedIds();
    if (!ids.length) { notice('先勾选要清理的项目'); return; }
    var btn = document.getElementById('mCleanBtn');
    if (btn && btn._armed) {
      btn._armed = false;
      btn.textContent = '清理';
      var st = document.getElementById('mCleanStatus');
      if (st) st.textContent = '正在清理…';
      post('maint_clean|' + ids.join(','));
      return;
    }
    if (btn) {
      btn._armed = true;
      btn.textContent = '再点一次确认清理';
      setTimeout(function () { if (btn._armed) { btn._armed = false; btn.textContent = '清理'; } }, 3500);
    }
  }

  function onScan(data) {
    MAINT.scan = data || {};
    var total = 0;
    Object.keys(MAINT.scan).forEach(function (k) {
      if (k.indexOf('__') === 0) return;
      total += Number((MAINT.scan[k] || {}).size) || 0;
    });
    MAINT.scan.__msg = '可清理约 ' + size(total);
    renderClean();
  }

  function onClean(data) {
    data = data || {};
    var freed = Number(data.freed) || 0;
    var lines = (data.messages || []).map(function (m) { return esc(m); }).join('；');
    var rows = (data.details || []).map(function (d) {
      if (!d.freed) return '';
      return esc(d.name) + ' <span style="color:var(--color-accent)">' + size(d.freed) + '</span>';
    }).filter(Boolean).join('；');
    MAINT.lastClean = '<div style="font-size:13px;color:var(--color-text-primary);margin-top:6px">' +
      '已释放 <b style="color:var(--color-accent)">' + size(freed) + '</b>' +
      (data.skipped ? '，跳过 ' + data.skipped + ' 个占用中的文件' : '') + '</div>' +
      (lines ? '<div class="m-meta">' + lines + '</div>' : '') +
      (rows ? '<div class="m-meta">' + rows + '</div>' : '');
    MAINT.scan = null;
    renderClean();
    notice('清理完成，释放 ' + size(freed));
  }

  /* ------------------------- 各项占用 ------------------------- */
  function renderUsage() {
    var body = bodyEl();
    if (!body) return;
    var mem = MAINT.stats && MAINT.stats.memory;
    var disks = (MAINT.stats && MAINT.stats.disks) || [];
    var memHtml = '<div class="m-card m-fixed">' +
      '<div style="display:flex;align-items:center;gap:10px">' +
        '<div class="m-title">内存监控</div><div style="flex:1"></div>' +
        '<button class="m-btn ghost" id="mMemRefresh">刷新</button>' +
      '</div>';
    if (!mem) {
      memHtml += '<div class="m-meta" style="margin-top:10px">正在读取内存…</div></div>';
    } else {
      var usedPct = mem.total ? (mem.used * 100 / mem.total) : 0;
      var cachePct = mem.total ? (mem.cached * 100 / mem.total) : 0;
      var availPct = Math.max(0, 100 - usedPct - cachePct);
      memHtml +=
        '<div style="display:flex;align-items:center;gap:14px;margin-top:8px;flex-wrap:wrap">' +
          '<div style="font-size:15px;font-weight:600;color:var(--color-text-primary)">总计 ' + size(mem.total) +
          ' · 已用 ' + size(mem.used) + ' · 缓存 ' + size(mem.cached) + ' · 可用 ' + size(mem.available) + '</div>' +
          '<div style="flex:1"></div>' +
          '<button class="m-btn" id="mMemClean">' + (MAINT.memArmed ? '再点一次确认' : '一键清理') + '</button>' +
        '</div>' +
        '<div class="m-bar m-bar-mem" style="margin-top:12px">' +
          '<span style="width:' + usedPct + '%;background:#e57373"></span>' +
          '<span style="width:' + cachePct + '%;background:#f0c14b"></span>' +
          '<span style="width:' + availPct + '%;background:#5ac36a"></span>' +
        '</div>' +
        '<div class="m-meta" style="margin-top:8px">● 已使用 ' + size(mem.used) +
        '　● 已缓存 ' + size(mem.cached) + '　● 可用 ' + size(mem.available) +
        '　· 占用 ' + mem.percent + '%</div>' +
        '<div class="m-meta">已提交 ' + size(mem.commitTotal) + ' · 提交上限 ' + size(mem.commitLimit) + '</div>' +
        '<div class="m-meta" id="mMemStatus">' + esc(MAINT.memMsg || '') + '</div></div>';
    }
    body.innerHTML = memHtml + '<div class="m-card m-grow">' +
      '<div class="m-title">磁盘监控</div>' +
      '<div class="m-meta" style="margin-top:4px">逻辑卷 ' + disks.length + ' 个 · 每 2 秒刷新</div>' +
      '<div class="m-scroll" style="margin-top:10px">' + (disks.length ? disks.map(diskRow).join('') :
        '<div class="m-meta">正在读取磁盘…</div>') + '</div></div>';

    var ref = document.getElementById('mMemRefresh');
    if (ref) ref.addEventListener('click', function () { post('maint_stats'); });
    var clr = document.getElementById('mMemClean');
    if (clr) clr.addEventListener('click', function () {
      if (MAINT.memArmed) {
        MAINT.memArmed = false;
        MAINT.memMsg = '正在整理工作集与待机缓存…';
        var st = document.getElementById('mMemStatus');
        if (st) st.textContent = MAINT.memMsg;
        clr.textContent = '一键清理';
        post('maint_mem_clean');
        return;
      }
      MAINT.memArmed = true;
      clr.textContent = '再点一次确认';
      setTimeout(function () {
        if (!MAINT.memArmed) return;
        MAINT.memArmed = false;
        var b = document.getElementById('mMemClean');
        if (b) b.textContent = '一键清理';
      }, 3500);
    });
  }

  function diskRow(d) {
    return '<div style="display:flex;gap:12px;align-items:center;padding:10px 0;border-top:1px dashed var(--border-color)">' +
      '<div style="width:34px;height:34px;border-radius:9px;background:var(--bg-hover);display:flex;align-items:center;' +
      'justify-content:center;font-weight:700;color:var(--color-text-primary)">' + esc(d.letter) + '</div>' +
      '<div style="flex:1;min-width:0">' +
        '<div style="font-size:13px;font-weight:600;color:var(--color-text-primary)">' + esc(d.letter) + ': ' +
        esc(d.label) + '</div>' +
        '<div class="m-bar m-bar-disk" style="margin-top:6px"><span style="width:' + d.percent + '%;background:' +
        (d.percent >= 90 ? '#e57373' : (d.percent >= 70 ? '#f0c14b' : '#4a9bff')) + '"></span></div>' +
        '<div class="m-meta">已用 ' + size(d.used) + ' · 可用 ' + size(d.free) + ' · 共 ' + size(d.total) + '</div>' +
      '</div>' +
      '<div style="font-size:13px;color:var(--color-text-secondary)">' + d.percent + '%</div>' +
    '</div>';
  }

  function onStats(data) {
    MAINT.stats = data || {};
    if (MAINT.tab === 'usage') renderUsage();
  }

  function onMemClean(data) {
    MAINT.memArmed = false;
    var bits = [];
    bits.push('已整理 ' + (data.cleared || 0) + ' 个进程');
    if (data.standby) bits.push('待机列表已清');
    else bits.push('待机缓存未清（以管理员身份运行可清）');
    bits.push('释放约 ' + size(data.freed));
    MAINT.memMsg = bits.join(' · ');
    var st = document.getElementById('mMemStatus');
    if (st) st.textContent = MAINT.memMsg;
    notice('清理完成，可用内存 +' + size(data.freed));
    post('maint_stats');
  }

  /* ------------------------- 软件卸载 ------------------------- */
  function appKind(app) {
    if (app.system) return ['系统', 'badge-sys'];
    var u = String(app.uninstall || '').toLowerCase();
    if (!u) return ['便携', 'badge-port'];
    if (u.indexOf('msiexec') >= 0) return ['MSI', 'badge-msi'];
    if (u.indexOf('unins') >= 0) return ['Inno / NSIS', 'badge-exe'];
    return ['EXE', 'badge-exe'];
  }

  function renderApps() {
    var body = bodyEl();
    if (!body) return;
    var apps = MAINT.apps || [];
    var list = apps.filter(function (a) {
      if (!MAINT.sysVisible && a.system) return false;
      var kw = MAINT.keyword.trim().toLowerCase();
      if (!kw) return true;
      return (a.name + ' ' + (a.publisher || '') + ' ' + (a.version || '')).toLowerCase().indexOf(kw) >= 0;
    });
    var hidden = apps.length - list.length;
    var rows = list.map(function (a) {
      var kind = appKind(a);
      var meta = [a.publisher, a.version, a.size ? size(a.size) : ''].filter(Boolean).join(' · ');
      return '<div class="m-app' + (MAINT.sel === a.id ? ' sel' : '') + '" data-mapp="' + esc(a.id) + '">' +
        '<div class="m-avatar">' + esc(String(a.name || '?').slice(0, 1).toUpperCase()) + '</div>' +
        '<div style="flex:1;min-width:0">' +
          '<div class="n">' + esc(a.name) + '</div>' +
          '<div class="m-meta">' + esc(meta) + '</div>' +
        '</div>' +
        '<span class="m-badge ' + kind[1] + '">' + kind[0] + '</span>' +
      '</div>';
    }).join('');
    body.innerHTML =
      '<div style="display:flex;gap:10px;align-items:center;margin-bottom:10px">' +
        '<input id="mAppSearch" class="m-input" placeholder="搜索名称 / 发布者 / 版本" value="' + esc(MAINT.keyword) + '">' +
        '<span class="m-meta" id="mAppCount">' + apps.length + ' 项' + (hidden > 0 ? ' · 藏 ' + hidden : '') + '</span>' +
        '<label class="m-meta" style="display:flex;align-items:center;gap:6px;white-space:nowrap">' +
          '<input type="checkbox" id="mSysToggle"' + (MAINT.sysVisible ? ' checked' : '') + '> 系统组件</label>' +
        '<button class="m-btn ghost" id="mAppRefresh">刷新</button>' +
      '</div>' +
      '<div class="m-list" id="mAppList">' + (rows || '<div class="m-meta" style="padding:16px">' +
        (MAINT.appsLoaded ? '没有匹配的应用' : '正在扫描已安装应用…') + '</div>') + '</div>' +
      '<div class="m-actions">' +
        '<button class="m-btn ghost" data-mmode="normal">卸载</button>' +
        '<button class="m-btn" data-mmode="deep">彻底清除</button>' +
        '<button class="m-btn danger" data-mmode="force">强制清除</button>' +
        '<button class="m-btn ghost" id="mOpenLog">上次日志</button>' +
      '</div>' +
      '<div class="m-meta" id="mAppStatus" style="margin-top:10px">当前：' +
        (MAINT.admin ? '管理员权限（彻底 / 强制清除可用）' : '普通权限（彻底 / 强制清除可能清不干净）') + '</div>' +
      '<div id="mLogBox"></div>';

    var search = document.getElementById('mAppSearch');
    if (search) {
      search.addEventListener('input', function () {
        MAINT.keyword = search.value;
        renderApps();
        var again = document.getElementById('mAppSearch');
        if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
      });
    }
    var sys = document.getElementById('mSysToggle');
    if (sys) sys.addEventListener('change', function () { MAINT.sysVisible = sys.checked; renderApps(); });
    var rf = document.getElementById('mAppRefresh');
    if (rf) rf.addEventListener('click', function () {
      MAINT.appsLoaded = false;
      post('maint_apps|1');
      notice('正在重新扫描…');
    });
    body.querySelectorAll('[data-mapp]').forEach(function (row) {
      row.addEventListener('click', function () {
        MAINT.sel = row.getAttribute('data-mapp');
        MAINT.pending = null;
        body.querySelectorAll('[data-mapp]').forEach(function (r) { r.classList.remove('sel'); });
        row.classList.add('sel');
        setAppStatus(null);
      });
    });
    body.querySelectorAll('[data-mmode]').forEach(function (btn) {
      btn.addEventListener('click', function () { startUninstall(btn, btn.getAttribute('data-mmode')); });
    });
    var logBtn = document.getElementById('mOpenLog');
    if (logBtn) logBtn.addEventListener('click', function () { post('maint_log'); });
    if (MAINT.logText) showLog(MAINT.logText);
    if (MAINT.appStatus) setAppStatusHtml(MAINT.appStatus);
  }

  function setAppStatus(text) {
    if (text) MAINT.appStatus = esc(text);
    setAppStatusHtml(MAINT.appStatus);
  }

  function setAppStatusHtml(html) {
    var box = document.getElementById('mAppStatus');
    if (box && html) box.innerHTML = html;
  }

  function startUninstall(btn, mode) {
    if (!MAINT.sel) { notice('先在列表里选一个应用'); return; }
    if (MAINT.busy) { notice('上一个卸载任务还在进行'); return; }
    if (MAINT.pending && MAINT.pending.id === MAINT.sel && MAINT.pending.mode === mode) {
      MAINT.pending = null;
      MAINT.busy = true;
      setAppStatus('正在执行…（卸载程序可能弹出自己的窗口，按它完成即可）');
      var label = btn.textContent;
      btn.textContent = '处理中…';
      post('maint_uninstall|' + MAINT.sel + '|' + mode);
      return;
    }
    MAINT.pending = { id: MAINT.sel, mode: mode };
    var names = { normal: '卸载', deep: '彻底清除', force: '强制清除' };
    setAppStatus('准备' + names[mode] + '：再点一次同样的按钮确认（4 秒内有效）');
    setTimeout(function () {
      if (MAINT.pending && MAINT.pending.id === MAINT.sel && MAINT.pending.mode === mode) {
        MAINT.pending = null;
        setAppStatus(null);
      }
    }, 4000);
  }

  function onApps(data) {
    MAINT.apps = (data && data.items) || [];
    MAINT.admin = !!(data && data.admin);
    MAINT.appsLoaded = true;
    if (MAINT.tab === 'apps') renderApps();
  }

  function onUninstall(data) {
    MAINT.busy = false;
    data = data || {};
    var text = (data.ok === false) ? ('失败：' + (data.message || '未知错误')) : (data.message || '已开始卸载');
    var extra = [];
    if (data.killed) extra.push('结束进程 ' + data.killed + ' 个');
    if (data.removedKey) extra.push('已清理卸载注册表');
    if (data.removedDir) extra.push('安装目录：' + data.removedDir);
    if (data.freed) extra.push('释放 ' + size(data.freed));
    setAppStatus(esc((data.name ? data.name + '：' : '') + text) + (extra.length ? ' · ' + esc(extra.join(' · ')) : ''));
    notice(text);
    post('maint_apps|1');
  }

  function onLog(data) {
    MAINT.logText = (data && data.text) || '还没有卸载记录。';
    showLog(MAINT.logText, data && data.path);
  }

  function showLog(text, path) {
    var box = document.getElementById('mLogBox');
    if (!box) return;
    box.innerHTML = '<div class="m-card" style="margin-top:12px">' +
      '<div style="display:flex;align-items:center;gap:10px"><div class="m-title">上次卸载日志</div>' +
      '<div style="flex:1"></div>' +
      (path ? '<button class="m-btn ghost" id="mLogPath">打开文件</button>' : '') +
      '<button class="m-btn ghost" id="mLogClose">收起</button></div>' +
      '<pre style="margin-top:10px;white-space:pre-wrap;font-size:12px;line-height:1.55;color:var(--color-text-secondary);' +
      'max-height:240px;overflow:auto">' + esc(text) + '</pre></div>';
    var close = document.getElementById('mLogClose');
    if (close) close.addEventListener('click', function () {
      MAINT.logText = '';
      box.innerHTML = '';
    });
    var open = document.getElementById('mLogPath');
    if (open) open.addEventListener('click', function () {
      post('maint_open_log|' + (path || ''));
    });
  }

  /* ------------------------- 原生消息 ------------------------- */
  function parse(s, prefix) {
    try { return JSON.parse(s.slice(prefix.length)); } catch (e) { return null; }
  }

  window.__xqMaintHandle = function (message) {
    var s = String(message || '');
    if (s.indexOf('maint_items|') === 0) {
      var d = parse(s, 'maint_items|') || {};
      MAINT.items = d.items || [];
      MAINT.admin = !!d.admin;
      if (MAINT.tab === 'clean') renderClean();
      else if (MAINT.tab === 'apps' && !MAINT.appsLoaded) post('maint_apps|1');
      return true;
    }
    if (s.indexOf('maint_scan|') === 0) { onScan(parse(s, 'maint_scan|')); return true; }
    if (s.indexOf('maint_clean|') === 0) { onClean(parse(s, 'maint_clean|')); return true; }
    if (s.indexOf('maint_stats|') === 0) { onStats(parse(s, 'maint_stats|')); return true; }
    if (s.indexOf('maint_mem_clean|') === 0) { onMemClean(parse(s, 'maint_mem_clean|') || {}); return true; }
    if (s.indexOf('maint_apps|') === 0) { onApps(parse(s, 'maint_apps|')); return true; }
    if (s.indexOf('maint_uninstall|') === 0) { onUninstall(parse(s, 'maint_uninstall|')); return true; }
    if (s.indexOf('maint_log|') === 0) { onLog(parse(s, 'maint_log|')); return true; }
    return false;
  };

  // 离开本页时停止轮询
  document.addEventListener('click', function (e) {
    var item = e.target && e.target.closest ? e.target.closest('.nav-item') : null;
    if (item && item.id !== 'xqMaintNav') {
      MAINT.active = false;
      window.xqMaintActive = false;
    }
  }, true);

  function init() {
    ensureNav();
    setInterval(function () { try { ensureNav(); } catch (e) { /* ignore */ } }, 1200);
  }

  window.xqShowMaintenance = show;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
