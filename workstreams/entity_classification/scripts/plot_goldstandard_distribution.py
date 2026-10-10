# -*- coding: utf-8 -*-
"""Build an Excel workbook visualizing the category distribution of
population_sample_1000.csv (the 1000-row goldstandard) -- one data sheet, one chart sheet.

Styling: horizontal bar, muted slate-blue series, dark-navy Calibri titles, light-gray plot
area with white gridlines.

No DB/network access needed; reads the existing goldstandard CSV only.
"""
import csv
import io
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText, Text
from openpyxl.chart.title import Title
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.text import (
    CharacterProperties,
    Paragraph,
    ParagraphProperties,
    RegularTextRun,
)
from openpyxl.drawing.text import Font as DrawingFont
from openpyxl.styles import Font

WORKSTREAM_ROOT = Path(__file__).resolve().parent.parent
GOLD_CSV = WORKSTREAM_ROOT / "data" / "goldstandard" / "population_sample_1000.csv"
OUT_XLSX = WORKSTREAM_ROOT / "results" / "goldstandard_kategorienverteilung.xlsx"

CATEGORIES = ["Forschung", "Unternehmen", "Behoerde", "NGO", "Community", "Individual", "Unklar"]

# Palette shared with the other report charts of the project.
NAVY = "2B3A55"  # titles
SLATE_BLUE = "7C93AC"  # bar fill
AXIS_TEXT = "5B6B7F"  # axis tick labels
AXIS_LINE = "C7D0DA"  # axis lines
PLOT_AREA_FILL = "F7F8FA"  # light-gray plot area
GRIDLINE = "FFFFFF"  # white gridlines against the gray plot area


def load_counts():
    with io.open(GOLD_CSV, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("label") in CATEGORIES]
    counts = dict.fromkeys(CATEGORIES, 0)
    for row in rows:
        counts[row["label"]] += 1
    total = len(rows)
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return ordered, total


def axis_text_properties(size=1000, color=AXIS_TEXT):
    char_props = CharacterProperties(
        sz=size, solidFill=color, latin=DrawingFont(typeface="Calibri")
    )
    return RichText(
        p=[Paragraph(pPr=ParagraphProperties(defRPr=char_props), endParaRPr=char_props)]
    )


def styled_title(text, size, color, bold=False):
    """Build a chart/axis title with explicit run formatting AND overlay=False --
    without both, the title can shrink the plot area unpredictably
    or fall back to default (black, default font) styling in some Excel versions."""
    char_props = CharacterProperties(
        sz=size, b=bold, solidFill=color, latin=DrawingFont(typeface="Calibri")
    )
    run = RegularTextRun(rPr=char_props, t=text)
    paragraph = Paragraph(pPr=ParagraphProperties(defRPr=char_props), r=[run])
    title = Title(tx=Text(rich=RichText(p=[paragraph])))
    title.overlay = False
    return title


def build_workbook(ordered_counts, total):
    wb = Workbook()

    data_ws = wb.active
    data_ws.title = "Daten"
    data_ws.append(["Kategorie", "Anzahl", "Anteil (%)"])
    for cell in data_ws[1]:
        cell.font = Font(name="Calibri", bold=True, color=NAVY)
    for cat, count in ordered_counts:
        data_ws.append([cat, count, round(100 * count / total, 2)])
    data_ws.append(["Gesamt", total, 100.0])
    for col, width in zip("ABC", (16, 10, 12)):
        data_ws.column_dimensions[col].width = width

    n_rows = len(ordered_counts)
    chart = BarChart()
    chart.type = "bar"  # horizontal
    chart.grouping = "clustered"
    chart.gapWidth = 40
    chart.title = styled_title(
        f"Kategorienverteilung im Goldstandard (n={total})", size=1400, color=NAVY
    )
    chart.x_axis.title = None
    chart.y_axis.title = styled_title("Anteil (%)", size=1100, color=AXIS_TEXT)
    chart.legend = None
    chart.style = None

    data_ref = Reference(data_ws, min_col=3, min_row=1, max_row=1 + n_rows)
    cats_ref = Reference(data_ws, min_col=1, min_row=2, max_row=1 + n_rows)
    chart.add_data(data_ref, titles_from_data=True)
    chart.set_categories(cats_ref)
    chart.series[0].graphicalProperties.solidFill = SLATE_BLUE
    chart.series[0].graphicalProperties.ln = LineProperties(noFill=True)

    for axis in (chart.x_axis, chart.y_axis):
        axis.delete = False
        axis.txPr = axis_text_properties()
        axis.spPr = GraphicalProperties(ln=LineProperties(solidFill=AXIS_LINE))
        axis.majorGridlines = ChartLines(
            spPr=GraphicalProperties(ln=LineProperties(w=9525, solidFill=GRIDLINE))
        )

    chart.plot_area.graphicalProperties = GraphicalProperties(solidFill=PLOT_AREA_FILL)
    chart.width = 15
    chart.height = 8.5

    chart_ws = wb.create_sheet("Grafik")
    chart_ws.add_chart(chart, "B2")

    return wb


def main():
    ordered_counts, total = load_counts()
    wb = build_workbook(ordered_counts, total)
    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_XLSX)
    print(f"Wrote {OUT_XLSX} (n={total})")
    for cat, count in ordered_counts:
        print(f"  {cat}: {count} ({100 * count / total:.1f}%)")


if __name__ == "__main__":
    main()
