"""
Step 2 - Load the nested shipment documents into MongoDB.

Prerequisite: MongoDB Community Server running locally (default mongodb://localhost:27017)
              pip install pymongo
Run:          python scripts/02_load_mongodb.py  [--uri mongodb://localhost:27017]

Creates database `scms_lifeline` with:
  shipments        - one nested document per line item (10,324), with a $jsonSchema validator
  vendors          - one document per vendor with KPIs           (built by $merge)
  countries        - one document per country with KPIs          (built by $merge)
  monthly_kpis     - one document per delivery month             (built by $merge)
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from pymongo import ASCENDING, DESCENDING, MongoClient

ROOT = Path(__file__).resolve().parents[1]
JSON_FILE = ROOT / "data" / "processed" / "shipments_mongo.json"
DB = "scms_lifeline"

VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "destination", "fulfilment", "vendor", "product", "dates", "cost", "kpi"],
        "properties": {
            "_id": {"bsonType": "int"},
            "destination": {"bsonType": "object", "required": ["country"],
                            "properties": {"country": {"bsonType": "string"}}},
            "fulfilment": {"bsonType": "object",
                           "properties": {"mode": {"enum": ["Air", "Truck", "Air Charter", "Ocean", "Unknown"]},
                                          "via": {"enum": ["Direct Drop", "From RDC"]}}},
            "dates": {"bsonType": "object", "required": ["scheduled", "delivered"],
                      "properties": {"scheduled": {"bsonType": "date"}, "delivered": {"bsonType": "date"}}},
            "cost": {"bsonType": "object", "required": ["line_value"],
                     "properties": {"line_value": {"bsonType": ["double", "int"], "minimum": 0}}},
            "kpi": {"bsonType": "object", "required": ["is_late"],
                    "properties": {"is_late": {"bsonType": "bool"}}},
        },
    }
}


def to_dates(doc):
    for k, v in doc["dates"].items():
        if v:
            doc["dates"][k] = datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="mongodb://localhost:27017")
    args = ap.parse_args()

    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    db = client[DB]
    db.drop_collection("shipments")
    db.create_collection("shipments", validator=VALIDATOR, validationLevel="strict")

    docs = [to_dates(d) for d in json.loads(JSON_FILE.read_text(encoding="utf-8"))]
    res = db.shipments.insert_many(docs, ordered=False)
    print(f"inserted {len(res.inserted_ids)} shipment documents")

    # Indexes that back the dashboard filters and the aggregation pipelines
    db.shipments.create_index([("destination.country", ASCENDING), ("dates.delivered", DESCENDING)])
    db.shipments.create_index([("vendor.name", ASCENDING)])
    db.shipments.create_index([("fulfilment.mode", ASCENDING), ("kpi.is_late", ASCENDING)])
    db.shipments.create_index([("product.group", ASCENDING), ("product.molecule", ASCENDING)])
    db.shipments.create_index([("dates.delivered", ASCENDING)])
    db.shipments.create_index([("product.description", "text"), ("vendor.name", "text")])

    # Summary collections (materialised views) - same logic as mongodb/aggregations.js
    db.shipments.aggregate([
        {"$group": {"_id": "$vendor.name", "short_name": {"$first": "$vendor.short_name"},
                    "line_items": {"$sum": 1},
                    "value_usd": {"$sum": "$cost.line_value"},
                    "freight_usd": {"$sum": {"$ifNull": ["$cost.freight_alloc", 0]}},
                    "late_rate": {"$avg": {"$cond": ["$kpi.is_late", 1, 0]}},
                    "avg_delay_days": {"$avg": "$kpi.delay_days"},
                    "countries": {"$addToSet": "$destination.country"},
                    "sites": {"$addToSet": "$vendor.manufacturing_site"}}},
        {"$set": {"country_count": {"$size": "$countries"}}},
        {"$merge": {"into": "vendors", "whenMatched": "replace"}},
    ])
    db.shipments.aggregate([
        {"$group": {"_id": "$destination.country", "region": {"$first": "$destination.region"},
                    "line_items": {"$sum": 1},
                    "value_usd": {"$sum": "$cost.line_value"},
                    "freight_usd": {"$sum": {"$ifNull": ["$cost.freight_alloc", 0]}},
                    "weight_kg": {"$sum": {"$ifNull": ["$logistics.weight_kg_alloc", 0]}},
                    "late_rate": {"$avg": {"$cond": ["$kpi.is_late", 1, 0]}},
                    "median_delay": {"$median": {"input": "$kpi.delay_days", "method": "approximate"}},
                    "modes": {"$addToSet": "$fulfilment.mode"}}},
        {"$set": {"freight_per_kg": {"$cond": [{"$gt": ["$weight_kg", 0]},
                                               {"$divide": ["$freight_usd", "$weight_kg"]}, None]}}},
        {"$merge": {"into": "countries", "whenMatched": "replace"}},
    ])
    db.shipments.aggregate([
        {"$group": {"_id": {"$dateTrunc": {"date": "$dates.delivered", "unit": "month"}},
                    "line_items": {"$sum": 1},
                    "value_usd": {"$sum": "$cost.line_value"},
                    "late": {"$sum": {"$cond": ["$kpi.is_late", 1, 0]}}}},
        {"$set": {"late_rate": {"$divide": ["$late", "$line_items"]}}},
        {"$sort": {"_id": 1}},
        {"$merge": {"into": "monthly_kpis", "whenMatched": "replace"}},
    ])
    for c in ["shipments", "vendors", "countries", "monthly_kpis"]:
        print(f"{c:13s} {db[c].count_documents({}):>6d} documents")


if __name__ == "__main__":
    main()
