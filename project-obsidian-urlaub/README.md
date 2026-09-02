# project-obsidian-urlaub

A Claude Desktop prompt that turns Claude into a **vacation-planning assistant
writing directly into an Obsidian vault** via the Filesystem MCP connector.

## What it does

Point Claude at a folder inside your Obsidian vault and it plans trips *in
place*: one emoji-prefixed folder per destination, a fixed set of per-trip files
(flights, accommodation, rental car, day plan, tours, packing list, budget), a
year overview, and a booking-table convention. It writes files straight into the
vault — no copy-paste — using **Tasks-plugin** syntax so due dates and open items
surface in queries, keeps cost tables separate from booking details, and uses
**web search** for current prices, availability, and entry/toll rules instead of
stale training data.

The value is the *structure and conventions*, not any particular trip. It's
mobile-friendly (checkboxes you tick off on your phone) and keeps sensitive
booking data at its natural place in the vault, never in the prompt.

## Build your own

1. In Claude Desktop, connect the **Filesystem MCP** to your own vault path.
2. Install the Obsidian **Tasks** plugin (and optionally Iconize / Advanced
   Tables for the emoji folders and table alignment).
3. Use `Prompt.md` as the project prompt. Replace `<obsidian-vault>` with your
   vault path and `<Ziel>` with real destinations as you plan them.
4. Ask "erstell mir eine Packliste für X" and let it write into the vault.

`Prompt.md` is sanitized: real destinations and travel dates, the vault path
(with username), and personal-name references are removed or replaced by
placeholders. No bookings, dates, or personal data remain.
