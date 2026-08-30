from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def plot_monthly_curve(report_path: Path, output_path: Path) -> Path:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    events = payload["equity_curve"]
    time_key = "timestamp" if events and "timestamp" in events[0] else "date"
    events = sorted(events, key=lambda item: item[time_key])
    initial_cash = float(payload["initial_cash"])
    months: dict[str, float] = {}
    for event in events:
        event_time = str(event[time_key])
        month = event_time[:7] if "-" in event_time else f"{event_time[:4]}-{event_time[4:6]}"
        months[month] = float(event["equity"])
    ordered_months = _all_months(min(months), max(months))
    equities: list[float] = []
    value = initial_cash
    for month in ordered_months:
        value = months.get(month, value)
        equities.append(value)
    returns = [(value / initial_cash - 1) * 100 for value in equities]

    width, height, pad = 1600, 820, 100
    low, high = min(min(returns), 0), max(max(returns), 0)
    spread = max(high - low, 4.0)
    low -= spread * 0.12
    high += spread * 0.18
    x = lambda index: pad + index * (width - 2 * pad) / max(len(returns) - 1, 1)
    y = lambda value: height - pad - (value - low) * (height - 2 * pad) / (high - low)
    points = " ".join(f"{x(index):.1f},{y(value):.1f}" for index, value in enumerate(returns))
    baseline = y(0)
    area = f"{x(0):.1f},{baseline:.1f} {points} {x(len(returns) - 1):.1f},{baseline:.1f}"
    grid = "".join(
        f'<line x1="{pad}" x2="{width-pad}" y1="{y(low + (high-low)*step/4):.1f}" y2="{y(low + (high-low)*step/4):.1f}" stroke="#CBD5E1" stroke-width="1"/>'
        for step in range(5)
    )
    labels = "".join(
        f'<text x="{x(index):.1f}" y="{height-pad+36}" text-anchor="middle" fill="#475569" font-size="20">{month}</text>'
        for index, month in enumerate(ordered_months)
        if index == 0 or index == len(ordered_months)-1 or month.endswith("-01")
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#F8FAFC"/>
<text x="{pad}" y="58" fill="#0F172A" font-family="Arial, sans-serif" font-size="34" font-weight="700">ETF Strategy Monthly Cumulative Return</text>
<text x="{pad}" y="90" fill="#64748B" font-family="Arial, sans-serif" font-size="20">1-minute execution replay | {ordered_months[0]} to {ordered_months[-1]}</text>
{grid}
<line x1="{pad}" x2="{width-pad}" y1="{baseline:.1f}" y2="{baseline:.1f}" stroke="#64748B" stroke-width="1.5"/>
<polygon points="{area}" fill="#14B8A6" opacity="0.14"/>
<polyline points="{points}" fill="none" stroke="#0F766E" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>
<circle cx="{x(len(returns)-1):.1f}" cy="{y(returns[-1]):.1f}" r="8" fill="#D97706"/>
<text x="{x(len(returns)-1):.1f}" y="{y(returns[-1])-20:.1f}" text-anchor="end" fill="#92400E" font-family="Arial, sans-serif" font-size="28" font-weight="700">{returns[-1]:+.2f}%</text>
{labels}
</svg>'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")
    return output_path


def _all_months(start: str, end: str) -> list[str]:
    current = datetime.strptime(start, "%Y-%m")
    final = datetime.strptime(end, "%Y-%m")
    output: list[str] = []
    while current <= final:
        output.append(current.strftime("%Y-%m"))
        current = current.replace(year=current.year + 1, month=1) if current.month == 12 else current.replace(month=current.month + 1)
    return output


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/private/tmp/minute_execution_2023_20260724.json")
    target = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path(__file__).resolve().parent / "local_data" / f"{source.stem}_monthly_return_curve.svg"
    )
    print(plot_monthly_curve(source, target))
