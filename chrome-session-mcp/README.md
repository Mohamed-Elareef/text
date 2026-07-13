# Chrome Session MCP

Dockerized **Google Chrome** driven by **Playwright** and exposed as a full
**MCP server** (streamable HTTP + bearer token) that you can attach to Claude
Code as a remote connector.

It mirrors the login sessions from the Chrome running in your desktop/GUI
(the noVNC Ubuntu box): every site you're signed into there is automatically
signed in here too. The agent then gets a rich, flexible tool surface to browse
and operate on **any** site — including ones that try to block automation —
right down to running arbitrary JavaScript against the live page.

## How session sync works

The container runs Chrome on a **separate copy** of the profile (so your GUI
Chrome keeps running, no lock conflicts). Your real profile is mounted
**read-only** at `/host-profile`, and the server refreshes login state on two
levels:

| Mode | What it copies | Disruptive? | When |
|------|----------------|-------------|------|
| **soft** | cookies (decrypted from the host profile, injected live) | no | every `SOFT_SYNC_INTERVAL` seconds (default 300) + `sync_session(mode="soft")` |
| **hard** | the whole profile: cookies **+ localStorage + IndexedDB** | yes (relaunches browser) | at startup + `sync_session(mode="hard")` |

Cookies are decrypted with Chrome's Linux "basic" key scheme (`peanuts`), which
matches how the host stores them. If a site uses non-cookie auth (localStorage /
IndexedDB tokens) and still looks logged out, call `sync_session(mode="hard")`.

## Deploy (on the server that runs the GUI Chrome)

```bash
cd chrome-session-mcp
./run.sh
```

This builds the image, starts the container, mounts the host profile read-only,
generates and persists a token, and prints the exact `claude mcp add` command.

Override defaults via env, e.g.:

```bash
PORT=8765 HOST_PROFILE_DIR=/root/.config/google-chrome SOFT_SYNC_INTERVAL=180 ./run.sh
```

## Reverse proxy + TLS (how it's actually exposed)

The container port is **not** published on a public interface. It binds to the
docker bridge gateway (`172.17.0.1:8765`), and the public HTTPS endpoint is
served by the existing **Traefik** reverse proxy using **path-based routing** on
the shared MCP subdomain — so every MCP lives under one domain / one TLS cert and
you add a new one by adding a path:

```
https://mcp.cloudstars.club/chrome/mcp   ->  172.17.0.1:8765/mcp   (this server)
https://mcp.cloudstars.club/<other>/mcp  ->  ...                    (future MCPs)
```

The Traefik dynamic config is in [`deploy/traefik-mcp.yml`](deploy/traefik-mcp.yml)
(a `PathPrefix(/chrome)` router at higher priority + a `stripPrefix` middleware so
the container receives `/mcp`). TLS is the subdomain's existing Cloudflare Origin
cert; with `providers.file.watch=true` the config hot-reloads, no restart.

> An nginx equivalent is in [`deploy/nginx-chrome.conf`](deploy/nginx-chrome.conf)
> if you route through nginx instead of Traefik.

## Connect to Claude Code

```bash
claude mcp add --transport http chrome-session \
    https://mcp.cloudstars.club/chrome/mcp \
    --header "Authorization: Bearer <TOKEN>"
```

(For claude.ai custom connectors that only support a URL, you can instead pass
the token as a query param: `.../chrome/mcp?token=<TOKEN>`.)

## Tools

Navigation: `browser_navigate`, `browser_back`, `browser_forward`,
`browser_reload`, `browser_current_url`, `browser_wait_for`.

Reading/structure: `browser_snapshot` (interactive elements with refs),
`browser_get_html`, `browser_get_text`, `browser_query`, `browser_screenshot`.

Interaction: `browser_click`, `browser_type`, `browser_fill_form`,
`browser_select_option`, `browser_hover`, `browser_press_key`, `browser_scroll`,
`browser_upload_file`, `browser_handle_dialog`.

Power tools: `browser_evaluate` (arbitrary JS in the page),
`browser_evaluate_on_element`.

Tabs: `browser_tabs_list`, `browser_tab_new`, `browser_tab_select`,
`browser_tab_close` (never closes the last tab), `browser_close_other_tabs`.

Session/cookies: `browser_get_cookies`, `browser_set_cookies`, `sync_session`,
`session_status`.

Screenshots & viewing:
- `browser_screenshot` — inline PNG (if your client renders images).
- `browser_screenshot_url` — saves the PNG and returns a **clickable HTTPS link**
  (`/chrome/shots/<id>.png`, token-exempt, unguessable name). Use this when inline
  images don't display in your client.
- `browser_screenshot_telegram` — pushes the screenshot to a Telegram bot
  (`browser_screenshot_telegram(chat_id=...)`); `telegram_get_chat_id` lists chats
  that have messaged the bot.
- `live_view_url` — returns the live interactive **noVNC** URL.

## Live interactive view (watch + control by hand)

`x11vnc` + `noVNC` run in the container on the Xvfb display, exposed through
Traefik at a **separate path behind HTTP Basic Auth**:

```
https://mcp.cloudstars.club/chrome-view/vnc.html?path=chrome-view/websockify&autoconnect=1&resize=scale
```

Open it in a browser to **see the automated Chrome live and control it with your
own mouse/keyboard** — for manual logins, solving a CAPTCHA, or any step you'd
rather do yourself. The agent and you share the same browser, so a login you do
here persists for the agent's next actions.

Telegram + view are configured via env on `run.sh`: `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `PUBLIC_BASE_URL`, `LIVE_VIEW_URL`. The Basic Auth user for
the view is set in `deploy/traefik-mcp.yml` (`openssl passwd -apr1 <password>`).

### Typical form flow
1. `browser_navigate(url)`
2. `browser_snapshot()` → read the refs of the fields and the submit button
3. `browser_fill_form(fields=[{ref:"3", value:"..."}, ...], submit_ref:"7")`
   — or `browser_evaluate(...)` to introspect/submit a tricky custom form.

## Anti-detection & proxy

The browser is driven by **patchright** (a patched Playwright that removes the
CDP automation fingerprint, e.g. the `Runtime.enable` leak) plus a real Google
Chrome profile, headful under Xvfb. It reports `navigator.webdriver = false`, a
full (non-reduced) Chrome UA, and a working WebGL context.

That defeats fingerprint-based detection, but **not IP-reputation detection**.
Sites behind **DataDome / PerimeterX** (e.g. `idealista.pt`) block *datacenter*
IPs regardless of how clean the browser looks. On a VPS you have two options:

1. **Residential/mobile proxy (reliable):** set `PROXY_SERVER` (+ `PROXY_USERNAME`
   / `PROXY_PASSWORD`) and the whole browser routes through it, e.g.
   `PROXY_SERVER=http://gate.example.com:7000 ./run.sh`.
2. **Solve once via the live view:** open the noVNC view and pass the human
   check yourself; the trust cookie persists in the profile (may re-challenge
   from a datacenter IP).

## Security notes
- The endpoint carries your live logged-in sessions and can run arbitrary JS on
  any site as you. Keep the token secret; prefer restricting the port to trusted
  IPs (firewall / reverse proxy with TLS) rather than exposing it wide open.
- Everything runs on your own server against your own accounts.
