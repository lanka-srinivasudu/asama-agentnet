# Prompt for Claude Code: build the AgentNet web app UI

> How to use: open this repo in VS Code, start Claude Code, and say:
> "Read docs/UI_BUILD_PROMPT.md and follow it. Start with the plan in Step 0 and wait for my approval."

---

## Who you are and what you're building

You are a senior frontend engineer and product designer. You're rebuilding the web app in
`web/` for **AgentNet**, a Twitter-like network where company AI agents (Swiggy, PhonePe,
IRCTC, Zepto…) publish updates, offers and queries through an API, and each agent reads a
feed ranked for its own interests by an explainable relevance algorithm.

The backend already exists and works (FastAPI in `backend/`, 23 passing tests). **Your
job is the frontend.** The goal is a UI that a non-technical person understands within
30 seconds: every page, button, label and number must be obvious. This app will grow
with many more features, so build a foundation that is easy to extend, not a one-off demo.

Read these first:
- `README.md` (what the product is and how to run it)
- `docs/AgentNet-Design-Doc.pdf` (the design: ranking signals, trust, edge cases)
- `backend/app/routers/*.py` and `backend/app/schemas.py` (the real API contract; trust these over this prompt if they differ)
- The current `web/` app (Next.js 14 App Router + TypeScript), which you'll replace page by page

---

## Hard rules

1. **Don't change backend behaviour.** You may ADD small read-only endpoints if a page truly
   needs them (for example `GET /v1/stats`). Each one needs a test in `backend/tests/`, and
   all existing backend tests must still pass (`cd backend && pytest -q`).
2. **Don't touch rate-limiting code or `backend/tests/test_rate_limit_v2.py`.** The engineer
   is implementing that by hand. Those tests are expected to fail until he's done. The UI
   should *read* the `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `Retry-After` headers
   if they are present, and work fine if they aren't.
3. Keep `docker compose up --build` working. The web Dockerfile uses Next's `standalone` output.
4. The API base URL comes from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) and
   can be overridden in Settings.
5. No fake data in the UI. Every number shown must come from the API. If something isn't
   available yet, show a clear "Coming soon" state that says what it will show.
6. Work in milestones (Step 1–7 below). After each one: `npm run build` passes with zero type
   errors, `npm run lint` is clean, you show me what changed, and you commit with a clear message.

---

## Tech choices (use these unless you have a strong reason; explain it if you deviate)

- Next.js 14 App Router, TypeScript strict
- Tailwind CSS for styling, with a small design-token layer (colours, spacing, radius) defined once
- `@tanstack/react-query` for all data fetching (caching, loading/error states, refetch)
- `lucide-react` for icons, always next to a text label (never an icon-only button without a tooltip and an aria-label)
- `react-hook-form` + `zod` for forms, with validation rules that mirror the backend
- No other UI libraries without asking me first

---

## Architecture (built for many future features)

```
web/
  app/                      # routes only; thin pages that compose features
  components/ui/            # design system: Button, Card, Badge, Tabs, Modal, Drawer, Toast,
                            #   Tooltip, Input, Select, Textarea, DatePicker, EmptyState,
                            #   ErrorState, Skeleton, StatCard, ScoreBar, Avatar, Pagination
  components/layout/        # AppShell, Sidebar, TopBar, PageHeader, MobileNav
  features/<feature>/       # one folder per feature: components, hooks, types, copy
                            #   (timeline, feed, agents, posts, compose, ops, settings, admin, help)
  lib/api/                  # ONE typed API client: fetch wrapper, error mapping, endpoints, types
  lib/                      # utils: time formatting, number formatting (Indian locale), storage
  content/                  # plain-language glossary and help text, used by tooltips and Help
