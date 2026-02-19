# Heist Board — Timeline & Architecture

> Real-time operational dashboard for the Dead Drop AI agent team.
> Themed after Payday 2's CRIME.NET — dark tactical aesthetic, neon glows, scanlines.

---

## 1. What Is the Heist Board?

The Heist Board is a single-page web application that visualizes the Dead Drop multi-agent system in real time. It connects directly to the Dead Drop MCP server via JSON-RPC 2.0 over HTTP, polling live data and rendering it across four coordinated views:

- **Crew Panel** (left) — agent cards with health rings, roles, active tasks, progress bars
- **SVG Canvas** (center) — war-room map with crew nodes, task cards, assignment wires, message particles
- **Info Panel** (right) — task board, contract shelf, activity feed with compose bar
- **Pipeline Strip** (top) — 8-hat phase stepper showing development lifecycle progress

The board also features a **Lobby** screen for team selection before entering the dashboard view.

### Tech Stack

- Pure HTML/CSS/JS — no frameworks, no build step
- CSS custom properties design system (`--hb-bg`, `--hb-panel`, `--hb-orange`, etc.)
- Web Audio API for notification beeps
- SVG with animated filters, particles, and bezier wires
- `localStorage` for panel collapse persistence

---

## 2. Architecture — File Ownership

Every file is named after its owning agent (the "Hemisphere Split" from the Drift protocol):

| File | Owner | Lines | Responsibility |
|------|-------|-------|----------------|
| `index.html` | juno | 376 | HTML structure, zoom/pan controller, shared contract |
| `spartan-style.css` | spartan | 2440 | Full CSS design system — PD2 theme, layouts, animations |
| `cortana-icons.js` | cortana | ~200 | SVG icon registry — PD2 masks, role icons, color maps |
| `cortana-canvas.js` | cortana | 1208 | SVG canvas renderer — grid, crew nodes, task cards, wires, particles |
| `roland-panels.js` | roland | 1332 | PanelController — all HTML panels, compose bar, toasts, phase stepper |
| `spartan-app.js` | spartan | 714 | AppController — MCP session, polling engine, state management, events |

### Load Order

```
1. spartan-style.css    (all CSS)
2. cortana-icons.js     (SVG icon definitions)
3. cortana-canvas.js    (canvas renderer)
4. roland-panels.js     (UI panels)
5. spartan-app.js       (AppController — starts polling, LAST)
```

### Global Namespace

```javascript
window.HeistBoard = {
  app:    AppController,    // spartan-app.js
  canvas: HeistCanvas,      // cortana-canvas.js
  icons:  IconRegistry,     // cortana-icons.js
  panels: PanelController,  // roland-panels.js
}
```

---

## 3. How Agents Connect — MCP Configuration

### The Dead Drop Server

A 3-tier MCP server architecture:

| Tier | Port | Purpose |
|------|------|---------|
| **Local** | `:9400` | Team-local messaging, tasks, contracts |
| **Hub** | `:9500` | Multi-team room management |
| **Room** | `:9501` | Cross-team collaboration rooms |

All communication uses **MCP Streamable HTTP** — JSON-RPC 2.0 over `POST /mcp`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "who",
    "arguments": {}
  }
}
```

Response: SSE format (`event: message\ndata: {...}\n\n`), with `result.content[0].text` containing a JSON string to parse.

### Session Initialization

1. `POST /mcp` with `method: "initialize"` — server returns `Mcp-Session-Id` header
2. Client sends `notifications/initialized` (fire-and-forget)
3. All subsequent requests include the `Mcp-Session-Id` header
4. On HTTP 401/403/404 — session expired, reconnect

### Agent Registration

Each Claude Code session registers on startup:

```
register(agent_name="roland", role="coder", description="...", team="Gypsy Danger")
```

Agents show up in `who()` with health tracking:
- **healthy** — heartbeat within 2 minutes
- **stale** — heartbeat within 10 minutes
- **dead** — no heartbeat for 10+ minutes
- **unknown** — never pinged

### Push Notifications (fswatch Watcher)

```
Message arrives in SQLite → fswatch detects DB change →
watcher daemon writes .alert file → Claude Code reads alert →
agent calls check_inbox() → watcher auto-resets
```

Watchers are persistent launchd daemons installed via:
```bash
~/dead-drop-teams/scripts/install-watchers.sh agent1 agent2 ...
```

For non-persistent sessions, a blocking watcher script is available:
```bash
~/dead-drop-teams/scripts/wait-for-message.sh <agent_name>
```

---

## 4. How the Dashboard Works

### Startup Sequence

```
DOMContentLoaded
  ├─ cortana-icons.js  → IconRegistry instantiated
  ├─ cortana-canvas.js → HeistCanvas.init() — renders grid, zones, scanline sweep
  ├─ roland-panels.js  → PanelController._setup()
  │    ├─ _bindEvents()          — listen for hb:* events
  │    ├─ _startClock()          — HH:MM:SS clock every 1s
  │    ├─ _initPlaceholders()    — empty panels + "CONNECTING..."
  │    ├─ _initPanelToggles()    — collapsible crew/info panels
  │    ├─ _initToastContainer()  — toast notification container
  │    └─ _injectComposeBar()    — message compose with @mention
  └─ spartan-app.js → AppController.start()
       ├─ _initialize()          — MCP handshake, get session ID
       ├─ _renderLobby(agents)   — show team selection cards
       └─ (user clicks team) → _selectTeam() → refresh() + _startPollers()
