#!/usr/bin/env python3
"""
01_standardise_financials.py
Multi-company financial integration pipeline  ->  Power BI star schema (CSV)

STRUCTURE
  1. Standard schema        DIM_LINES: the single chart of accounts every company is mapped to
  2. Adapters (EXTRACT)     one class per company; each knows ONLY how to read its own files and
                            returns data in a common "staging" shape
  3. Standardiser (TRANSFORM) shared logic: map lines -> sign convention -> aggregate -> FX -> validate
  4. Writer (LOAD)          fact_financials, fact_product_sales, fact_vendor_performance,
                            dim_company, dim_line_item, dim_product_assumptions, dq_report

To onboard company #5: write one new Adapter subclass and add it to ADAPTERS. Nothing else changes.

Staging shapes returned by adapters
  statement : month | scenario ('Actual'/'Budget') | source_line | amount        (native sign & currency)
  products  : month | product | units | avg_price | revenue | direct_cost
  vendors   : month | vendor | spend | on_time_pct (0-1) | defect_rate (0-1)
"""
from __future__ import annotations

import argparse
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

log = logging.getLogger("pipeline")
REPORTING_CCY = "USD"
TOLERANCE = 1.0          # currency units, for control-total reconciliation
REV_TOLERANCE = 0.005    # 0.5% for product-vs-P&L revenue reconciliation

# =====================================================================================
# 1. STANDARD SCHEMA
# =====================================================================================
DIM_LINES = pd.DataFrame(
    [  # line_std, line_name, statement, line_type, line_group, sort_order
        ("REVENUE", "Revenue", "P&L", "Revenue", "Revenue", 10),
        ("COGS", "Cost of Goods Sold", "P&L", "Expense", "Cost of Sales", 20),
        ("PAYROLL", "Payroll", "P&L", "Expense", "Operating Expenses", 30),
        ("RENT", "Rent & Facilities", "P&L", "Expense", "Operating Expenses", 31),
        ("MARKETING", "Marketing", "P&L", "Expense", "Operating Expenses", 32),
        ("UTILITIES", "Utilities", "P&L", "Expense", "Operating Expenses", 33),
        ("OTHER_OPEX", "Other Operating Expenses", "P&L", "Expense", "Operating Expenses", 34),
        ("DA", "Depreciation & Amortisation", "P&L", "Expense", "Below EBITDA", 40),
        ("INTEREST", "Interest", "P&L", "Expense", "Below EBITDA", 41),
        ("TAX", "Income Tax", "P&L", "Expense", "Below EBITDA", 42),
        ("CF_OPERATING", "Operating Cash Flow", "Cash Flow", "CashFlow", "Cash Flow", 50),
        ("CF_CAPEX", "Capital Expenditure", "Cash Flow", "CashFlow", "Cash Flow", 51),
        ("CF_FINANCING", "Financing Cash Flow", "Cash Flow", "CashFlow", "Cash Flow", 52),
        ("CASH_CLOSING", "Closing Cash Balance", "Cash Flow", "Balance", "Cash Balance", 53),
    ],
    columns=["line_std", "line_name", "statement", "line_type", "line_group", "sort_order"],
)
# Conventions after standardisation:
#   P&L: revenue AND expenses are POSITIVE numbers (DAX subtracts).  Cash flow: inflow +, outflow -.
#   Closing cash is a balance (non-additive) -> use LASTNONBLANK in DAX.

# Default what-if assumptions seeded per company (editable in the CSV / a Power BI table)
DEFAULT_ELASTICITY = {"C1": -1.4, "C2": -0.7, "C3": -1.0, "C4": -1.8}


