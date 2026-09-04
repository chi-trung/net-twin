# Lab Guide — demo data sources

net-twin must be demoable with **zero hardware**. Three options, easiest first.

## Option A — built-in simulator (default, zero setup)

```bash
DISCOVERY_SOURCE=simulator docker compose up --build
```

A 15-node campus topology (core router → 2 distribution → 4 access → 8 hosts)
appears in the UI within seconds, with live latency/loss metrics and alerts.

## Option B — snmpsim (synthetic SNMP agents, no Docker privileges)

`snmpsim` emulates SNMP agents from `.snmprec` data files — enough to prove the
real discovery pipeline (SNMP inventory + classification) without any device.

```bash
pip install snmpsim
# one agent per "device", different ports
snmpsim-snmprec -v 2c -c public -d lab/snmpsim/core-rtr.snmprec --agent-udpv4-endpoint=127.0.0.1:1161
```

Then point the collector at the lab subnet and set `DISCOVERY_SOURCE=live`.
See `lab/snmpsim/README.md` for the sample data files.

## Option C — GNS3 / Containerlab (real protocol behavior)

The most convincing demo for the thesis defense:

1. Build a small topology: 1 router (IOSv/FRR) + 2 switches (or Linux bridges)
   + a few hosts.
2. Enable SNMP + LLDP on each device:
   ```
   router(config)# snmp-server community public RO
   router(config)# lldp run
   ```
3. Attach a cloud node bridged to your host network (or use the Docker
   management network directly with Containerlab).
4. Run net-twin with:
   ```bash
   DISCOVERY_SOURCE=live DISCOVERY_SUBNET=192.168.100.0/24 docker compose up
   ```
5. Demo script:
   - discovery fills the graph automatically,
   - shut an interface in GNS3 → node turns red, `node_down` alert fires,
   - bring it back → alert clears, node recovers — all live in the browser.

## Verifying discovery from the CLI

```bash
curl -X POST http://localhost:8000/api/v1/discovery/run
curl -s http://localhost:8000/api/v1/topology | python -m json.tool
```
