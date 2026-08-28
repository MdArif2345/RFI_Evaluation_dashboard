# CMC Dashboard

Static portfolio report for CMC response / RFI metrics. Open it in a browser to see KPIs, charts, tables, and recommendations.

This folder is **independent of `yaap-infra-v2`** (AWS/Terraform). They are unrelated projects that only share the same parent workspace.

---

## What’s inside

| File | Role |
|------|------|
| `index.html` | Main dashboard: layout, TOC, sections, Chart.js rendering logic |
| `data.js` | Pre-aggregated metrics as `window.CMC_DATA` (what the UI reads) |
| `metrics.json` | Same metrics as plain JSON (audit / reuse; not loaded by the UI) |
| `cmc-portfolio-report.html` | Single-file bundle (HTML + embedded data) for offline / alternate sharing |
| `decision.md` | Why Cloudflare Quick Tunnel was chosen for shareable links |
| `README.md` | This file — how pieces connect and how to run |

There is **no backend, database, or build step**. The browser loads HTML + JS and draws charts client-side.

---

## How the pieces connect

```
Source extract (CMC NDJSON / JSON)
        │
        ▼  (aggregated once offline)
   metrics.json  ←→  data.js   (same numbers; JS wraps them for the page)
        │
        ▼
   index.html
     ├─ loads Chart.js from CDN
     ├─ loads ./data.js  →  window.CMC_DATA
     └─ JS fills KPIs, tables, canvas charts from CMC_DATA
```

### Page load sequence

1. Browser requests `index.html` (locally or via tunnel).
2. `<script src="…chart.js…">` loads Chart.js from jsDelivr (viewer needs internet for CDN + fonts).
3. `<script src="./data.js">` sets `window.CMC_DATA`.
4. Inline script in `index.html` reads `CMC_DATA`, builds the sidebar TOC, fills section DOM, and creates Chart.js charts on `<canvas>` elements.

### Data shape (high level)

`CMC_DATA` includes objects used by sections such as:

- `kpis` — totals, countries, products, status, date range  
- `findings` / `recommendations` — highlight cards  
- `questions_per_country`, `ha_analysis`, `product_analysis`  
- `quarterly`, `monthly`, `top_keywords`, `top_codes`  
- `heatmap`, chart-ready slices (`chart_country_top15`, etc.)  

`metrics.json` mirrors that content without the `window.CMC_DATA = …` wrapper.

---

## Report sections (UI)

Sidebar TOC (`00`–`09`) maps to blocks in `index.html`, for example:

- Overview KPIs  
- Questions per country  
- Top findings / keywords / CTD codes  
- Quarterly & monthly trends  
- Status / answer quality / language  
- Product analysis  
- HA analysis  
- Heatmap / country–product pairs  
- Recent documents  
- Recommendations  

---

## How to run it

### Option A — Local only (this machine)

```bash
cd /Users/mohd.arif1/Documents/Healthcare_app/cmc-dashboard
python3 -m http.server 8765
```

Open: [http://127.0.0.1:8765/](http://127.0.0.1:8765/)

Serving over HTTP matters for consistent relative loads of `data.js`. Opening `index.html` via `file://` can work in some browsers but is less reliable.

### Option B — Shareable meeting link (Cloudflare Quick Tunnel)

Files stay on your Mac. Cloudflare only forwards public HTTPS traffic to the local server:

```
Viewer → https://*.trycloudflare.com → cloudflared (this Mac) → python :8765 → cmc-dashboard/
```

```bash
# Terminal 1
cd /Users/mohd.arif1/Documents/Healthcare_app/cmc-dashboard
python3 -m http.server 8765

# Terminal 2 (cloudflared installed or binary available)
cloudflared tunnel --url http://127.0.0.1:8765
```

Copy the printed `https://….trycloudflare.com` URL for meetings.

**Temporary:** the URL dies when you stop `cloudflared`, stop the Python server, or the Mac sleeps/goes offline. A new tunnel run gets a **new** random URL.

See `decision.md` for the full decision record (why tunnel vs GitHub Pages / Netlify, security notes).

### Option C — Single-file HTML

Open or email `cmc-portfolio-report.html` when you want one file without a separate `data.js`. Charts still need Chart.js CDN access unless you offline-bundle further.

---

## Updating the numbers later

1. Re-aggregate the source CMC extract into the same metric shape.  
2. Refresh `metrics.json` and `data.js` (`window.CMC_DATA = {…};`).  
3. Refresh the single-file report if you still use it.  
4. Restart or keep serving the folder; viewers refresh the browser to see changes.

The UI does **not** query a live API — it only shows what is baked into `data.js`.

---

## Dependencies (runtime)

| Dependency | Where | Needed for |
|------------|--------|------------|
| Chart.js 4.x | CDN in `index.html` | Charts |
| IBM Plex fonts | Google Fonts CDN | Typography |
| Python 3 | Local | `http.server` |
| cloudflared | Local (optional) | Public Quick Tunnel |

---

## Bottom line

- **Source of truth for the live multi-file dashboard:** `index.html` + `data.js` on disk.  
- **How it runs:** static files → local HTTP server → (optional) Cloudflare Quick Tunnel for a shareable HTTPS link.  
- **Not permanently hosted** on Cloudflare Pages; the tunnel is only a temporary reverse proxy.  
