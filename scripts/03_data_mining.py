"""
Step 3 - Data mining on the cleaned SCMS shipments.

Reads data/processed/shipments_clean.csv (same content as the MongoDB `ml_features` collection)
and writes results to outputs/.

  A. Classification  - will a line item be delivered late?   (RQ1)
  B. Regression      - freight cost in USD                    (RQ3)
  C. Clustering      - country segments                       (RQ4)
  D. Association     - attribute combinations linked to lateness (RQ5)

Run: python scripts/03_data_mining.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from mlxtend.frequent_patterns import apriori, association_rules
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (classification_report, mean_absolute_error, r2_score,
                             roc_auc_score, silhouette_score)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
PBI = ROOT / "data" / "powerbi"
OUT.mkdir(exist_ok=True)
SEED = 42


def load() -> pd.DataFrame:
    return pd.read_csv(ROOT / "data" / "processed" / "shipments_clean.csv",
                       parse_dates=["scheduled_date", "delivered_date", "po_sent_date"])


# ---------------------------------------------------------------- A. classification
def classify(df: pd.DataFrame) -> dict:
    d = df.copy()
    d["sched_month"] = d["scheduled_date"].dt.month
    d["sched_year"] = d["scheduled_date"].dt.year
    d["log_packs"] = np.log1p(d["quantity_packs"])
    d["log_value"] = np.log1p(d["line_value_usd"])
    cat = ["country", "shipment_mode", "fulfill_via", "inco_term", "vendor_short",
           "product_group", "sub_classification", "dosage_form"]
    num = ["sched_month", "sched_year", "log_packs", "log_value", "unit_price_usd", "first_line"]
    X, y = d[cat + num], d["is_late"]

    # Time-based split: train on schedules before 2013, test on 2013-2015 (no look-ahead)
    tr = d["sched_year"] < 2013
    X_tr, X_te, y_tr, y_te = X[tr], X[~tr], y[tr], y[~tr]

    ohe = ColumnTransformer([("c", OneHotEncoder(handle_unknown="ignore", min_frequency=20), cat),
                             ("n", StandardScaler(), num)])
    logit = Pipeline([("prep", ohe), ("m", LogisticRegression(max_iter=2000, class_weight="balanced"))])
    ordn = ColumnTransformer([("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), cat),
                              ("n", "passthrough", num)])
    gbm = Pipeline([("prep", ordn), ("m", HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, categorical_features=list(range(len(cat))),
        class_weight="balanced", random_state=SEED))])

    res = {"train_rows": int(tr.sum()), "test_rows": int((~tr).sum()),
           "late_rate_train": round(y_tr.mean(), 3), "late_rate_test": round(y_te.mean(), 3)}
    for name, model in [("logistic_regression", logit), ("gradient_boosting", gbm)]:
        model.fit(X_tr, y_tr)
        p = model.predict_proba(X_te)[:, 1]
        res[name] = {"roc_auc_time_split": round(roc_auc_score(y_te, p), 3),
                     "report": classification_report(y_te, (p > 0.5).astype(int), output_dict=True)["1"]}
    # Random stratified split for comparison (optimistic, mixes years)
    Xa, Xb, ya, yb = train_test_split(X, y, test_size=0.25, stratify=y, random_state=SEED)
    gbm.fit(Xa, ya)
    res["gradient_boosting"]["roc_auc_random_split"] = round(roc_auc_score(yb, gbm.predict_proba(Xb)[:, 1]), 3)
    return res


# ---------------------------------------------------------------- B. regression
def regress(df: pd.DataFrame) -> dict:
    # Only consignments whose freight is recorded on this line item: "Shared with ID" rows repeat another
    # row's freight and weight, so keeping them would put duplicate targets in both train and test sets.
    d = df[(df["freight_status"] == "Recorded") & (df["freight_usd"] > 0) & (df["weight_kg"] > 0)
           & (df["shipment_mode"] != "Unknown")].copy()
    d["log_w"] = np.log1p(d["weight_kg"])
    d["log_value"] = np.log1p(d["line_value_usd"])
    d["year"] = d["delivered_date"].dt.year
    cat = ["shipment_mode", "country", "fulfill_via", "inco_term", "product_group", "vendor_short"]
    num = ["log_w", "log_value", "year"]
    tr, te = train_test_split(d, test_size=0.25, random_state=SEED)

    # Baseline: median freight-per-kg of the shipment mode x weight
    fpk = tr.groupby("shipment_mode")["freight_per_kg"].median()
    base = te["shipment_mode"].map(fpk) * te["weight_kg"]

    prep = ColumnTransformer([("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), cat),
                              ("n", "passthrough", num)])
    model = Pipeline([("prep", prep), ("m", HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.05, categorical_features=list(range(len(cat))), random_state=SEED))])
    model.fit(tr[cat + num], np.log1p(tr["freight_usd"]))
    pred = np.expm1(model.predict(te[cat + num]))
    return {"rows_used": len(d),
            "baseline_mode_x_weight": {"MAE_usd": round(mean_absolute_error(te["freight_usd"], base), 0),
                                       "R2": round(r2_score(te["freight_usd"], base), 3)},
            "gradient_boosting": {"MAE_usd": round(mean_absolute_error(te["freight_usd"], pred), 0),
                                  "R2": round(r2_score(te["freight_usd"], pred), 3)},
            "median_freight_usd_test": round(te["freight_usd"].median(), 0)}


# ---------------------------------------------------------------- C. clustering
def name_segments(prof: pd.DataFrame) -> pd.Series:
    """Readable names for K-means clusters, assigned by profile so they survive re-runs."""
    names = pd.Series("Reliable air-served", index=prof.index)
    single = prof.index[prof["line_items"].eq(prof["line_items"].min()) & (prof["late_rate"] == prof["late_rate"].max())]
    rest = prof.drop(index=single)
    names[single] = "Outlier (very late)"
    truck = rest["air_share"].idxmin()
    names[truck] = "High-volume truck hubs"
    others = rest.drop(index=truck)
    if len(others) >= 2:  # only name a high-cost segment when there is something to contrast it with
        names[others["freight_pct"].idxmax()] = "Fragile, high freight cost"
    return names


def cluster(df: pd.DataFrame) -> dict:
    g = df.groupby("country").agg(
        line_items=("id", "size"), late_rate=("is_late", "mean"),
        p75_delay=("delay_days", lambda s: s.quantile(0.75)),
        value_musd=("line_value_usd", lambda s: s.sum() / 1e6),
        air_share=("shipment_mode", lambda s: s.isin(["Air", "Air Charter"]).mean()),
        rdc_share=("fulfill_via", lambda s: (s == "From RDC").mean()))
    # freight as % of value, counting each consignment's freight once (allocated column)
    known = df[df["freight_status"].isin(["Recorded", "Shared with ID"])]
    g["freight_pct"] = (known.groupby("country")["freight_usd_alloc"].sum()
                        / known.groupby("country")["line_value_usd"].sum())
    g = g[g["line_items"] >= 20].fillna(g.median(numeric_only=True))
    feats = ["late_rate", "p75_delay", "air_share", "rdc_share", "freight_pct"]
    Z = StandardScaler().fit_transform(g[feats].assign(freight_pct=np.log1p(g["freight_pct"])))
    scores = {k: silhouette_score(Z, KMeans(k, n_init=20, random_state=SEED).fit_predict(Z)) for k in range(2, 7)}
    # k=2 only isolates a single outlier country (Burundi); pick the best k in 3..6 for usable segments
    k = max((k_ for k_ in scores if k_ >= 3), key=scores.get)
    g["cluster"] = KMeans(k, n_init=20, random_state=SEED).fit_predict(Z)
    g.round(3).to_csv(OUT / "country_clusters.csv")
    prof = g.groupby("cluster")[feats + ["line_items"]].mean().round(3)
    prof["countries"] = g.groupby("cluster").apply(lambda x: ", ".join(x.index), include_groups=False)
    prof["segment"] = name_segments(prof)
    prof.to_csv(OUT / "cluster_profiles.csv")
    seg = g[["cluster"]].join(prof["segment"], on="cluster").reset_index()
    seg[["country", "cluster", "segment"]].to_csv(PBI / "country_segments.csv", index=False)
    return {"countries_clustered": len(g), "silhouette_by_k": {k_: round(v, 3) for k_, v in scores.items()},
            "chosen_k": k, "profiles": prof.reset_index().to_dict("records")}


# ---------------------------------------------------------------- D. association rules
def rules(df: pd.DataFrame) -> dict:
    d = df.copy()
    d["value_band"] = pd.qcut(d["line_value_usd"], 3, labels=["low", "mid", "high"]).astype(str)
    cols = ["shipment_mode", "fulfill_via", "region", "product_group", "sub_classification", "value_band"]
    items = pd.get_dummies(d[cols].astype(str), prefix=cols, prefix_sep="=").astype(bool)
    items["LATE"] = d["is_late"].astype(bool)
    fi = apriori(items, min_support=0.01, use_colnames=True, max_len=4)
    r = association_rules(fi, metric="lift", min_threshold=1.2)
    r = r[r["consequents"].apply(lambda s: s == frozenset({"LATE"}))]
    r = r.sort_values(["lift", "support"], ascending=False)
    r["antecedents"] = r["antecedents"].apply(lambda s: " + ".join(sorted(s)))
    keep = r[["antecedents", "support", "confidence", "lift"]].round(3)
    keep.to_csv(OUT / "association_rules_late.csv", index=False)
    keep.head(25).assign(rank=range(1, min(25, len(keep)) + 1)).to_csv(PBI / "association_rules.csv", index=False)
    return {"baseline_late_rate": round(d["is_late"].mean(), 3), "rules_found": len(keep),
            "top_rules": keep.head(8).to_dict("records")}


def main():
    df = load()
    results = {"classification": classify(df), "regression": regress(df),
               "clustering": cluster(df), "association_rules": rules(df)}
    (OUT / "mining_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
