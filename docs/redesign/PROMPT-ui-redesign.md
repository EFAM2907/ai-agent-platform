# UI redesign — AI Support frontend (`frontend/`)

You are working in the `frontend/` folder of this repo (React 18 + Vite + TypeScript, plain CSS in `src/styles.css` and `src/views.css`). Goal: restyle the whole UI so it looks like a production ops console (Linear / Vercel dashboard / Langfuse), and add a few missing chat UI pieces. A visual reference was prototyped in Lovable; the design tokens below come from that prototype.

UI copy stays in **Spanish (es-CO)**. Code, identifiers and comments you add are in **English**.

## Hard constraints (read before touching anything)

1. **Do NOT change behavior or data flow.** `src/api.ts` (auth, refresh-token dedup, SSE parser over fetch + ReadableStream) and `src/useChat.ts` must keep working exactly as today. Do not edit `api.ts`. In `useChat.ts` and `types.ts`, only additive changes if strictly necessary — and say so in your summary. No backend changes.
2. **Do NOT migrate frameworks.** No Tailwind, no shadcn, no router, no TanStack. Keep plain CSS with CSS variables. Reason: this is a visual change; a framework migration would make the diff unreviewable and risk breaking working logic.
3. Allowed new dependencies, only these: `lucide-react` (icons), `@fontsource-variable/geist`, `@fontsource-variable/geist-mono` (fonts, imported in `main.tsx`). Nothing else.
4. Keep existing CSS class names where components already use them, so you change values and structure rather than rewiring every component. Remove the purple theme entirely.
5. Accessibility must not regress: keep the existing `aria-*` attributes, visible focus rings (`:focus-visible` with the accent ring), and keyboard behavior (Enter sends, Shift+Enter new line).
6. Work in the phases below. After each phase run `npm run build` (must pass with zero TS errors), then **stop and give me a short summary + the list of files changed** before starting the next phase.

## Design tokens (Phase 1 — replace the `:root` block in `styles.css`)

Dark-first, neutral near-black, ONE accent. Surfaces step up in lightness instead of using shadows.

```css
:root {
  color-scheme: dark;
  --radius: 6px;

  /* surfaces: #0B0C0E → #121317 → #17181D → #1D1F25 */
  --bg:        oklch(0.154 0.005 264.4);
  --surface-1: oklch(0.187 0.008 274.5);
  --surface-2: oklch(0.21  0.01  276.6);
  --surface-3: oklch(0.24  0.012 270.9);

  --text:       oklch(0.949 0.003 264.5);
  --text-muted: oklch(0.694 0.013 276);
  --text-subtle:oklch(0.539 0.016 272.5);

  --border:        oklch(0.27 0.013 272.9);
  --border-strong: oklch(0.31 0.015 274.4);

  /* the single accent: calm teal — primary buttons, active nav indicator, focus ring, streaming cursor ONLY */
  --accent:    oklch(0.778 0.146 169.3);
  --accent-fg: oklch(0.2 0.03 170);
  --ring:      oklch(0.778 0.146 169.3 / 70%);

  --danger:  oklch(0.62 0.2 24.5);
  --warning: oklch(0.782 0.158 72.3);   /* pending approval */
  --success: oklch(0.736 0.164 155.4);

  --font-sans: "Geist Variable", ui-sans-serif, system-ui, "Segoe UI", sans-serif;
  --font-mono: "Geist Mono Variable", "JetBrains Mono", ui-monospace, monospace;
}
```

Rules:
- Body: 14px / 22px line height, `-webkit-font-smoothing: antialiased`. Type scale only 12 / 13 / 14 / 16 / 20 / 24 px.
- A `.mono` utility class (font-mono + `font-variant-numeric: tabular-nums`) for IDs, tracking numbers, trace ids, tokens, latency, costs, JSON. Use it everywhere those appear.
- Radii 4–8px. No box-shadows except on popovers/menus. Separate areas with 1px borders and surface steps.
- Selection color: accent at ~28% alpha. Thin scrollbars using `--border-strong`.
- Semantic tints for banners/cards: color at ~10% alpha background + ~30% alpha border (e.g. error banner = danger tint).
- Keyframes: `blink` (streaming cursor), `shimmer` (live tool status text), `fade-up` (4px, 220ms, for new messages / cards).
- Map old variable names (`--panel`, `--panel-2`, `--text-dim`, `--text-faint`, `--accent-dim`, `--assistant-bubble`, `--danger-bg`, `--danger-text`, etc.) onto the new tokens or replace their usages, so nothing renders with an undefined variable. Grep for every `var(--` afterwards to verify.
- Replace the inline SVG `Icon` helper and `RobotIcon` usages with `lucide-react` icons (16px, `strokeWidth={1.75}`).

