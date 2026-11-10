"""
Step 4 - Generate the Power BI dashboard as a Power BI Project (PBIP).

Writes powerbi/LifelineDashboard.pbip plus
  LifelineDashboard.SemanticModel/   TMDL model: 6 tables, 5 relationships, DAX measures, Power Query (M) loads
  LifelineDashboard.Report/          PBIR report: 5 pages, all visuals, custom theme

Open powerbi/LifelineDashboard.pbip in Power BI Desktop, click Refresh, then File > Save as > .pbix if needed.
Re-run this script after moving the project folder (the CSV path is baked into the ProjectFolder parameter).

Run from the project root:  python scripts/04_build_powerbi.py
"""
from pathlib import Path
import json
import shutil
import uuid

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NAME = "LifelineDashboard"
OUT = ROOT / "powerbi"
SM = OUT / f"{NAME}.SemanticModel"
RP = OUT / f"{NAME}.Report"
NS = uuid.UUID("6f1c2a8e-3b7d-4c55-9a0e-1d2f3a4b5c6d")

VC = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json"
PAGE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json"
REPORT = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.1.0/schema.json"
PAGES = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json"
VERSION = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json"
PBIR = "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json"
PBISM = "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json"
PBIP = "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json"
PLATFORM = "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json"


def gid(key: str) -> str:
    return str(uuid.uuid5(NS, key))


def q(name: str) -> str:
    """TMDL object name, quoted when needed."""
    if any(c in name for c in " .=:'%()<>-+&/") or name[0].isdigit():
        return "'" + name.replace("'", "''") + "'"
    return name


def reset_dir(path: Path):
    """Empty a generated folder but keep Power BI Desktop's local .pbi folder (data cache, local settings)."""
    if not path.exists():
        return
    for child in path.iterdir():
        if child.name == ".pbi":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, obj):
    write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# =====================================================================================
# SEMANTIC MODEL
# =====================================================================================
INT, DBL, STR, DATE, BOOL = "int64", "double", "string", "dateTime", "boolean"
M_TYPE = {INT: "Int64.Type", DBL: "type number", STR: "type text", DATE: "type date", BOOL: "type logical"}

CSV_TABLES = {
    "fact_shipment": {
        "file": "fact_shipment.csv",
        "hidden": {"country_key", "vendor_key", "product_key", "delivery_status_order", "weight_kg_alloc",
                   "freight_usd_alloc", "is_late"},
        "types": {
            **{c: INT for c in ["id", "country_key", "vendor_key", "product_key", "quantity_packs", "units_total",
                                "delay_days", "is_late", "delivery_status_order"]},
            **{c: DATE for c in ["pq_sent_date", "po_sent_date", "scheduled_date", "delivered_date", "recorded_date"]},
            **{c: DBL for c in ["line_value_usd", "pack_price_usd", "unit_price_usd", "weight_kg", "weight_kg_alloc",
                                "freight_usd", "freight_usd_alloc", "insurance_usd", "total_landed_cost_usd",
                                "lead_time_days", "pq_to_po_days", "recording_lag_days", "freight_per_kg",
                                "freight_pct_of_value"]},
        },
        "sort_by": {"delivery_status": "delivery_status_order"},
        "description": "One row per shipment line item (USAID SCMS, 2006-2015).",
    },
    "dim_country": {
        "file": "dim_country.csv", "hidden": {"country_key"}, "types": {"country_key": INT},
        "extra_columns": [("segment", STR)],
        "description": "Destination countries with region and the K-means segment from 03_data_mining.py.",
    },
    "dim_vendor": {"file": "dim_vendor.csv", "hidden": {"vendor_key"}, "types": {"vendor_key": INT},
                   "description": "Vendors (\"SCMS from RDC\" = regional distribution centres)."},
    "dim_product": {"file": "dim_product.csv", "hidden": {"product_key"},
                    "types": {"product_key": INT, "units_per_pack": INT, "first_line": BOOL},
                    "description": "Products: group, molecule, brand, dosage form."},
    "association_rules": {"file": "association_rules.csv", "hidden": set(),
                          "types": {"support": DBL, "confidence": DBL, "lift": DBL, "rank": INT},
                          "formats": {"support": "0.0%", "confidence": "0.0%", "lift": "0.00"},
                          "description": "Top 25 Apriori rules whose consequent is LATE (03_data_mining.py)."},
}

FORMATS = {INT: "0", DBL: "#,0.00", DATE: "yyyy-mm-dd"}
MONEY_COLS = {"line_value_usd", "freight_usd", "freight_usd_alloc", "insurance_usd", "total_landed_cost_usd"}


