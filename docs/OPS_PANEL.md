# Operator panel

Plan for rebuilding the staff panel (the pages operators use to run the product, not the sellers' panel). Written
2026-11-10 with the user: the current panel is too complicated; the target is the same look with a right-hand sidebar
where **every section is its own page with its own logic**, and a separate section for proxy management.

Status: plan only, not started. The code changes behind each section are in `docs/SECOND_REFACTOR.md`.

## What exists today

Three panels, all staff only (`apps.core.permissions.staff_required`):

| Panel | URL | Code | What is on it |
|---|---|---|---|
| Ops | `/ops/` | `apps/billing/views.py`, `apps/billing/urls.py`, `templates/ops/*.html` | Dashboard, costs, funnel, LLM providers and models, wallets and charging, Telegram communities, X source, billing settings |
| Manage | `/manage/` | `apps/billing/manage/`, `apps/billing/manage_urls.py`, `templates/ops/manage_dashboard.html` + `templates/ops/manage/tab_*.html` | One page with 12 tabs (below), plus Instagram account pages, the account cockpit and the live log API |
| Django admin | `/admin/` | `admin.py` files | Raw tables |

The 12 tabs of `/manage/`: lead matrix per tenant, crawl audit, billing, Instagram scrapers, X scrapers, verified
tweets, Instagram leads, outreach, senders, proxies, Instagram hub, Instagram live log.

Why it is hard to use and to change:

1. **One page loads everything.** `manage_dashboard` builds the data of all 12 tabs on every visit (accounts, leads,
   DM counts, the tenant matrix, finance, X runs, proxies), even when one tab is open. Slow, and every change to one
   tab risks the others.
2. **The same thing in several places.**
   - Instagram accounts: the scrapers tab, the senders tab, the Instagram hub tab, `/manage/instagram/accounts/`, the
     cockpit, and the old endpoints in `manage/ig_legacy.py` that the dashboard JavaScript still calls.
   - Money: the billing tab and `/ops/wallets/`.
   - X: two tabs and `/ops/x/`.
3. **Two navigation systems.** The sidebar in `templates/ops/base.html` mixes `/ops/` pages with links to `/manage/`
   tabs (`?tab=outreach`, `?tab=crawler-audit`).
4. **In the wrong app.** The panel is about 3,200 of the 4,400 lines of `apps/billing` (audit finding F-3).
5. **Logic in views.** Some views query and compute directly instead of calling the module that owns the data, so the
   same rule (for example "which accounts are active") is written more than once.

## Target

Same technology and look as today: Django templates, Persian, right-to-left, the existing CSS. No new frontend.

### Sections (right-hand sidebar, one page each)

| # | Section | What it shows and does | Owner of the logic it calls |
|---|---|---|---|
| 1 | Overview | Is everything healthy: xray up, live configs, active accounts per platform, leads today per platform, LLM credit, last errors. Links to the section that needs attention | Each section's own summary function |
| 2 | Proxies | Configs (status, ping, local port, which account uses it), config channels (add, pause, delete, sync), test one / test all, xray status and last reload, the pool | `apps/discovery/proxies/` |
| 3 | Instagram accounts | One list for scrapers and senders (role filter, status filter); add account; login / challenge code; each account's page (today's cockpit) | `apps/discovery/ig_accounts.py`, `ig_login.py`, `ig_state.py` |
| 4 | X accounts | Accounts, sessions, status, collector status | `apps/discovery` X modules, `workers/x_collector` status file |
| 5 | Sources | Telegram groups (communities), X queries, Instagram pages and hashtags | `telegram_crawler` tables, X query models, Instagram sources |
| 6 | Leads and opportunities | All platforms, per seller, with filters; exports | `apps/discovery` opportunity queries |
| 7 | Outreach | DM queue, sent, replies, follow-ups, per sender | `apps/discovery/outreach_engine.py`, `outreach/` |
| 8 | Sellers and money | Sellers, wallets, charges, billing settings | `apps/billing/services.py` |
| 9 | LLM | Providers, models, costs, prompts | `apps/billing` provider models, `apps/core.prompts` |
| 10 | Logs | Instagram live log, crawl audit, funnel | existing live log and audit code |

