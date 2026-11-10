"""
Step 1 - Clean and transform the USAID SCMS Delivery History dataset.

Input : data/raw/SCMS_Delivery_History_Dataset.csv   (Kaggle / USAID, 10,324 rows x 33 cols)
Output: data/processed/shipments_clean.csv          (flat, analysis-ready, one row per line item)
        data/processed/shipments_mongo.json          (nested documents for MongoDB, JSON array)
        data/powerbi/*.csv                           (star schema for Power BI)
        data/processed/data_quality_report.csv       (before/after quality log)

Run from the project root:  python scripts/01_clean_transform.py
"""
from pathlib import Path
import json
import re

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "SCMS_Delivery_History_Dataset.csv"
PROC = ROOT / "data" / "processed"
PBI = ROOT / "data" / "powerbi"
PROC.mkdir(parents=True, exist_ok=True)
PBI.mkdir(parents=True, exist_ok=True)

RENAME = {
    "ID": "id", "Project Code": "project_code", "PQ #": "pq_no", "PO / SO #": "po_so_no",
    "ASN/DN #": "asn_dn_no", "Country": "country", "Managed By": "managed_by",
    "Fulfill Via": "fulfill_via", "Vendor INCO Term": "inco_term", "Shipment Mode": "shipment_mode",
    "PQ First Sent to Client Date": "pq_sent_date", "PO Sent to Vendor Date": "po_sent_date",
    "Scheduled Delivery Date": "scheduled_date", "Delivered to Client Date": "delivered_date",
    "Delivery Recorded Date": "recorded_date", "Product Group": "product_group",
    "Sub Classification": "sub_classification", "Vendor": "vendor",
    "Item Description": "item_description", "Molecule/Test Type": "molecule",
    "Brand": "brand", "Dosage": "dosage", "Dosage Form": "dosage_form",
    "Unit of Measure (Per Pack)": "units_per_pack", "Line Item Quantity": "quantity_packs",
    "Line Item Value": "line_value_usd", "Pack Price": "pack_price_usd", "Unit Price": "unit_price_usd",
    "Manufacturing Site": "manufacturing_site", "First Line Designation": "first_line",
    "Weight (Kilograms)": "weight_raw", "Freight Cost (USD)": "freight_raw",
    "Line Item Insurance (USD)": "insurance_usd",
}

# Rough region grouping used for slicers and clustering
REGION = {
    "South Africa": "Southern Africa", "Zambia": "Southern Africa", "Mozambique": "Southern Africa",
    "Zimbabwe": "Southern Africa", "Botswana": "Southern Africa", "Namibia": "Southern Africa",
    "Malawi": "Southern Africa", "Lesotho": "Southern Africa", "Swaziland": "Southern Africa",
    "Angola": "Southern Africa",
    "Nigeria": "West Africa", "Côte d'Ivoire": "West Africa", "Ghana": "West Africa",
    "Guinea": "West Africa", "Benin": "West Africa", "Togo": "West Africa", "Mali": "West Africa",
    "Senegal": "West Africa", "Liberia": "West Africa", "Sierra Leone": "West Africa",
    "Burkina Faso": "West Africa", "Cameroon": "Central Africa",
    "Congo, DRC": "Central Africa", "Burundi": "East Africa",
    "Uganda": "East Africa", "Tanzania": "East Africa", "Kenya": "East Africa",
    "Rwanda": "East Africa", "Ethiopia": "East Africa", "South Sudan": "East Africa",
    "Sudan": "East Africa", "Eritrea": "East Africa",
    "Haiti": "Caribbean", "Dominican Republic": "Caribbean", "Guyana": "South America",
    "Vietnam": "Asia", "Pakistan": "Asia", "Afghanistan": "Asia", "Kazakhstan": "Asia",
    "Kyrgyzstan": "Asia", "Lebanon": "Middle East", "Libya": "Middle East", "Belize": "Central America",
}

quality_log = []


def log(step, column, rows_affected, action):
    quality_log.append({"step": step, "column": column, "rows_affected": int(rows_affected), "action": action})


def parse_date(s: pd.Series) -> pd.Series:
    """Dates come in two formats ('2-Jun-06' and '9/11/14') plus text placeholders."""
    a = pd.to_datetime(s, format="%d-%b-%y", errors="coerce")
    b = pd.to_datetime(s, format="%m/%d/%y", errors="coerce")
    return a.fillna(b)


REF = re.compile(r"See .*\(ID#:(\d+)\)")