def csv_partition(table: str, spec: dict, cols: list) -> str:
    types = ", ".join(f'{{"{c}", {M_TYPE[spec["types"].get(c, STR)]}}}' for c in cols)
    src = f'Csv.Document(File.Contents(ProjectFolder & "data\\powerbi\\{spec["file"]}"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv])'
    lines = [
        "let",
        f"    Source = {src},",
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        '    Blanks = Table.TransformColumns(Promoted, {}, each if _ = "" then null else _),',
        f'    Typed = Table.TransformColumnTypes(Blanks, {{{types}}}, "en-US")',
    ]
    if table == "dim_country":
        seg = 'Csv.Document(File.Contents(ProjectFolder & "data\\powerbi\\country_segments.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv])'
        lines[-1] += ","
        lines += [
            f"    SegSource = {seg},",
            "    SegPromoted = Table.PromoteHeaders(SegSource, [PromoteAllScalars = true]),",
            '    Seg = Table.SelectColumns(SegPromoted, {"country", "segment"}),',
            '    Joined = Table.NestedJoin(Typed, {"country"}, Seg, {"country"}, "seg", JoinKind.LeftOuter),',
            '    Expanded = Table.ExpandTableColumn(Joined, "seg", {"segment"}, {"segment"}),',
            '    Filled = Table.ReplaceValue(Expanded, null, "Not clustered (<20 line items)", Replacer.ReplaceValue, {"segment"}),',
            '    Result = Table.TransformColumnTypes(Filled, {{"segment", type text}})',
            "in",
            "    Result",
        ]
    else:
        lines += ["in", "    Typed"]
    return "\n".join(lines)


DATE_M = """let
    StartDate = #date(2006, 1, 1),
    EndDate = #date(2015, 12, 31),
    Dates = List.Dates(StartDate, Duration.Days(EndDate - StartDate) + 1, #duration(1, 0, 0, 0)),
    AsTable = Table.FromList(Dates, Splitter.SplitByNothing(), {"Date"}),
    Typed = Table.TransformColumnTypes(AsTable, {{"Date", type date}}),
    AddYear = Table.AddColumn(Typed, "Year", each Date.Year([Date]), Int64.Type),
    AddQuarter = Table.AddColumn(AddYear, "Quarter", each "Q" & Text.From(Date.QuarterOfYear([Date])), type text),
    AddMonthNo = Table.AddColumn(AddQuarter, "MonthNo", each Date.Month([Date]), Int64.Type),
    AddMonth = Table.AddColumn(AddMonthNo, "Month", each Date.ToText([Date], "MMM", "en-US"), type text),
    AddYearMonth = Table.AddColumn(AddMonth, "YearMonth", each Date.ToText([Date], "yyyy-MM"), type text)
in
    AddYearMonth"""

MEASURES_M = '#table(type table [Placeholder = text], {})'

