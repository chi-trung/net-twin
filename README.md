# net-twin

> A real-time **digital twin** system for network topology discovery and infrastructure monitoring.

**net-twin** builds a live virtual mirror ("digital twin") of a physical/virtual computer network.
It automatically discovers devices and links, continuously mirrors their state, and visualizes
everything as an interactive topology graph with real-time metrics and alerting.

## Features

- 🔍 **Automatic topology discovery** — subnet sweep (ICMP/ARP), SNMP inventory, LLDP/CDP neighbor
  correlation to infer links between devices.
- 🧬 **Digital twin model** — the network is represented as a live graph (nodes + edges) kept in sync
  with reality through a sense → build → mirror loop.
- 📡 **Real-time monitoring** — latency, packet loss, interface traffic (in/out), port state, uptime;
  pushed to the UI over WebSocket.
- 🕸️ **Interactive topology view** — Cytoscape.js graph with live health coloring (up / degraded / down).
- 🚨 **Threshold-based alerting** — node down, high latency, interface errors.
- 📈 **Metric history** — time-series charts per device/interface.
- 🧪 **What-if simulation** *(roadmap)* — fail a node on the twin and see which parts of the network
  become isolated (graph connectivity analysis).

## Architecture

```
[Real network / Lab: GNS3, Containerlab, snmpsim]
        SNMP · ICMP · ARP · LLDP · SSH
                    │
                    ▼
        ┌───────────────────────────┐
        │   Collector & Discovery   │  Python: Scapy, pysnmp, python-nmap
        └─────────────┬─────────────┘
                      ▼
        ┌──────────────┐   ┌─────────────────┐
        │ PostgreSQL   │──▶│  Redis pub/sub  │
        │ topo+metrics │   │  (event stream) │
        └──────────────┘   └────────┬────────┘
                                    │ WebSocket
                                    ▼
        ┌────────────────────────────────────────┐
        │  React + Cytoscape.js + ECharts        │
        └────────────────────────────────────────┘
```

See [docs/architecture.md](docs/architecture.md) for the full design document.

## Repository layout

```
net-twin/
├── backend/          # FastAPI service: discovery, monitoring, REST + WebSocket API
│   ├── app/
│   │   ├── api/      # HTTP & WS routes
│   │   ├── core/     # config, logging
│   │   ├── db/       # SQLAlchemy models & session
│   │   ├── discovery/# scanners, SNMP collectors, topology builder
│   │   ├── monitor/  # pollers, metric store, alert engine
│   │   └── events/   # Redis pub/sub + WebSocket broadcaster
│   └── tests/
├── frontend/         # React + Vite + TypeScript dashboard
├── docs/             # architecture, API, lab guides
├── lab/              # simulation lab (snmpsim / containerlab topologies)
└── docker-compose.yml
```

## Quick start

```bash
# 1. Clone & env
git clone https://github.com/chi-trung/net-twin.git && cd net-twin
cp .env.example .env

# 2. Run infrastructure + backend + frontend
docker compose up --build

# 3. Open
#    UI        http://localhost:5173
#    API docs  http://localhost:8000/docs
```

Development without Docker (backend):

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic |
| Discovery | Scapy, pysnmp, python-nmap |
| Data | PostgreSQL (+ TimescaleDB optional), Redis |
| Realtime | WebSocket, Redis pub/sub |
| Frontend | React 18, Vite, TypeScript, Cytoscape.js, ECharts |
| Lab | GNS3 / Containerlab / snmpsim |
| Ops | Docker Compose |

## License

MIT
