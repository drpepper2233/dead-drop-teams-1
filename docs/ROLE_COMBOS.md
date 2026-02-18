# Role Combinations by Team Size

Roles are hats, not people. Smaller teams wear more hats. These are the recommended combos.

8 active roles: lead, builder, fixer, tester, reviewer, productionalizer, pen, shipper.

## 2-Agent Team

| Agent | Roles |
|-------|-------|
| Agent A | lead + reviewer + pen + shipper |
| Agent B | builder + fixer + tester (tester only for OTHER code) |

## 3-Agent Team

| Agent | Roles |
|-------|-------|
| Agent A | lead + reviewer + shipper |
| Agent B | builder + fixer + productionalizer |
| Agent C | tester + pen (independent quality gate) |

## 4-Agent Team (Sweet Spot)

| Agent | Roles |
|-------|-------|
| Agent A | lead + reviewer + shipper |
| Agent B | builder + productionalizer |
| Agent C | tester + pen |
| Agent D | fixer + builder (second builder) |

## 5+ Agent Team

One hat per agent where possible. At 8 agents, each role gets exactly one agent.

## Key Principles

- **Tester + Reviewer stay together on small teams** — unified quality gate. Split them at 5+ agents.
- **Fixer = original builder by default** — they know the code best. Lead can override if fix is rejected twice.
- **Lead always has reviewer + shipper** — quality gate + git gate. Non-negotiable.
- **Builder and reviewer NEVER on same agent** — you don't review your own code.
- **Builder and tester NEVER on same agent** — independent verification.
- **Fixer and tester NEVER on same agent** — fixer shouldn't adjust tests to match their fix.
- **Pen and builder NEVER on same agent** — fresh eyes find more vulnerabilities.
- **Shipper and tester NEVER on same agent** — separation of concerns.
