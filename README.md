# net-twin

> A real-time **digital twin** system for network topology discovery and infrastructure monitoring.

**net-twin** builds a live virtual mirror ("digital twin") of a physical/virtual computer network.
It automatically discovers devices and links, continuously mirrors their state, and answers
operational questions a plain dashboard cannot: *what breaks if this switch dies? what path does
traffic take? how loaded is each link?*

```
   sense  ──▶  model  ──▶  mirror  ──▶  (analyze / alert / report)
     ▲                                     │
     └─────────────────────────────────────┘
```

## Features

**Discovery — building the twin**
- 🔍 Subnet sweep (ICMP/ARP) with unprivileged fallback (`Scapy` → OS `ping` + `arp -a`)
- 🧾 SNMPv2c inventory: `system` + `ifTable` enrichment, device classification
- 🔗 Link inference: LLDP neighbors (802.1AB), CDP neighbors (CISCO-CDP-MIB), ARP/MAC correlation
  fallback — merged with provenance ranking `manual > lldp > cdp > arp > inferred`
- 🧪 **Simulator mode** — a deterministic 15-node campus topology, so the full pipeline is demoable
  with zero hardware

**Monitoring — mirroring reality**
- 📡 Latency & packet-loss probes on every device, per-link in/out throughput samples
- 🚨 Threshold + state-change alert engine (`node_down`, `high_latency`, `packet_loss`) with
  raise-once / clear dedup
- 🧨 **Simulated outages** — REST-controlled "unplug" that drives the *real* pipeline
  (discovery staleness → DOWN → alerts → recovery)

**Realtime & UI**
- ⚡ Redis pub/sub event bus → WebSocket fan-out (`/ws/events`); in-memory fallback when Redis is down
- 🕸 Cytoscape.js live topology: health coloring, device-type shapes, four toolbar modes
- 💥 **What-if analysis** — click any node: blast radius (isolated / degraded devices) computed on
  the twin graph, one-click "apply outage" to make it real
- 🛣 **Path tracing** — shortest path between any two devices, highlighted on the graph
- 📊 Overview KPI row + health-mix bar, per-device metric charts, per-link traffic charts
- 📈 **Traffic anomaly detection** — EWMA baseline per link-direction; deviations beyond the
  confidence band auto-raise `traffic_anomaly` alerts (and auto-clear when traffic normalizes)
- 🧭 **Root-cause analysis** — topology-aware RCA ranks suspects (failing fan-out switch >
  unhealthy upstream > noisy neighbors) with alert evidence for every down/degraded device
- 🕐 **Time travel** — topology snapshots auto-captured on real changes (discovery, health
  transitions) or manual capture; timeline UI, historical graph rendering, diff vs live
- 📄 **PDF health report** — executive summary, device inventory, active alerts, top-talker links

## Architecture

```
[Real network | GNS3 / Containerlab | snmpsim | built-in simulator]
            ICMP · ARP · SNMP · LLDP
                        │
                        ▼
        ┌──────────────────────────────┐
        │  discovery + monitor engine  │  asyncio loops (FastAPI backend)
        └──────────────┬───────────────┘
                       ▼
        ┌──────────────┐    ┌────────────────┐
        │  PostgreSQL  │    │  Redis pub/sub │
        │  twin state  │───▶│  event stream  │
        └──────────────┘    └───────┬────────┘
                                    │ WebSocket /ws/events
                                    ▼
        ┌─────────────────────────────────────────┐
        │  React 18 · Cytoscape.js · ECharts      │
        │  topology · what-if · paths · charts    │
        └─────────────────────────────────────────┘
```

See [docs/architecture.md](docs/architecture.md) for the design document,
[docs/deployment.md](docs/deployment.md) for ops, and [docs/lab-guide.md](docs/lab-guide.md)
for hooking up real devices / GNS3 / snmpsim.

