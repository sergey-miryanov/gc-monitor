# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those
roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the
corresponding label string from this table.

## How a label is applied here

This repo's tracker is local markdown, not GitHub Issues (see
`issue-tracker.md`), so there is no CLI to apply a label with. A label is a
`Status:` line near the top of the issue file:

```markdown
# 03: Accept loop dies on the first transient error

Status: ready-for-agent
```

One status per file. Changing a label means editing that line, and the file's
history in the working copy is the only record of the change; `.scratch/` is
gitignored.

Edit the right-hand column above to match whatever vocabulary you actually use.