## Phase 2 — Shell: login + sidebar

**Login** (`App.tsx` → `LoginScreen`): centered 360px column; above the card a small logo mark (28px rounded square, accent-tinted, with a letter "A") + "AI Support". Card on `--surface-1` with border: title "Inicia sesión" (16px, medium), subtitle "Usa la cuenta de tu organización." (13px muted), labeled fields (12px muted labels, 36px inputs), full-width accent button "Continuar →" with spinner + "Ingresando…" while submitting, inline error as a danger-tint row with an alert icon. Background: very faint 48px grid made of `--border` lines, masked with a radial gradient so it fades out from the center.

**Sidebar** (`Sidebar.tsx`), 260px, background `--bg`, right border:
- Header: logo mark + "AI Support" (13px semibold).
- Organization block (looks like a tenant switcher, no real switching): bordered row on `--surface-1` with org initials in a small square, org name (13px medium), plan as a small accent badge, chevrons icon. Hover → `--surface-2`.
- "Nuevo chat" primary button with a `kbd` hint (`⌘K` on Mac, `Ctrl K` otherwise) and wire the actual shortcut (Ctrl/⌘+K → new chat).
- Nav (Chat / Base de conocimiento / Integraciones / Trazabilidad — admin-only items stay admin-only exactly as now): 32px rows, 13px muted text, icon; active item = `--surface-2` background + 2px accent bar on the left edge.
- History: group sessions under labels "Hoy", "Ayer", "Últimos 7 días", "Anteriores" (12px subtle labels) based on `created_at`. Keep rename / delete with inline confirm exactly as the current logic does; restyle the "…" menu to appear on hover, the rename input with an accent ring, and the delete confirm as a compact danger-tint row "¿Eliminar? [Eliminar] [Cancelar]".
- Members section stays collapsible, collapsed by default, count in `.mono`.
- Footer: user row with initials avatar (accent-tinted square), name + email (truncate), role as a small badge; menu with "Cambiar contraseña" and "Cerrar sesión" (existing handlers).
- Mobile (< 768px): sidebar becomes an off-canvas drawer with a hamburger button in a top bar.

## Phase 3 — Chat (`MainChat.tsx`, `MessageDetails.tsx`, `markdown.tsx`) — highest priority

