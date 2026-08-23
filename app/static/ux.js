/* Data Convo UX helpers */
(function () {
    'use strict';
    var box = null;
    function tb() {
        if (box) return box;
        box = document.createElement('div');
        box.id = 'dc-toast-box';
        box.style.cssText = 'position:fixed;top:20px;right:20px;z-index:99999;display:flex;flex-direction:column;gap:10px;max-width:360px;pointer-events:none;';
        document.body.appendChild(box);
        return box;
    }
    function toast(m, t) {
        var b = tb(), el = document.createElement('div');
        var bg = '#111827', bd = '#1e293b', c = '#f8fafc';
        if (t === 'success') { bg = 'rgba(16,185,129,.12)'; bd = 'rgba(16,185,129,.4)'; c = '#34d399'; }
        else if (t === 'error') { bg = 'rgba(239,68,68,.12)'; bd = 'rgba(239,68,68,.4)'; c = '#f87171'; }
        else if (t === 'warning') { bg = 'rgba(245,158,11,.12)'; bd = 'rgba(245,158,11,.4)'; c = '#fbbf24'; }
        el.style.cssText = 'background:' + bg + ';border:1px solid ' + bd + ';color:' + c + ';border-radius:10px;padding:12px 16px;font-size:.85rem;font-weight:500;line-height:1.45;box-shadow:0 8px 30px rgba(0,0,0,.35);opacity:0;transform:translateY(-8px);transition:all .25s ease;pointer-events:auto;';
        el.textContent = m;
        b.appendChild(el);
        requestAnimationFrame(function () { el.style.opacity = '1'; el.style.transform = 'translateY(0)'; });
        setTimeout(function () {
            el.style.opacity = '0'; el.style.transform = 'translateY(-8px)';
            setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 250);
        }, 4200);
        return el;
    }
    window.dcToast = toast;
    function busy(btn, b, t) {
        if (!btn) return;
        if (b) { btn.dataset.dcOrig = btn.textContent; btn.disabled = true; btn.textContent = t || 'Working...'; }
        else { btn.disabled = false; btn.textContent = btn.dataset.dcOrig || btn.textContent; }
    }
    window.dcBusy = busy;
    var of = window.fetch;
    if (of) {
        window.fetch = function (i, ini) {
            return of.call(this, i, ini).then(function (r) {
                if (r.status === 401) {
                    try { toast('Your session has expired. Please log in again.', 'warning'); } catch (e) {}
                    setTimeout(function () {
                        var n = encodeURIComponent(window.location.pathname + window.location.search);
                        window.location.href = '/auth/login?next=' + n + '&expired=1';
                    }, 1200);
                }
                return r;
            });
        };
    }
})();