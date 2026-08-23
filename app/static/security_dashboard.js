/* Semantic Studio — Member Access & Security modal controller. */
(function () {
  'use strict';

  var AMP = '&' + 'amp;';
  var LT = '&' + 'lt;';
  var GT = '&' + 'gt;';
  var QUOT = '&' + 'quot;';

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, AMP)
      .replace(/</g, LT)
      .replace(/>/g, GT)
      .replace(/"/g, QUOT);
  }

  var state = {
    tables: [],
    allowed: [],
    colMap: {},
    allCols: {},
    members: [],
    selectedUserId: null,
    workspaceName: ''
  };

  function openMemberAccessModal() {
    var mask = $('memberAccessModal');
    if (!mask) return;
    mask.classList.add('open');
    switchMA('audit', document.querySelector('#memberAccessModal .ma-tabs button'));
    // UX: show a loading placeholder immediately so the audit summary is never
    // blank; the real data replaces it the moment /admin/member-tables returns.
    var at = $('maAudit');
    if (at) {
      at.innerHTML = '<div class="ma-summary">Loading member access...</div>';
    }
    loadModalData();
  }

  function closeMemberAccessModal() {
    var mask = $('memberAccessModal');
    if (mask) mask.classList.remove('open');
  }

  function switchMA(name, btn) {
    document.querySelectorAll('#memberAccessModal .ma-tabs button').forEach(function (b) {
      b.classList.toggle('active', b === btn);
    });
    var audit = $('maAudit');
    var perms = $('maPerms');
    if (!audit || !perms) return;
    if (name === 'audit') {
      audit.style.display = 'block';
      perms.style.display = 'none';
    } else {
      audit.style.display = 'none';
      perms.style.display = 'block';
    }
  }

  function loadModalData(uid, cb) {
    state.selectedUserId = uid || null;
    var url = '/admin/member-tables' + (uid ? '?user_id=' + uid : '');
    return fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.tables = d.all_tables || [];
        state.allowed = d.allowed_tables || [];
        state.colMap = d.column_permissions || {};
        state.allCols = d.all_tables_columns || {};
        state.members = d.members || [];
        state.workspaceName =
          (d.workspace_name || '') ||
          (window.DC_WORKSPACE_NAME || '') ||
          'Workspace';
        // AUTO-RENDER: render the audit summary + member dropdown + permission
        // tree immediately when data arrives — no Save/refresh required, so
        // the modal is never blank right after opening.
        fillMemberSelect();
        renderAudit();
        renderTree();
        if (cb) cb(d);
        return d;
      });
  }

  function renderAudit() {
    var at = $('maAudit');
    if (!at) return;
    var empty = '<div class="ma-summary">No members in this workspace yet.</div>';
    if (!state.members.length) {
      at.innerHTML = empty;
      return;
    }
    var rows = state.members.map(function (m) {
      return '<tr><td>' + esc(m.email) + '</td><td>Member</td>' +
        '<td>' + state.allowed.length + '</td>' +
        '<td>' + (state.colMap && Object.keys(state.colMap).length
          ? '<span class="badge badge-restricted">' + Object.keys(state.colMap).length + ' table(s) masked</span>'
          : '<span class="badge badge-open">Full access</span>') + '</td></tr>';
    }).join('');
    at.innerHTML = '<table class="ma-table"><thead><tr><th>Member</th><th>Role</th><th>Active Tables</th><th>Column Status</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function fillMemberSelect() {
    var sel = $('maMemberSelect');
    if (!sel) return;
    var wsName = state.workspaceName || window.DC_WORKSPACE_NAME || 'Workspace';
    sel.innerHTML = '<option value="">' + esc(wsName) + ' default (all members)</option>';
    state.members.forEach(function (m) {
      var o = document.createElement('option');
      o.value = m.id;
      o.textContent = (m.username || m.email || ('Member #' + m.id)) + ' — ' + m.email;
      sel.appendChild(o);
    });
    if (state.selectedUserId) sel.value = String(state.selectedUserId);
  }

  function renderTree() {
    var con = $('maTree');
    if (!con) return;
    con.innerHTML = '';
    if (!state.tables.length) {
      con.innerHTML = '<div class="tb-empty">No tables found. Connect a database first.</div>';
      return;
    }
    state.tables.forEach(function (t) {
      var chk = state.allowed.indexOf(t) !== -1 ? 'checked' : '';
      var sv = state.colMap[t] || null;
      var cols = state.allCols[t] || [];
      var colList = cols.map(function (c) {
        var ck = sv ? (sv.indexOf(c) !== -1 ? 'checked' : '') : 'checked';
        return '<label class="tb-col-item"><input type="checkbox" data-tbl="' + esc(t) + '" data-col="1" value="' + esc(c) + '" ' + ck + '>' + esc(c) + '</label>';
      }).join('') || '<div class="tb-empty">No columns detected</div>';
      var actions = cols.length
        ? '<div class="col-actions"><button type="button" class="ma-all" data-tbl="' + esc(t) + '">✓ Select All</button>' +
          '<button type="button" class="ma-none" data-tbl="' + esc(t) + '">✕ Deselect All</button></div>'
        : '';
      var row = '<div class="rbac-table-group">' +
        '<div class="tb-row"><input type="checkbox" data-tbl="' + esc(t) + '" value="' + esc(t) + '" class="ma-tbl" ' + chk + '><span>' + esc(t) + '</span><span class="tb-caret">▶</span></div>' +
        '<div class="tb-cols">' + actions + colList + '</div></div>';
      con.insertAdjacentHTML('beforeend', row);
    });
  }

  function loadModalDataAndRender(uid) {
    loadModalData(uid, function () {
      fillMemberSelect();
      renderAudit();
      renderTree();
    });
  }

  function maSelectMember() {
    var sel = $('maMemberSelect');
    loadModalDataAndRender(sel && sel.value ? Number(sel.value) : null);
  }

  function saveMemberAccessModal() {
    var btn = document.querySelector('#memberAccessModal .btn-save');
    if (window.dcBusy) dcBusy(btn, true, 'Saving...');

    var allowed = [];
    var cp = {};
    document.querySelectorAll('#maTree .ma-tbl').forEach(function (cb) {
      if (!cb.checked) return;
      var t = cb.getAttribute('value') || cb.getAttribute('data-tbl');
      allowed.push(t);
      var boxes = document.querySelectorAll('#maTree input[data-col="1"][data-tbl="' + t + '"]');
      if (boxes.length) {
        var ck = Array.prototype.filter.call(boxes, function (b) { return b.checked; })
          .map(function (b) { return b.value; });
        if (ck.length < boxes.length) cp[t] = ck;
      }
    });

    var sel = $('maMemberSelect');
    var uid = (sel && sel.value) ? Number(sel.value) : null;

    fetch('/admin/member-tables', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        workspace_id: null,
        user_id: uid,
        table_permissions: allowed,
        column_permissions: cp
      })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (res.ok) {
          if (window.dcToast) dcToast('✅ Member access saved & applied instantly', 'success');
          return loadModalDataAndRender(uid);
        }
        if (window.dcToast) dcToast(res.d.error || 'Save failed', 'error');
      })
      .catch(function (e) {
        if (window.dcToast) dcToast('Network error: ' + e.message, 'error');
      })
      .finally(function () {
        if (window.dcBusy) dcBusy(btn, false);
      });
  }

  // Event delegation inside the permission tree.
  document.addEventListener('click', function (ev) {
    var el = ev.target;
    var root = $('maTree');
    if (!root || !root.contains(el)) return;
    if (el.classList.contains('ma-all')) {
      var ta = el.getAttribute('data-tbl');
      root.querySelectorAll('input[data-col="1"][data-tbl="' + ta + '"]').forEach(function (i) { i.checked = true; });
    } else if (el.classList.contains('ma-none')) {
      var tn = el.getAttribute('data-tbl');
      root.querySelectorAll('input[data-col="1"][data-tbl="' + tn + '"]').forEach(function (i) { i.checked = false; });
    } else if (el.classList.contains('ma-tbl')) {
      ev.stopPropagation();
    } else if (el.classList.contains('tb-row')) {
      var cols = el.nextElementSibling;
      if (cols) cols.classList.toggle('open');
      var caret = el.querySelector('.tb-caret');
      if (caret) caret.classList.toggle('open');
    }
  });

  // Global hooks used by the template.
  window.openMemberAccessModal = openMemberAccessModal;
  window.closeMemberAccessModal = closeMemberAccessModal;
  window.switchMA = switchMA;
  window.maSelectMember = maSelectMember;
  window.saveMemberAccessModal = saveMemberAccessModal;
})();