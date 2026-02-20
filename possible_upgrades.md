# Dead Drop - Possible Upgrades & Future Ideas

**Date:** 2026-02-20
**Team:** gypsy-danger (juno, spartan, cortana)

---

## 1. PRODUCTION DEPLOYMENT FOR WEB APP

### Infrastructure
- Deploy on VPS (DigitalOcean/Linode) or cloud (AWS/GCP)
- Ubuntu 24.04 LTS or Debian 12
- Docker + docker-compose installed
- Nginx reverse proxy for SSL termination
- Firewall: only ports 80/443 exposed

### Network
- Domain name pointing to server
- Let's Encrypt SSL/TLS certificates (auto-renewal)
- nginx config for proxying to port 5001

### Security Hardening
**Container Security:**
- Run container as non-root user
- Read-only filesystem where possible
- Database volume mounted read-only
- Resource limits (CPU/memory caps)

**Application Security:**
- Environment variables for all config
- CORS whitelist (only allowed origins)
- Rate limiting: 100 req/min per IP
- CSP headers to prevent XSS
- Remove Flask debug mode

**Server Security:**
- SSH key-only auth (no password)
- Fail2ban for brute force protection
- Automatic security updates enabled
- Regular vulnerability scanning

### CI/CD Pipeline
```
git push → GitHub Actions → Tests → Build → Deploy
```

**Process:**
- Automated testing (pytest for backend)
- Docker image build and tag
- Push to container registry
- SSH to production server
- Pull new image
- docker-compose down/up with zero downtime
- Health check verification

**Rollback:**
- Keep last 3 images
- One-command rollback to previous version

### Monitoring & Logging
**Application Monitoring:**
- Structured JSON logging (Flask + gunicorn)
- Log rotation (max 100MB, keep 7 days)
- Uptime monitoring (UptimeRobot or Pingdom)
- SSE connection count tracking
- Database query performance metrics

**Error Tracking:**
- Sentry integration for error reporting
- Email alerts on critical errors
- Slack webhook for deployment notifications

**Metrics Dashboard:** (optional)
- Grafana + Prometheus
- Track: requests/sec, response time, active SSE connections
- Database size growth monitoring

### Maintenance Procedures
**Regular Maintenance:**
- Weekly: Review logs for anomalies
- Monthly: Update dependencies (pip, Docker base image)
- Quarterly: Database cleanup (archive old messages)
- Security patches: Apply within 48 hours

**Backup Strategy:**
- Database is read-only - no backup needed from watcher
- Application code in git (source of truth)
- nginx config backed up to git repo

**Incident Response:**
1. Check logs: `docker-compose logs -f`
2. Check resource usage: `docker stats`
3. Restart container if needed: `docker-compose restart`
4. Rollback if bug: `docker-compose down && docker-compose up -d` (previous image)
5. Document incident in runbook

**Health Checks:**
- Automated: nginx health check endpoint (`/health`)
- Manual: Weekly dashboard spot-check
- Alert if SSE connections drop unexpectedly

---

## 2. TEAM COLLABORATION SOP (6-PHASE WORKFLOW)

### PHASE 0: TEAM COMPOSITION (Lead Assessment)
**juno's role:**
- Receive tasking from command
- Analyze task complexity and scope
- Assess parallelization opportunities
- Recommend optimal team size:
  - "2-agent task: juno + spartan (single component)"
  - "3-agent task: juno + spartan + cortana (frontend + backend)"
  - "4-agent task: needs 3 builders (complex system)"

**Decision criteria:**
- Can work be parallelized effectively?
- How many independent components?
- Timeline constraints?
- Coordination overhead vs. speed benefit?

**Command response:**
- Approve recommended team size
- Adjust if different assessment
- Give GO signal for Phase 1

**Output:** Approved team composition → proceed to planning

### PHASE 1: PLANNING (Lead-Driven)
**juno:**
- Break down into discrete subtasks
- Assign to spartan/cortana based on strengths
- Define success criteria and deliverables
- Set priorities and dependencies

**Builders:**
- Acknowledge assignment
- Ask clarifying questions
- Propose technical approach
- Get approval before starting

**Output:** Approved plan with task assignments

### PHASE 2: BUILDING (Builder-Driven)
**spartan/cortana:**
- Execute tasks independently
- Write code/docs/configs as specified
- Test locally before reporting
- Report completion with summary

**Communication:**
- Progress updates every 30 min if long task
- Immediately flag blockers
- No "I'm working on it" spam - results only

**juno:**
- Monitor progress
- Unblock builders if stuck
- Coordinate dependencies

**Output:** Working implementation ready for review

### PHASE 3: REVIEW (Lead-Driven)
**juno:**
- Code review (functionality, security, style)
- Test the implementation
- Provide specific, actionable feedback
- Approve or request changes

**Builders:**
- Receive feedback without argument
- Acknowledge each point
- Ask for clarification if needed

**Output:** Approval or list of required changes

### PHASE 4: FIXING (Builder-Driven)
**spartan/cortana:**
- Address ALL feedback points
- Test fixes thoroughly
- Report: "Fixed: [list items]"
- Request re-review

**juno:**
- Verify fixes
- Approve or iterate (back to Phase 3)

**Loop until:** All feedback addressed

**Output:** Approved, production-ready work

### PHASE 5: DELIVERY (Team-Driven)
**juno:**
- Final sign-off
- Report to command: "Task complete"
- Archive project artifacts
- Document lessons learned

**spartan/cortana:**
- Clean up working files
- Update documentation
- Stand by for next tasking

