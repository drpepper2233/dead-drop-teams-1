# Role Combinations by Team Size

Roles are hats, not people. Smaller teams wear more hats. These are the recommended combos.

## 2 Sessions

| Agent | Roles |
|-------|-------|
| juno (lead) | lead + reviewer + pusher |
| spartan | builder + tester + fixer + pen + productionalizer + demoer + deliverer + maintainer |

## 3 Sessions

| Agent | Roles |
|-------|-------|
| juno (lead) | lead + reviewer + pusher |
| spartan | builder + fixer + productionalizer + maintainer + deliverer |
| cortana | tester + pen + demoer + reviewer |

## 4 Sessions

| Agent | Roles |
|-------|-------|
| juno (lead) | lead + reviewer + pusher |
| spartan | builder + fixer + productionalizer |
| cortana | tester + pen + demoer |
| roland | deliverer + maintainer + reviewer |

## 5+ Sessions

Start assigning single roles. At 10 sessions, each agent gets exactly one role.

## Role Assignment Rules

- **Lead always has reviewer + pusher** — quality gate + git gate. Non-negotiable.
- **Builder and reviewer should NEVER be the same agent** — you don't review your own code.
- **Pusher should ideally be separate from builders** — separation of concerns.
- **Tester and builder should be separate when possible** — independent verification.
