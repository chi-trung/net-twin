# Deployment & Operations

## One-command stack (Docker Compose)

```bash
cp .env.example .env        # optional: tweak discovery settings
docker compose up --build -d
```

| Service | URL | Notes |
|---|---|---|
| UI | http://localhost:5173 | nginx serving the built SPA, proxies `/api` + `/ws` |
| API | http://localhost:8000/docs | FastAPI + Swagger |
| WebSocket | ws://localhost:8000/ws/events | twin event stream |
| PostgreSQL | localhost:5432 | nettwin/nettwin (dev credentials) |
| Redis | localhost:6379 | event bus between processes |

Useful commands:

```bash
make up        # build & start
make logs      # tail all services
make ps        # status
make down      # stop
make clean     # stop + wipe volumes
```

## Discovery modes

`DISCOVERY_SOURCE` in the environment selects the data source:

- **`simulator`** (default) — a deterministic 15-node campus topology is
  generated and monitored with synthetic metrics. Ideal for demos and CI.
- **`live`** — real scanning of `DISCOVERY_SUBNET` (e.g. `192.168.1.0/24`):
  1. subnet sweep (Scapy ARP when privileges allow, TCP+ARP-table fallback),
  2. SNMPv2c enrichment (`SNMP_COMMUNITY`),
  3. link inference from LLDP/CDP neighbors and ARP/MAC correlation.

  The backend container needs `NET_RAW`/`NET_ADMIN` for Scapy ARP capture:

  ```yaml
  backend:
    cap_add: [NET_RAW, NET_ADMIN]
  ```

## Manual triggers (useful during a demo)

```bash
curl -X POST http://localhost:8000/api/v1/discovery/run   # one discovery cycle now
curl -X POST http://localhost:8000/api/v1/monitor/run     # one monitoring cycle now
```

## Alert thresholds

Configured via environment (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `ALERT_LATENCY_THRESHOLD_MS` | 200 | raise `high_latency` above this |
| `ALERT_PACKET_LOSS_THRESHOLD_PCT` | 10 | raise `packet_loss` at/above this |
| (state) | — | `node_down` fires when a device stops answering |

Alerts deduplicate: a sustained condition raises once and clears on recovery.

## Database schema

Tables are created automatically on startup (`create_all`, idempotent).
When migrations become necessary, initialize Alembic against
`app.db.base.Base.metadata` and switch the lifespan to run `alembic upgrade head`.

## Backup / reset

```bash
docker compose exec db pg_dump -U nettwin nettwin > backup.sql   # backup
make clean && make up                                            # full reset
```