# name, DAX, format, folder, description
MEASURES = [
    # ---- volume
    ("Line Items", "COUNTROWS ( fact_shipment )", "#,0", "Volume", "Number of shipment line items."),
    ("Shipments (ASN/DN)", "DISTINCTCOUNT ( fact_shipment[asn_dn_no] )", "#,0", "Volume",
     "Distinct advance shipment notices / delivery notes (physical consignments)."),
    ("Countries Served", "DISTINCTCOUNT ( fact_shipment[country_key] )", "#,0", "Volume", None),
    ("Vendors Used", "DISTINCTCOUNT ( fact_shipment[vendor_key] )", "#,0", "Volume", None),
    ("Units Shipped", "SUM ( fact_shipment[units_total] )", "#,0", "Volume", "Packs x units per pack."),
    # ---- value and cost
    ("Commodity Value", "SUM ( fact_shipment[line_value_usd] )", "\\$#,0", "Cost", "Line item value, USD."),
    ("Freight Cost", "SUM ( fact_shipment[freight_usd_alloc] )", "\\$#,0", "Cost",
     "Freight in USD, each consignment counted once (shared line items carry 0)."),
    ("Insurance Cost", "SUM ( fact_shipment[insurance_usd] )", "\\$#,0", "Cost", None),
    ("Landed Cost", "SUM ( fact_shipment[total_landed_cost_usd] )", "\\$#,0", "Cost",
     "Commodity value + allocated freight + insurance."),
    ("Total Weight (kg)", "SUM ( fact_shipment[weight_kg_alloc] )", "#,0", "Cost",
     "Weight in kg, each consignment counted once."),
    ("Freight % of Value",
     "VAR _f = CALCULATE ( SUM ( fact_shipment[freight_usd_alloc] ), KEEPFILTERS ( fact_shipment[freight_status] IN { \"Recorded\", \"Shared with ID\" } ) )\n"
     "VAR _v = CALCULATE ( SUM ( fact_shipment[line_value_usd] ), KEEPFILTERS ( fact_shipment[freight_status] IN { \"Recorded\", \"Shared with ID\" } ) )\n"
     "RETURN\n    DIVIDE ( _f, _v )",
     "0.0%", "Cost", "Freight / commodity value, only for line items whose freight is known."),
    ("Freight per kg",
     "VAR _f = CALCULATE ( SUM ( fact_shipment[freight_usd_alloc] ), KEEPFILTERS ( fact_shipment[freight_status] = \"Recorded\" ), KEEPFILTERS ( fact_shipment[weight_status] = \"Recorded\" ) )\n"
     "VAR _w = CALCULATE ( SUM ( fact_shipment[weight_kg_alloc] ), KEEPFILTERS ( fact_shipment[freight_status] = \"Recorded\" ), KEEPFILTERS ( fact_shipment[weight_status] = \"Recorded\" ) )\n"
     "RETURN\n    DIVIDE ( _f, _w )",
     "\\$#,0.00", "Cost", "Weighted freight per kg over consignments with both freight and weight recorded."),
    ("Freight Recorded %",
     "DIVIDE (\n    CALCULATE ( [Line Items], KEEPFILTERS ( fact_shipment[freight_status] IN { \"Recorded\", \"Shared with ID\" } ) ),\n    [Line Items]\n)",
     "0.0%", "Cost", "Share of line items with a usable freight value - read cost KPIs against this."),
    ("Avg Unit Price", "AVERAGE ( fact_shipment[unit_price_usd] )", "\\$#,0.000", "Cost", None),
    # ---- delivery
    ("Late Line Items", "CALCULATE ( [Line Items], KEEPFILTERS ( fact_shipment[is_late] = 1 ) )", "#,0", "Delivery", None),
    ("Late Rate %", "DIVIDE ( [Late Line Items], [Line Items] )", "0.0%", "Delivery",
     "Share of line items delivered after the scheduled date."),
    ("On-Time Rate %", "IF ( NOT ISBLANK ( [Line Items] ), 1 - [Late Rate %] )", "0.0%", "Delivery", None),
    ("Avg Delay When Late (days)",
     "CALCULATE ( AVERAGE ( fact_shipment[delay_days] ), KEEPFILTERS ( fact_shipment[is_late] = 1 ) )",
     "0.0", "Delivery", None),
    ("Median Lead Time (days)", "MEDIAN ( fact_shipment[lead_time_days] )", "0", "Delivery",
     "PO sent to delivery; only Direct Drop orders have a PO date."),
    ("Severe Late (>30d)",
     "CALCULATE ( [Line Items], KEEPFILTERS ( fact_shipment[delivery_status] = \"Late >30d\" ) )",
     "#,0", "Delivery", None),
    ("Late Rate % PY", "CALCULATE ( [Late Rate %], SAMEPERIODLASTYEAR ( dim_date[Date] ) )", "0.0%", "Delivery", None),
    ("Late Rate Change (pp)",
     "IF ( NOT ISBLANK ( [Late Rate % PY] ), ( [Late Rate %] - [Late Rate % PY] ) * 100 )",
     "+0.0;-0.0;0.0", "Delivery", "Change in late rate vs previous year, percentage points."),
    ("Late Rate by Scheduled Date %",
     "CALCULATE ( [Late Rate %], USERELATIONSHIP ( fact_shipment[scheduled_date], dim_date[Date] ) )",
     "0.0%", "Delivery", "Late rate using the inactive relationship on scheduled date."),
    ("Late Rate % (min 50)", "IF ( [Line Items] >= 50, [Late Rate %] )", "0.0%", "Delivery",
     "Late rate shown only where there are at least 50 line items (hides noisy small groups)."),
    ("Late Rate Colour",
     "SWITCH (\n    TRUE (),\n    ISBLANK ( [Late Rate %] ), BLANK (),\n    [Late Rate %] >= 0.20, \"#C0392B\",\n    [Late Rate %] >= 0.12, \"#E8A33D\",\n    \"#2E8B57\"\n)",
     None, "Display", "Red at 20% or more, amber at 12% or more, green otherwise."),
    # ---- vendors
    ("Direct Drop Value",
     "CALCULATE ( [Commodity Value], KEEPFILTERS ( fact_shipment[fulfill_via] = \"Direct Drop\" ) )",
     "\\$#,0", "Vendor", None),
    ("RDC Share of Value %",
     "DIVIDE ( CALCULATE ( [Commodity Value], KEEPFILTERS ( fact_shipment[fulfill_via] = \"From RDC\" ) ), [Commodity Value] )",
     "0.0%", "Vendor", "Share of value shipped from regional distribution centres."),
    ("Vendor Value Share %",
     "DIVIDE ( [Commodity Value], CALCULATE ( [Commodity Value], ALL ( dim_vendor ) ) )", "0.0%", "Vendor", None),
    ("Top 5 Direct Vendor Share %",
     "VAR _all = CALCULATE ( [Direct Drop Value], ALL ( dim_vendor ) )\n"
     "VAR _top = SUMX ( TOPN ( 5, ALL ( dim_vendor[vendor_short] ), [Direct Drop Value] ), [Direct Drop Value] )\n"
     "RETURN\n    DIVIDE ( _top, _all )",
     "0.0%", "Vendor", "Concentration of direct-drop spend in the five largest vendors."),
    ("Cumulative Direct Vendor Share %",
     "VAR _cur = [Direct Drop Value]\n"
     "VAR _tbl = FILTER ( ADDCOLUMNS ( ALLSELECTED ( dim_vendor[vendor_short] ), \"@v\", [Direct Drop Value] ), [@v] > 0 )\n"
     "RETURN\n    IF ( _cur > 0, DIVIDE ( SUMX ( FILTER ( _tbl, [@v] >= _cur ), [@v] ), SUMX ( _tbl, [@v] ) ) )",
     "0.0%", "Vendor", "Pareto line: running share of direct-drop value, largest vendor first."),
    ("Vendor Score (0-100)",
     "VAR _rel = [On-Time Rate %]\n"
     "VAR _fr = [Freight % of Value]\n"
     "VAR _cost = IF ( ISBLANK ( _fr ), 0.5, 1 - MIN ( 1, _fr * 5 ) )\n"
     "RETURN\n    IF ( NOT ISBLANK ( _rel ), ROUND ( 100 * ( 0.6 * _rel + 0.4 * _cost ), 0 ) )",
     "0", "Vendor", "60% on-time rate + 40% cost efficiency (freight at 20% of value or more scores 0)."),
]


