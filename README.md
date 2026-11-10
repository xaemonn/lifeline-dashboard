# Lifeline Logistics: Mining the Global HIV/AIDS Medicine Supply Chain

Business Data Mining project: MongoDB + Python data mining + Power BI, built on the real
USAID SCMS Delivery History dataset (Kaggle: "Supply Chain Shipment Pricing Data",
10,324 line items x 33 columns, deliveries 2006-2015, 43 countries).

Project report: https://claude.ai/artifact/GfhLxMzLCQmdmP56r9QVzs

## After cloning

The cleaned data files are not in the repo (the USAID dataset is CC BY-ND 4.0, so only the original is shared).
Recreate them, then rebuild the dashboard so its data path points at your folder:

```bash
pip install -r requirements.txt
python scripts/01_clean_transform.py
python scripts/03_data_mining.py
python scripts/04_build_powerbi.py
```

Dataset: [Supply Chain Shipment Pricing Data](https://www.kaggle.com/datasets/divyeshardeshana/supply-chain-shipment-pricing-data) (USAID SCMS), CC BY-ND 4.0.

## Run order

```bash
pip install -r requirements.txt

# 1. Clean + transform (writes data/processed and data/powerbi)      - tested
python scripts/01_clean_transform.py

# 2. Load MongoDB (needs MongoDB 7.0+ running on localhost:27017)    - not yet run on a live server
python scripts/02_load_mongodb.py
mongosh "mongodb://localhost:27017/scms_lifeline" mongodb/aggregations.js

# 3. Data mining (writes outputs/ and data/powerbi/ segments + rules) - tested
python scripts/03_data_mining.py

# 4. Generate the Power BI dashboard (powerbi/LifelineDashboard.pbip)
python scripts/04_build_powerbi.py

# 5. Static checks on the dashboard (DAX refs, visual fields, relationships)
python scripts/05_check_powerbi.py
```

## Power BI

The finished dashboard is `powerbi/LifelineDashboard.pbip` (5 pages, 67 visuals, 31 DAX measures).
See [powerbi/HOW_TO_OPEN.md](powerbi/HOW_TO_OPEN.md): open it in Power BI Desktop, click Refresh,
then Save as .pbix if you need a single file.

## Layout

| Path | Contents |
| --- | --- |
| `data/raw/` | Original CSV (unchanged; CC BY-ND 4.0, USAID) |
| `data/processed/` | `shipments_clean.csv`, `shipments_mongo.json`, `data_quality_report.csv` |
| `data/powerbi/` | Star schema: `fact_shipment`, `dim_country`, `dim_vendor`, `dim_product` |
| `scripts/` | ETL, MongoDB loader, data mining, Power BI generator and checker |
| `mongodb/aggregations.js` | 11 aggregation pipelines (Q1-Q11) |
| `powerbi/` | `LifelineDashboard.pbip` (+ SemanticModel / Report folders), DAX measures, theme, how-to-open guide |
| `outputs/` | Mining results, country clusters, association rules |
