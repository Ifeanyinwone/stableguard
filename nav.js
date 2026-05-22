// StableGuard — nav.js
// Shared navigation, styles, and utilities across all pages

const API = 'http://localhost:8000';

// ── Shared CSS variables and base styles ──────────────────────
const SHARED_STYLES = `
  @import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Mono:wght@300;400;500&family=Syne:wght@400;600;700;800&display=swap');

  :root {
    --bg:        #030507;
    --bg2:       #070b0f;
    --bg3:       #0c1118;
    --bg4:       #111820;
    --border:    rgba(255,255,255,0.05);
    --border2:   rgba(255,255,255,0.10);
    --text:      #e8edf2;
    --text2:     #7a8a99;
    --text3:     #3d5060;
    --accent:    #00d4ff;
    --accent2:   #0088aa;
    --green:     #00e87a;
    --yellow:    #ffc930;
    --orange:    #ff7a2f;
    --red:       #ff3366;
    --green-bg:  rgba(0,232,122,0.07);
    --yellow-bg: rgba(255,201,48,0.07);
    --orange-bg: rgba(255,122,47,0.07);
    --red-bg:    rgba(255,51,102,0.07);
    --nav-h:     60px;
  }

  *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

  html { scroll-behavior: smooth; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Syne', sans-serif;
    min-height: 100vh;
    padding-top: var(--nav-h);
  }

  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(rgba(0,212,255,0.015) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,212,255,0.015) 1px, transparent 1px);
    background-size: 48px 48px;
    pointer-events: none;
    z-index: 0;
  }

  /* ── Navigation ── */
  .sg-nav {
    position: fixed;
    top: 0; left: 0; right: 0;
    height: var(--nav-h);
    background: rgba(3,5,7,0.92);
    backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border);
    z-index: 1000;
    display: flex;
    align-items: center;
    padding: 0 28px;
    gap: 0;
  }

  .sg-nav-logo {
    display: flex;
    align-items: center;
    gap: 10px;
    text-decoration: none;
    margin-right: 40px;
    flex-shrink: 0;
  }

  .sg-nav-logo-icon {
    width: 32px; height: 32px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px;
    box-shadow: 0 0 16px rgba(0,212,255,0.25);
  }

  .sg-nav-logo-text {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 22px;
    letter-spacing: 2px;
    color: var(--text);
  }

  .sg-nav-links {
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
  }

  .sg-nav-link {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 7px 14px;
    border-radius: 8px;
    text-decoration: none;
    font-size: 13px;
    font-weight: 600;
    color: var(--text2);
    transition: all 0.2s;
    letter-spacing: 0.3px;
  }

  .sg-nav-link:hover {
    color: var(--text);
    background: rgba(255,255,255,0.05);
  }

  .sg-nav-link.active {
    color: var(--accent);
    background: rgba(0,212,255,0.08);
    border: 1px solid rgba(0,212,255,0.15);
  }

  .sg-nav-link .nav-icon { font-size: 15px; }

  .sg-nav-right {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-left: auto;
  }

  .sg-nav-status {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    background: var(--bg3);
    border: 1px solid var(--border);
    border-radius: 20px;
    font-family: 'DM Mono', monospace;
    font-size: 10px;
    color: var(--text2);
  }

  .pulse-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%,100% { opacity:1; transform: scale(1); }
    50% { opacity:0.4; transform: scale(0.8); }
  }

  /* ── Page wrapper ── */
  .sg-page {
    max-width: 1440px;
    margin: 0 auto;
    padding: 28px 28px 60px;
    position: relative;
    z-index: 1;
  }

  /* ── Page header ── */
  .sg-page-header {
    margin-bottom: 32px;
    padding-bottom: 24px;
    border-bottom: 1px solid var(--border);
  }

  .sg-page-eyebrow {
    font-family: 'DM Mono', monospace;
    font-size: 10px;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 8px;
  }

  .sg-page-title {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 42px;
    letter-spacing: 2px;
    color: var(--text);
    line-height: 1;
  }

  .sg-page-sub {
    font-size: 14px;
    color: var(--text2);
    margin-top: 8px;
    max-width: 600px;
    line-height: 1.6;
  }

  /* ── Cards ── */
  .sg-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 24px;
    transition: border-color 0.2s;
  }

  .sg-card:hover { border-color: var(--border2); }

  .sg-card-title {
    font-family: 'DM Mono', monospace;
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--text3);
    margin-bottom: 12px;
  }

  /* ── Section title ── */
  .sg-section-title {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    color: var(--text2);
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .sg-section-title::before {
    content: '';
    display: inline-block;
    width: 3px; height: 14px;
    background: var(--accent);
    border-radius: 2px;
  }

  /* ── Alert badges ── */
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 4px 10px;
    border-radius: 20px;
    font-family: 'DM Mono', monospace;
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 1px;
    text-transform: uppercase;
  }

  .badge-healthy { background: var(--green-bg);  color: var(--green);  border: 1px solid rgba(0,232,122,0.2); }
  .badge-watch   { background: var(--yellow-bg); color: var(--yellow); border: 1px solid rgba(255,201,48,0.2); }
  .badge-reduce  { background: var(--orange-bg); color: var(--orange); border: 1px solid rgba(255,122,47,0.2); }
  .badge-exit    { background: var(--red-bg);    color: var(--red);    border: 1px solid rgba(255,51,102,0.2); }

  /* ── Buttons ── */
  .sg-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border-radius: 8px;
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.2s;
    border: none;
    text-decoration: none;
  }

  .sg-btn-primary {
    background: var(--accent);
    color: var(--bg);
    font-weight: 500;
  }

  .sg-btn-primary:hover { background: #00bbdd; }

  .sg-btn-ghost {
    background: transparent;
    color: var(--accent);
    border: 1px solid rgba(0,212,255,0.25);
  }

  .sg-btn-ghost:hover { background: rgba(0,212,255,0.08); }

  /* ── Loading ── */
  .sg-loading {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 80px 20px;
    color: var(--text3);
    gap: 16px;
  }

  .sg-spinner {
    width: 36px; height: 36px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Grid ── */
  .sg-grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
  .sg-grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
  .sg-grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }

  /* ── Table ── */
  .sg-table { width: 100%; border-collapse: collapse; }
  .sg-table th {
    font-family: 'DM Mono', monospace;
    font-size: 9px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--text3);
    padding: 10px 16px;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }
  .sg-table td {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
    color: var(--text2);
  }
  .sg-table tr:hover td { background: rgba(255,255,255,0.02); }
  .sg-table tr:last-child td { border-bottom: none; }

  /* ── Animations ── */
  @keyframes fadeInUp {
    from { opacity:0; transform: translateY(12px); }
    to   { opacity:1; transform: translateY(0); }
  }

  @keyframes fadeIn {
    from { opacity:0; }
    to   { opacity:1; }
  }

  .anim-fade-up { animation: fadeInUp 0.5s ease both; }

  /* ── Responsive ── */
  @media (max-width: 900px) {
    .sg-grid-4 { grid-template-columns: repeat(2,1fr); }
    .sg-grid-3 { grid-template-columns: repeat(2,1fr); }
    .sg-page { padding: 20px 16px 40px; }
    .sg-page-title { font-size: 32px; }
  }

  @media (max-width: 600px) {
    .sg-grid-2, .sg-grid-3, .sg-grid-4 { grid-template-columns: 1fr; }
    .sg-nav-link span:not(.nav-icon) { display: none; }
  }
`;