### Rules

- **One page per section, one view per page.** Each page has its own URL and loads only its own data. No page with
  tabs that each need different data; use links between sections instead.
- **One place per thing.** An Instagram account has one list and one page. Other sections link to it; they never
  show a second copy with its own buttons.
- **Thin views.** A view reads the request, calls functions of the module that owns the data, and renders. No
  queries with business rules, no counting rules, no calls to external services inside views or templates. If a
  function is missing, add it to the owning module (for proxies: `apps/discovery/proxies/`).
- **Actions are POST forms** with CSRF and a redirect back to the page, plus a short message ("3 configs removed").
  Slow actions (test all configs, sync all channels) run as a Celery task and the page shows the task's result.
- **Anti-ban.** Buttons that make an account act on Instagram or X (send a DM, scroll a feed, organic DM in the
  cockpit) keep the limits and delays they have today; moving them changes nothing in how they behave.
- **Access**: every view uses `staff_required`; actions are logged with the operator's name (`actor_name`).
- **Django admin** stays for fixing raw rows only, not for daily work.

### Code layout

A new Django app `apps/ops/` (confirm with the user before step 1), one package per section:

```
apps/ops/
    urls.py                 includes every section under /ops/
    overview/   views.py urls.py
    proxies/    views.py urls.py
    instagram/  views.py urls.py   (list, account page, login, cockpit APIs)
    x/          views.py urls.py
    sources/    views.py urls.py
    leads/      views.py urls.py
    outreach/   views.py urls.py
    money/      views.py urls.py
    llm/        views.py urls.py
    logs/       views.py urls.py
templates/ops/
    base.html               the sidebar, built from one list of sections
    <section>/*.html
static/js/ops/<section>.js, static/css/ops/...   (scripts and styles stay out of templates)
```

Each package stays small (under 400 lines per file, as `AGENTS.md` asks). Tests per section live next to it
(`apps/ops/<section>/tests.py`): the page loads for staff, is refused for a seller, makes a bounded number of queries
(`assertNumQueries`), and each action does what it says.

### Not chosen

- **A themed Django admin** (for example django-unfold): quick for list and edit pages, but the panel is mostly
  custom actions (test a config, log in an account with a code, the live log), and it is a new dependency.
- **A separate frontend** (React or similar over an API): much more work and a second codebase for a small team.
- **htmx** (a small script for refreshing part of a page) may be added later for the live parts (log, task results);
  it is a new dependency, so ask first.

## Steps (one PR each, in this order)

1. **Move, no changes**: `apps/billing/manage/`, `manage_urls.py`, the panel views of `apps/billing/views.py` and
   `ig_accounts_views.py` move to `apps/ops/`; URLs stay the same. `scripts/check_baseline.py --update --moved-from`
   for each moved file. Closes F-3.
2. **One sidebar**: `templates/ops/base.html` builds the sidebar from one list of sections; the links still point to
   today's pages.
3. **Proxies page** (first new section, together with `docs/SECOND_REFACTOR.md` part 1): its own URL and view; the
   proxies tab is deleted.
4. **Overview page**: the health summary; it replaces the lead matrix tab as the landing page.
5. **Instagram accounts, one place**: one list (scrapers and senders) and one account page; the scrapers, senders and
   hub tabs and the old `ig_legacy` endpoints are deleted after their JavaScript is moved.
6. **X accounts**, then **Sources**, **Leads and opportunities**, **Outreach**, **Sellers and money**, **LLM**,
   **Logs**: one section per PR, each deleting the tab or page it replaces.
7. **Delete `manage_dashboard`** and `templates/ops/manage_dashboard.html` when the last tab is gone; `/manage/`
   redirects to the overview for a while, then the redirect is removed.

After step 7: one panel, one sidebar, ten pages, and each page's code is in one folder.