def resolve_numeric(raw: pd.Series, ids: pd.Series, name: str):
    """Turn weight / freight text into numbers and a status flag.

    - plain numbers          -> value, status 'Recorded'
    - 'See ASN-93 (ID#:1281)' -> value copied from line item 1281, status 'Shared with ID'
    - 'Freight Included in Commodity Cost' / 'Invoiced Separately' / 'Weight Captured Separately'
                              -> NaN, status = that text
    """
    value = pd.to_numeric(raw, errors="coerce")
    status = pd.Series(np.where(value.notna(), "Recorded", raw), index=raw.index, dtype="object")
    ref_id = raw.str.extract(REF)[0].astype("float")
    lookup = pd.Series(value.values, index=ids.values)
    is_ref = ref_id.notna()
    value.loc[is_ref] = ref_id[is_ref].map(lookup)
    status.loc[is_ref] = "Shared with ID"
    dead = is_ref & value.isna()
    status.loc[dead] = "Shared - referenced value missing"
    log("numeric", name, dead.sum(), "reference points to a line item that itself has no number")
    log("numeric", name, is_ref.sum(), "resolved 'See ... (ID#:n)' reference to the referenced line item")
    log("numeric", name, (status.isin(["Recorded", "Shared with ID"]) == False).sum(),
        "non-numeric placeholder kept as status, value set to null")
    return value, status