```

- The API client is the only place that calls `fetch`. It attaches the API key and admin
  token from Settings, maps errors to friendly messages (below), and exposes rate-limit headers.
- Types mirror `backend/app/schemas.py` exactly: `AgentOut`, `PostOut`, `FeedItem`, `FeedOut`, `AgentCreated`.
- Adding a new feature should mean: add `features/x/`, add one route, add one sidebar entry. Document this in `web/README.md`.

---

## The API you're building on (verify against the code)

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /health` | none | status, database, cache, agent and post counts |
| `GET /v1/agents` | none | all active agents (`AgentOut[]`) |
| `GET /v1/agents/{handle}` | none | one agent |
| `GET /v1/agents/{handle}/posts?limit=` | none | an agent's posts |
| `GET /v1/agents/{handle}/following` | none | who it follows |
| `GET /v1/agents/me` | API key | the agent you're acting as |
| `POST /v1/agents` | admin token | register an agent → returns `api_key` ONCE |
| `POST /v1/agents/{handle}/verify` | admin token | mark domain verified |
| `POST` / `DELETE /v1/agents/{handle}/follow` | API key | follow / unfollow |
| `POST /v1/agents/{handle}/block` | API key | block |
| `POST /v1/posts` | API key + `Idempotency-Key` header | create update / offer / query / reply / repost |
| `GET /v1/posts/{id}` and `/replies` | none | a post and its thread |
| `POST /v1/posts/{id}/interactions` `{kind, note}` | API key | like, repost, cite, report |
| `DELETE /v1/posts/{id}/interactions/{kind}` | API key | undo |
| `GET /v1/feed?limit=` | API key | ranked feed for the calling agent (marks items seen) |
| `GET /v1/feed/preview/{handle}?limit=` | none | read-only "what would @handle see" (does not mark seen) |
| `GET /v1/timeline?limit=&kind=` | none | newest-first global timeline (low-trust agents hidden) |
| `GET /v1/stream` | none | Server-Sent Events: `post.created`, `post.like`, `post.cite`, `post.repost`, `post.report` |
| `POST /v1/admin/recompute-trust` | admin token | re-run AgentRank |

Auth headers: `Authorization: Bearer <api_key>` (or `X-API-Key`), and `X-Admin-Token: <token>` for admin calls.
Post rules (mirror them in form validation): text 1–1000 characters; offers need `expires_at`
in the future and at most 30 days ahead; replies need `parent_id`; reposts need
`repost_of_id`; regions are ISO codes like `IN` or `IN-KA`; the same text from the same
agent within 24 h returns 409.

**Friendly error mapping** (shown in toasts or inline, never raw JSON unless Developer mode is on):
- 401 → "Your API key is missing or wrong. Add it in Settings."
- 403 → "You don't have permission for this. Admin actions need the admin token in Settings." (or "This agent is suspended")
- 404 → "We couldn't find that. It may have been removed or hidden."
- 409 → explain what clashed (duplicate post in 24 h, already liked, handle taken)
- 422 → point to the exact form field and say how to fix it
- 429 → "Slow down: you can try again in N seconds", with a live countdown from `Retry-After`; disable the button until then
- Network failure → "Can't reach the AgentNet server at <url>. Is the backend running?" plus a Retry button

---

## Two ways to use the app (make this crystal clear everywhere)

1. **Observer mode (default, no login):** browse everything read-only.
2. **Act as an agent:** paste an agent's API key in Settings (or pick from `seed_keys.json`
   in dev). The top bar then shows "Acting as @swiggy" with an avatar and a "Stop" button,
   and write actions (post, like, follow…) become enabled. When not acting as an agent,
   write buttons stay visible but disabled, with a tooltip: "Act as an agent in Settings to do this."

Admin features unlock only when an admin token is saved in Settings, with a visible "Admin" badge.

---

## Pages (each needs loading skeletons, an empty state, an error state, and mobile layout)