**Team:**
- Brief after-action review
- Update SOP if needed

**Output:** Delivered project, team ready for next task

### TEAM SIZE FRAMEWORK

**CORE TEAM (Default) - 80% of tasks**
- 2-agent: juno (lead) + spartan (primary builder)
- Single-component work, quick turnaround
- Examples: scripts, configs, simple apps, bug fixes

**SCALED TEAM (As Needed) - 15% of tasks**
- 3-agent: juno + spartan + cortana
- Multi-component work (frontend + backend)
- Parallel development possible
- Examples: web apps with separate UI/API work

**MAXIMUM TEAM - 5% of tasks**
- 4-agent: juno + spartan + cortana + temp builder
- Complex systems with 3+ independent components
- High-priority rush jobs
- Examples: full-stack app + infrastructure + docs

**SCALING CRITERIA:**
- When to scale UP: 2+ independent components, tight timeline, specialized skills needed
- When to stay LEAN: Sequential work, single focus, coordination overhead > benefit
- Hard limit: 1 lead + 3 builders (4 total max)

**RECOMMENDED DEFAULT:** Start with 2-agent for every task. Scale up ONLY if juno determines clear benefit. When in doubt, stay lean.

### COMMUNICATION RULES
1. Check inbox after every task - don't go deaf
2. Relaunch watcher after check_inbox - stay responsive
3. Structured messages: What you did, what you found, what's next
4. No redundant messages - one task = one completion message
5. Blockers get immediate escalation to juno

### ROLE CLARITY
- **juno:** Lead, reviewer, coordinator, decision maker
- **spartan:** Coder, builder (backend/systems focus)
- **cortana:** Builder, maintainer (when active)

---

## 3. DEAD DROP MESSAGE LOGGER (JSON)

### Implementation Approach
**Python daemon script:**
- Polls `~/.dead-drop/messages.db` every 1 second
- Tracks last processed message ID
- Writes new messages to JSON log file
- Runs as background process (launchd/systemd)

**Why Python polling:**
- Simple, reliable, cross-platform
- Direct SQLite access (no dependencies)
- Easy error recovery
- Low resource usage

### Log File Location & Naming
**Directory:** `~/.dead-drop/logs/`

**Daily rotation:**
- `messages-2026-02-20.json` (today)
- `messages-2026-02-21.json` (tomorrow)
- Auto-creates new file at midnight

**Format:** Newline-delimited JSON (NDJSON)
- One JSON object per line
- Easy to grep, tail -f, parse
- Example:
```json
{"id":486,"from_agent":"juno","to_agent":"spartan","content":"...","timestamp":"2026-02-20T00:38:06.815086","read_flag":1,"is_cc":0,"cc_original_to":null,"task_id":null,"reply_to":null}
{"id":487,"from_agent":"spartan","to_agent":"juno","content":"...","timestamp":"2026-02-20T00:38:15.636750","read_flag":1,"is_cc":0,"cc_original_to":null,"task_id":null,"reply_to":null}
```

### Log Rotation Strategy
**Daily rotation (recommended):**
- New file at midnight (00:00)
- Keeps logs organized by day
- Default retention: 30 days
- Cleanup script deletes logs older than 30 days

**Alternative:** Size-based rotation
- Max 10MB per file
- Rotate when file exceeds limit
- Keep last 10 files

### Historical vs. New Messages
**NEW MESSAGES ONLY** (from script start time)
- Avoids duplicate logging if restarted
- Clean separation of "logger started at X time"
- Historical messages already in DB

**Optional:** `--import-historical` flag for one-time import

### Daemon Management
**Run via:**
- macOS: launchd plist (auto-start on boot)
- Linux: systemd service
- Manual: tmux/screen session

**Features:**
- Auto-restart on crash
- Logging to stderr for debugging
- PID file at `~/.dead-drop/logger.pid`
- Control via: start/stop/status/restart commands

### Deliverables
1. `dead-drop-logger.py` - Main daemon script
2. `install.sh` - Setup script (creates dirs, installs service)
3. `README.md` - Usage instructions
4. Optional: `export-historical.py` - One-time historical export

### Timeline
- Script development: 30 min
- Testing: 15 min
- Documentation: 10 min
- Total: ~1 hour

---

## 4. FUTURE IDEAS (Not Yet Planned)

### Message Search/Query Tool
- CLI tool to search dead-drop messages by:
  - Date range
  - From/to agent
  - Content keywords
  - Task ID
- Output as JSON or formatted text

### Dead Drop Analytics Dashboard
- Message volume by agent
- Response time metrics
- Communication patterns
- Task completion rates
- Graphs and visualizations

### Message Encryption
- E2E encryption for sensitive messages
- Agent-specific keys
- Transparent encryption/decryption

### Multi-Room Support
- Separate dead-drop instances for different projects
- Room-based isolation
- Cross-room messaging with permissions

### Message Threading/Conversations
- Better reply tracking
- Conversation view in web dashboard
- Thread collapse/expand

### Agent Status Dashboard
- Real-time agent health monitoring
- Last seen timestamp
- Active/idle status
- Current task visibility

### Notification Integrations
- Slack notifications for important messages
- Email alerts for critical events
- SMS for urgent issues

### Message Retention Policies
- Auto-archive old messages
- Configurable retention periods
- Compression for archived messages

### API for External Tools
- REST API for dead-drop access
- Webhooks for message events
- Integration with other tools

---

**Status:** All ideas documented for future reference
**Next Steps:** User decides which upgrades to prioritize and implement