# =====================================================================================
# 2. ADAPTERS (EXTRACT) - one per company
# =====================================================================================
class Adapter(ABC):
    company_id: str
    company_name: str
    currency: str
    sector: str
    # 'natural'         : revenue & expenses already positive
    # 'credit_negative' : ledger style - revenue negative, expenses positive
    # 'expense_negative': expenses shown as negatives
    sign_convention: str
    # source label -> standard line.  'IGNORE' drops subtotals; 'CTRL_*' keeps them for reconciliation only.
    line_map: dict[str, str]

    def __init__(self, src: Path):
        self.src = src
        self.issues: list[dict] = []

    def issue(self, check: str, severity: str, detail: str):
        self.issues.append(dict(company_id=self.company_id, check=check, severity=severity, detail=detail))

    @abstractmethod
    def extract_pl(self) -> pd.DataFrame: ...
    @abstractmethod
    def extract_cashflow(self) -> pd.DataFrame: ...
    @abstractmethod
    def extract_products(self) -> pd.DataFrame: ...
    @abstractmethod
    def extract_vendors(self) -> pd.DataFrame: ...


def month_start(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.to_period("M").dt.to_timestamp()


# ---------------------------------------------------------------- C1: wide Excel, positive costs
class NorthbridgeAdapter(Adapter):
    company_id, company_name, currency, sector = "C1", "Northbridge Retail Ltd", "USD", "Retail"
    sign_convention = "natural"
    FILE = "co1_northbridge_mgmt_accounts.xlsx"
    line_map = {
        "Net Sales": "REVENUE", "Cost of Sales": "COGS", "Gross Profit": "CTRL_GROSS_PROFIT",
        "Wages & Salaries": "PAYROLL", "Store Rent & Rates": "RENT", "Advertising & Promotion": "MARKETING",
        "Utilities": "UTILITIES", "General & Admin": "OTHER_OPEX", "Depreciation": "DA",
        "Interest Expense": "INTEREST", "Income Tax": "TAX", "Net Profit": "CTRL_NET_INCOME",
        "Cash from Operations": "CF_OPERATING", "Capital Expenditure": "CF_CAPEX",
        "Debt Repayments": "CF_FINANCING", "Closing Cash Balance": "CASH_CLOSING",
    }

    def _wide(self, sheet: str, scenario: str) -> pd.DataFrame:
        df = pd.read_excel(self.src / self.FILE, sheet_name=sheet, header=3)  # 3 title rows above header
        df = df.rename(columns={df.columns[0]: "source_line"}).dropna(subset=["source_line"])
        long = df.melt(id_vars="source_line", var_name="month", value_name="amount")
        long["month"] = pd.to_datetime(long["month"], format="%b-%y")
        long["scenario"] = scenario
        return long

    def extract_pl(self):
        return pd.concat([self._wide("P&L Actual", "Actual"), self._wide("P&L Budget", "Budget")])

    def extract_cashflow(self):
        df = self._wide("Cash Flow", "Actual")
        # This company shows capex / debt repayments as positive outflows -> standard is outflow negative
        df.loc[df["source_line"].isin(["Capital Expenditure", "Debt Repayments"]), "amount"] *= -1
        return df

    def extract_products(self):
        d = pd.read_excel(self.src / self.FILE, sheet_name="Sales by Category")
        return pd.DataFrame({"month": month_start(d["Month"]), "product": d["Category"], "units": d["Units Sold"],
                             "avg_price": d["Unit Price"], "revenue": d["Net Sales"],
                             "direct_cost": d["Units Sold"] * d["Unit Cost"]})

    def extract_vendors(self):
        d = pd.read_excel(self.src / self.FILE, sheet_name="Supplier Scorecard")
        return pd.DataFrame({"month": month_start(d["Month"]), "vendor": d["Supplier"], "spend": d["Spend"],
                             "on_time_pct": d["On-Time %"] / 100, "defect_rate": d["Defect %"] / 100})


# ---------------------------------------------------------------- C2: CSV general-ledger export
class HelixAdapter(Adapter):
    company_id, company_name, currency, sector = "C2", "Helix Manufacturing plc", "GBP", "Manufacturing"
    sign_convention = "credit_negative"
    line_map = {
        "4000": "REVENUE", "5000": "COGS", "6100": "PAYROLL", "6200": "RENT", "6300": "MARKETING",
        "6400": "UTILITIES", "6500": "OTHER_OPEX", "7000": "DA", "7100": "INTEREST", "7200": "TAX",
        "Net cash from operating activities": "CF_OPERATING", "Purchase of PPE": "CF_CAPEX",
        "Loan repayments": "CF_FINANCING", "Cash at end of period": "CASH_CLOSING",
    }

    def extract_pl(self):
        gl = pd.read_csv(self.src / "co2_helix_gl_export.csv", dtype={"Period": str, "AcctNo": str})
        before = len(gl)
        gl = gl.drop_duplicates()
        if len(gl) < before:
            self.issue("duplicate_rows", "WARN", f"Removed {before - len(gl)} exact duplicate GL rows")
        return pd.DataFrame({"month": pd.to_datetime(gl["Period"], format="%Y%m"),
                             "scenario": gl["Ver"].map({"A": "Actual", "B": "Budget"}),
                             "source_line": gl["AcctNo"], "amount": gl["NetAmount"]})

    def extract_cashflow(self):
        d = pd.read_csv(self.src / "co2_helix_cashflow.csv", dtype={"period": str})
        return pd.DataFrame({"month": pd.to_datetime(d["period"], format="%Y%m"), "scenario": "Actual",
                             "source_line": d["line"], "amount": d["amount"]})

    def extract_products(self):
        d = pd.read_csv(self.src / "co2_helix_sales_by_sku_group.csv", dtype={"period": str})
        return pd.DataFrame({"month": pd.to_datetime(d["period"], format="%Y%m"), "product": d["sku_group"],
                             "units": d["qty"], "avg_price": d["avg_price"], "revenue": d["sales_gbp"],
                             "direct_cost": d["direct_cost_gbp"]})

    def extract_vendors(self):
        d = pd.read_csv(self.src / "co2_helix_supplier_spend.csv", dtype={"period": str})
        return pd.DataFrame({"month": pd.to_datetime(d["period"], format="%Y%m"), "vendor": d["supplier"],
                             "spend": d["po_value"], "on_time_pct": d["on_time_pct"] / 100,
                             "defect_rate": d["defect_rate_pct"] / 100})


# ---------------------------------------------------------------- C3: side-by-side Act|Bud, negative costs
class SolsticeAdapter(Adapter):
    company_id, company_name, currency, sector = "C3", "Solstice Software Inc.", "USD", "SaaS"
    sign_convention = "expense_negative"
    FILE = "co3_solstice_financials.xlsx"
    line_map = {  # two revenue lines & two COGS lines collapse into one standard line each
        "Subscription Revenue": "REVENUE", "Professional Services Revenue": "REVENUE", "Total Revenue": "IGNORE",
        "Hosting & Support Costs": "COGS", "Services Delivery Costs": "COGS", "Gross Profit": "CTRL_GROSS_PROFIT",
        "Staff Costs": "PAYROLL", "Office & Facilities": "RENT", "Marketing & Sales Programs": "MARKETING",
        "Software & Utilities": "UTILITIES", "Other Operating Costs": "OTHER_OPEX", "Total Opex": "IGNORE",
        "EBITDA": "CTRL_EBITDA", "Amortisation of Capitalised Software": "DA", "Finance Costs": "INTEREST",
        "Income Taxes": "TAX", "Net Income": "CTRL_NET_INCOME",
        "Operating Cash Flow": "CF_OPERATING", "Capex & Capitalised Dev": "CF_CAPEX",
        "Debt Service": "CF_FINANCING", "Ending Cash": "CASH_CLOSING",
    }

    def extract_pl(self):
        df = pd.read_excel(self.src / self.FILE, sheet_name="P&L", header=2)
        df = df.rename(columns={"Account": "source_line"})
        long = df.melt(id_vars="source_line", var_name="col", value_name="amount")
        parts = long["col"].str.extract(r"^(?P<m>\w{3} \d{2})\s*\|\s*(?P<s>Act|Bud)$")
        long["month"] = pd.to_datetime(parts["m"], format="%b %y")
        long["scenario"] = parts["s"].map({"Act": "Actual", "Bud": "Budget"})
        return long.drop(columns="col")  # section-header rows have NaN amounts -> dropped downstream

    def extract_cashflow(self):
        df = pd.read_excel(self.src / self.FILE, sheet_name="Cash Flow", header=2)
        df = df.rename(columns={"Account": "source_line"})
        long = df.melt(id_vars="source_line", var_name="month", value_name="amount")
        long["month"] = pd.to_datetime(long["month"], format="%b %y")
        long["scenario"] = "Actual"
        return long

    def extract_products(self):
        d = pd.read_excel(self.src / self.FILE, sheet_name="Plans & Pricing")
        return pd.DataFrame({"month": month_start(d["Month"]), "product": d["Plan"], "units": d["Seats"],
                             "avg_price": d["ARPU (monthly)"], "revenue": d["Revenue"],
                             "direct_cost": d["Cost to Serve"]})

    def extract_vendors(self):
        d = pd.read_excel(self.src / self.FILE, sheet_name="Vendors")
        return pd.DataFrame({"month": month_start(d["Month"]), "vendor": d["Vendor"], "spend": d["Spend"],
                             "on_time_pct": d["SLA Met %"], "defect_rate": d["Incident Rate"]})


# ---------------------------------------------------------------- C4: German trial balance, ; and decimal comma
class KesslerAdapter(Adapter):
    company_id, company_name, currency, sector = "C4", "Kessler Foods GmbH", "EUR", "Food Distribution"
    sign_convention = "credit_negative"   # amount = Soll - Haben -> revenue negative
    csv_kw = dict(sep=";", decimal=",", encoding="utf-8")
    line_map = {
        "8400": "REVENUE", "3400": "COGS", "4100": "PAYROLL", "4210": "RENT", "4600": "MARKETING",
        "4240": "UTILITIES", "4900": "OTHER_OPEX", "4830": "DA", "2100": "INTEREST", "4950": "TAX",
        "1200": "IGNORE",  # balancing bank/receivables account
        "Operativer Cashflow": "CF_OPERATING", "Investitionen": "CF_CAPEX",
        "Finanzierung": "CF_FINANCING", "Kassenbestand": "CASH_CLOSING",
    }

    def extract_pl(self):
        tb = pd.read_csv(self.src / "co4_kessler_saldenliste.csv", dtype={"Konto": str}, **self.csv_kw)
        tb[["Soll", "Haben"]] = tb[["Soll", "Haben"]].fillna(0)
        bal = tb.groupby(["Periode", "Szenario"])[["Soll", "Haben"]].sum()
        off = (bal["Soll"] - bal["Haben"]).abs()
        if (off > 0.01).any():
            self.issue("tb_balance", "ERROR", f"Trial balance out of balance in {int((off > 0.01).sum())} period(s)")
        else:
            self.issue("tb_balance", "INFO", "Trial balance balances (Soll = Haben) in every period")
        return pd.DataFrame({"month": pd.to_datetime(tb["Periode"], format="%Y-%m"),
                             "scenario": tb["Szenario"].map({"Ist": "Actual", "Plan": "Budget"}),
                             "source_line": tb["Konto"], "amount": tb["Soll"] - tb["Haben"]})

    def extract_cashflow(self):
        d = pd.read_csv(self.src / "co4_kessler_cashflow.csv", **self.csv_kw)
        return pd.DataFrame({"month": pd.to_datetime(d["Periode"], format="%Y-%m"), "scenario": "Actual",
                             "source_line": d["Kennzahl"], "amount": d["Betrag"]})

    def extract_products(self):
        d = pd.DataFrame(json.loads((self.src / "co4_kessler_umsatz_produkte.json").read_text(encoding="utf-8")))
        return pd.DataFrame({"month": pd.to_datetime(d["period"], format="%Y-%m"), "product": d["produkt"],
                             "units": d["menge"], "avg_price": d["preis_eur"], "revenue": d["umsatz_eur"],
                             "direct_cost": d["wareneinsatz_eur"]})

    def extract_vendors(self):
        d = pd.read_csv(self.src / "co4_kessler_lieferanten.csv", **self.csv_kw)
        return pd.DataFrame({"month": pd.to_datetime(d["Periode"], format="%Y-%m"), "vendor": d["Lieferant"],
                             "spend": d["Einkaufsvolumen"], "on_time_pct": d["Termintreue"],
                             "defect_rate": d["Reklamationsquote"]})


ADAPTERS: list[type[Adapter]] = [NorthbridgeAdapter, HelixAdapter, SolsticeAdapter, KesslerAdapter]


# =====================================================================================
# 3. STANDARDISER (TRANSFORM) - shared by every company
# =====================================================================================
def standardise_statement(a: Adapter, raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (standardised lines, control totals)."""
    raw = raw.copy()
    raw["source_line"] = raw["source_line"].astype(str).str.strip()
    raw["amount"] = pd.to_numeric(raw["amount"], errors="coerce")
    raw = raw.dropna(subset=["amount", "month", "scenario"])          # section headers / blanks

    unmapped = sorted(set(raw["source_line"]) - set(a.line_map))
    for u in unmapped:
        a.issue("unmapped_line", "ERROR", f"Source line '{u}' has no mapping - excluded")
    raw = raw[raw["source_line"].isin(a.line_map)]
    raw["line_std"] = raw["source_line"].map(a.line_map)
    raw = raw[raw["line_std"] != "IGNORE"]

    controls = raw[raw["line_std"].str.startswith("CTRL_")]
    raw = raw[~raw["line_std"].str.startswith("CTRL_")].merge(DIM_LINES[["line_std", "line_type"]], on="line_std")

    if a.sign_convention == "credit_negative":
        raw.loc[raw["line_type"] == "Revenue", "amount"] *= -1
    elif a.sign_convention == "expense_negative":
        raw.loc[raw["line_type"] == "Expense", "amount"] *= -1

    std = raw.groupby(["month", "scenario", "line_std"], as_index=False)["amount"].sum()
    return std, controls[["month", "scenario", "line_std", "amount"]]


def reconcile_controls(a: Adapter, pl: pd.DataFrame, controls: pd.DataFrame):
    """Rebuild subtotals from standardised lines and compare to what the company reported."""
    if controls.empty:
        return
    p = pl.pivot_table(index=["month", "scenario"], columns="line_std", values="amount", aggfunc="sum").fillna(0)
    opex = p[["PAYROLL", "RENT", "MARKETING", "UTILITIES", "OTHER_OPEX"]].sum(axis=1)
    rebuilt = {
        "CTRL_GROSS_PROFIT": p["REVENUE"] - p["COGS"],
        "CTRL_EBITDA": p["REVENUE"] - p["COGS"] - opex,
        "CTRL_NET_INCOME": p["REVENUE"] - p["COGS"] - opex - p["DA"] - p["INTEREST"] - p["TAX"],
    }
    for ctrl, grp in controls.groupby("line_std"):
        rep = grp.set_index(["month", "scenario"])["amount"]
        diff = (rebuilt[ctrl].reindex(rep.index) - rep).abs()
        n_bad = int((diff > TOLERANCE).sum())
        sev, msg = ("ERROR", f"{ctrl}: {n_bad} month(s) differ from reported by > {TOLERANCE}") if n_bad else \
                   ("INFO", f"{ctrl} reconciles to reported figures")
        a.issue("control_total", sev, msg)


def reconcile_products(a: Adapter, pl: pd.DataFrame, prod: pd.DataFrame):
    rev_pl = pl[(pl.scenario == "Actual") & (pl.line_std == "REVENUE")].set_index("month")["amount"]
    rev_pr = prod.groupby("month")["revenue"].sum()
    rel = ((rev_pr - rev_pl).abs() / rev_pl).dropna()
    n_bad = int((rel > REV_TOLERANCE).sum())
    if n_bad:
        a.issue("product_vs_pl_revenue", "ERROR", f"{n_bad} month(s): product revenue != P&L revenue (>0.5%)")
    else:
        a.issue("product_vs_pl_revenue", "INFO", "Product-level revenue ties to P&L revenue every month")


def load_fx(src: Path) -> pd.DataFrame:
    fx = pd.read_csv(src / "fx_rates.csv", parse_dates=["month"])
    return fx.rename(columns={"rate_to_usd": "fx_rate"})


def add_fx(a: Adapter, df: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    rates = fx[fx["currency"] == a.currency].set_index("month")["fx_rate"]
    df = df.copy()
    df["fx_rate"] = df["month"].map(rates)
    if df["fx_rate"].isna().any():
        a.issue("fx_missing", "ERROR", f"{int(df['fx_rate'].isna().sum())} rows without FX rate")
    return df


def finalise_financials(a: Adapter, std: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    df = add_fx(a, std.rename(columns={"amount": "amount_local"}), fx)
    df["amount_usd"] = (df["amount_local"] * df["fx_rate"]).round(2)
    df.insert(0, "company_id", a.company_id)
    df["currency"] = a.currency
    return df.merge(DIM_LINES[["line_std", "statement"]], on="line_std")[
        ["company_id", "month", "scenario", "statement", "line_std", "currency",
         "amount_local", "fx_rate", "amount_usd"]]


def finalise_products(a: Adapter, prod: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    df = add_fx(a, prod, fx)
    df.insert(0, "company_id", a.company_id)
    df["product_key"] = a.company_id + "|" + df["product"].str.strip()
    df["price_usd"] = (df["avg_price"] * df["fx_rate"]).round(4)
    df["unit_cost_usd"] = (df["direct_cost"] / df["units"] * df["fx_rate"]).round(4)
    df["revenue_usd"] = (df["revenue"] * df["fx_rate"]).round(2)
    df["direct_cost_usd"] = (df["direct_cost"] * df["fx_rate"]).round(2)
    return df[["company_id", "product_key", "product", "month", "units", "price_usd", "unit_cost_usd",
               "revenue_usd", "direct_cost_usd", "fx_rate"]]


def finalise_vendors(a: Adapter, vend: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    df = add_fx(a, vend, fx)
    df.insert(0, "company_id", a.company_id)
    df["spend_usd"] = (df["spend"] * df["fx_rate"]).round(2)
    return df[["company_id", "vendor", "month", "spend_usd", "on_time_pct", "defect_rate"]]


# =====================================================================================
# 4. WRITER (LOAD)
# =====================================================================================
def run(src: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    fx = load_fx(src)
    fin, prods, vends, dims, issues = [], [], [], [], []

    for cls in ADAPTERS:
        a = cls(src)
        log.info("Processing %s (%s)", a.company_name, a.company_id)
        pl, controls = standardise_statement(a, a.extract_pl())
        cf, _ = standardise_statement(a, a.extract_cashflow())
        reconcile_controls(a, pl, controls)

        prod = a.extract_products()
        reconcile_products(a, pl, prod)

        fin.append(finalise_financials(a, pd.concat([pl, cf]), fx))
        prods.append(finalise_products(a, prod, fx))
        vends.append(finalise_vendors(a, a.extract_vendors(), fx))
        dims.append(dict(company_id=a.company_id, company_name=a.company_name,
                         source_currency=a.currency, sector=a.sector))
        issues += a.issues

    fact_fin = pd.concat(fin).sort_values(["company_id", "month", "scenario", "line_std"])
    fact_prod = pd.concat(prods)
    fact_vend = pd.concat(vends)

    assumptions = (fact_prod[["company_id", "product_key", "product"]].drop_duplicates()
                   .assign(price_elasticity=lambda d: d["company_id"].map(DEFAULT_ELASTICITY)))

    fact_fin.to_csv(out / "fact_financials.csv", index=False)        # <- the single master CSV
    fact_prod.to_csv(out / "fact_product_sales.csv", index=False)
    fact_vend.to_csv(out / "fact_vendor_performance.csv", index=False)
    pd.DataFrame(dims).to_csv(out / "dim_company.csv", index=False)
    DIM_LINES.to_csv(out / "dim_line_item.csv", index=False)
    assumptions.to_csv(out / "dim_product_assumptions.csv", index=False)
    pd.DataFrame(issues).to_csv(out / "dq_report.csv", index=False)

    errors = [i for i in issues if i["severity"] == "ERROR"]
    log.info("Wrote %d financial rows, %d product rows, %d vendor rows -> %s",
             len(fact_fin), len(fact_prod), len(fact_vend), out)
    log.info("Data-quality: %d error(s), %d warning(s)", len(errors),
             sum(i["severity"] == "WARN" for i in issues))
    if errors:
        raise SystemExit("Pipeline finished with data-quality ERRORS - see dq_report.csv")


if __name__ == "__main__":
    here = Path(__file__).parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=here / "source_data")
    ap.add_argument("--out", type=Path, default=here / "output")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    run(args.src, args.out)
