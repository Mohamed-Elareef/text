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

## Connect to Claude Code

```bash
claude mcp add --transport http chrome-session \
    http://<SERVER_IP>:8765/mcp \
    --header "Authorization: Bearer <TOKEN>"
```

(For claude.ai custom connectors that only support a URL, you can instead pass
the token as a query param: `.../mcp?token=<TOKEN>`.)

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
`browser_tab_close`.

Session/cookies: `browser_get_cookies`, `browser_set_cookies`, `sync_session`,
`session_status`.

### Typical form flow
1. `browser_navigate(url)`
2. `browser_snapshot()` → read the refs of the fields and the submit button
3. `browser_fill_form(fields=[{ref:"3", value:"..."}, ...], submit_ref:"7")`
   — or `browser_evaluate(...)` to introspect/submit a tricky custom form.

## Security notes
- The endpoint carries your live logged-in sessions and can run arbitrary JS on
  any site as you. Keep the token secret; prefer restricting the port to trusted
  IPs (firewall / reverse proxy with TLS) rather than exposing it wide open.
- Everything runs on your own server against your own accounts.