def tmdl_expr_lines(expr: str, indent: int) -> str:
    return "\n".join(("\t" * indent + line) if line.strip() else "" for line in expr.split("\n"))


def tmdl_measure(name, dax, fmt, folder, desc) -> str:
    out = []
    if desc:
        out.append(f"\t/// {desc}")
    if "\n" in dax:
        out.append(f"\tmeasure {q(name)} =")
        out.append(tmdl_expr_lines(dax, 3))
    else:
        out.append(f"\tmeasure {q(name)} = {dax}")
    if fmt:
        out.append(f"\t\tformatString: {fmt}")
    out.append(f"\t\tdisplayFolder: {folder}")
    out.append(f"\t\tlineageTag: {gid('m:' + name)}")
    return "\n".join(out)


def tmdl_column(table, name, dtype, hidden=False, fmt=None, sort_by=None, summarize="none", is_key=False) -> str:
    out = [f"\tcolumn {q(name)}", f"\t\tdataType: {dtype}"]
    if hidden:
        out.append("\t\tisHidden")
    if is_key:
        out.append("\t\tisKey")
    if fmt:
        out.append(f"\t\tformatString: {fmt}")
    out.append(f"\t\tlineageTag: {gid(f'c:{table}.{name}')}")
    out.append(f"\t\tsummarizeBy: {summarize}")
    out.append(f"\t\tsourceColumn: {name}")
    if sort_by:
        out.append(f"\t\tsortByColumn: {q(sort_by)}")
    if dtype == DATE:
        out += ["", "\t\tannotation UnderlyingDateTimeDataType = Date"]
    return "\n".join(out)


def tmdl_table(name, columns_txt, partition_m, desc=None, measures_txt="", extra_props=()) -> str:
    out = []
    if desc:
        out.append(f"/// {desc}")
    out.append(f"table {q(name)}")
    out += [f"\t{p}" for p in extra_props]
    out.append(f"\tlineageTag: {gid('t:' + name)}")
    out.append("")
    if measures_txt:
        out.append(measures_txt)
        out.append("")
    out.append(columns_txt)
    out.append("")
    out.append(f"\tpartition {q(name)} = m")
    out.append("\t\tmode: import")
    out.append("\t\tsource =")
    out.append(tmdl_expr_lines(partition_m, 4))
    out.append("")
    out.append("\tannotation PBI_ResultType = Table")
    return "\n".join(out) + "\n"


def build_semantic_model():
    d = SM / "definition"
    reset_dir(SM)
    tables_order = ["_Measures", "fact_shipment", "dim_country", "dim_vendor", "dim_product", "dim_date",
                    "association_rules"]

    write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1600\n")
    model = [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        "annotation PBI_QueryOrder = " + json.dumps(["ProjectFolder"] + tables_order[1:]),
        "",
    ] + [f"ref table {q(t)}" for t in tables_order]
    write(d / "model.tmdl", "\n".join(model) + "\n")

    folder = str(ROOT).rstrip("\\/") + "\\"
    write(d / "expressions.tmdl",
          f'/// Absolute path of the BDM-project folder (ends with a backslash). Change it if you move the folder.\n'
          f'expression ProjectFolder = "{folder}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
          f"\tlineageTag: {gid('e:ProjectFolder')}\n\n\tannotation PBI_ResultType = Text\n")

    # CSV-backed tables: columns taken from the actual CSV headers
    for t, spec in CSV_TABLES.items():
        cols = list(pd.read_csv(ROOT / "data" / "powerbi" / spec["file"], nrows=0).columns)
        col_txt = []
        for c in cols + [n for n, _ in spec.get("extra_columns", [])]:
            dtype = dict(spec.get("extra_columns", [])).get(c) or spec["types"].get(c, STR)
            fmt = spec.get("formats", {}).get(c) or ("\\$#,0" if c in MONEY_COLS else FORMATS.get(dtype))
            if c.endswith("_key") or c in ("id", "rank", "delivery_status_order", "units_per_pack"):
                fmt = "0"
            summarize = "sum" if (dtype in (INT, DBL) and t == "fact_shipment" and not c.endswith("_key")
                                  and c not in ("id", "is_late", "delivery_status_order")) else "none"
            col_txt.append(tmdl_column(t, c, dtype, hidden=c in spec["hidden"], fmt=fmt,
                                       sort_by=spec.get("sort_by", {}).get(c), summarize=summarize))
        write(d / "tables" / f"{t}.tmdl",
              tmdl_table(t, "\n\n".join(col_txt), csv_partition(t, spec, cols), spec.get("description")))

    # Date table (generated in M)
    date_cols = [tmdl_column("dim_date", "Date", DATE, fmt="yyyy-mm-dd", is_key=True),
                 tmdl_column("dim_date", "Year", INT, fmt="0"),
                 tmdl_column("dim_date", "Quarter", STR),
                 tmdl_column("dim_date", "MonthNo", INT, hidden=True, fmt="0"),
                 tmdl_column("dim_date", "Month", STR, sort_by="MonthNo"),
                 tmdl_column("dim_date", "YearMonth", STR)]
    write(d / "tables" / "dim_date.tmdl",
          tmdl_table("dim_date", "\n\n".join(date_cols), DATE_M, "Calendar 2006-2015, marked as date table.",
                     extra_props=["dataCategory: Time"]))

    # Measure table
    meas_txt = "\n\n".join(tmdl_measure(*m) for m in MEASURES)
    write(d / "tables" / "_Measures.tmdl",
          tmdl_table("_Measures", tmdl_column("_Measures", "Placeholder", STR, hidden=True), MEASURES_M,
                     "All report measures, grouped in display folders.", measures_txt=meas_txt))

    rels = [("fact_shipment", "country_key", "dim_country", "country_key", True),
            ("fact_shipment", "vendor_key", "dim_vendor", "vendor_key", True),
            ("fact_shipment", "product_key", "dim_product", "product_key", True),
            ("fact_shipment", "delivered_date", "dim_date", "Date", True),
            ("fact_shipment", "scheduled_date", "dim_date", "Date", False)]
    rel_txt = []
    for ft, fc, tt, tc, active in rels:
        r = [f"relationship {gid(f'r:{ft}.{fc}')}"]
        if not active:
            r.append("\tisActive: false")
        r += [f"\tfromColumn: {q(ft)}.{q(fc)}", f"\ttoColumn: {q(tt)}.{q(tc)}"]
        rel_txt.append("\n".join(r))
    write(d / "relationships.tmdl", "\n\n".join(rel_txt) + "\n")

    write_json(SM / "definition.pbism", {"$schema": PBISM, "version": "4.2", "settings": {}})
    write_json(SM / ".platform", {"$schema": PLATFORM,
                                  "metadata": {"type": "SemanticModel", "displayName": NAME},
                                  "config": {"version": "2.0", "logicalId": gid("item:semanticmodel")}})

    # keep powerbi/measures.dax in sync with the model
    dax = ["// Generated by scripts/04_build_powerbi.py - same measures as the semantic model (_Measures table)\n"]
    for name, expr, fmt, folder_, desc in MEASURES:
        dax.append(f"// [{folder_}] {desc or ''}".rstrip())
        dax.append(f"{name} =\n{expr}\n")
    write(OUT / "measures.dax", "\n".join(dax))