```

### Polling Engine

After team selection, AppController polls four endpoints on staggered intervals:

| Endpoint | Interval | Event Emitted |
|----------|----------|---------------|
| `get_history(count=30)` | 5s | `hb:messages-updated` |
| `list_tasks()` | 10s | `hb:tasks-updated` |
| `who()` | 15s | `hb:agents-updated` |
| `list_contracts()` | 30s | `hb:contracts-updated` |

State diffing via `JSON.stringify` comparison — events only fire when data actually changes.

### Event Flow

```
AppController polls → state diff detected → emit hb:* CustomEvent on document
  ├─ PanelController listens → re-renders HTML panels
  └─ HeistCanvas listens → re-renders SVG elements
```

All events use `hb:` prefix and fire on `document`:

| Event | Payload | Listeners |
|-------|---------|-----------|
| `hb:connected` | `{ sessionId }` | panels (status line) |
| `hb:disconnected` | `{}` | panels (status line) |
| `hb:agents-updated` | `{ agents }` | panels (crew cards), canvas (crew nodes) |
| `hb:tasks-updated` | `{ tasks }` | panels (task board), canvas (task cards + wires) |
| `hb:contracts-updated` | `{ contracts }` | panels (contract shelf) |
| `hb:messages-updated` | `{ messages }` | panels (activity feed), canvas (message particles) |
| `hb:risk-updated` | `{ score, breakdown }` | app (risk meter DOM) |
| `hb:sync-tick` | `{ endpoint, ok }` | panels (sync indicator) |

### Team Filtering

When a specific team is selected (not "ALL TEAMS"), the AppController filters data before emitting events:
- **Agents**: only agents matching the selected team
- **Tasks**: tasks assigned to or created by team members
- **Messages**: messages from/to team members, plus broadcasts

---

## 5. Panel Details

### Crew Panel (`roland-panels.js → renderCrew`)

Each crew card displays:
- **Avatar** with PD2 mask icon from IconRegistry (Dallas, Wolf, Sydney, Hoxton, Chains)
- **Health ring** — green pulse (healthy), amber blink (stale), red static (dead)
- **Role** in PD2 class naming: lead=MASTERMIND, coder=ENFORCER, tester=TECHNICIAN, etc.
- **Active task** with progress bar (assigned=10%, in_progress=50%, review=75%)
- **Last seen** relative timestamp from heartbeat
- **Status** line from `set_status()`

Sorting: lead first, then by health (healthy > stale > unknown > dead), then alphabetical.

### Task Board (`roland-panels.js → renderTasks`)

Tasks grouped by status in display order:

| Status | PD2 Label | Visual |
|--------|-----------|--------|
| `in_progress` | IN PLAY | Active, cyan glow |
| `review` | SECURING | Amber pulse, approve/reject buttons |
| `completed` | SECURED | Green check |
| `verified` | CONFIRMED | Purple double-check |
| `assigned` | BRIEFED | Waiting |
| `pending` | LOCKED | Hatched overlay, dim |
| `failed` | COMPROMISED | Red X |

Review tasks show **quick-action buttons** (APPROVE / REJECT) on hover, calling `approve_task()` / `reject_task()` via the MCP API.

### Contract Shelf (`roland-panels.js → renderContracts`)

Displays declared interface contracts with PD2 category mapping:
- `function`, `dom_id`, `css_class` → EQUIPMENT
- `file_path`, `api_endpoint` → INTEL
- `event` → COMMS
- `other` → ASSET

### Activity Feed (`roland-panels.js → renderFeed`)

- Newest-first, max 50 messages
- Click to expand/collapse full message content
- **REPLY** button sets compose bar recipient and shows quote preview
- **@mention** autocomplete with dropdown
- **Toast notifications** + 800Hz audio beep on new messages (skipped on initial page load)
- **Unread badge** on COMMS header when feed is scrolled away from top

### SVG Canvas (`cortana-canvas.js`)

The center SVG (1600x900 viewBox) renders:
- **Blueprint grid** — minor (20px) and major (100px) lines with corner brackets
- **Crew nodes** — semicircle layout at top, with health-ring glow filters, mask icons, name labels, task count badges
- **Task cards** — grid layout below separator, status-colored with neon pulse filters on active tasks
- **Assignment wires** — dashed bezier curves from crew nodes to their task cards, with arrowhead markers
- **Message particles** — glowing dots that animate along bezier paths from sender to recipient
- **Ambient data flow** — periodic particles traveling along wires to simulate network activity
- **Scanline sweep** — startup animation for PD2 atmosphere

### Phase Stepper (Pipeline Strip)

8-hat development pipeline derived from task `role_hat` values:

```
LEAD → RESEARCH → BUILD → REVIEW → TEST → FIX → PROD → DELIVER
```

Each phase shows as: **pending** (dim), **active** (glowing), or **complete** (checkmark). A progress fill bar tracks the active phase position.

### Compose Bar

- Recipient dropdown populated from live agent list
- @mention autocomplete (type `@` followed by agent name)
- Reply context with quoted preview
- Enter to send, Escape to dismiss mention dropdown
- Configurable sender identity via `data-sender` attribute or `app.senderName`

---

## 6. Full Agent Workflow — End-to-End Timeline

### Phase 1: Registration & Discovery

```
Agent starts Claude Code session
  → register(agent_name, role, description, team)
  → check_inbox() for any waiting messages
  → set_status("standing by")
  → ping() every 60s for health tracking
