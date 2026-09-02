# project-azure-training

A Claude Code "learning guide" prompt for teaching yourself **Azure + OpenTofu +
Ansible** hands-on, in a cost-controlled free-tier subscription.

## What it does

The prompt casts Claude as a technical learning assistant and infrastructure
architect that **never touches the cloud** — you run every `az` / `tofu` /
`ansible` command yourself. Per step, Claude explains what and why (senior
level, just-in-time, no upfront theory dumps), writes or reviews the file, tells
you exactly what to run and what to expect, then waits.

Its defining trait is **cost discipline**: every Azure resource gets an explicit
`[ALWAYS FREE] / [12-MONTH FREE] / [COSTS MONEY]` label, defaults to the
smallest SKUs (`Standard_B1s`, LRS, no zone redundancy), and forces explicit
confirmation before anything billable enters a plan. Secrets-first hygiene
(real tfvars and credentials never in git), explicit provider pinning, and a
verify-between-steps workflow round it out. Durable state and gotchas are kept
in an external memory service and an OtterWiki, not in the prompt.

## Build your own

1. Create a free Azure subscription and a service principal for OpenTofu.
2. Set up a control host with `az`, `opentofu`, and `ansible` (the prompt
   assumes a container toolchain, but a plain VM works too).
3. Use `Prompt.md` alongside your own general Profile/system prompt. Replace the
   placeholders — `<control-server>`, `<ssh-user>`, `<ssh-key>`,
   `<memory-service-host>`, `<wiki-host>` — with your own, or delete the
   memory/wiki sections if you don't run those.
4. Point it at your subscription and learn one concept per iteration.

`Prompt.md` is sanitized: control-server host, SSH user/key, and internal
service URLs are replaced by placeholders. No credentials, subscription IDs, or
service-principal identifiers are included.
