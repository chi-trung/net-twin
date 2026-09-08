# net-twin live-discovery lab

A self-contained Docker lab where net-twin builds its twin from **real SNMP
conversations** — the same LLDP-MIB / CISCO-CDP-MIB tables real gear exposes.
Three snmpsim agents play a classic campus triangle:

```
172.30.0.10  core-rtr-01   Cisco ISR (LLDP)
172.30.0.11  dist-sw-01    Catalyst 9300 (LLDP + CDP)
172.30.0.12  acc-sw-01     Catalyst 2960 (CDP)
```

Expected result: the twin forms 3 devices and 2 links with provenance —
`core-rtr-01 → dist-sw-01` via **lldp**, `dist-sw-01 → acc-sw-01` via **cdp**.

## Layout

```
lab/
├── docker-compose.lab.yml
└── snmpsim/
    ├── Dockerfile              # snmpsim 1.1.7 + pysnmp 6 (pinned combo)
    ├── core-rtr.snmprec        # 172.30.0.10
    ├── dist-sw.snmprec         # 172.30.0.11
    └── acc-sw.snmprec          # 172.30.0.12
```

Each `.snmprec` file encodes one device: sysName/sysDescr, an ifTable, and
neighbor tables (LLDP-MIB remSysName, CISCO-CDP-MIB cdpCache with a 4-byte
management address). snmpsim maps the v2c community to the data-file name, so
the container renames its recording to `public.snmprec` — the community the
twin sweeps with.

## Run it

Docker Desktop (or any Docker engine) must be running. From `lab/`:

```bash
docker compose -f docker-compose.lab.yml up -d --build
```

That starts db + redis + 3 SNMP agents on a dedicated `172.30.0.0/24` network,
plus a second backend on port **8001** configured with
`DISCOVERY_SOURCE=live`, sweeping that subnet. The main stack on 8000 is
untouched (its simulator keeps working).

Trigger a discovery cycle and inspect the result:

```bash
curl -X POST http://localhost:8001/api/v1/discovery/run
curl http://localhost:8001/api/v1/topology
```

You should see 3 nodes with real names/types (`core-rtr-01` classified as
router from its sysDescr) and 2 edges carrying their collection protocol.
The monitor keeps every device `up` — ping fails inside the container, so
the scheduler's `PingOrSnmpProbe` falls back to an SNMP GET of sysName.0 and
reads the round-trip time as latency.

## Notes

- snmpsim refuses to run as root; the agent image drops to nobody/nogroup
  after preparing its data dir. The responder also misbehaves on Windows
  hosts directly — run the agents in Docker, not with `pip install snmpsim`.
- The lab only proves the **live** path. Discovery runs every 60s in the lab
  backend (`DISCOVERY_INTERVAL_SECONDS=60`), so the twin heals itself if you
  stop an agent: the device goes stale on the next cycle.