# =====================================================================================
# REPORT
# =====================================================================================
M_ = "_Measures"


def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def s(text):  # string literal
    return lit("'" + text.replace("'", "''") + "'")


def measure(name):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": M_}}, "Property": name}}


def column(table, col):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}}


def proj(field, active=False, display=None):
    if "Measure" in field:
        ent, prop = M_, field["Measure"]["Property"]
    else:
        ent, prop = field["Column"]["Expression"]["SourceRef"]["Entity"], field["Column"]["Property"]
    p = {"field": field, "queryRef": f"{ent}.{prop}", "nativeQueryRef": prop}
    if active:
        p["active"] = True
    if display:
        p["displayName"] = display
    return p


def title_objects(text, show=True):
    return {"title": [{"properties": {"show": lit("true" if show else "false"), "text": s(text), "titleWrap": lit("true")}}]}


class Page:
    def __init__(self, name, display):
        self.name, self.display, self.visuals, self.z = name, display, [], 0

    def add(self, vname, vtype, x, y, w, h, query_state=None, sort=None, objects=None, vco=None, sync=None):
        visual = {"visualType": vtype}
        if query_state:
            visual["query"] = {"queryState": {role: {"projections": ps} for role, ps in query_state.items()}}
            if sort:
                visual["query"]["sortDefinition"] = {"sort": [{"field": f, "direction": d} for f, d in sort],
                                                     "isDefaultSort": False}
        if objects:
            visual["objects"] = objects
        if vco:
            visual["visualContainerObjects"] = vco
        if sync:
            visual["syncGroup"] = sync
        visual["drillFilterOtherVisuals"] = True
        self.visuals.append({"$schema": VC, "name": f"{self.name}_{vname}",
                             "position": {"x": x, "y": y, "z": self.z, "height": h, "width": w,
                                          "tabOrder": self.z},
                             "visual": visual})
        self.z += 1


# layout grid (1280 x 720 page)
L, GAP = 16, 12
KPI_Y, KPI_H = 76, 92
R1_Y, R1_H = 180, 262
R2_Y, R2_H = 452, 260
# colours (keep in sync with powerbi/lifeline_theme.json)
NAVY, CANVAS, WALLPAPER = "#12304A", "#EEF2F6", "#E2E8EF"
TITLE_ON_NAVY, SUBTITLE_ON_NAVY = "#FFFFFF", "#B8CAD9"
BAND_H = 70


def plain_container(bg=None):
    """No border, shadow or hover header; optional solid background colour."""
    vco = {"border": [{"properties": {"show": lit("false")}}],
           "dropShadow": [{"properties": {"show": lit("false")}}],
           "visualHeader": [{"properties": {"show": lit("false")}}],
           "title": [{"properties": {"show": lit("false")}}]}
    if bg:
        vco["background"] = [{"properties": {"show": lit("true"), "color": {"solid": {"color": s(bg)}},
                                             "transparency": lit("0D")}}]
    else:
        vco["background"] = [{"properties": {"show": lit("false")}}]
    return vco


