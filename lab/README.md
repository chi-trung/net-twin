# net-twin lab

Demo data sources for net-twin. See [docs/lab-guide.md](../docs/lab-guide.md).

```
lab/
└── snmpsim/            # synthetic SNMP agents (snmpsim .snmprec files)
    ├── core-rtr.snmprec
    └── access-sw.snmprec
```

Run an agent:

```bash
pip install snmpsim pysnmp
snmpsim-snmprec -v 2c -c public -d lab/snmpsim/core-rtr.snmprec \
    --agent-udpv4-endpoint=127.0.0.1:1161
```
