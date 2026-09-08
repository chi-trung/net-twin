# snmpsim agent: one container per device, one fixed lab IP each.
#
# snmpsim maps the v2c community to the data-file name, and net-twin sweeps
# with a single community ("public") — so the entrypoint renames this
# container's recording to public.snmprec before starting the responder.
# DEVICE=<basename of the recording> selects which device to serve.
FROM python:3.12-slim

# snmpsim 1.2.x declares a pysnmp>=7 requirement but still calls the
# registerRecvCbFun API that pysnmp 7.1 removed — pin the last known-good
# combo (snmpsim 1.1.x + pysnmp 6.x)
RUN pip install --no-cache-dir "snmpsim==1.1.7" pysmi "pysnmp>=6.0,<7"

COPY lab/snmpsim/*.snmprec /records/

ENV DATA_DIR=/data
# snmpsim refuses to run as root: drop to nobody after preparing the
# writable data dir (the responder builds its .dbm index there)
CMD ["sh", "-c", "mkdir -p $DATA_DIR && cp /records/$DEVICE.snmprec $DATA_DIR/public.snmprec && chmod 777 $DATA_DIR && exec snmpsim-command-responder-lite --data-dir=$DATA_DIR --agent-udpv4-endpoint=0.0.0.0:161 --process-user=nobody --process-group=nogroup"]