def header(page: Page, title, subtitle):
    # full-width navy band behind the title and slicers
    page.add("header_band", "textbox", 0, 0, 1280, BAND_H,
             objects={"general": [{"properties": {"paragraphs": [{"textRuns": [{"value": " "}]}]}}]},
             vco=plain_container(NAVY))
    page.add("title", "textbox", L, 4, 520, 62, objects={"general": [{"properties": {"paragraphs": [
        {"textRuns": [{"value": title, "textStyle": {"fontSize": "20pt", "fontWeight": "bold", "color": TITLE_ON_NAVY}}]},
        {"textRuns": [{"value": subtitle, "textStyle": {"fontSize": "10pt", "color": SUBTITLE_ON_NAVY}}]},
    ]}}]}, vco=plain_container())
    slicers = [("year", column("dim_date", "Year"), "Year"),
               ("region", column("dim_country", "region"), "Region"),
               ("mode", column("fact_shipment", "shipment_mode"), "Shipment mode"),
               ("group", column("dim_product", "product_group"), "Product group")]
    for i, (key, fld, label) in enumerate(slicers):
        page.add(f"slicer_{key}", "slicer", 548 + i * 180, 7, 176, 56,
                 query_state={"Values": [proj(fld, active=True, display=label)]},
                 objects={"data": [{"properties": {"mode": s("Dropdown")}}]},
                 sync={"groupName": label, "fieldChanges": True, "filterChanges": True})


def kpis(page: Page, names):
    n = len(names)
    w = (1280 - 2 * L - (n - 1) * GAP) // n
    for i, m in enumerate(names):
        page.add(f"kpi{i + 1}", "card", L + i * (w + GAP), KPI_Y, w, KPI_H,
                 query_state={"Values": [proj(measure(m))]},
                 objects={"labels": [{"properties": {"fontSize": lit("24D")}}]})


def labels_on():
    return {"labels": [{"properties": {"show": lit("true")}}]}