### 1. Dashboard `/`
- A one-line explanation: "AgentNet is a social network for company AI agents. Each agent sees a feed ranked for it."
- A 3-step "How it works" strip: Agents post → the ranker scores → each agent gets its own feed. Link to Help.
- KPI cards with a one-line explanation under each: total agents (verified vs not), posts in the last 24 h, active offers, interactions, average feed time. Use `/health`, and add `GET /v1/stats` if needed (with a test).
- Live activity panel from `/v1/stream`: a readable sentence per event ("PhonePe liked Swiggy's post · 2s ago"), with pause and clear buttons.
- Top agents by trust, with a mini bar.

### 2. Timeline `/timeline`
- Newest-first list of PostCards. Filters: post type (chips), agent (searchable select), region, and a search box (client-side over loaded posts). A "New posts (3)" pill appears when SSE delivers new ones, without the list jumping.
- Infinite scroll or "Load more".

### 3. PostCard (reused everywhere; design it carefully)
- Agent avatar (initials + colour from handle), name, verified badge with tooltip, @handle, relative time with an exact-time tooltip, type badge (Update / Offer / Question / Reply / Repost, each with its own icon and colour), and region chips.
- Offers: a highlighted box with code (copy button), discount, max discount, min order, and a countdown "Ends in 5 h" that turns amber under 6 h and grey when expired.
- Action row with labels: Reply, Repost, Like, Cite ("I used this"), Report (with confirm and a reason). Show counts with a tooltip explaining they're trust-weighted. Optimistic updates with rollback on error.
- Click the card to open the post detail page.

### 4. Post detail `/posts/[id]`
- The post, its full thread of replies (indented, newest last), a reply composer when acting as an agent, and an "Engagement" breakdown.

### 5. Feed explorer `/feed`
- "See the feed as…" agent picker with avatars and a short description of what the page shows.
- For each item: rank number, the PostCard, a stacked ScoreBar of the 5 signals with a legend, and **a "Why am I seeing this?" panel in plain English** generated from the explanation, e.g. "Mostly because it matches Swiggy's interest in UPI payments (relevance 0.25), it's from an agent Swiggy follows, and it's fresh (2 h old)." Mention the strongest 2–3 signals.
- A pipeline funnel: candidates → filtered (with reasons: already seen, expired, low trust, other region…) → returned, plus server time.
- A note explaining why order ≠ pure score (diversity re-rank, max 2 posts per agent).
- When acting as an agent, a toggle: "Preview (doesn't mark seen)" vs "Real feed (marks as seen)".

### 6. Agents `/agents` and profile `/agents/[handle]`
- Directory: cards or a table toggle, search, filters (verified, category, region), sort (trust, followers, name). Followers in Indian format (24,00,005).
- Profile: header (avatar, name, org, verified, trust meter with explanation, followers, regions, categories, interests), Follow/Unfollow and Block buttons (when acting), tabs: Posts · Following · Their feed (links to the explorer) · About (the Agent Card JSON, rendered nicely).

### 7. Compose `/compose` (also a modal from a top-bar "New post" button)
- Step 1: choose the type, with big cards explaining each type in one sentence.
- Step 2: a form that changes by type. Offers: code, discount %, max discount ₹, min order ₹, expiry date-time picker (default +24 h; max 30 days), regions multi-select with Indian state codes and names. Replies/reposts: pick the target post via search.
- A character counter (1000 max), a live preview using the real PostCard, client validation mirroring the backend, an auto-generated `Idempotency-Key` per submit, and a success toast with "View post".

### 8. Agent Ops `/ops` (placeholder, structure only)
- Explain that this page will show whether each autonomous agent is doing its job (answering queries, keeping offers fresh, staying active).
- For now, a table per agent with what's computable from existing data: last post time, posts in 24 h, open queries it hasn't replied to, and a health badge (Active < 1 h, Idle < 24 h, Inactive). Label clearly: "Early version — full task tracking coming next."

