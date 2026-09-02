/**
 * ACT Widget — Embeddable Web Component for bug/feature reporting
 * 
 * Usage (inline link — recommended):
 *   <script src="https://act.claude.int/widget/act-widget.js"></script>
 *   <act-widget endpoint="https://act.claude.int" token="act_xxx" mode="link" label="Feedback"></act-widget>
 *
 * Usage (floating button — optional):
 *   <act-widget endpoint="https://act.claude.int" token="act_xxx" mode="fab"></act-widget>
 * 
 * Attributes:
 *   endpoint    — ACT server URL (required)
 *   token       — App API token (required)
 *   mode        — "link" (default, inline text) | "fab" (floating button)
 *   label       — Link text (default: "Feedback")
 *   position    — FAB position: "bottom-right" | "bottom-left" (only for mode="fab")
 *   app-version — App version string (auto-included in context)
 */

class ActWidget extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
        this._open = false;
        this._errorLog = [];
        this._captureErrors();
    }

    connectedCallback() {
        this._render();
    }

    get endpoint() { return this.getAttribute('endpoint') || ''; }
    get token() { return this.getAttribute('token') || ''; }
    get mode() { return this.getAttribute('mode') || 'link'; }
    get label() { return this.getAttribute('label') || 'Feedback'; }
    get position() { return this.getAttribute('position') || 'bottom-right'; }
    get appVersion() { return this.getAttribute('app-version') || document.querySelector('meta[name="app-version"]')?.content || ''; }

    _captureErrors() {
        this._onError = (e) => {
            this._errorLog.push(`${e.message} (${e.filename}:${e.lineno})`);
            if (this._errorLog.length > 10) this._errorLog.shift();
        };
        this._onUnhandledRejection = (e) => {
            this._errorLog.push(`Promise: ${e.reason}`);
            if (this._errorLog.length > 10) this._errorLog.shift();
        };
        window.addEventListener('error', this._onError);
        window.addEventListener('unhandledrejection', this._onUnhandledRejection);
    }

    disconnectedCallback() {
        window.removeEventListener('error', this._onError);
        window.removeEventListener('unhandledrejection', this._onUnhandledRejection);
    }

    _render() {
        const isFab = this.mode === 'fab';
        const pos = this.position === 'bottom-left' ? 'left: 1rem;' : 'right: 1rem;';

        this.shadowRoot.innerHTML = `
        <style>
            :host { --accent: #3b82f6; --bg: #1e293b; --surface: #0f172a; --border: #334155; --text: #e2e8f0; --text-dim: #94a3b8; }
            
            /* Link mode trigger */
            .trigger-link { color: var(--text-dim, #94a3b8); cursor: pointer; font-size: 0.8rem; font-family: system-ui, sans-serif;
                           text-decoration: none; opacity: 0.7; transition: opacity 0.15s; display: inline-flex; align-items: center; gap: 0.3rem; }
            .trigger-link:hover { opacity: 1; text-decoration: underline; }
            .trigger-link svg { width: 14px; height: 14px; fill: currentColor; }
            
            /* FAB mode trigger */
            .fab { position: fixed; bottom: 1rem; ${pos} z-index: 99999; width: 48px; height: 48px;
                   border-radius: 50%; background: var(--accent); border: none; cursor: pointer;
                   display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 12px rgba(0,0,0,0.4);
                   transition: transform 0.2s; }
            .fab:hover { transform: scale(1.1); }
            .fab svg { width: 22px; height: 22px; fill: white; }
            
            /* Panel (shared) */
            .panel { position: fixed; z-index: 99999; width: 360px; max-height: 480px;
                     background: var(--bg); border: 1px solid var(--border); border-radius: 12px;
                     box-shadow: 0 8px 32px rgba(0,0,0,0.5); display: none; flex-direction: column; overflow: hidden; }
            .panel.open { display: flex; }
            .panel.pos-fab { bottom: 4.5rem; ${pos} }
            .panel.pos-link { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); }
            .panel-header { padding: 0.75rem 1rem; background: var(--surface); border-bottom: 1px solid var(--border);
                           display: flex; justify-content: space-between; align-items: center; }
            .panel-header h3 { font-size: 0.9rem; margin: 0; color: var(--text); font-family: system-ui, sans-serif; }
            .panel-close { background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 1.2rem; padding: 0.2rem 0.4rem; }
            .panel-body { padding: 1rem; overflow-y: auto; overflow-x: hidden; font-family: system-ui, sans-serif; }
            .overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.4); z-index: 99998; }
            .overlay.open { display: block; }
            
            label { display: block; font-size: 0.75rem; color: var(--text-dim); margin: 0.75rem 0 0.25rem; }
            label:first-child { margin-top: 0; }
            select, input, textarea { width: 100%; padding: 0.45rem 0.5rem; background: var(--surface);
                                       border: 1px solid var(--border); border-radius: 6px; color: var(--text);
                                       font-size: 0.85rem; font-family: system-ui, sans-serif; box-sizing: border-box; }
            textarea { min-height: 80px; resize: vertical; }
            .btn-submit { width: 100%; margin-top: 1rem; padding: 0.55rem; background: var(--accent); color: white;
                         border: none; border-radius: 6px; font-size: 0.85rem; cursor: pointer; font-weight: 600; }
            .btn-submit:hover { opacity: 0.9; }
            .btn-submit:disabled { opacity: 0.5; cursor: not-allowed; }
            .success { text-align: center; padding: 2rem 1rem; color: var(--text); }
            .success .tick { font-size: 2.5rem; margin-bottom: 0.5rem; }
            .success .tid { font-size: 0.8rem; color: var(--text-dim); margin-top: 0.5rem; }
            .type-pills { display: flex; gap: 0.4rem; }
            .type-pill { flex: 1; padding: 0.4rem; text-align: center; background: var(--surface); border: 1px solid var(--border);
                        border-radius: 6px; cursor: pointer; font-size: 0.8rem; color: var(--text-dim); transition: all 0.15s; }
            .type-pill.active { border-color: var(--accent); color: var(--accent); background: rgba(59,130,246,0.1); }
        </style>

        ${isFab ? `
        <button class="fab" id="trigger" title="Report Bug / Request Feature">
            <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.2L4 17.2V4h16v12z"/><path d="M7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/></svg>
        </button>` : `
        <span class="trigger-link" id="trigger">
            <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.2L4 17.2V4h16v12z"/><path d="M7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/></svg>
            ${this.label}
        </span>`}

        <div class="overlay" id="overlay"></div>
        <div class="panel ${isFab ? 'pos-fab' : 'pos-link'}" id="panel">
            <div class="panel-header">
                <h3>🎫 Report Issue</h3>
                <button class="panel-close" id="close">✕</button>
            </div>
            <div class="panel-body" id="body"></div>
        </div>`;

        this._panel = this.shadowRoot.getElementById('panel');
        this._body = this.shadowRoot.getElementById('body');
        this._overlay = this.shadowRoot.getElementById('overlay');

        this.shadowRoot.getElementById('trigger').addEventListener('click', () => this._toggle());
        this.shadowRoot.getElementById('close').addEventListener('click', () => this._toggle(false));
        this._overlay.addEventListener('click', () => this._toggle(false));
        this._showForm();
    }

    _toggle(force) {
        this._open = force !== undefined ? force : !this._open;
        this._panel.classList.toggle('open', this._open);
        if (this.mode !== 'fab') this._overlay.classList.toggle('open', this._open);
        if (this._open) this._showForm();
    }

    _showForm() {
        this._body.innerHTML = `
            <label>Type</label>
            <div class="type-pills">
                <div class="type-pill active" data-type="bug">🐛 Bug</div>
                <div class="type-pill" data-type="feature">✨ Feature</div>
            </div>
            <label>Title *</label>
            <input type="text" id="act-title" placeholder="Brief summary">
            <label>Description *</label>
            <textarea id="act-desc" placeholder="What happened? What did you expect?"></textarea>
            <label>Priority</label>
            <select id="act-prio">
                <option value="low">Low</option>
                <option value="normal" selected>Normal</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
            </select>
            <button class="btn-submit" id="act-submit">Submit</button>`;

        let selectedType = 'bug';
        this._body.querySelectorAll('.type-pill').forEach(pill => {
            pill.addEventListener('click', () => {
                this._body.querySelectorAll('.type-pill').forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                selectedType = pill.dataset.type;
            });
        });

        this._body.querySelector('#act-submit').addEventListener('click', () => {
            const title = this._body.querySelector('#act-title').value.trim();
            const desc = this._body.querySelector('#act-desc').value.trim();
            if (!title || !desc) return;
            this._submit(selectedType, title, desc, this._body.querySelector('#act-prio').value);
        });
    }

    async _submit(type, title, description, priority) {
        const btn = this._body.querySelector('#act-submit');
        btn.disabled = true;
        btn.textContent = 'Submitting…';

        const context = {
            url: window.location.href,
            browser: navigator.userAgent,
            app_version: this.appVersion,
            errors: this._errorLog.slice(-5).join('\n'),
        };

        try {
            const resp = await fetch(`${this.endpoint}/api/tickets`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
                body: JSON.stringify({ type, title, description, priority, context }),
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const ticket = await resp.json();
            this._body.innerHTML = `
                <div class="success">
                    <div class="tick">✅</div>
                    <div>Submitted successfully!</div>
                    <div class="tid">${ticket.id}</div>
                </div>`;
            setTimeout(() => this._toggle(false), 2500);
        } catch (err) {
            btn.disabled = false;
            btn.textContent = 'Submit (retry)';
            console.error('[ACT Widget]', err);
        }
    }
}

customElements.define('act-widget', ActWidget);
