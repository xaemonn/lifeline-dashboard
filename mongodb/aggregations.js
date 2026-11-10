// =====================================================================
// SCMS Lifeline - MongoDB aggregation pipelines (run in mongosh)
//   mongosh "mongodb://localhost:27017/scms_lifeline" mongodb/aggregations.js
// Each pipeline answers one research question from the report.
// =====================================================================
const db_ = db.getSiblingDB("scms_lifeline");
const show = (title, cursor) => { print("\n=== " + title + " ==="); printjson(cursor.toArray()); };

// ---------- Q1  Overall KPI card ----------
show("Q1 Overall KPIs", db_.shipments.aggregate([
  { $group: {
      _id: null,
      line_items: { $sum: 1 },
      shipments: { $addToSet: "$refs.asn_dn" },
      value_usd: { $sum: "$cost.line_value" },
      freight_usd: { $sum: { $ifNull: ["$cost.freight_alloc", 0] } },
      late: { $sum: { $cond: ["$kpi.is_late", 1, 0] } },
      avg_delay_when_late: { $avg: { $cond: ["$kpi.is_late", "$kpi.delay_days", null] } } } },
  { $project: { _id: 0, line_items: 1, shipments: { $size: "$shipments" },
      value_musd: { $round: [{ $divide: ["$value_usd", 1e6] }, 1] },
      freight_musd: { $round: [{ $divide: ["$freight_usd", 1e6] }, 1] },
      on_time_pct: { $round: [{ $multiply: [{ $subtract: [1, { $divide: ["$late", "$line_items"] }] }, 100] }, 1] },
      avg_delay_when_late: { $round: ["$avg_delay_when_late", 1] } } }
]));

// ---------- Q2  Late rate by shipment mode and fulfilment route (RQ1) ----------
show("Q2 Late rate by mode x route", db_.shipments.aggregate([
  { $group: { _id: { mode: "$fulfilment.mode", via: "$fulfilment.via" },
      n: { $sum: 1 }, late_rate: { $avg: { $cond: ["$kpi.is_late", 1, 0] } } } },
  { $match: { n: { $gte: 30 } } },
  { $sort: { late_rate: -1 } }
]));

// ---------- Q3  Countries with the worst delivery reliability (RQ1) ----------
show("Q3 Country risk (min 50 line items)", db_.shipments.aggregate([
  { $group: { _id: "$destination.country", n: { $sum: 1 },
      late_rate: { $avg: { $cond: ["$kpi.is_late", 1, 0] } },
      p90_delay: { $percentile: { input: "$kpi.delay_days", p: [0.9], method: "approximate" } } } },
  { $match: { n: { $gte: 50 } } },
  { $sort: { late_rate: -1 } }, { $limit: 10 }
]));

// ---------- Q4  Lead-time decomposition for Direct Drop orders (RQ2) ----------
show("Q4 Lead time by vendor (Direct Drop)", db_.shipments.aggregate([
  { $match: { "fulfilment.via": "Direct Drop", "kpi.lead_time_days": { $ne: null } } },
  { $group: { _id: "$vendor.short_name", n: { $sum: 1 },
      median_lead: { $median: { input: "$kpi.lead_time_days", method: "approximate" } },
      median_pq_to_po: { $median: { input: "$kpi.pq_to_po_days", method: "approximate" } } } },
  { $match: { n: { $gte: 30 } } }, { $sort: { median_lead: -1 } }
]));

// ---------- Q5  Freight cost per kg by mode and region (RQ3) ----------
show("Q5 Freight per kg", db_.shipments.aggregate([
  { $match: { "cost.freight_alloc": { $gt: 0 }, "logistics.weight_kg_alloc": { $gt: 0 } } },
  { $group: { _id: { mode: "$fulfilment.mode", region: "$destination.region" },
      n: { $sum: 1 },
      freight: { $sum: "$cost.freight_alloc" }, kg: { $sum: "$logistics.weight_kg_alloc" },
      median_fpk: { $median: { input: "$logistics.freight_per_kg", method: "approximate" } } } },
  { $set: { weighted_fpk: { $round: [{ $divide: ["$freight", "$kg"] }, 2] } } },
  { $match: { n: { $gte: 20 } } }, { $sort: { weighted_fpk: -1 } }
]));