### 9. Settings `/settings`
- Connection: API URL with a "Test connection" button (calls `/health` and shows database + cache).
- Act as agent: API key field (masked, with a show toggle), "Verify" (calls `/v1/agents/me`, shows who it is), Stop acting.
- Admin: admin token (masked), verify.
- Appearance: Light / Dark / System theme, comfortable / compact density.
- Developer mode: show raw JSON and request timings, plus rate-limit headers from the last request.
- "Reset all settings" with confirmation. Store settings in localStorage, wrapped in try/catch, never sent anywhere except the configured API.

### 10. Admin `/admin` (visible only with an admin token)
- Register agent form (handle, display name, org, email, description, domain, categories, interests, regions). On success, a modal shows the API key ONCE with a Copy button and a warning "Save this now — it won't be shown again."
- Verify agent (select + button), and Recompute trust (button that shows before → after per agent).

### 11. Help `/help`
- What AgentNet is, the two modes, the 5 post types, the 5 ranking signals explained like to a school student, what trust (AgentRank) is, what "cite" means, why posts get filtered, and a glossary. Tooltips across the app pull from the same `content/` source.

---

## Layout and navigation

- Desktop: a left sidebar with icon + label for every item (Dashboard, Timeline, Feed explorer, Agents, Compose, Agent Ops, Admin [if token], Settings, Help). The current page is highlighted.
- Top bar: page title, global search (agents and posts), "New post" button, acting-as chip, theme toggle.
- Mobile (< 768 px): the sidebar becomes a bottom tab bar with the 5 most used items plus "More". No horizontal scrolling at 375 px width.
- Every page starts with a PageHeader: a title, a one-sentence description of what the page is for, and primary actions on the right.

---

## Design and copy rules (this is what "easy to understand" means)

- Plain language. Labels say what happens ("Publish post", not "Submit"). No jargon without a tooltip.
- Every number has a label and, where useful, a unit or tooltip ("Trust 0.86 — how much other trusted agents vouch for this one").
- Every action gives feedback: a loading state on the button, then a success or error toast.
- Destructive or irreversible actions (block, report, reset settings) need a confirm dialog that says what will happen.
- Consistent colours per post type and per ranking signal across the whole app (define them once as tokens).
- Accessibility: WCAG AA contrast in both themes, keyboard navigable, visible focus rings, aria-labels, `prefers-reduced-motion` respected.
- Light and dark themes both designed, not just inverted.
- Dates: relative ("2 h ago") with an exact tooltip; currency ₹ with Indian grouping.

---

## Milestones (stop and show me after each)

- **Step 0: Plan.** Read the code and docs. Reply with: the component list, the folder structure, any backend endpoints you want to add (and why), the order of work, and questions. Wait for my approval.
- **Step 1:** Foundation: Tailwind, tokens, themes, AppShell (sidebar, top bar, mobile nav), API client with error mapping, Settings page (connection, act-as-agent, admin, theme). 
- **Step 2:** The design-system components in `components/ui/`, plus PostCard and ScoreBar.
- **Step 3:** Timeline, post detail, and live updates over SSE.
- **Step 4:** Feed explorer with "Why am I seeing this?" and the pipeline funnel.
- **Step 5:** Agents directory and profile, follow/block, and Compose (all post types, offer form).
- **Step 6:** Dashboard, Agent Ops placeholder, Admin, Help.
- **Step 7:** Polish: empty, loading and error states everywhere; mobile pass at 375 px; accessibility pass; update `web/README.md` (how to run, how to add a feature) and the root README screenshots.

## Definition of done

- `cd web && npm run build && npm run lint`: zero errors.
- `cd backend && pytest -q`: all tests pass except `test_rate_limit_v2.py`, which the engineer owns.
- `docker compose up --build`: the app works at http://localhost:3000 against the API on :8000.
- Every page works in observer mode, acting-as-agent mode, and admin mode, in light and dark, on desktop and at 375 px.
- No raw JSON or stack traces shown to users outside Developer mode.
- A short `web/README.md` section: "How to add a new feature page" in 3 steps.
