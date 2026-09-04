# net-twin — Architecture

## 1. Problem statement

The capstone topic is: *"Build a digital-twin system supporting network topology discovery and
real-time computer network infrastructure monitoring."*

A **digital twin** is more than a monitoring dashboard. It is a live, queryable model of the real
network that stays synchronized with reality through a continuous loop:

```
   sense  ──▶  model  ──▶  mirror  ──▶  (simulate / alert / visualize)
     ▲                                      │
     └──────────────────────────────────────┘
```

This document defines the components that implement that loop.

## 2. Core concepts

| Concept | Definition in net-twin |
|---|---|
| **Node** | A network device (router, switch, host) — has identity (IP/MAC), type, health, metrics. |
| **Edge** | A link between two nodes — inferred from LLDP/CDP neighbors or ARP+MAC correlation. |
| **Twin state** | The current graph (nodes + edges) plus each element's live attributes. |
| **Snapshot** | A point-in-time copy of the twin, persisted for history and diffing. |
| **Event** | A change in twin state (node.up, node.down, link.created, metric.threshold_breached). |

## 3. Subsystems

### 3.1 Discovery engine (`backend/app/discovery`)
Responsibilities:
- **Subnet sweep** — ICMP echo + ARP scan (Scapy) to find live hosts.
- **Device inventory** — SNMP `system` + `ifTable` to classify devices and read interfaces.
- **Link inference** — LLDP/CDP neighbor MIBs; fallback to ARP table + MAC-address matching to
  deduce which switch port connects to which host/device.
- **Topology builder** — merges the above into a consistent graph and emits `topology.updated`.

### 3.2 Monitoring engine (`backend/app/monitor`)
- Periodic pollers (asyncio) for latency (ICMP RTT), packet loss, interface counters
  (`ifInOctets`/`ifOutOctets` → rate), port oper status, device uptime.
- **Metric store** — time-series points written to PostgreSQL (TimescaleDB hypertable optional).
- **Alert engine** — threshold + state-change rules; emits `alert.raised` / `alert.cleared`.

### 3.3 Twin store (`backend/app/db`)
- PostgreSQL via SQLAlchemy 2.0. Tables: `devices`, `interfaces`, `links`, `metric_samples`,
  `alerts`, `snapshots`.
- The graph is the source of truth for the twin; every mutation is also published as an event.

### 3.4 Event bus & realtime API (`backend/app/events`, `backend/app/api`)
- Collectors publish state changes to **Redis pub/sub**.
- A **WebSocket broadcaster** subscribes and fans out to connected UI clients.
- REST API (FastAPI) exposes topology, metrics, alerts, and discovery triggers.

### 3.5 Frontend (`frontend`)
- React + Vite + TypeScript.
- **Cytoscape.js** renders the topology graph; node/edge color reflects live health.
- **ECharts** renders metric history.
- Consumes REST for initial load + WebSocket for incremental updates.

## 4. Data flow (steady state)

```
 poller/discoverer ──▶ twin store (PG) ──▶ Redis pub/sub ──▶ WS broadcaster ──▶ UI
        │                                                              ▲
        └── on change: emit event ────────────────────────────────────┘
```

## 5. Simulation lab strategy

Because a production network is not always available, net-twin supports three data sources:
1. **GNS3 / Containerlab** — real IOS/Linux devices with SNMP + LLDP (primary demo).
2. **snmpsim** — synthetic SNMP agents (fallback / CI).
3. **Built-in simulator** — generates a fake topology + metrics so the UI is always demoable.

## 6. Roadmap (phases)

- [x] Phase 1 — Repo scaffolding & docs
- [ ] Phase 2 — Backend core (FastAPI, config, DB models, migrations)
- [ ] Phase 3 — Discovery engine (scan, SNMP, topology builder)
- [ ] Phase 4 — Monitoring engine (pollers, metrics, alerts, WebSocket)
- [ ] Phase 5 — Frontend scaffold (React + Vite + TS)
- [ ] Phase 6 — Topology UI (Cytoscape live graph + dashboard)
- [ ] Phase 7 — Docker Compose + operations docs
