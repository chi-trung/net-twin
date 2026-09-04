# net-twin frontend

React + Vite + TypeScript dashboard for the net-twin digital twin.

## Run (development)

```bash
npm install
npm run dev        # http://localhost:5173 (proxies /api and /ws to :8000)
```

## Scripts

| Command | Description |
|---|---|
| `npm run dev` | Vite dev server with API/WS proxy |
| `npm run build` | Type-check + production build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | ESLint |
| `npm run format` | Prettier |

## Structure

```
src/
├── api/client.ts        # typed REST client (axios)
├── hooks/useTwinEvents  # resilient WebSocket → twin store
├── state/twinStore.ts   # observable mirror of the digital twin
├── components/          # DeviceTable, (Phase 6: TopologyGraph, MetricsChart, AlertFeed)
├── types.ts             # API/event contracts
└── styles.css           # dark NOC theme
```
