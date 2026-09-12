# AI Quotas

An [Omarchy](https://omarchy.org/) bar plugin — install by cloning this repo
and linking it into the plugin directory:

```bash
git clone https://github.com/danielrod36/omarchy-quotas ~/Projects/omarchy-quotas
ln -s ~/Projects/omarchy-quotas ~/.config/omarchy/plugins/daniel.quotas
omarchy restart shell
```

Then add `{"id": "daniel.quotas"}` to a bar section in
`~/.config/omarchy/shell.json`. The panel is personal-tooling: coding-plan
credentials are read from [omp](https://github.com/can1357/oh-my-pi)'s
credential database, and everything else from
`~/.config/omarchy/quotas.json` (fillable from the panel's own setup page).

One bar icon and one panel for every AI provider configured on this machine:
coding-plan quota windows with resets, and prepaid credit ledgers for
usage-based API keys. Based on Omarchy's stock `omarchy.agents` widget — same
record contract, same panel — with collectors for eleven providers. Provider
chips and the hero use each provider's brand mark (`assets/<id>.svg`, with a
`-light` twin for light surfaces; CodexBar's icon set where available).

Coding-plan tabs (limits with reset countdowns). The Zhipu subscription
carries exactly two windows — the 5-hour token lane and the monthly native
MCP tool lane (web search / web reader / zread); no weekly or monthly token
window is invented:

| Tab | Provider | Source |
|---|---|---|
| Z.ai | Z.ai GLM Coding (global) | omp credential `zai` → `api.z.ai/api/monitor/usage/quota/limit` |
| Zhipu Coding | Zhipu GLM Coding (bigmodel.cn) | omp credential `zhipu-coding-plan` → same API on `open.bigmodel.cn` |
| Codex | ChatGPT Plus/Pro | stock `omarchy-agent-usage-codex` collector (app-server RPC) |
| Kimi | Kimi for Coding | omp credential `kimi-code` (OAuth) → `api.kimi.com/coding/v1/usages` |
| Ollama | Ollama Cloud | omp credential `ollama-cloud` (API key) → `ollama.com/api/usage` (plan buckets as usage fractions + 4-week activity cost) |
| Mistral | La Plateforme | console session → `admin.mistral.ai` credits + `console.mistral.ai` vibeUsage |
| Aliyun | Bailian Token Plan | omp credential `alibaba-token-plan` (console cookie) → bailian console gateway |

Prepaid credit tabs (money ledger + provider-available token windows — never
quota-style meters):

| Tab | Provider | Source |
|---|---|---|
| Zhipu Credits | Zhipu pay-as-you-go lane, same key as the coding plan | `open.bigmodel.cn/api/biz/account/query-customer-account-report` (availableBalance / recharge + grants / totalSpend, CNY) + the 7-day hourly token series |
| DeepSeek | pay-as-you-go | omp credential `deepseek` (or `DEEPSEEK_API_KEY`) → `api.deepseek.com/user/balance` |
| OpenRouter | prepaid credits | management key → `openrouter.ai/api/v1/credits` |
| MiMo | Xiaomi MiMo | console cookie → `platform.xiaomimimo.com/api/v1/balance` |

The Z.ai tab also carries its account's pay-as-you-go ledger when one exists
(`api.z.ai/api/biz/account/query-customer-account-report`, USD) — a $0.00
wallet never fakes a prepaid line onto a subscription tab. Z.ai and Zhipu
Coding tabs fill "tokens by day" / "tokens by model" from the provider's
hourly usage series; Zhipu Credits shows the same series for the account's
usage-based lane.

## Credentials

Coding-plan providers read omp's credential database
(`~/.omp/agent/agent.db`), which omp keeps fresh:

- **Kimi**: access tokens live ~15 min and Moonshot **rotates the refresh
  token on every refresh**. The collector refreshes expired tokens itself and
  writes the rotated pair back into omp's database — the same row omp
  maintains — so both omp and this widget keep working.
- **Aliyun**: the quota API is the Bailian console gateway, authenticated by
  the `Cookie:` header captured during omp's `alibaba-token-plan` login. When
  it expires the tab says so; re-capture via omp's provider login.
- **Z.ai / Zhipu Coding / Zhipu Credits / DeepSeek**: plain API keys read
  from omp; `ZAI_API_KEY` and `DEEPSEEK_API_KEY` work as fallbacks. Both
  Zhipu tabs share the coding-plan key — the credit lane is a different
  endpoint, not a different credential (a dedicated PAYG key can still go in
  `bigmodel.apiKey` below).

Providers omp does not store take keys from `~/.config/omarchy/quotas.json`
(or the env vars below). A provider with credentials configured keeps its tab
even when its endpoint rejects them, so the error card is always visible:

```json
{
  "openrouter": { "apiKey": "sk-or-v1-…" },
  "mimo": { "cookie": "api-platform_serviceToken=…; userId=…" },
  "mistral": { "cookie": "…full Cookie header from admin.mistral.ai…", "csrfToken": "…" },
  "bigmodel": { "apiKey": "…" }
}
```

- **OpenRouter** needs a *Management* key (https://openrouter.ai/settings/management-keys);
  provisioning keys get 403 on `/api/v1/credits`. Env: `OPENROUTER_API_KEY`.
- **MiMo** has no balance API key: log in at
  https://platform.xiaomimimo.com/#/console/balance, copy the request's
  `Cookie:` header (must contain `api-platform_serviceToken` and `userId`).
- **Mistral** has no API-key quota endpoint either: log in at
  https://admin.mistral.ai, copy the `Cookie:` header and the `csrftoken`
  cookie value.
- **Kimi monthly lane**: the coding API exposes the 5-hour window and the
  weekly pool. The monthly membership lane needs the kimi.com browser token —
  from kimi.com DevTools, copy the `kimi-auth` cookie value into `kimi.token`
  (or set `KIMI_AUTH_TOKEN`); without it the tab shows the two coding lanes.

DeepSeek and MiMo report only the *remaining* balance, so the funded amount of
the meter is the highest balance ever observed (cached in
~/.cache/omarchy/quotas/); OpenRouter, Mistral, and Zhipu report exact
ledgers.

## Data flow

Each provider is one JSON record in `~/.local/state/omarchy/quotas/usage/`,
written atomically by this plugin's `usage-update` script (same contract as
`omarchy-agent-usage-update`). `Main.qml` discovers and watches the records;
`Panel.qml` renders them. The plugin refreshes on its timer (default 15 min),
on panel open (limits only), and on `r`/Enter. Codex is delegated to the
stock collector.

## Overview page and provider visibility

Opening the panel lands on an **overview**: one row per provider — brand mark,
name, and the binding number (the fullest quota window's percentage, or the
remaining credit for balance tabs) with a meter and a caption (reset
countdown / spent-of-funded / error status). Providers with two live windows
(Kimi, Codex) split the row's width in half: Session and Weekly meters side
by side, each with its own percentage, and the next meaningful reset riding the
name line itself — the session reset while weekly quota remains, the weekly
reset once the pool is spent. Click a row (or `h`/`l`) to open the
provider's detail page; Esc (or tapping the selected chip) backs out to the
overview; the bar icon lights when any provider's binding number is hot.

Providers without usable credentials never appear: a missing key, a missing
cookie, or one the endpoint rejects (expired session, 401/403) makes the
collector emit `configured: false` and the panel hides the tab entirely —
Aliyun's stale console cookie hides it the same way.

A **+** button on the hero line (top right of the panel) opens the setup
page: one card per unconfigured provider with step-by-step instructions for
capturing its key or cookie, a field per credential, and a Save button that
writes `~/.config/omarchy/quotas.json` (mode 600, other sections preserved)
and jumps to the freshly configured tab. Providers whose credentials omp
owns (Aliyun) show the omp re-capture steps instead of a paste field.
`omarchy-shell daniel.quotas setup` opens the same page directly.

Binding numbers prefer token-quota windows. The legacy z.ai and Zhipu
subscriptions carry only a 5-hour token window plus the monthly native-MCP
tool lane — the tool allowance is flagged `secondary` and never outranks the
5-hour window in the overview, the hero alarm, or the headline.

## Interactions

Identical to the stock agents widget: bar icon left = panel, right = launch
`omarchy-agent --pick`, middle = next provider. In the panel: `h`/`l` switch
provider, `j`/`k` scroll, `r` or Enter refresh, Esc closes.

IPC: `omarchy-shell daniel.quotas <open|close|toggle|refresh|next>`.

Settings live under the widget entry in `~/.config/omarchy/shell.json` —
`omarchy bar set daniel.quotas refreshIntervalSec 300 --json`, and per-provider
enablement through the `providers` object (same as the stock widget).

## Upstream drift

`Panel.qml` / `Main.qml` / `Agent.qml` are a clone of the stock
`omarchy.agents` plugin with a three-line diff: the module/IPC id, the usage
directory + update command, and a `configured` flag that keeps credential-
bearing tabs visible while their endpoint is down. If Omarchy improves the
stock panel, re-clone and re-apply:

```bash
omarchy plugin clone omarchy.agents   # then diff the QML files and port changes
```