## API overview

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/topology` | full twin graph (nodes + links) |
| `GET /api/v1/devices/{id}` / `.../metrics` | device detail, metric time-series |
| `GET /api/v1/links/{id}/metrics` | per-link in/out throughput series |
| `POST /api/v1/analysis/whatif/{id}` | blast-radius simulation for a device |
| `GET /api/v1/analysis/rca/{id}` | root-cause hypotheses for a down/degraded device |
| `GET /api/v1/topology/path?from=A&to=B` | shortest path between two devices |
| `GET /api/v1/overview` | fleet KPIs (health counts, alerts, latency) |
| `POST /api/v1/sim/outages` · `DELETE /sim/outages/{ip}` | simulate / heal a device failure |
| `POST /api/v1/discovery/run` · `POST /monitor/run` | trigger a cycle immediately |
| `GET /api/v1/snapshots` · `POST /api/v1/snapshots` | topology snapshot timeline / manual capture |
| `GET /api/v1/snapshots/{id}` · `.../diff?against=live` | historical graph / diff vs live or another snapshot |
| `GET /api/v1/reports/health.pdf` | downloadable PDF health report |
| `WS /ws/events` | realtime twin snapshot + change events |

Interactive docs: `http://localhost:8000/docs` (Swagger UI).

## Repository layout

```
net-twin/
├── backend/            # FastAPI service
│   ├── app/
│   │   ├── api/        # REST routes + schemas
│   │   ├── analysis/   # blast-radius / shortest-path / RCA graph engine
│   │   ├── core/       # config, logging, outage registry
│   │   ├── db/         # SQLAlchemy models & session
│   │   ├── discovery/  # sweep, SNMP, LLDP/CDP/ARP, simulator, builder
│   │   ├── events/     # bus (Redis/in-memory) + WebSocket broadcaster
│   │   ├── history/    # topology snapshots, time-travel diffing
│   │   ├── monitor/    # probes, metrics, alerts, anomaly detection, scheduler
│   │   └── reports/    # PDF report generator
│   └── tests/          # 89 pytest tests
├── frontend/           # React 18 + Vite + TypeScript console
├── docs/               # architecture, deployment, lab guide
├── lab/                # snmpsim fixtures for hardware-free labs
└── docker-compose.yml  # postgres + redis + backend + nginx frontend
```

## Quick start

```bash
git clone https://github.com/chi-trung/net-twin.git && cd net-twin
cp .env.example .env
docker compose up --build
# UI:  http://localhost:5173
# API: http://localhost:8000/docs
```

The default `DISCOVERY_SOURCE=simulator` boots a full 15-node campus network with live metrics —
no hardware needed. Switch to `DISCOVERY_SOURCE=live` + `DISCOVERY_SUBNET=...` to scan a real
network (see [docs/lab-guide.md](docs/lab-guide.md)).

### 5-minute demo script

1. Watch the topology build itself and the KPI row populate (first ~15 s). Edges carry real
   provenance: LLDP on the core uplinks, CDP between distribution and access switches, ARP to hosts.
2. **What-if**: click the toolbar 💥, click `dist-sw-01` → half the network lights up as isolated;
   press *Apply outage* → nodes turn red, alerts stream into the feed.
3. Heal: `curl -X DELETE localhost:8000/api/v1/sim/outages/10.0.1.1` → the twin recovers on its own.
4. **Path**: toolbar 🛣, click the core router then any host → the path highlights hop by hop.
5. Click a **link** → live in/out throughput chart; anomalous spikes raise `traffic_anomaly` alerts
   automatically.
6. **Time travel**: toolbar 🕐 → snapshot timeline; pick one to see the topology as it was, with a
   diff against the live network. `📸 Capture now` pins the current state.
7. Click a red/down device → the detail panel suggests **root causes** ranked by topology.
8. Top bar **⬇ Report PDF** → an operational report ready for management.

## Testing & quality

```bash
cd backend
.venv/Scripts/python -m pytest -q      # 89 tests
.venv/Scripts/python -m ruff check app tests
```

Frontend: `npm run build` (strict TypeScript, tree-shaken ECharts/Cytoscape chunks).

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic-ready |
| Analysis | pure-Python graph algorithms (BFS blast radius, shortest path) |
| Data | PostgreSQL 16, Redis 7 |
| Realtime | WebSocket, Redis pub/sub event bus |
| Frontend | React 18, Vite 5, TypeScript, Cytoscape.js (fcose), ECharts |
| Reports | fpdf2 |
| Lab | GNS3 / Containerlab / snmpsim |
| Ops | Docker Compose, Makefile |

## License

MIT