def main():
    df = pd.read_csv(RAW, encoding="utf-8-sig")
    log("load", "*", len(df), f"loaded {df.shape[0]} rows x {df.shape[1]} columns")
    df = df.rename(columns=RENAME)

    # ---------- text fixes ----------
    # note: the file is valid UTF-8 ("Côte d'Ivoire"); read it with encoding="utf-8-sig"
    for c in df.select_dtypes(include=["object", "str"]).columns:
        df[c] = df[c].str.strip()
    na_mode = df["shipment_mode"].isna()
    df["shipment_mode"] = df["shipment_mode"].fillna("Unknown")
    log("missing", "shipment_mode", na_mode.sum(), "null -> 'Unknown'")
    df["dosage"] = df["dosage"].fillna("N/A")
    df["region"] = df["country"].map(REGION).fillna("Other")
    df["vendor_short"] = df["vendor"].str.replace(r"\s*\(.*\)", "", regex=True).str.title().str.slice(0, 40)

    # ---------- dates ----------
    for c in ["pq_sent_date", "po_sent_date", "scheduled_date", "delivered_date", "recorded_date"]:
        raw = df[c].copy()
        df[c] = parse_date(raw)
        log("date", c, df[c].isna().sum(), "unparseable placeholder (Pre-PQ / N/A - From RDC / Date Not Captured) -> null")

    # ---------- weight / freight ----------
    df["weight_kg"], df["weight_status"] = resolve_numeric(df["weight_raw"], df["id"], "weight_raw")
    df["freight_usd"], df["freight_status"] = resolve_numeric(df["freight_raw"], df["id"], "freight_raw")

    # Allocated values for totals: a "Shared with ID" line item repeats the consignment's freight/weight,
    # which is already counted on the referenced line item. Count it once (0 on the sharing rows).
    for v, s in [("weight_kg", "weight_status"), ("freight_usd", "freight_status")]:
        alloc = df[v].copy()
        alloc[df[s].str.startswith("Shared")] = 0.0
        df[v.replace("_kg", "_kg_alloc").replace("_usd", "_usd_alloc")] = alloc
    log("dedup", "freight_usd / weight_kg", int(df["freight_status"].str.startswith("Shared").sum()),
        "shared consignment rows set to 0 in *_alloc columns so totals count each consignment once")

    # ---------- engineered features ----------
    df["delay_days"] = (df["delivered_date"] - df["scheduled_date"]).dt.days
    df["is_late"] = (df["delay_days"] > 0).astype(int)
    df["delivery_status"] = np.select(
        [df["delay_days"] < 0, df["delay_days"] == 0, df["delay_days"].between(1, 30)],
        ["Early", "On time", "Late 1-30d"], default="Late >30d")
    df["delivery_status_order"] = df["delivery_status"].map({"Early": 1, "On time": 2, "Late 1-30d": 3, "Late >30d": 4})
    df["lead_time_days"] = (df["delivered_date"] - df["po_sent_date"]).dt.days
    df["pq_to_po_days"] = (df["po_sent_date"] - df["pq_sent_date"]).dt.days
    df["recording_lag_days"] = (df["recorded_date"] - df["delivered_date"]).dt.days
    df["delivery_year"] = df["delivered_date"].dt.year
    df["delivery_month"] = df["delivered_date"].dt.to_period("M").dt.to_timestamp()
    df["freight_per_kg"] = df["freight_usd"] / df["weight_kg"].replace(0, np.nan)
    df["freight_pct_of_value"] = df["freight_usd"] / df["line_value_usd"].replace(0, np.nan)
    df["insurance_pct_of_value"] = df["insurance_usd"] / df["line_value_usd"].replace(0, np.nan)
    df["total_landed_cost_usd"] = df["line_value_usd"] + df["freight_usd_alloc"].fillna(0) + df["insurance_usd"].fillna(0)
    df["units_total"] = df["quantity_packs"] * df["units_per_pack"]
    df["first_line"] = df["first_line"].eq("Yes")

    # sanity: negative lead times / absurd values are data-entry errors
    neg = df["lead_time_days"] < 0
    df.loc[neg, "lead_time_days"] = np.nan
    log("validity", "lead_time_days", neg.sum(), "negative lead time -> null")

    out_cols = [c for c in df.columns if c not in ("weight_raw", "freight_raw")]
    df[out_cols].to_csv(PROC / "shipments_clean.csv", index=False, date_format="%Y-%m-%d")

    # ---------- star schema for Power BI ----------
    dim_country = df[["country", "region"]].drop_duplicates().sort_values("country").reset_index(drop=True)
    dim_country.insert(0, "country_key", dim_country.index + 1)
    dim_vendor = df[["vendor", "vendor_short"]].drop_duplicates().sort_values("vendor").reset_index(drop=True)
    dim_vendor.insert(0, "vendor_key", dim_vendor.index + 1)
    prod_cols = ["item_description", "product_group", "sub_classification", "molecule", "brand",
                 "dosage", "dosage_form", "units_per_pack", "first_line"]
    dim_product = df[prod_cols].drop_duplicates("item_description").sort_values("item_description").reset_index(drop=True)
    dim_product.insert(0, "product_key", dim_product.index + 1)

    fact = (df.merge(dim_country[["country_key", "country"]], on="country")
              .merge(dim_vendor[["vendor_key", "vendor"]], on="vendor")
              .merge(dim_product[["product_key", "item_description"]], on="item_description"))
    fact_cols = ["id", "country_key", "vendor_key", "product_key", "project_code", "po_so_no", "asn_dn_no",
                 "managed_by", "fulfill_via", "inco_term", "shipment_mode", "manufacturing_site",
                 "pq_sent_date", "po_sent_date", "scheduled_date", "delivered_date", "recorded_date",
                 "quantity_packs", "units_total", "line_value_usd", "pack_price_usd", "unit_price_usd",
                 "weight_kg", "weight_kg_alloc", "weight_status", "freight_usd", "freight_usd_alloc",
                 "freight_status", "insurance_usd",
                 "total_landed_cost_usd", "delay_days", "is_late", "delivery_status", "delivery_status_order",
                 "lead_time_days",
                 "pq_to_po_days", "recording_lag_days", "freight_per_kg", "freight_pct_of_value"]
    fact[fact_cols].sort_values("id").to_csv(PBI / "fact_shipment.csv", index=False, date_format="%Y-%m-%d")
    dim_country.to_csv(PBI / "dim_country.csv", index=False)
    dim_vendor.to_csv(PBI / "dim_vendor.csv", index=False)
    dim_product.to_csv(PBI / "dim_product.csv", index=False)

    # ---------- nested documents for MongoDB ----------
    def iso(v):
        return None if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%dT00:00:00Z")

    def num(v):
        return None if pd.isna(v) else float(v)

    docs = []
    for r in df.itertuples(index=False):
        docs.append({
            "_id": int(r.id),
            "project_code": r.project_code,
            "refs": {"pq": r.pq_no, "po_so": r.po_so_no, "asn_dn": r.asn_dn_no},
            "destination": {"country": r.country, "region": r.region, "managed_by": r.managed_by},
            "fulfilment": {"via": r.fulfill_via, "inco_term": r.inco_term, "mode": r.shipment_mode},
            "vendor": {"name": r.vendor, "short_name": r.vendor_short, "manufacturing_site": r.manufacturing_site},
            "product": {"group": r.product_group, "sub_class": r.sub_classification, "description": r.item_description,
                        "molecule": r.molecule, "brand": r.brand, "dosage": r.dosage, "form": r.dosage_form,
                        "units_per_pack": int(r.units_per_pack), "first_line": bool(r.first_line)},
            "dates": {"pq_sent": iso(r.pq_sent_date), "po_sent": iso(r.po_sent_date), "scheduled": iso(r.scheduled_date),
                      "delivered": iso(r.delivered_date), "recorded": iso(r.recorded_date)},
            "quantity": {"packs": int(r.quantity_packs), "units": int(r.units_total)},
            "cost": {"line_value": num(r.line_value_usd), "pack_price": num(r.pack_price_usd),
                     "unit_price": num(r.unit_price_usd), "freight": num(r.freight_usd),
                     "freight_alloc": num(r.freight_usd_alloc),
                     "freight_status": r.freight_status, "insurance": num(r.insurance_usd),
                     "landed_total": num(r.total_landed_cost_usd)},
            "logistics": {"weight_kg": num(r.weight_kg), "weight_kg_alloc": num(r.weight_kg_alloc),
                          "weight_status": r.weight_status,
                          "freight_per_kg": num(r.freight_per_kg)},
            "kpi": {"delay_days": num(r.delay_days), "is_late": bool(r.is_late), "status": r.delivery_status,
                    "status_order": int(r.delivery_status_order),
                    "lead_time_days": num(r.lead_time_days), "pq_to_po_days": num(r.pq_to_po_days)},
        })
    with open(PROC / "shipments_mongo.json", "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False)

    pd.DataFrame(quality_log).to_csv(PROC / "data_quality_report.csv", index=False)
    print(pd.DataFrame(quality_log).to_string(index=False))
    print(f"\nWrote {len(df)} rows -> {PROC} and {PBI}")


if __name__ == "__main__":
    main()
