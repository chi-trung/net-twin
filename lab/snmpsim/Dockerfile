# snmpsim agent: one container per device, one fixed lab IP each.
#
# snmpsim maps the v2c community to the data-file name, and net-twin sweeps
# with a single community ("public") — so the entrypoint renames this
# container's recording to public.snmprec before starting the responder.
# DEVICE=<basename of the recording> selects which device to serve.
FROM python:3.12-slim

RUN pip install --no-cache-dir "snmpsim>=1.2" pysmi "pysnmp>=6.0"

COPY lab/snmpsim/*.snmprec /records/

ENV DATA_DIR=/data
CMD ["sh", "-c", "mkdir -p $DATA_DIR && cp /records/$DEVICE.snmprec $DATA_DIR/public.snmprec && exec snmpsim-command-responder-lite --data-dir=$DATA_DIR --agent-udpv4-endpoint=0.0.0.0:161"]