def colour_by_late_rate():
    return {"dataPoint": [{"properties": {"fill": {"solid": {"color": {"expr": measure("Late Rate Colour")}}}},
                           "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}],
            **labels_on()}


def build_report(facts: dict):
    reset_dir(RP)
    d = RP / "definition"
    pages = []

    # ---------------- Page 1: Executive overview
    p = Page("p1_overview", "1. Executive overview")
    header(p, "Executive overview",
           f"US${facts['value_bn']:.2f} bn of HIV/AIDS commodities to {facts['countries']} countries, "
           f"{facts['first_year']}-{facts['last_year']}")
    kpis(p, ["Commodity Value", "Shipments (ASN/DN)", "Countries Served", "On-Time Rate %", "Freight % of Value"])
    year = column("dim_date", "Year")
    p.add("value_by_year", "clusteredColumnChart", L, R1_Y, 612, R1_H,
          {"Category": [proj(year, active=True)], "Y": [proj(measure("Commodity Value"))]},
          sort=[(year, "Ascending")], objects=labels_on(), vco=title_objects("Commodity value by delivery year"))
    p.add("late_by_route", "lineChart", 640, R1_Y, 624, R1_H,
          {"Category": [proj(year, active=True)], "Y": [proj(measure("Late Rate %"))],
           "Series": [proj(column("fact_shipment", "fulfill_via"), display="Fulfilment route")]},
          sort=[(year, "Ascending")],
          vco=title_objects("Late rate by year: regional warehouse (From RDC) vs Direct Drop"))
    p.add("value_by_group", "donutChart", L, R2_Y, 408, R2_H,
          {"Category": [proj(column("dim_product", "product_group"), active=True)],
           "Y": [proj(measure("Commodity Value"))]},
          vco=title_objects("Commodity value by product group"))
    p.add("top_countries", "clusteredBarChart", 436, R2_Y, 408, R2_H,
          {"Category": [proj(column("dim_country", "country"), active=True)],
           "Y": [proj(measure("Commodity Value"))]},
          sort=[(measure("Commodity Value"), "Descending")], vco=title_objects("Destination countries by value"))
    p.add("late_by_mode", "clusteredColumnChart", 856, R2_Y, 408, R2_H,
          {"Category": [proj(column("fact_shipment", "shipment_mode"), active=True)],
           "Y": [proj(measure("Late Rate %"))]},
          sort=[(measure("Late Rate %"), "Descending")], objects=colour_by_late_rate(),
          vco=title_objects("Late rate by shipment mode"))
    pages.append(p)

    # ---------------- Page 2: Delivery performance
    p = Page("p2_delivery", "2. Delivery performance")
    header(p, "Delivery performance", "Where and why shipments arrive after the scheduled date (RQ1, RQ2)")
    kpis(p, ["Late Rate %", "Avg Delay When Late (days)", "Severe Late (>30d)", "Median Lead Time (days)"])
    p.add("mode_route_matrix", "pivotTable", L, R1_Y, 500, R1_H,
          {"Rows": [proj(column("fact_shipment", "shipment_mode"), display="Shipment mode")],
           "Columns": [proj(column("fact_shipment", "fulfill_via"), display="Route")],
           "Values": [proj(measure("Late Rate %")), proj(measure("Line Items"))]},
          vco=title_objects("Late rate: shipment mode x fulfilment route"))
    status = column("fact_shipment", "delivery_status")
    p.add("status_distribution", "clusteredColumnChart", 528, R1_Y, 736, R1_H,
          {"Category": [proj(status, active=True)], "Y": [proj(measure("Line Items"))]},
          sort=[(status, "Ascending")], objects=labels_on(),
          vco=title_objects("Line items by delivery status (Early / On time / Late 1-30d / Late >30d)"))
    p.add("late_by_country", "clusteredBarChart", L, R2_Y, 612, R2_H,
          {"Category": [proj(column("dim_country", "country"), active=True)],
           "Y": [proj(measure("Late Rate % (min 50)"))]},
          sort=[(measure("Late Rate % (min 50)"), "Descending")], objects=colour_by_late_rate(),
          vco=title_objects("Late rate by country (50+ line items); red >= 20%, amber >= 12%"))
    p.add("late_by_vendor", "clusteredBarChart", 640, R2_Y, 624, R2_H,
          {"Category": [proj(column("dim_vendor", "vendor_short"), active=True)],
           "Y": [proj(measure("Late Rate % (min 50)"))]},
          sort=[(measure("Late Rate % (min 50)"), "Descending")], objects=colour_by_late_rate(),
          vco=title_objects("Late rate by vendor (50+ line items)"))
    pages.append(p)

    # ---------------- Page 3: Freight and cost
    p = Page("p3_freight", "3. Freight and cost")
    header(p, "Freight and cost", "What drives freight cost; each consignment's freight counted once (RQ3)")
    kpis(p, ["Freight Cost", "Freight per kg", "Freight % of Value", "Freight Recorded %"])
    p.add("weight_vs_freight", "scatterChart", L, R1_Y, 612, R1_H,
          {"Category": [proj(column("dim_country", "country"), active=True)],
           "Series": [proj(column("dim_country", "region"), display="Region")],
           "X": [proj(measure("Total Weight (kg)"))], "Y": [proj(measure("Freight Cost"))]},
          vco=title_objects("Countries: weight shipped vs freight cost"))
    p.add("fpk_by_mode", "clusteredColumnChart", 640, R1_Y, 624, R1_H,
          {"Category": [proj(column("fact_shipment", "shipment_mode"), active=True)],
           "Y": [proj(measure("Freight per kg"))]},
          sort=[(measure("Freight per kg"), "Descending")], objects=labels_on(),
          vco=title_objects("Freight per kg by shipment mode (USD)"))
    p.add("freight_pct_region", "clusteredBarChart", L, R2_Y, 612, R2_H,
          {"Category": [proj(column("dim_country", "region"), active=True)],
           "Y": [proj(measure("Freight % of Value"))]},
          sort=[(measure("Freight % of Value"), "Descending")], objects=labels_on(),
          vco=title_objects("Freight as % of commodity value, by region"))
    p.add("freight_status", "donutChart", 640, R2_Y, 624, R2_H,
          {"Category": [proj(column("fact_shipment", "freight_status"), active=True)],
           "Y": [proj(measure("Line Items"))]},
          vco=title_objects("How freight was recorded on each line item"))
    pages.append(p)

    # ---------------- Page 4: Vendor and product
    p = Page("p4_vendor", "4. Vendor and product")
    header(p, "Vendor and product", "Who we depend on, and how they perform (RQ6)")
    kpis(p, ["Vendors Used", "Top 5 Direct Vendor Share %", "RDC Share of Value %", "Avg Unit Price"])
    vend = column("dim_vendor", "vendor_short")
    p.add("pareto", "lineClusteredColumnComboChart", L, R1_Y, 1248, R1_H,
          {"Category": [proj(vend, active=True, display="Vendor")], "Y": [proj(measure("Direct Drop Value"))],
           "Y2": [proj(measure("Cumulative Direct Vendor Share %"))]},
          sort=[(measure("Direct Drop Value"), "Descending")],
          vco=title_objects("Direct-drop vendors: value (bars) and cumulative share (line)"))
    p.add("scorecard", "tableEx", L, R2_Y, 736, R2_H,
          {"Values": [proj(vend, display="Vendor"), proj(measure("Line Items")), proj(measure("Commodity Value")),
                      proj(measure("On-Time Rate %")), proj(measure("Freight % of Value")),
                      proj(measure("Vendor Score (0-100)"))]},
          sort=[(measure("Commodity Value"), "Descending")], vco=title_objects("Vendor scorecard"))
    p.add("top_molecules", "clusteredBarChart", 764, R2_Y, 500, R2_H,
          {"Category": [proj(column("dim_product", "molecule"), active=True)],
           "Y": [proj(measure("Commodity Value"))]},
          sort=[(measure("Commodity Value"), "Descending")], vco=title_objects("Molecules / tests by value"))
    pages.append(p)

    # ---------------- Page 5: Country risk and segments
    p = Page("p5_segments", "5. Country risk and segments")
    header(p, "Country risk and segments",
           f"K-means segments and association rules. Late-delivery model AUC {facts['auc']:.2f}; "
           f"freight model R² {facts['r2']:.2f}")
    seg = column("dim_country", "segment")
    top5 = 76
    p.add("segment_table", "tableEx", L, top5, 612, 300,
          {"Values": [proj(seg, display="Segment"), proj(measure("Countries Served")), proj(measure("Line Items")),
                      proj(measure("Late Rate %")), proj(measure("Freight % of Value")),
                      proj(measure("Commodity Value"))]},
          sort=[(measure("Line Items"), "Descending")], vco=title_objects("Country segments (K-means, 24 countries)"))
    p.add("segment_late", "clusteredBarChart", 640, top5, 624, 300,
          {"Category": [proj(seg, active=True)], "Y": [proj(measure("Late Rate %"))]},
          sort=[(measure("Late Rate %"), "Descending")], objects=colour_by_late_rate(),
          vco=title_objects("Late rate by segment"))
    rk = column("association_rules", "rank")
    p.add("rules", "tableEx", L, 388, 612, 324,
          {"Values": [proj(rk, display="#"), proj(column("association_rules", "antecedents"), display="If a line item is ..."),
                      proj(column("association_rules", "support"), display="Support"),
                      proj(column("association_rules", "confidence"), display="Late (confidence)"),
                      proj(column("association_rules", "lift"), display="Lift")]},
          sort=[(rk, "Ascending")], vco=title_objects("Association rules that lead to late delivery (Apriori)"))
    p.add("country_detail", "tableEx", 640, 388, 624, 324,
          {"Values": [proj(column("dim_country", "country"), display="Country"), proj(seg, display="Segment"),
                      proj(measure("Line Items")), proj(measure("Late Rate %")),
                      proj(measure("Avg Delay When Late (days)")), proj(measure("Freight % of Value"))]},
          sort=[(measure("Line Items"), "Descending")], vco=title_objects("Country detail"))
    pages.append(p)

    # ---------------- write pages
    page_objects = {
        "background": [{"properties": {"color": {"solid": {"color": s(CANVAS)}}, "transparency": lit("0D")}}],
        "outspace": [{"properties": {"color": {"solid": {"color": s(WALLPAPER)}}, "transparency": lit("0D")}}],
    }
    for pg in pages:
        write_json(d / "pages" / pg.name / "page.json",
                   {"$schema": PAGE, "name": pg.name, "displayName": pg.display, "displayOption": "FitToPage",
                    "height": 720, "width": 1280, "objects": page_objects})
        for v in pg.visuals:
            write_json(d / "pages" / pg.name / "visuals" / v["name"] / "visual.json", v)
    write_json(d / "pages" / "pages.json",
               {"$schema": PAGES, "pageOrder": [pg.name for pg in pages], "activePageName": pages[0].name})
    write_json(d / "version.json", {"$schema": VERSION, "version": "2.0.0"})

    ver = {"visual": "2.5.0", "report": "3.1.0", "page": "2.0.0"}
    write_json(d / "report.json", {
        "$schema": REPORT,
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": ver, "type": "SharedResources"},
            "customTheme": {"name": "lifeline_theme.json", "reportVersionAtImport": ver, "type": "RegisteredResources"},
        },
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": "lifeline_theme.json", "path": "lifeline_theme.json", "type": "CustomTheme"}]},
        ],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                     "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True},
    })
    res = RP / "StaticResources"
    base_theme = ROOT / "powerbi" / "theme_base" / "CY24SU10.json"
    (res / "SharedResources" / "BaseThemes").mkdir(parents=True, exist_ok=True)
    shutil.copy(base_theme, res / "SharedResources" / "BaseThemes" / "CY24SU10.json")
    (res / "RegisteredResources").mkdir(parents=True, exist_ok=True)
    shutil.copy(OUT / "lifeline_theme.json", res / "RegisteredResources" / "lifeline_theme.json")

    write_json(RP / "definition.pbir",
               {"$schema": PBIR, "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    write_json(RP / ".platform", {"$schema": PLATFORM, "metadata": {"type": "Report", "displayName": NAME},
                                  "config": {"version": "2.0", "logicalId": gid("item:report")}})
    write_json(OUT / f"{NAME}.pbip", {"$schema": PBIP, "version": "1.0",
                                      "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                                      "settings": {"enableAutoRecovery": True}})
    write(OUT / ".gitignore", "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")
    return pages


def main():
    fact = pd.read_csv(ROOT / "data" / "powerbi" / "fact_shipment.csv", parse_dates=["delivered_date"])
    mining = json.loads((ROOT / "outputs" / "mining_results.json").read_text(encoding="utf-8"))
    facts = {"value_bn": fact["line_value_usd"].sum() / 1e9, "countries": fact["country_key"].nunique(),
             "first_year": fact["delivered_date"].dt.year.min(), "last_year": fact["delivered_date"].dt.year.max(),
             "auc": mining["classification"]["gradient_boosting"]["roc_auc_time_split"],
             "r2": mining["regression"]["gradient_boosting"]["R2"]}
    build_semantic_model()
    pages = build_report(facts)
    n_vis = sum(len(p.visuals) for p in pages)
    print(f"Semantic model: {len(CSV_TABLES) + 2} tables, {len(MEASURES)} measures -> {SM}")
    print(f"Report: {len(pages)} pages, {n_vis} visuals -> {RP}")
    print(f"Open: {OUT / (NAME + '.pbip')}")


if __name__ == "__main__":
    main()