- Top bar: "Chat" + org name in muted text, 48px tall, bottom border.
- Message column: max-width 760px, centered.
- **Empty state:** "Hola, {firstName}" (20px medium) + "¿En qué te ayudo hoy con la operación de {org}?" (13px muted), then the role-based `suggestions` (already computed in `App.tsx`) as 3 bordered cards in a grid (icon + text, hover → `--surface-2` + `--border-strong`); click sends that suggestion.
- **User messages:** right-aligned, max 80% width, `--surface-2` background, 1px border, radius 8px, `white-space: pre-wrap`.
- **Assistant messages:** no bubble. Render markdown as a document: 14px / 1.6, spaced paragraphs, `code` inline with surface-2 + border, `pre` blocks on surface-1 with border, tables full-width with 13px tabular numbers, muted header row on surface-1, row separators. Put these styles in a `.prose-doc` class.
- **Streaming cursor:** while a message streams, append a blinking accent block after the last text node (CSS `::after` on the last child of `.prose-doc.is-streaming`).
- **Live tool status:** while `activeTool` is set, show above the streaming text a row with a spinning `Loader2` (accent) + `toolLabel(activeTool)…` using the shimmer text effect.
- **"Pasos" list:** after the message is done, if `message.trace?.tools` has items, show a compact collapsible row above the answer: "Pasos (n)" → expands to the list of tools with a check (success) or x (danger) icon and the human label from `toolLabel`. Collapsed by default. (Derive it from `trace.tools` — no hook change needed.)
- **Sources:** under the answer, label "Fuentes" (12px subtle) + each source as a small chip (surface-2, border, 12px, book icon). Only when not streaming.
- **Human-approval action card (`ActionCard`) — make it the most visually distinct element on screen:** surface-1, border, 3px left border in `--warning`, `fade-up` on appear. Header: warning-colored shield/alert icon + "Acción pendiente de aprobación" (13px medium) + on the right a countdown "Vence en m:ss" computed from `expires_at` (update every second, `.mono`; when it reaches 0 show "Vencida" — UI only, the backend stays the source of truth). Body: the human action name via `toolLabel(tool_name)` (14px medium), then `arguments` as a key/value definition list (keys 12px subtle, values 13px, `.mono` for ids/numbers) instead of raw JSON, plus a small "Ver JSON" toggle that shows the current `<pre>`. Footer buttons: "Aprobar y ejecutar" (danger style) and "Rechazar" (ghost). Status visuals: `approved` = spinner + "Ejecutando…" with buttons disabled; `executed` = success tint + check "Ejecutada"; `failed` = danger tint "Falló al ejecutarse"; `rejected` = muted "Rechazada"; `expired` = muted "Venció sin respuesta". Keep the existing `onDecide` wiring.
- **Action bar** (copy / 👍 / 👎 / Detalle with latency): visible on hover of the assistant message (always visible on touch devices / when focused within), 28px icon buttons, ghost style; active thumbs up = accent, active thumbs down = danger. Keep existing handlers and aria-pressed.
- **Trace panel (`MessageDetails` / `TraceView`):** bordered surface-1 panel. Top: 4 metrics in a grid (Modelo, Tiempo total, Tokens "in → out", Costo estimado) — labels 12px subtle, values 13px `.mono`. Route reason line ("Modelo elegido: …"). KB chunks: each row = title + a thin score bar (accent fill, width = score) + score in `.mono`. Tool calls: name label, duration `.mono`, ok/error icon, error text in danger. Reuse the same `TraceView` in Trazabilidad.
- **Composer:** pinned at the bottom of the column, bordered surface-1 box, focus-within → `--border-strong` + accent ring; auto-growing textarea (max ~200px); bottom row with hint "Enter para enviar · Shift + Enter nueva línea" (12px subtle, hidden on mobile) and the send button (32px accent icon button with ArrowUp, disabled when empty) which becomes a secondary "Detener" button with a square icon while sending (existing `stop`). Error banner above the composer: danger tint, alert icon, message, and "Ref: xxxxxxxx" in `.mono` on the right.
- New messages animate in with `fade-up`. Auto-scroll to bottom on new content (keep current behavior if it exists).

## Phase 4 — Admin views (`KnowledgeView.tsx`, `IntegrationsView.tsx`, `TracesView.tsx`, `views.css`)

Restyle only; keep all logic and API calls. Common page pattern: 48px top bar with the page title, content max-width ~1100px, 24px padding, sections separated by 24px.
- **Stat cards:** row of bordered surface-1 cards; value 20px `.mono`, label 12px subtle. Error rate value in danger when > 0.
- **Tables** (documents, traces, unresolved emails): full width, 13px, header row 12px muted on surface-1, 36–40px rows, row hover surface-1, row separators; numbers/ids/dates in `.mono`; status as small badges (ok = success tint, error = danger tint); source types (Carpeta / Subido / Google Drive / Manual) and access levels as neutral badges.
- **Knowledge:** upload drop zone with dashed `--border-strong` border, highlighted with accent border + faint accent tint while dragging; per-file upload lines with spinner / check / error. "Probar búsqueda" results: title, snippet (13px muted), score bar like in the trace panel.
- **Integrations:** Gmail and Drive as two cards side by side (stack on mobile), header with icon, name and status dot (success when connected, subtle when not); key facts as a definition list; primary/secondary/ghost buttons consistent with the rest; result summaries (examined/replied/skipped/failed, new/changed/unchanged/unsupported) as small `.mono` stat pills.
- **Trazabilidad:** period select and refresh styled as compact controls; stat cards; by-model and by-tool breakdowns as simple horizontal bars built with CSS (no chart library); clicking a trace row opens the detail in a right-side panel/drawer (480px, full-screen on mobile, closes with Esc and a close button) using the shared `TraceView`.
- Also restyle `ChangePassword.tsx` (full screen + modal) to match the login card style.

## Done criteria

- `npm run build` passes; no leftover purple values; no undefined `var(--…)`.
- Login, chat streaming, tool status, approval flow (approve/reject), feedback, trace panel, rename/delete session, KB upload/search, Gmail/Drive actions and traces all behave exactly as before.
- Final summary: files changed per phase, any additive change to `useChat.ts`/`types.ts` (ideally none), and anything you could not do.