// ── Navigation HTML ───────────────────────────────────────────
function renderNav(activePage) {
  const pages = [
    { id: 'dashboard', href: 'index.html', icon: '📊', label: 'Traders' },
    { id: 'analyst',   href: 'analyst.html',   icon: '🔬', label: 'Analysts' },
    { id: 'risk',      href: 'risk-manager.html', icon: '⚙️', label: 'Risk Managers' },
    { id: 'institutions', href: 'institutions.html', icon: '🏛', label: 'Institutions' },
  ];

  return `
    <nav class="sg-nav">
      <a href="index.html" class="sg-nav-logo">
        <div class="sg-nav-logo-icon">🛡</div>
        <span class="sg-nav-logo-text">StableGuard</span>
      </a>
      <div class="sg-nav-links">
        ${pages.map(p => `
          <a href="${p.href}" class="sg-nav-link ${activePage === p.id ? 'active' : ''}">
            <span class="nav-icon">${p.icon}</span>
            <span>${p.label}</span>
          </a>
        `).join('')}
      </div>
      <div class="sg-nav-right">
        <div class="sg-nav-status">
          <div class="pulse-dot"></div>
          <span id="nav-status">Live</span>
        </div>
      </div>
    </nav>`;
}

// ── Shared utilities ──────────────────────────────────────────
function alertClass(level) {
  if (!level) return 'healthy';
  if (level.includes('EXIT'))   return 'exit';
  if (level.includes('REDUCE')) return 'reduce';
  if (level.includes('WATCH'))  return 'watch';
  return 'healthy';
}

function alertColor(level) {
  const cls = alertClass(level);
  return cls === 'exit' ? 'var(--red)' :
         cls === 'reduce' ? 'var(--orange)' :
         cls === 'watch' ? 'var(--yellow)' : 'var(--green)';
}

function scoreColor(score) {
  if (score >= 70) return 'var(--red)';
  if (score >= 45) return 'var(--orange)';
  if (score >= 20) return 'var(--yellow)';
  return 'var(--green)';
}

function timeAgo(iso) {
  if (!iso) return '—';
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  return `${Math.floor(diff/3600)}h ago`;
}

function fmtTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-US', { hour:'2-digit', minute:'2-digit' });
}

function fmtNum(n, decimals = 2) {
  if (n === null || n === undefined) return '—';
  if (Math.abs(n) >= 1e9) return (n/1e9).toFixed(1) + 'B';
  if (Math.abs(n) >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (Math.abs(n) >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return Number(n).toFixed(decimals);
}

// Inject shared styles
function injectStyles() {
  const style = document.createElement('style');
  style.textContent = SHARED_STYLES;
  document.head.insertBefore(style, document.head.firstChild);
}