```

On the Heist Board:
- Agent appears as a crew card in the left panel
- Agent node appears on the SVG canvas with health ring
- Lobby team cards update with member count

### Phase 2: Neural Handshake (Sync)

```
Lead (juno) → initiate_handshake(message="<plan>")
  → All agents receive plan via check_inbox()
  → Each agent → ack_handshake(handshake_id)
  → Lead checks handshake_status() — GO when all ACK
  → Lead → declare_contract() for shared interfaces
```

On the Heist Board:
- Handshake messages appear in the activity feed with particle animations
- Contracts appear in the contract shelf

### Phase 3: Task Assignment

```
Lead → create_task(title, description, assigned_to, project, role_hat)
  → Assignee receives notification
  → Assignee → update_task(status="in_progress")
  → Assignee → set_status("working on TASK-XXX")
```

On the Heist Board:
- Task card appears in BRIEFED group, moves to IN PLAY
- Assignment wire draws from crew node to task card
- Crew card shows active task with progress bar
- Phase stepper updates based on role_hat
- Toast notification for status change

### Phase 4: Work & Communication

```
Agent works on task, exchanging messages:
  → send(from_agent, to_agent, message, task_id)
  → check_inbox() for replies
  → set_status() to track progress
```

On the Heist Board:
- Messages appear in activity feed with slide-in animation
- Message particles animate on SVG canvas (sender → recipient)
- Status updates reflected on crew cards
- Ambient data flow particles pulse along assignment wires

### Phase 5: Review Submission

```
Agent completes work:
  → submit_for_review(task_id, summary, files_changed, test_results)
  → Task transitions to "review" status
  → Lead receives structured review message
```

On the Heist Board:
- Task card moves to SECURING group with amber pulse animation
- APPROVE / REJECT quick-action buttons appear on hover
- Phase stepper shows REVIEW as active

### Phase 6: Approval or Rejection

```
Lead reviews:
  → approve_task(task_id) → status="completed" → assignee notified
  OR
  → reject_task(task_id, reason) → status="in_progress" → assignee reworks
```

On the Heist Board:
- Approved: task card moves to SECURED with green checkmark, toast "SECURED"
- Rejected: task card returns to IN PLAY, toast with rejection reason
- Quick-actions: approve button calls API directly; reject opens inline modal for reason input

### Phase 7: Verification

```
Independent verifier (not the builder, not the approver):
  → verify_task(task_id) → status="verified"
  OR
  → reject_verification(task_id, reason) → back to in_progress
