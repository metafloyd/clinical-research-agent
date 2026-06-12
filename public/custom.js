(function () {
    // Avoid the landing-screen FLASH when reloading an existing thread. Chainlit
    // renders #welcome-screen (greeting+starters) whenever there are no messages, and
    // on a /thread/ reload there are briefly none until on_chat_resume loads them. Mark
    // <html> on /thread/ URLs so CSS hides the welcome screen; Chainlit's own loading
    // spinner shows during the ~0.2s resume instead. (A slow/stuck resume is a backend
    // issue, not this — keep the agent warm.)
    function syncThreadView() {
        document.documentElement.classList.toggle(
            'cl-thread-view', /\/thread\//.test(location.pathname));
    }
    syncThreadView();

    // Single shared AudioContext — browsers cap total contexts at ~6, so
    // creating a new one per sound silently fails after a few messages.
    var _audioCtx = null;
    function _getCtx() {
        if (!_audioCtx) {
            try { _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
            catch (e) { return null; }
        }
        if (_audioCtx.state === 'suspended') { _audioCtx.resume(); }
        return _audioCtx;
    }

    function playSend() {
        var ctx = _getCtx();
        if (!ctx) return;
        try {
            var osc = ctx.createOscillator(), gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.type = 'sine';
            osc.frequency.setValueAtTime(440, ctx.currentTime);
            osc.frequency.linearRampToValueAtTime(520, ctx.currentTime + 0.06);
            gain.gain.setValueAtTime(0.04, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.1);
            osc.start(); osc.stop(ctx.currentTime + 0.1);
        } catch (e) {}
    }

    function playDone() {
        var ctx = _getCtx();
        if (!ctx) return;
        try {
            [[660, 0], [880, 0.12]].forEach(function (p) {
                var osc = ctx.createOscillator(), gain = ctx.createGain();
                osc.connect(gain); gain.connect(ctx.destination);
                osc.type = 'sine'; osc.frequency.value = p[0];
                var t = ctx.currentTime + p[1];
                gain.gain.setValueAtTime(0.05, t);
                gain.gain.exponentialRampToValueAtTime(0.001, t + 0.4);
                osc.start(t); osc.stop(t + 0.4);
            });
        } catch (e) {}
    }

    // The app is themed for LIGHT mode only (brand is deep blue on light); dark mode
    // renders half-broken. So: force light + hide the toggle (also rescues any session
    // already stuck in dark from a prior toggle).
    function forceLightTheme() {
        try {
            ['theme', 'vite-ui-theme', 'ui-theme'].forEach(function(k) {
                if (localStorage.getItem(k) && localStorage.getItem(k) !== 'light') {
                    localStorage.setItem(k, 'light');
                }
            });
        } catch (e) {}
        var html = document.documentElement;
        html.classList.remove('dark');
        html.classList.add('light');
        html.setAttribute('data-theme', 'light');
        try { html.style.colorScheme = 'light'; } catch (e) {}
        // Belt-and-suspenders: also hide the toggle button by icon/label.
        var header = document.getElementById('header');
        if (header) {
            header.querySelectorAll('button').forEach(function(btn) {
                var svg = btn.querySelector('svg');
                var testId = (svg && svg.getAttribute('data-testid') || '').toLowerCase();
                var label = (btn.getAttribute('aria-label') || btn.getAttribute('title') || '').toLowerCase();
                if (testId.includes('mode') || label.includes('theme') ||
                    label.includes('dark') || label.includes('light')) {
                    btn.style.display = 'none';
                }
            });
        }
    }
    forceLightTheme();
    setTimeout(forceLightTheme, 500);
    setTimeout(forceLightTheme, 2000);
    // Re-assert light if anything flips <html> back to dark.
    try {
        new MutationObserver(function() {
            if (document.documentElement.classList.contains('dark')) forceLightTheme();
        }).observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
    } catch (e) {}

    function _nameFromEmail(email) {
        if (!email || !email.includes('@')) return '';
        var local = email.split('@')[0].split('.')[0].replace(/[^a-zA-Z]/g, '');
        return local ? local.charAt(0).toUpperCase() + local.slice(1).toLowerCase() : '';
    }

    function _timePeriod() {
        var h = new Date().getHours();
        return h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening';
    }

    // The /user endpoint never changes within a session, so fetch it ONCE and cache
    // the promise. Without this, the body MutationObserver below (which fires on every
    // DOM node Plotly/streaming adds — thousands when charts render) would re-hit /user
    // repeatedly, producing a multi-minute request storm that makes reloads feel stuck.
    var _userPromise = null;
    function _getUser() {
        if (!_userPromise) {
            _userPromise = fetch('/user', { credentials: 'include' })
                .then(function(r) { return r.ok ? r.json() : null; })
                .catch(function() { return null; });
        }
        return _userPromise;
    }

    function fetchAndUpdateGreeting(el) {
        _getUser().then(function(data) {
            if (!data) return;
            var meta = data.metadata || {};
            var name = meta.given_name || meta.name || _nameFromEmail(data.identifier || data.email || '');
            if (!name) return;
            el.textContent = 'Good ' + _timePeriod() + ', ' + name + '.';
        });
    }

    function injectGreeting() {
        if (document.getElementById('cl-greeting')) return;
        var ws = document.getElementById('welcome-screen');
        if (!ws) return;
        var img = ws.querySelector('img');
        if (!img) return;

        var el = document.createElement('p');
        el.id = 'cl-greeting';
        el.textContent = 'Good ' + _timePeriod() + '.';
        fetchAndUpdateGreeting(el);
        el.style.cssText = [
            'font-size:34px',
            'font-weight:700',
            'color:#0E3293',
            'margin:40px 0 6px 0',
            'text-align:center',
            'font-family:Inter,sans-serif',
            'width:100%',
            'letter-spacing:-0.02em',
            'line-height:1.2'
        ].join(';') + ';';
        img.insertAdjacentElement('afterend', el);

        if (!document.getElementById('cl-greeting-sub')) {
            var sub = document.createElement('p');
            sub.id = 'cl-greeting-sub';
            sub.textContent = 'What would you like to research today?';
            sub.style.cssText = [
                'font-size:15px',
                'font-weight:400',
                'color:#6B7280',
                'margin:0 0 24px 0',
                'text-align:center',
                'font-family:Inter,sans-serif',
                'width:100%'
            ].join(';') + ';';
            el.insertAdjacentElement('afterend', sub);
        }
    }
    injectGreeting();
    setTimeout(injectGreeting, 500);
    setTimeout(injectGreeting, 1500);

    function setPlaceholder() {
        var el = document.querySelector('textarea');
        if (el) {
            el.placeholder = 'Enter a condition, drug name, NCT ID, or research question…';
            return;
        }
        var ce = document.querySelector('[contenteditable="true"]');
        if (ce) ce.setAttribute('data-placeholder', 'Enter a condition, drug name, NCT ID, or research question…');
    }
    setPlaceholder();
    setTimeout(setPlaceholder, 1000);
    setTimeout(setPlaceholder, 3000);

    function injectDataSourceBadge() {
        var header = document.getElementById('header');
        if (!header || document.getElementById('cl-datasource-badge')) return;
        var badge = document.createElement('div');
        badge.id = 'cl-datasource-badge';
        badge.innerHTML = '<span>🏥 ClinicalTrials.gov</span><span class="cl-dot"> · </span><span>📚 PubMed</span><span class="cl-dot"> · </span><span>🔍 Internal CTMS</span>';
        header.appendChild(badge);
    }
    injectDataSourceBadge();
    setTimeout(injectDataSourceBadge, 500);
    setTimeout(injectDataSourceBadge, 2000);

    // Show the user's name on the existing user-nav avatar button (keeps the
    // logout dropdown working) instead of the "S" initial. CSS hides the avatar
    // circle and renders the name via ::after from this CSS variable.
    function applyUserName() {
        if (document.documentElement.style.getPropertyValue('--cl-username')) return;
        _getUser().then(function(data) {
            if (!data) return;
            var meta = data.metadata || {};
            var name = meta.given_name || meta.name || data.identifier || 'Account';
            document.documentElement.style.setProperty('--cl-username', JSON.stringify(name));
            // Google profile photo → shown as a round avatar before the name (CSS ::before).
            if (meta.picture) {
                document.documentElement.style.setProperty('--cl-userpic', 'url("' + meta.picture + '")');
            }
        });
    }
    applyUserName();
    setTimeout(applyUserName, 800);
    setTimeout(applyUserName, 2000);

    // Send sound: click on #chat-submit.
    // Done sound: #stop-button disappearing (Chainlit swaps submit↔stop during streaming).
    function wireAudio() {
        var btn = document.getElementById('chat-submit');
        if (!btn || btn._clWired) return;
        btn._clWired = true;
        btn.addEventListener('click', playSend);
    }
    wireAudio();

    // Run the per-mutation work, but check the streaming→done transition on EVERY
    // mutation (it's cheap and timing-sensitive for the done sound).
    var _wasStreaming = false;
    function _checkStreamDone() {
        var streaming = !!document.getElementById('stop-button');
        if (_wasStreaming && !streaming) { setTimeout(playDone, 300); }
        _wasStreaming = streaming;
    }
    // The header-button tooltips ("New Chat" / "Open sidebar") rendered as unreadable
    // white-on-white and their colour couldn't be overridden by CSS or JS inline styles.
    // The icons are self-explanatory, so just HIDE the tooltip outright — inline
    // display:none !important can't be overridden. Hide the [role=tooltip] box and its
    // Radix popper wrapper so no empty box flashes.
    function paintTooltips() {
        var tips = document.querySelectorAll('[role="tooltip"]');
        for (var i = 0; i < tips.length; i++) {
            var t = tips[i];
            t.style.setProperty('display', 'none', 'important');
            var wrap = t.closest && t.closest('[data-radix-popper-content-wrapper]');
            if (wrap) wrap.style.setProperty('display', 'none', 'important');
        }
    }

    function _applyChrome() {
        syncThreadView();   // keep in sync across new-chat / thread navigation
        injectGreeting();
        injectDataSourceBadge();
        applyUserName();
        forceLightTheme();
        setPlaceholder();
        wireAudio();
        paintTooltips();
    }
    // DEBOUNCE the expensive DOM work. Plotly/streaming add thousands of nodes; firing
    // _applyChrome (which queries the DOM + can fetch /user) per node pegs the main thread
    // for minutes on a chart-heavy reload. Coalesce to one run ~150ms after mutations settle.
    // Tooltips appear on hover and must be painted INSTANTLY (not on the 150ms debounce),
    // so paintTooltips() runs every mutation — it's cheap (querySelectorAll on a near-empty
    // selector + a one-time per-element guard).
    var _chromeTimer = null;
    new MutationObserver(function () {
        _checkStreamDone();                 // cheap, every time (done-sound timing)
        paintTooltips();                    // cheap, every time (tooltip appears on hover)
        if (_chromeTimer) return;
        _chromeTimer = setTimeout(function () {
            _chromeTimer = null;
            _applyChrome();
        }, 150);
    }).observe(document.body, { childList: true, subtree: true });
})();
