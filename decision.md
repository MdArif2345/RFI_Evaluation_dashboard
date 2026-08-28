# Decision: How the CMC Dashboard Shareable Link Works

## Summary

The dashboard is **not hosted on Cloudflare’s permanent cloud storage**.  
It stays on **your Mac**, and Cloudflare only creates a **temporary public tunnel** that forwards internet traffic to a local web server.

```
Viewer (browser)
    │
    ▼
https://*.trycloudflare.com     ← public URL (Cloudflare edge)
    │
    ▼
cloudflared (on your Mac)       ← tunnel client
    │
    ▼
python3 -m http.server :8765    ← local static file server
    │
    ▼
/Users/.../cmc-dashboard/       ← index.html + data.js (source of truth)
```

---

## What was created (files)

| File | Role |
|------|------|
| `index.html` | Dashboard UI + charts (Chart.js via CDN) |
| `data.js` | Pre-aggregated metrics (`window.CMC_DATA`) |
| `metrics.json` | Same metrics in JSON (for reuse / audit) |
| `cmc-portfolio-report.html` | Single-file bundle (HTML + embedded data) for easier sharing later |
| `decision.md` | This document |

**There is no Cloudflare Pages / Workers / R2 project** for this dashboard. Nothing was uploaded to a Cloudflare account as a permanent site.

---

## How the shareable link was created

### Step 1 — Serve the folder locally

From `cmc-dashboard/`:

```bash
python3 -m http.server 8765
```

This exposes:

- `http://127.0.0.1:8765/` → `index.html`
- `http://127.0.0.1:8765/data.js` → metrics script

Only your machine can reach `127.0.0.1` by default.

### Step 2 — Open a Cloudflare Quick Tunnel

A `cloudflared` binary was downloaded and run as:

```bash
cloudflared tunnel --url http://127.0.0.1:8765
```

Cloudflare then assigned a random public URL, e.g.:

```text
https://climbing-appliance-would-tire.trycloudflare.com
```

That URL is a **Quick Tunnel** (`trycloudflare.com`):

- No Cloudflare account / named tunnel required
- No permanent DNS record under a custom domain
- No uptime guarantee
- Stops when `cloudflared` or the local server stops

### Step 3 — Traffic path when someone opens the link

1. User opens the `https://…trycloudflare.com` link  
2. Request hits Cloudflare’s edge  
3. Cloudflare forwards it through the active Quick Tunnel to your Mac’s `cloudflared` process  
4. `cloudflared` proxies to `http://127.0.0.1:8765`  
5. Python returns `index.html` / `data.js`  
6. Browser loads Chart.js + fonts from public CDNs and renders the report  

So Cloudflare is acting as a **reverse proxy / tunnel**, not as the place where the HTML is stored.

---

## Decision record

### Decision

Use a **local static server + Cloudflare Quick Tunnel** to produce a shareable HTTPS link quickly.

### Why this option

| Option | Pros | Cons | Chosen? |
|--------|------|------|---------|
| **Local + Cloudflare Quick Tunnel** | Fast; no GitHub/Netlify login; works immediately | Temporary; needs this Mac online; URL changes if restarted | **Yes** |
| GitHub Pages / gist | More durable public hosting | `gh` auth token was invalid at the time | No (blocked) |
| Netlify / Vercel / Surge | Durable static hosting | Needs account/auth and deploy step | No (not set up) |
| Share folder / `file://` only | Private, simple | Not a real shareable web link for others | No (user rejected) |
| Upload HTML to anonymous paste hosts | One-click URL | Weak for multi-file apps; poor fit for dashboards; data exposure risk | No |

### Alternatives considered later (if you need a permanent link)

1. **GitHub Pages** after `gh auth refresh`  
2. **Netlify / Vercel** static deploy of `cmc-dashboard/`  
3. **Named Cloudflare Tunnel** + your own domain (production-style)  
4. Host only `cmc-portfolio-report.html` as a single artifact on any static host  

---

## Important properties / constraints

1. **Source of truth is local**  
   Editing `index.html` / `data.js` on disk is what viewers see (after refresh), as long as the tunnel + local server are running.

2. **Link lifetime**  
   The `trycloudflare.com` URL dies when:
   - `cloudflared` is stopped
   - the Python server on port `8765` is stopped
   - the machine sleeps / goes offline / restarts

3. **Security / confidentiality**  
   Anyone with the URL can view the dashboard while the tunnel is up.  
   This CMC extract may contain sensitive regulatory content — treat the public URL as confidential and shut the tunnel down when sharing is done.

4. **Not “hosted on Cloudflare” in the Pages sense**  
   Cloudflare provides connectivity. Your laptop provides the files.

---

## How to recreate the shareable link

```bash
# Terminal 1 — local static server
cd /Users/mohd.arif1/Documents/Healthcare_app/cmc-dashboard
python3 -m http.server 8765

# Terminal 2 — public tunnel (requires cloudflared installed)
cloudflared tunnel --url http://127.0.0.1:8765
```

Copy the printed `https://….trycloudflare.com` URL and share that.

To stop sharing: stop both processes (Ctrl+C), or kill the PIDs for `http.server` and `cloudflared`.

---

## Related product decisions for the dashboard itself

- **Aggregated metrics in `data.js`** instead of shipping the full NDJSON into the browser (smaller, faster, shareable report).  
- **Dark navy TOC** styled like a portfolio cover page (numbered `00`–`09`, teal active state).  
- **Chart.js via CDN** so the HTML stays light; charts need internet access in the viewer’s browser.  
- **Generation timestamp removed** from the UI per request (still present inside `metrics.json` / `data.js` metadata if needed for audit).

---

## Bottom line

**Where is it hosted?** On your machine under `cmc-dashboard/`.  
**How does the link work?** Cloudflare Quick Tunnel publicly forwards HTTPS traffic to a local Python static server.  
**Is it permanent?** No — it is a temporary share mechanism, not a deployed Cloudflare website.
