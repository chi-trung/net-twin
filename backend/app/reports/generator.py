"""PDF health report generator.

Builds a concise operational report from the current twin state:
executive summary → device inventory → active alerts → top-talker links.
Rendered with fpdf2 (pure Python, no system deps). Latin-1 safe: device
names/ips are ASCII in practice; anything else is transliterated.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fpdf import FPDF
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Alert, AlertStatus, Device, HealthState, Link, MetricSample

PAGE_W = 190  # A4 210mm − 2×10mm margins


def _latin(text: object) -> str:
    """fpdf2 core fonts are latin-1; transliterate everything else."""
    return str(text or "").encode("latin-1", "replace").decode("latin-1")


def _fmt_bps(v: float) -> str:
    if v >= 1e9:
        return f"{v / 1e9:.1f} Gbps"
    if v >= 1e6:
        return f"{v / 1e6:.1f} Mbps"
    if v >= 1e3:
        return f"{v / 1e3:.1f} Kbps"
    return f"{v:.0f} bps"


class _ReportPDF(FPDF):
    def header(self) -> None:  # noqa: N802 - fpdf2 API
        if self.page_no() == 1:
            return
        self.set_font("helvetica", "I", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 6, "net-twin network health report", align="C")
        self.ln(10)

    def footer(self) -> None:  # noqa: N802 - fpdf2 API
        self.set_y(-14)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 8, f"page {self.page_no()}/{{nb}}", align="C")


def _section(pdf: _ReportPDF, title: str) -> None:
    pdf.ln(4)
    pdf.set_font("helvetica", "B", 12)
    pdf.set_text_color(20, 40, 80)
    pdf.cell(0, 8, _latin(title))
    pdf.ln(2)
    pdf.set_draw_color(80, 120, 200)
    pdf.line(10, pdf.get_y(), 10 + PAGE_W, pdf.get_y())
    pdf.ln(3)
    pdf.set_text_color(30, 30, 30)


async def generate_health_report(db: AsyncSession) -> bytes:
    """Render the current twin state into a PDF; returns the bytes."""
    pdf = _ReportPDF()
    pdf.alias_nb_pages()
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(True, 16)
    pdf.add_page()

    # ── cover / summary ────────────────────────────────────────────
    pdf.set_font("helvetica", "B", 20)
    pdf.set_text_color(20, 40, 80)
    pdf.cell(0, 12, "Network Health Report")
    pdf.ln(12)
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(0, 6, _latin(f"generated {stamp} - net-twin digital twin"))
    pdf.ln(10)

    devices = (await db.scalars(select(Device).order_by(Device.name))).all()
    by_health = {h: 0 for h in HealthState}
    for d in devices:
        by_health[d.health] += 1
    total_links = await db.scalar(select(func.count(Link.id))) or 0
    active = (
        await db.scalars(
            select(Alert)
            .where(Alert.status == AlertStatus.ACTIVE)
            .order_by(Alert.created_at.desc())
        )
    ).all()
    critical = sum(1 for a in active if a.severity.value == "critical")

    _section(pdf, "Executive summary")
    pdf.set_font("helvetica", "", 10)
    status = "HEALTHY" if by_health[HealthState.DOWN] == 0 else "DEGRADED"
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(0, 130, 60) if status == "HEALTHY" else pdf.set_text_color(200, 60, 30)
    pdf.cell(0, 7, _latin(f"Fleet status: {status}"))
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("helvetica", "", 10)
    pdf.ln(8)
    summary_cols = [
        ("Devices", str(len(devices))),
        ("Up", str(by_health[HealthState.UP])),
        ("Down", str(by_health[HealthState.DOWN])),
        ("Links", str(total_links)),
        ("Active alerts", str(len(active))),
        ("Critical", str(critical)),
    ]
    col_w = PAGE_W / len(summary_cols)
    for label, _value in summary_cols:
        pdf.set_font("helvetica", "", 8)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(col_w, 5, _latin(label))
    pdf.ln()
    for _label, value in summary_cols:
        pdf.set_font("helvetica", "B", 13)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(col_w, 8, value)
    pdf.ln(10)

    # ── device inventory ───────────────────────────────────────────
    _section(pdf, "Device inventory")
    headers = ["Name", "IP", "Type", "Health", "Source"]
    widths = [52, 40, 26, 26, 30]
    pdf.set_font("helvetica", "B", 9)
    pdf.set_fill_color(228, 234, 246)
    for h, w in zip(headers, widths, strict=True):
        pdf.cell(w, 7, h, fill=True)
    pdf.ln()
    pdf.set_font("helvetica", "", 9)
    for d in devices:
        if d.health == HealthState.DOWN:
            pdf.set_text_color(190, 40, 40)
        elif d.health == HealthState.DEGRADED:
            pdf.set_text_color(180, 140, 0)
        else:
            pdf.set_text_color(30, 30, 30)
        pdf.cell(widths[0], 6.5, _latin(d.name))
        pdf.cell(widths[1], 6.5, _latin(d.ip_address))
        pdf.cell(widths[2], 6.5, _latin(d.device_type.value))
        pdf.cell(widths[3], 6.5, _latin(d.health.value))
        pdf.cell(widths[4], 6.5, _latin(d.source))
        pdf.ln()
    pdf.set_text_color(30, 30, 30)

    # ── active alerts ──────────────────────────────────────────────
    _section(pdf, f"Active alerts ({len(active)})")
    if not active:
        pdf.set_font("helvetica", "I", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, 6, "No active alerts - all monitored conditions within thresholds.")
        pdf.ln()
    else:
        pdf.set_font("helvetica", "B", 9)
        pdf.set_fill_color(228, 234, 246)
        alert_headers = ["Severity", "Device", "Rule", "Message", "Since"]
        alert_widths = [20, 34, 28, 80, 28]
        for h, w in zip(alert_headers, alert_widths, strict=True):
            pdf.cell(w, 7, h, fill=True)
        pdf.ln()
        device_names = {d.id: d.name for d in devices}
        pdf.set_font("helvetica", "", 8)
        for a in active:
            sev_color = {"critical": (190, 40, 40), "warning": (180, 140, 0)}.get(
                a.severity.value, (30, 30, 30)
            )
            pdf.set_text_color(*sev_color)
            pdf.cell(20, 6, _latin(a.severity.value))
            pdf.set_text_color(30, 30, 30)
            pdf.cell(34, 6, _latin(device_names.get(a.device_id, a.device_id)))
            pdf.cell(28, 6, _latin(a.rule))
            pdf.cell(80, 6, _latin(a.message[:70]))
            pdf.cell(28, 6, _latin(a.created_at.strftime("%H:%M UTC")))
            pdf.ln()

    # ── top-talker links ───────────────────────────────────────────
    _section(pdf, "Top-talker links (latest throughput)")
    rows = (
        await db.execute(
            select(MetricSample)
            .where(MetricSample.metric_name == "if_out_bps")
            .order_by(MetricSample.timestamp.desc())
            .limit(300)
        )
    ).all()
    latest_by_iface: dict[int, float] = {}
    for r in rows:
        if r.interface_id is not None and r.interface_id not in latest_by_iface:
            latest_by_iface[r.interface_id] = r.value

    if latest_by_iface:
        links = (await db.scalars(select(Link))).all()
        device_by_id = {d.id: d for d in devices}
        ranked = []
        for lnk in links:
            bps = latest_by_iface.get(lnk.source_interface_id) or latest_by_iface.get(
                lnk.target_interface_id
            )
            if bps is not None:
                src = device_by_id.get(lnk.source_device_id)
                dst = device_by_id.get(lnk.target_device_id)
                ranked.append((bps, f"{src.name if src else '?'} -> {dst.name if dst else '?'}"))
        ranked.sort(reverse=True)
        pdf.set_font("helvetica", "B", 9)
        pdf.set_fill_color(228, 234, 246)
        for h, w in zip(["Link", "Throughput"], [130, 60], strict=True):
            pdf.cell(w, 7, h, fill=True)
        pdf.ln()
        pdf.set_font("helvetica", "", 9)
        for bps, name in ranked[:10]:
            pdf.cell(130, 6.5, _latin(name))
            pdf.cell(60, 6.5, _fmt_bps(bps))
            pdf.ln()
    else:
        pdf.set_font("helvetica", "I", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, 6, "No link-traffic samples recorded yet.")
        pdf.ln()

    return bytes(pdf.output())
