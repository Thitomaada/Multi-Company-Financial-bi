# Multi-Company-Financial-bi

# Multi-Company Financial Integration & Interactive Management Dashboard

An end-to-end BI project: four companies' management accounts, each in a different format, are standardised with
**Python** into one star schema, then analysed in **Power BI** with a **what-if Scenario Lab** where users test price, cost
and volume changes and see the effect on profit and cash.

**Live demo (interactive web version):** https://thato-monyaki.github.io/Multi-Company-Financial-bi/

> All data is **synthetic**. The companies and figures are fictional and were generated for this project.

![Workflow](docs/workflow.png) ![alt text](image.png)

---

## The problem
Group management receives accounts from four entities. Each uses a different file format, chart of accounts, sign
convention and currency, so comparing them is slow and error-prone, and there is no way to test a pricing or cost decision
before making it.

## The solution
| Stage | What happens | Tools |
|---|---|---|
| 1. Ingest | Four sets of accounts in four formats: wide Excel, CSV general ledger, Excel with Actual/Budget side by side, German trial balance (`;` delimiter, decimal commas) plus JSON | Excel, CSV, JSON |
| 2. Standardise | One adapter per company maps accounts to a unified chart of accounts, fixes sign conventions, converts GBP and EUR to USD, removes duplicates | Python, pandas |
| 3. Validate | Automated checks rebuild reported subtotals, confirm trial balances balance, and tie product-level revenue to the P&L; results go to a data-quality report | Python |
| 4. Model | Star schema with fact tables (financials, product sales, vendor performance) and dimensions | Power BI, DAX |
| 5. Analyse | KPIs, pricing deep dive, Scenario Lab, operations and vendor views | Power BI |

## Companies
| Company | Sector | Currency | Source format |
|---|---|---|---|
| Northbridge Retail | Retail | USD | Wide Excel workbook with subtotal rows |
| Helix Manufacturing | Manufacturing | GBP | CSV general-ledger export, credits negative, duplicate rows |
| Solstice Software | SaaS | USD | Excel, Actual and Budget side by side, costs negative |
| Kessler Foods | Food distribution | EUR | German trial balance (Soll/Haben) plus JSON product sales |

## Features
- **Core KPIs:** gross profit and margin, EBITDA and margin, revenue growth, closing cash.
- **Pricing Deep Dive:** price versus unit cost by product, with editable price elasticity.
- **Scenario Lab:** sliders for price, COGS and volume changes drive live results for revenue, gross profit, EBITDA and operating cash flow.
  - EBITDA **waterfall** (price, volume and COGS effects)
  - Price × COGS **sensitivity heat-map**
  - **Break-even** price change
  - Saved Base, Downside and Upside bookmarks
- **Operations:** cash flow, budget versus actual, vendor on-time delivery and defect rates.
- **Typed what-if input** (demo): type *"What if COGS rises 5% at Kessler?"* and the dashboard sets the filters and sliders. See
  [`USER_INPUT_GUIDE.md`](USER_INPUT_GUIDE.md) for the HTML, Streamlit and Fabric (translytical task flow) options.

## What the data shows
Headline results from the sample data (Jan 2025 to Sep 2026, USD):
- Group revenue is about **$182M**, with a **41.3%** gross margin and a thin **4.7%** EBITDA margin.
- Revenue is up about **12%** year to date, led by Solstice (SaaS, +21.5%).
- **Kessler** is the risk: 1.9% EBITDA margin and negative closing cash by September 2026.
- **Helix** is squeezed: unit costs have risen faster than prices.
- In the scenarios, a 5% rise in COGS costs far more EBITDA than a 1% price rise adds.

Scenario results depend on placeholder price-elasticity assumptions (editable in `dim_product_assumptions.csv`), not on
observed demand.

## Repository structure
```
├── 00_generate_source_data.py     # builds the 4 synthetic source datasets
├── 01_standardise_financials.py   # the pipeline (adapters, mapping, FX, validation)
├── source_data/                   # the four companies' raw files + fx_rates.csv
├── output/                        # standardised CSVs loaded into Power BI
├── streamlit_app/                 # companion app: typed what-if input
├── powerbi/                       # .pbix / .pbip report
├── docs/                          # screenshots, project brief, measure documentation
├── POWERBI_SCENARIO_GUIDE.md      # build guide and DAX
├── USER_INPUT_GUIDE.md            # typed-input options
├── HTML_Content_Measure.md        # HTML Content visual measure
└── PBI_REPORT_BUILD_SPEC.md       # page-by-page report build sheet
```

## How to run
**Requirements:** Python 3.10+, Power BI Desktop (Windows).

```bash
pip install pandas numpy openpyxl
python 00_generate_source_data.py       # optional: regenerates source_data/
python 01_standardise_financials.py     # writes standardised CSVs to output/
```
Then in Power BI Desktop, load the CSVs from `output/` and follow `POWERBI_SCENARIO_GUIDE.md`. The pipeline exits with an
error if any data-quality check fails; see `output/dq_report.csv`.


## Tech stack
Python (pandas, openpyxl) · Power BI and DAX · HTML/JavaScript · Streamlit · Microsoft Fabric (design) · GitHub Pages

## Project status
- **Done:** Python pipeline (tested, no data-quality errors), semantic model and measures, web dashboard, Streamlit app, documentation.
- **Design only, not yet run:** Fabric translytical task flow for write-back.
- **Known limits:** operating expenses are held fixed in scenarios, demand response is linear, and the tax rate in the cash estimate is a fixed 25%.

Full measure definitions are in [`docs/Power_BI_Scenario_Lab_Measure_Documentation_Combined.docx`](docs/).

## Author
**Thato Monyaki**: [LinkedIn](https://www.linkedin.com/) · [GitHub](https://github.com/thato-monyaki)
