(function () {
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

    // The app is themed for LIGHT mode only (brand is Mayo blue on light); dark mode
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

    function fetchAndUpdateGreeting(el) {
        fetch('/user', { credentials: 'include' })
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                var meta = data.metadata || {};
                var name = meta.given_name || meta.name || _nameFromEmail(data.identifier || data.email || '');
                if (!name) return;
                el.textContent = 'Good ' + _timePeriod() + ', ' + name + '.';
            })
            .catch(function() {});
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
        badge.innerHTML = '<span>🏥 ClinicalTrials.gov</span><span class="cl-dot"> · </span><span>📚 PubMed</span>';
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
        fetch('/user', { credentials: 'include' })
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                var meta = data.metadata || {};
                var name = meta.given_name || meta.name || data.identifier || 'Account';
                document.documentElement.style.setProperty('--cl-username', JSON.stringify(name));
                // Google profile photo → shown as a round avatar before the name (CSS ::before).
                if (meta.picture) {
                    document.documentElement.style.setProperty('--cl-userpic', 'url("' + meta.picture + '")');
                }
            })
            .catch(function() {});
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

    var _wasStreaming = false;
    new MutationObserver(function () {
        injectGreeting();
        injectDataSourceBadge();
        applyUserName();
        forceLightTheme();
        setPlaceholder();
        wireAudio();
        var streaming = !!document.getElementById('stop-button');
        if (_wasStreaming && !streaming) { setTimeout(playDone, 300); }
        _wasStreaming = streaming;
    }).observe(document.body, { childList: true, subtree: true });
})();
