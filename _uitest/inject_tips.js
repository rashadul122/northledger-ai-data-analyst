(function(){var PLAN={
 "plan": {
  "goal": "What drives how much a table tips \u2014 does the tip track the size of the bill and the party, and does it differ by day, meal time, and smoker status?",
  "goal_candidates": [
   "Which day and meal time produce the largest bills and the largest tips per table?",
   "Do larger parties spend and tip more per bill, and how do the amounts scale with party size?",
   "Is the tip a stable fraction of the bill, or do smaller bills tip proportionally more?"
  ],
  "kind": "cross_section",
  "understanding": "tips.csv is a small observational restaurant dataset of 244 rows, one row per table/party that ran a bill. Each row records the bill total, the tip left, the party size, and the day, meal time, smoker status, and sex label recorded for the party. There is no date or bill id, so this is a cross-section of bills, not a time series, and tip percentage must be derived from tip \u00f7 total_bill.",
  "columns": [
   {
    "name": "total_bill",
    "semantic_type": "flow_amount",
    "role": "driver",
    "unit": "USD",
    "why": "the amount of the bill, the base the tip is measured against"
   },
   {
    "name": "tip",
    "semantic_type": "flow_amount",
    "role": "target",
    "unit": "USD",
    "why": "the amount tipped per table \u2014 the outcome the question is about"
   },
   {
    "name": "size",
    "semantic_type": "count",
    "role": "driver",
    "unit": "people",
    "why": "number of people at the table, expected to drive both bill and tip"
   },
   {
    "name": "day",
    "semantic_type": "category",
    "role": "segment",
    "unit": "",
    "why": "day of week the bill was run, a grouping for comparisons"
   },
   {
    "name": "time",
    "semantic_type": "category",
    "role": "segment",
    "unit": "",
    "why": "Lunch vs Dinner, a grouping for comparisons"
   },
   {
    "name": "smoker",
    "semantic_type": "boolean",
    "role": "segment",
    "unit": "",
    "why": "whether the party sat in the smoking section, a grouping"
   },
   {
    "name": "sex",
    "semantic_type": "category",
    "role": "segment",
    "unit": "",
    "why": "Male/Female group label on the bill, a segment not a personal identifier"
   }
  ],
  "operations": [
   {
    "op": "keep_columns",
    "columns": [
     "total_bill",
     "tip",
     "size",
     "day",
     "time",
     "smoker",
     "sex"
    ]
   },
   {
    "op": "not_personal",
    "columns": [
     "sex"
    ]
   }
  ],
  "analyses": [
   {
    "type": "relationship",
    "columns": [
     "total_bill",
     "tip"
    ],
    "why": "whether the tip rises with the size of the bill"
   },
   {
    "type": "compare",
    "columns": [
     "tip"
    ],
    "why": "tip levels across the four days",
    "by": "day"
   },
   {
    "type": "compare",
    "columns": [
     "tip"
    ],
    "why": "lunch versus dinner tipping",
    "by": "time"
   },
   {
    "type": "compare",
    "columns": [
     "tip"
    ],
    "why": "how the tip scales with party size",
    "by": "size"
   },
   {
    "type": "distribution",
    "columns": [
     "tip"
    ],
    "why": "spread and round-number pile-up of tip amounts"
   },
   {
    "type": "compare",
    "columns": [
     "total_bill"
    ],
    "why": "which days carry the biggest bills",
    "by": "day"
   }
  ],
  "primary": "tip",
  "quality_risks": [
   "No date column at all, so nothing here can be shown as a trend over time \u2014 only comparisons across groups.",
   "Tip is capped at 10 with a median of 2.90 and only 123 distinct values, so many tips are round amounts; this pile-up skews averages and any tip-percentage figure.",
   "There is no tip percentage column; it must be derived as tip \u00f7 total_bill, and small bills will give unstable, extreme percentages.",
   "Party size runs 1 to 6 people, so per-bill amounts mix parties of very different size unless size is held constant or per-person values are derived.",
   "Group cells are uneven and some (Friday, Lunch) are likely thin, so small-group differences may be noise.",
   "The sex field is flagged like personal data but holds only a Male/Female group label, so it is a segment, never a measure."
  ]
 },
 "model": "deepseek-flash"
};var of=window.fetch;window.fetch=function(u,o){if(String(u).indexOf('/plan')>=0){return Promise.resolve(new Response(JSON.stringify(PLAN),{status:200,headers:{'Content-Type':'application/json'}}));}return of.apply(this,arguments);};
var f=new File(["\"total_bill\",\"tip\",\"sex\",\"smoker\",\"day\",\"time\",\"size\"\n16.99,1.01,\"Female\",\"No\",\"Sun\",\"Dinner\",2\n10.34,1.66,\"Male\",\"No\",\"Sun\",\"Dinner\",3\n21.01,3.5,\"Male\",\"No\",\"Sun\",\"Dinner\",3\n23.68,3.31,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n24.59,3.61,\"Female\",\"No\",\"Sun\",\"Dinner\",4\n25.29,4.71,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n8.77,2,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n26.88,3.12,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n15.04,1.96,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n14.78,3.23,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n10.27,1.71,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n35.26,5,\"Female\",\"No\",\"Sun\",\"Dinner\",4\n15.42,1.57,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n18.43,3,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n14.83,3.02,\"Female\",\"No\",\"Sun\",\"Dinner\",2\n21.58,3.92,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n10.33,1.67,\"Female\",\"No\",\"Sun\",\"Dinner\",3\n16.29,3.71,\"Male\",\"No\",\"Sun\",\"Dinner\",3\n16.97,3.5,\"Female\",\"No\",\"Sun\",\"Dinner\",3\n20.65,3.35,\"Male\",\"No\",\"Sat\",\"Dinner\",3\n17.92,4.08,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n20.29,2.75,\"Female\",\"No\",\"Sat\",\"Dinner\",2\n15.77,2.23,\"Female\",\"No\",\"Sat\",\"Dinner\",2\n39.42,7.58,\"Male\",\"No\",\"Sat\",\"Dinner\",4\n19.82,3.18,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n17.81,2.34,\"Male\",\"No\",\"Sat\",\"Dinner\",4\n13.37,2,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n12.69,2,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n21.7,4.3,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n19.65,3,\"Female\",\"No\",\"Sat\",\"Dinner\",2\n9.55,1.45,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n18.35,2.5,\"Male\",\"No\",\"Sat\",\"Dinner\",4\n15.06,3,\"Female\",\"No\",\"Sat\",\"Dinner\",2\n20.69,2.45,\"Female\",\"No\",\"Sat\",\"Dinner\",4\n17.78,3.27,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n24.06,3.6,\"Male\",\"No\",\"Sat\",\"Dinner\",3\n16.31,2,\"Male\",\"No\",\"Sat\",\"Dinner\",3\n16.93,3.07,\"Female\",\"No\",\"Sat\",\"Dinner\",3\n18.69,2.31,\"Male\",\"No\",\"Sat\",\"Dinner\",3\n31.27,5,\"Male\",\"No\",\"Sat\",\"Dinner\",3\n16.04,2.24,\"Male\",\"No\",\"Sat\",\"Dinner\",3\n17.46,2.54,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n13.94,3.06,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n9.68,1.32,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n30.4,5.6,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n18.29,3,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n22.23,5,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n32.4,6,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n28.55,2.05,\"Male\",\"No\",\"Sun\",\"Dinner\",3\n18.04,3,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n12.54,2.5,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n10.29,2.6,\"Female\",\"No\",\"Sun\",\"Dinner\",2\n34.81,5.2,\"Female\",\"No\",\"Sun\",\"Dinner\",4\n9.94,1.56,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n25.56,4.34,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n19.49,3.51,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n38.01,3,\"Male\",\"Yes\",\"Sat\",\"Dinner\",4\n26.41,1.5,\"Female\",\"No\",\"Sat\",\"Dinner\",2\n11.24,1.76,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n48.27,6.73,\"Male\",\"No\",\"Sat\",\"Dinner\",4\n20.29,3.21,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n13.81,2,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n11.02,1.98,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n18.29,3.76,\"Male\",\"Yes\",\"Sat\",\"Dinner\",4\n17.59,2.64,\"Male\",\"No\",\"Sat\",\"Dinner\",3\n20.08,3.15,\"Male\",\"No\",\"Sat\",\"Dinner\",3\n16.45,2.47,\"Female\",\"No\",\"Sat\",\"Dinner\",2\n3.07,1,\"Female\",\"Yes\",\"Sat\",\"Dinner\",1\n20.23,2.01,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n15.01,2.09,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n12.02,1.97,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n17.07,3,\"Female\",\"No\",\"Sat\",\"Dinner\",3\n26.86,3.14,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n25.28,5,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n14.73,2.2,\"Female\",\"No\",\"Sat\",\"Dinner\",2\n10.51,1.25,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n17.92,3.08,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n27.2,4,\"Male\",\"No\",\"Thur\",\"Lunch\",4\n22.76,3,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n17.29,2.71,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n19.44,3,\"Male\",\"Yes\",\"Thur\",\"Lunch\",2\n16.66,3.4,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n10.07,1.83,\"Female\",\"No\",\"Thur\",\"Lunch\",1\n32.68,5,\"Male\",\"Yes\",\"Thur\",\"Lunch\",2\n15.98,2.03,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n34.83,5.17,\"Female\",\"No\",\"Thur\",\"Lunch\",4\n13.03,2,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n18.28,4,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n24.71,5.85,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n21.16,3,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n28.97,3,\"Male\",\"Yes\",\"Fri\",\"Dinner\",2\n22.49,3.5,\"Male\",\"No\",\"Fri\",\"Dinner\",2\n5.75,1,\"Female\",\"Yes\",\"Fri\",\"Dinner\",2\n16.32,4.3,\"Female\",\"Yes\",\"Fri\",\"Dinner\",2\n22.75,3.25,\"Female\",\"No\",\"Fri\",\"Dinner\",2\n40.17,4.73,\"Male\",\"Yes\",\"Fri\",\"Dinner\",4\n27.28,4,\"Male\",\"Yes\",\"Fri\",\"Dinner\",2\n12.03,1.5,\"Male\",\"Yes\",\"Fri\",\"Dinner\",2\n21.01,3,\"Male\",\"Yes\",\"Fri\",\"Dinner\",2\n12.46,1.5,\"Male\",\"No\",\"Fri\",\"Dinner\",2\n11.35,2.5,\"Female\",\"Yes\",\"Fri\",\"Dinner\",2\n15.38,3,\"Female\",\"Yes\",\"Fri\",\"Dinner\",2\n44.3,2.5,\"Female\",\"Yes\",\"Sat\",\"Dinner\",3\n22.42,3.48,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n20.92,4.08,\"Female\",\"No\",\"Sat\",\"Dinner\",2\n15.36,1.64,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n20.49,4.06,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n25.21,4.29,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n18.24,3.76,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n14.31,4,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n14,3,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n7.25,1,\"Female\",\"No\",\"Sat\",\"Dinner\",1\n38.07,4,\"Male\",\"No\",\"Sun\",\"Dinner\",3\n23.95,2.55,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n25.71,4,\"Female\",\"No\",\"Sun\",\"Dinner\",3\n17.31,3.5,\"Female\",\"No\",\"Sun\",\"Dinner\",2\n29.93,5.07,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n10.65,1.5,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n12.43,1.8,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n24.08,2.92,\"Female\",\"No\",\"Thur\",\"Lunch\",4\n11.69,2.31,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n13.42,1.68,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n14.26,2.5,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n15.95,2,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n12.48,2.52,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n29.8,4.2,\"Female\",\"No\",\"Thur\",\"Lunch\",6\n8.52,1.48,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n14.52,2,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n11.38,2,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n22.82,2.18,\"Male\",\"No\",\"Thur\",\"Lunch\",3\n19.08,1.5,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n20.27,2.83,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n11.17,1.5,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n12.26,2,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n18.26,3.25,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n8.51,1.25,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n10.33,2,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n14.15,2,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n16,2,\"Male\",\"Yes\",\"Thur\",\"Lunch\",2\n13.16,2.75,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n17.47,3.5,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n34.3,6.7,\"Male\",\"No\",\"Thur\",\"Lunch\",6\n41.19,5,\"Male\",\"No\",\"Thur\",\"Lunch\",5\n27.05,5,\"Female\",\"No\",\"Thur\",\"Lunch\",6\n16.43,2.3,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n8.35,1.5,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n18.64,1.36,\"Female\",\"No\",\"Thur\",\"Lunch\",3\n11.87,1.63,\"Female\",\"No\",\"Thur\",\"Lunch\",2\n9.78,1.73,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n7.51,2,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n14.07,2.5,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n13.13,2,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n17.26,2.74,\"Male\",\"No\",\"Sun\",\"Dinner\",3\n24.55,2,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n19.77,2,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n29.85,5.14,\"Female\",\"No\",\"Sun\",\"Dinner\",5\n48.17,5,\"Male\",\"No\",\"Sun\",\"Dinner\",6\n25,3.75,\"Female\",\"No\",\"Sun\",\"Dinner\",4\n13.39,2.61,\"Female\",\"No\",\"Sun\",\"Dinner\",2\n16.49,2,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n21.5,3.5,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n12.66,2.5,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n16.21,2,\"Female\",\"No\",\"Sun\",\"Dinner\",3\n13.81,2,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n17.51,3,\"Female\",\"Yes\",\"Sun\",\"Dinner\",2\n24.52,3.48,\"Male\",\"No\",\"Sun\",\"Dinner\",3\n20.76,2.24,\"Male\",\"No\",\"Sun\",\"Dinner\",2\n31.71,4.5,\"Male\",\"No\",\"Sun\",\"Dinner\",4\n10.59,1.61,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n10.63,2,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n50.81,10,\"Male\",\"Yes\",\"Sat\",\"Dinner\",3\n15.81,3.16,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n7.25,5.15,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n31.85,3.18,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n16.82,4,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n32.9,3.11,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n17.89,2,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n14.48,2,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n9.6,4,\"Female\",\"Yes\",\"Sun\",\"Dinner\",2\n34.63,3.55,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n34.65,3.68,\"Male\",\"Yes\",\"Sun\",\"Dinner\",4\n23.33,5.65,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n45.35,3.5,\"Male\",\"Yes\",\"Sun\",\"Dinner\",3\n23.17,6.5,\"Male\",\"Yes\",\"Sun\",\"Dinner\",4\n40.55,3,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n20.69,5,\"Male\",\"No\",\"Sun\",\"Dinner\",5\n20.9,3.5,\"Female\",\"Yes\",\"Sun\",\"Dinner\",3\n30.46,2,\"Male\",\"Yes\",\"Sun\",\"Dinner\",5\n18.15,3.5,\"Female\",\"Yes\",\"Sun\",\"Dinner\",3\n23.1,4,\"Male\",\"Yes\",\"Sun\",\"Dinner\",3\n15.69,1.5,\"Male\",\"Yes\",\"Sun\",\"Dinner\",2\n19.81,4.19,\"Female\",\"Yes\",\"Thur\",\"Lunch\",2\n28.44,2.56,\"Male\",\"Yes\",\"Thur\",\"Lunch\",2\n15.48,2.02,\"Male\",\"Yes\",\"Thur\",\"Lunch\",2\n16.58,4,\"Male\",\"Yes\",\"Thur\",\"Lunch\",2\n7.56,1.44,\"Male\",\"No\",\"Thur\",\"Lunch\",2\n10.34,2,\"Male\",\"Yes\",\"Thur\",\"Lunch\",2\n43.11,5,\"Female\",\"Yes\",\"Thur\",\"Lunch\",4\n13,2,\"Female\",\"Yes\",\"Thur\",\"Lunch\",2\n13.51,2,\"Male\",\"Yes\",\"Thur\",\"Lunch\",2\n18.71,4,\"Male\",\"Yes\",\"Thur\",\"Lunch\",3\n12.74,2.01,\"Female\",\"Yes\",\"Thur\",\"Lunch\",2\n13,2,\"Female\",\"Yes\",\"Thur\",\"Lunch\",2\n16.4,2.5,\"Female\",\"Yes\",\"Thur\",\"Lunch\",2\n20.53,4,\"Male\",\"Yes\",\"Thur\",\"Lunch\",4\n16.47,3.23,\"Female\",\"Yes\",\"Thur\",\"Lunch\",3\n26.59,3.41,\"Male\",\"Yes\",\"Sat\",\"Dinner\",3\n38.73,3,\"Male\",\"Yes\",\"Sat\",\"Dinner\",4\n24.27,2.03,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n12.76,2.23,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n30.06,2,\"Male\",\"Yes\",\"Sat\",\"Dinner\",3\n25.89,5.16,\"Male\",\"Yes\",\"Sat\",\"Dinner\",4\n48.33,9,\"Male\",\"No\",\"Sat\",\"Dinner\",4\n13.27,2.5,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n28.17,6.5,\"Female\",\"Yes\",\"Sat\",\"Dinner\",3\n12.9,1.1,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n28.15,3,\"Male\",\"Yes\",\"Sat\",\"Dinner\",5\n11.59,1.5,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n7.74,1.44,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n30.14,3.09,\"Female\",\"Yes\",\"Sat\",\"Dinner\",4\n12.16,2.2,\"Male\",\"Yes\",\"Fri\",\"Lunch\",2\n13.42,3.48,\"Female\",\"Yes\",\"Fri\",\"Lunch\",2\n8.58,1.92,\"Male\",\"Yes\",\"Fri\",\"Lunch\",1\n15.98,3,\"Female\",\"No\",\"Fri\",\"Lunch\",3\n13.42,1.58,\"Male\",\"Yes\",\"Fri\",\"Lunch\",2\n16.27,2.5,\"Female\",\"Yes\",\"Fri\",\"Lunch\",2\n10.09,2,\"Female\",\"Yes\",\"Fri\",\"Lunch\",2\n20.45,3,\"Male\",\"No\",\"Sat\",\"Dinner\",4\n13.28,2.72,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n22.12,2.88,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n24.01,2,\"Male\",\"Yes\",\"Sat\",\"Dinner\",4\n15.69,3,\"Male\",\"Yes\",\"Sat\",\"Dinner\",3\n11.61,3.39,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n10.77,1.47,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n15.53,3,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n10.07,1.25,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n12.6,1,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n32.83,1.17,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n35.83,4.67,\"Female\",\"No\",\"Sat\",\"Dinner\",3\n29.03,5.92,\"Male\",\"No\",\"Sat\",\"Dinner\",3\n27.18,2,\"Female\",\"Yes\",\"Sat\",\"Dinner\",2\n22.67,2,\"Male\",\"Yes\",\"Sat\",\"Dinner\",2\n17.82,1.75,\"Male\",\"No\",\"Sat\",\"Dinner\",2\n18.78,3,\"Female\",\"No\",\"Thur\",\"Dinner\",2\n"],'tips.csv',{type:'text/csv'});var dt=new DataTransfer();dt.items.add(f);var inp=document.getElementById('try-file');var pl=document.getElementById('try-plan');if(pl&&!pl.checked){pl.checked=true;pl.dispatchEvent(new Event('change',{bubbles:true}));}
inp.files=dt.files;inp.dispatchEvent(new Event('change',{bubbles:true}));return 'sent '+f.size+' plan box '+(pl&&pl.checked);})()