// ---------- Q6  How freight is recorded (data governance) ----------
show("Q6 Freight status mix", db_.shipments.aggregate([
  { $sortByCount: "$cost.freight_status" }
]));

// ---------- Q7  Vendor concentration / Pareto (RQ6) ----------
show("Q7 Vendor Pareto", db_.shipments.aggregate([
  { $group: { _id: "$vendor.short_name", value: { $sum: "$cost.line_value" } } },
  { $setWindowFields: { sortBy: { value: -1 }, output: {
      cum_value: { $sum: "$value", window: { documents: ["unbounded", "current"] } },
      total: { $sum: "$value", window: { documents: ["unbounded", "unbounded"] } },
      rank: { $rank: {} } } } },
  { $project: { rank: 1, value_musd: { $round: [{ $divide: ["$value", 1e6] }, 1] },
      cum_share: { $round: [{ $divide: ["$cum_value", "$total"] }, 3] } } },
  { $limit: 10 }
]));

// ---------- Q8  Spend by product group and year (RQ6) ----------
show("Q8 Spend by group x year", db_.shipments.aggregate([
  { $group: { _id: { year: { $year: "$dates.delivered" }, group: "$product.group" },
      value_musd: { $sum: { $divide: ["$cost.line_value", 1e6] } } } },
  { $sort: { "_id.year": 1, value_musd: -1 } }
]));

// ---------- Q9  Year-over-year late rate with a 3-year moving average ----------
show("Q9 Late rate trend", db_.shipments.aggregate([
  { $group: { _id: { $year: "$dates.delivered" }, n: { $sum: 1 },
      late_rate: { $avg: { $cond: ["$kpi.is_late", 1, 0] } } } },
  { $sort: { _id: 1 } },
  { $setWindowFields: { sortBy: { _id: 1 }, output: {
      ma3: { $avg: "$late_rate", window: { documents: [-2, 0] } } } } }
]));

// ---------- Q10  Price benchmarking: same molecule, different vendors ----------
show("Q10 Unit price spread by molecule", db_.shipments.aggregate([
  { $match: { "product.group": "ARV" } },
  { $group: { _id: { molecule: "$product.molecule", dosage: "$product.dosage", vendor: "$vendor.short_name" },
      avg_unit_price: { $avg: "$cost.unit_price" }, n: { $sum: 1 } } },
  { $group: { _id: { molecule: "$_id.molecule", dosage: "$_id.dosage" },
      vendors: { $push: { vendor: "$_id.vendor", price: { $round: ["$avg_unit_price", 3] }, n: "$n" } },
      min_p: { $min: "$avg_unit_price" }, max_p: { $max: "$avg_unit_price" } } },
  { $match: { "vendors.1": { $exists: true }, min_p: { $gt: 0 } } },
  { $set: { spread_x: { $round: [{ $divide: ["$max_p", "$min_p"] }, 1] } } },
  { $sort: { spread_x: -1 } }, { $limit: 10 }
]));

// ---------- Q11  Feature table export for the mining step (flat view) ----------
db_.ml_features.drop();
db_.shipments.aggregate([
  { $project: { _id: 1, country: "$destination.country", region: "$destination.region",
      mode: "$fulfilment.mode", via: "$fulfilment.via", inco: "$fulfilment.inco_term",
      vendor: "$vendor.short_name", group: "$product.group", sub: "$product.sub_class",
      form: "$product.form", first_line: "$product.first_line",
      month: { $month: "$dates.scheduled" }, year: { $year: "$dates.scheduled" },
      packs: "$quantity.packs", value: "$cost.line_value", weight: "$logistics.weight_kg",
      freight: "$cost.freight", is_late: "$kpi.is_late", delay: "$kpi.delay_days" } },
  { $out: "ml_features" }
]);
print("\nml_features rows: " + db_.ml_features.countDocuments());
