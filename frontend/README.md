# Jawnix frontend

The redesigned Jawnix Platform interface: React, TypeScript, Vite, and React
Router Data Mode. Established by [#47]; later slices fill in the screens.

## Where it is served

The shell is built to `/app/` and served by FastAPI (`jawnix/frontend.py`), not
by the static file server. FastAPI owns the whole `/app` prefix so it can:

- apply the `JAWNIX_ENABLE_NEW_UI` feature flag — while it is off the prefix
  answers **404**, so the shell is undiscoverable rather than merely protected;
- cache content-hashed assets immutably while always revalidating
  `index.html`, which names the current hashes;
- return the shell document for direct navigation to any application route, so
  a hard refresh of `/app/admin/fulfillment` works.

The current static UI keeps the site root and is untouched until the cutover
in #71.

## Commands

```bash
npm install
npm run dev          # dev server, proxying /api to FastAPI on :8001
npm run build        # typecheck + production build to dist/
npm run typecheck
npm test             # unit tests (vitest + testing-library)
npm run test:e2e     # Playwright, against the compiled build
```

`npm run test:e2e` builds and serves the production bundle via `vite preview`,
so the journeys exercise the same hashed assets the deployment edge serves.

## Layout

```
src/
├── design-system/
│   ├── styles/          tokens.css (both themes) + reset.css
│   ├── primitives/      layout, typography, status, Button, form, Dialog, feedback
│   └── theme/           ThemeProvider — swaps data-theme on <html>
└── app/
    ├── routes.tsx       route table (basename /app)
    ├── shell/           AppShell, Navigation, RouteAnnouncer
    ├── shells/          Customer and Administration navigation
    └── routes/          screens (placeholders until their slice lands)
```

## Themes

Two themes resolve one token contract, so every primitive is written once:

- **`jawnix`** — the navy/blue product language (light).
- **`terminal`** — the dark GMS/OPS acquisition workspace, mono-first.

`AdminShell` switches to `terminal` on entry to Acquisition and back on exit, so
an operator never sets it by hand.

## Accessibility

Target is WCAG 2.2 AA. `e2e/accessibility.spec.ts` runs an axe sweep over every
route — each in the theme that route uses, plus the design-system gallery in
both — and asserts the parts axe cannot see: skip-link order, dialog focus
containment and restoration, 44px touch targets on gallery controls and
navigation links, visible focus, and reduced-motion behaviour.

`src/app/routes/DesignSystem.tsx` renders every primitive in both themes. It is
the fixture that sweep and the visual-regression baseline in #70 run against —
add new primitives there so they are covered automatically.

[#47]: https://github.com/nbleicher/jawnix/issues/47