```

On the Heist Board:
- Task card moves to CONFIRMED with purple double-checkmark
- Phase stepper shows completion

### Phase 8: Goal Completion

```
When all linked tasks are verified:
  → Lead → verify_goal(goal_id) → goal status="verified"
  → Pipeline shows full completion
```

On the Heist Board:
- All phase stepper steps show checkmarks
- Progress fill bar reaches 100%

---

## 7. Data Flow Diagram

```
Dead Drop MCP Server (localhost:9400)
  │
  │ POST /mcp (JSON-RPC 2.0)
  │ Mcp-Session-Id header
  │
  ▼
AppController (spartan-app.js)
  │
  │ Polls: who, list_tasks, get_history, list_contracts
  │ State diff via JSON.stringify
  │ Filters by selected team
  │
  │ hb:* CustomEvents on document
  │
  ├───────────────────┬──────────────────────────────┐
  │                   │                              │
  ▼                   ▼                              ▼
PanelController     HeistCanvas                 Risk Meter
(roland-panels.js)  (cortana-canvas.js)         (spartan-app.js)
  │                   │                              │
  │ HTML panels:      │ SVG layers:                  │ DOM:
  │ - Crew cards      │ - Grid + zones               │ - #risk-fill
  │ - Task board      │ - Crew nodes                 │ - #risk-value
  │ - Contracts       │ - Task cards                 │
  │ - Activity feed   │ - Assignment wires           │
  │ - Compose bar     │ - Message particles          │
  │ - Phase stepper   │ - Ambient data flow          │
  │ - Toast notifs    │ - Glow filters               │
  │ - Clock           │ - Scanline sweep             │
  │                   │                              │
  │ Writes to DOM:    │ Writes to SVG:               │
  │ #crew-slots       │ #layer-grid                  │
  │ #task-board       │ #layer-zones                 │
  │ #contract-shelf   │ #layer-wires                 │
  │ #activity-feed    │ #layer-tasks                 │
  │ #phase-stepper    │ #layer-crew                  │
  │ #status-line      │ #layer-fx                    │
  │ #clock            │ #layer-ui                    │
```

---

## 8. PD2 Aesthetic Mapping

| Real Concept | PD2 Equivalent |
|--------------|----------------|
| Dashboard | CRIME.NET |
| Agent | Crew member |
| Agent name | Mask (Dallas, Wolf, Sydney, Hoxton, Chains) |
| Task | Objective / Heist |
| Task status | PD2 labels (LOCKED, BRIEFED, IN PLAY, etc.) |
| Contract | Equipment / Intel / Comms |
| Message | Transmission |
| Health monitoring | Threat level |
| Development pipeline | 8-hat pipeline phases |
| Role | PD2 class (MASTERMIND, ENFORCER, GHOST, TECHNICIAN) |
| Team | Crew |
| Lobby | CRIME.NET crew selection |

### Agent-to-Mask Color Mapping

| Agent | Mask | Accent Color | CSS Variable |
|-------|------|-------------|--------------|
| juno | Dallas | `#3399ff` (blue) | `--mask-juno` |
| spartan | Wolf | `#ff3333` (red) | `--mask-spartan` |
| cortana | Sydney | `#cc66ff` (purple) | `--mask-cortana` |
| roland | Hoxton | `#e8640a` (orange) | `--mask-roland` |
| *other* | Chains | `#00ff88` (green) | default |

---

## 9. Responsive Breakpoints

| Width | Layout |
|-------|--------|
| >1600px | Wide — expanded panels and canvas |
| 901-1200px | Medium — reduced panel widths |
| <=900px | Stacked — panels below canvas |
| <=700px | Mobile — crew hidden, info panel overlay |

Panels are also independently collapsible via toggle buttons, with state persisted to `localStorage`.

---

## 10. Shared Contract (v2.0)

The shared contract lives as an HTML comment block in `index.html` (lines 114-291). It defines:

- **API call format** — JSON-RPC 2.0 POST to `/mcp`
- **Endpoints used** — `who`, `list_tasks`, `goal_status`, `list_contracts`, `get_history`, `approve_task`, `reject_task`, `send`
- **Global namespace** — `window.HeistBoard` with four modules
- **Event system** — all `hb:` prefixed CustomEvents
- **DOM IDs** — every element ID used across all files
- **CSS class conventions** — every class generated by JS
- **Load order** — guaranteed file execution sequence
- **Polling intervals** — per-endpoint timing
- **Data attributes** — `data-health`, `data-status`, `data-agent`, `data-task-id`

This contract is the single source of truth for cross-file integration. When any agent changes a shared interface, they must update the contract and use `declare_contract()` to notify the team.
