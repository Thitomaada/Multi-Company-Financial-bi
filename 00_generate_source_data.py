#!/usr/bin/env python3
"""
00_generate_source_data.py
Creates 4 fictional companies' management accounts, each in a DELIBERATELY different
format (the whole point of the integration project). Underlying numbers come from one
internally consistent "truth" model per company, so the pipeline can reconcile them.

  C1 Northbridge Retail    USD  .xlsx, wide months, costs positive, subtotal rows, DD-MMM-YY headers
  C2 Helix Manufacturing   GBP  .csv GL export (credits negative), account numbers, duplicate rows
  C3 Solstice Software     USD  .xlsx, "Jan 25 | Act/Bud" side-by-side, costs negative, section headers
  C4 Kessler Foods GmbH    EUR  German trial balance (Soll/Haben), ; delimiter, decimal commas, JSON products
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent / "source_data"
OUT.mkdir(parents=True, exist_ok=True)
MONTHS = pd.period_range("2025-01", "2026-09", freq="M")

# product tuple: (name, list price, unit cost, base monthly units, group)
CFG = {
    "C1": dict(seed=11, growth=0.006, season=0.12, price_drift=0.002, cost_drift=0.003,
               products=[("Apparel", 42, 23, 9000, "main"), ("Footwear", 85, 51, 4200, "main"),
                         ("Accessories", 25, 12, 12000, "main"), ("Home & Living", 60, 35, 3000, "main")],
               opex=dict(payroll=.17, rent=.06, marketing=.06, utilities=.015, other=.04),
               da=45000, interest=18000, open_cash=2_500_000, capex_pct=.025, repay=40000,
               vendors=[("Harbor Textiles", .34, .95, .012), ("Stride Footwear Co", .26, .91, .020),
                        ("PackRight", .12, .97, .008), ("Metro Logistics", .18, .84, .035),
                        ("HomeCraft Imports", .10, .89, .028)]),
    "C2": dict(seed=22, growth=0.004, season=0.05, price_drift=0.0015, cost_drift=0.0035,
               products=[("Industrial Valves", 310, 215, 1500, "main"), ("Pump Assemblies", 720, 520, 500, "main"),
                         ("Seals & Gaskets", 18, 11, 22000, "main"), ("Spare Parts Kits", 95, 66, 3500, "main")],
               opex=dict(payroll=.11, rent=.03, marketing=.02, utilities=.03, other=.04),
               da=60000, interest=30000, open_cash=1_800_000, capex_pct=.04, repay=55000,
               vendors=[("Sheffield Steel", .38, .93, .015), ("Midlands Castings", .22, .88, .030),
                        ("Precision Fasteners", .12, .96, .010), ("Northern Rubber", .10, .90, .022),
                        ("Euro Hydraulics GmbH", .18, .82, .040)]),
    "C3": dict(seed=33, growth=0.015, season=0.03, price_drift=0.001, cost_drift=0.001,
               products=[("Starter", 29, 6, 14000, "sub"), ("Professional", 89, 19, 7500, "sub"),
                         ("Enterprise", 420, 85, 1800, "sub"), ("Implementation Services", 150, 112, 2200, "svc")],
               opex=dict(payroll=.38, rent=.03, marketing=.17, utilities=.02, other=.05),
               da=80000, interest=12000, open_cash=3_200_000, capex_pct=.03, repay=20000,
               vendors=[("CloudHost Pro", .38, .99, .002), ("DataStream API", .15, .97, .004),
                        ("SupportDesk Outsourcing", .22, .92, .015), ("Contractor Pool", .25, .86, .030)]),
    "C4": dict(seed=44, growth=0.003, season=0.06, price_drift=0.0015, cost_drift=0.0025,
               products=[("Fresh Produce", 4.2, 3.3, 160000, "main"), ("Dry Goods", 2.6, 2.05, 220000, "main"),
                         ("Beverages", 1.9, 1.5, 300000, "main"), ("Frozen", 5.4, 4.4, 90000, "main")],
               opex=dict(payroll=.08, rent=.03, marketing=.01, utilities=.02, other=.035),
               da=70000, interest=25000, open_cash=900_000, capex_pct=.02, repay=30000,
               vendors=[("AgriFresh Coop", .30, .94, .020), ("Nordic Beverages", .25, .96, .008),
                        ("Dry Goods Wholesale", .22, .91, .012), ("FrostChain", .15, .85, .030),
                        ("TransEuropa Freight", .08, .80, .045)]),
}


# ------------------------------------------------------------------ truth model
def build_truth(cfg):
    r = np.random.default_rng(cfg["seed"])
    prod_rows = []
    for i, m in enumerate(MONTHS):
        season = 1 + cfg["season"] * np.sin(2 * np.pi * (m.month - 3) / 12)
        for name, price, ucost, base, grp in cfg["products"]:
            units = int(round(base * (1 + cfg["growth"]) ** i * season * r.normal(1, 0.03)))
            pr = round(price * (1 + cfg["price_drift"]) ** i * r.normal(1, 0.01), 2)
            uc = round(ucost * (1 + cfg["cost_drift"]) ** i * r.normal(1, 0.01), 2)
            prod_rows.append(dict(month=m, product=name, grp=grp, units=units, price=pr, unit_cost=uc,
                                  revenue=round(units * pr, 2), cogs=round(units * uc, 2)))
    prod = pd.DataFrame(prod_rows)
    rev_ref = prod.groupby("month")["revenue"].sum().mean()

    pl_rows, cf_rows, v_rows, cash = [], [], [], cfg["open_cash"]
    for i, m in enumerate(MONTHS):
        pm = prod[prod.month == m]
        act = {}
        for g, sub in pm.groupby("grp"):
            act[f"rev_{g}"] = sub.revenue.sum()
            act[f"cogs_{g}"] = sub.cogs.sum()
        for k, pct in cfg["opex"].items():
            act[k] = rev_ref * pct * (1 + 0.003 * i) * r.normal(1, 0.02)
        act["da"], act["interest"] = cfg["da"], cfg["interest"]
        bud = {k: (v if k in ("da", "interest")
                   else v * (1 + r.normal(0.02 if k.startswith("rev_") else -0.01, 0.035)))
               for k, v in act.items()}
        for sc, d in (("Actual", act), ("Budget", bud)):
            rev = sum(v for k, v in d.items() if k.startswith("rev_"))
            costs = sum(v for k, v in d.items() if not k.startswith("rev_"))
            d["tax"] = max(0.0, (rev - costs) * 0.25)
            for k, v in d.items():
                pl_rows.append(dict(month=m, scenario=sc, line=k, amount=round(float(v), 2)))

        rev = sum(v for k, v in act.items() if k.startswith("rev_"))
        cogs = sum(v for k, v in act.items() if k.startswith("cogs_"))
        opex = sum(act[k] for k in cfg["opex"])
        ebitda = rev - cogs - opex
        opcf = ebitda - act["tax"] - act["interest"] - r.normal(0.005, 0.02) * rev
        capex = -rev * cfg["capex_pct"] * r.normal(1, 0.2)
        fin = -cfg["repay"]
        cash += opcf + capex + fin
        for k, v in (("opcf", opcf), ("capex", capex), ("fin", fin), ("cash", cash)):
            cf_rows.append(dict(month=m, line=k, amount=round(float(v), 2)))

        for name, share, otd, dfc in cfg["vendors"]:
            v_rows.append(dict(month=m, vendor=name,
                               spend=round(cogs * share * r.normal(1, 0.05), 2),
                               otd=round(float(np.clip(otd + r.normal(0, 0.02), 0.6, 1)), 4),
                               defect=round(float(np.clip(dfc + r.normal(0, 0.004), 0, 0.2)), 4)))
    return dict(prod=prod, pl=pd.DataFrame(pl_rows), cf=pd.DataFrame(cf_rows), vend=pd.DataFrame(v_rows))


def S(t, sc, key):
    d = t["pl"]
    return d[(d.scenario == sc) & (d.line == key)].set_index("month")["amount"].reindex(MONTHS)


def C(t, key):
    d = t["cf"]
    return d[d.line == key].set_index("month")["amount"].reindex(MONTHS)


def to_wide(rows, fmt):
    df = pd.DataFrame(rows).T
    df.columns = [fmt(m) for m in df.columns]
    return df.round(2)


# ------------------------------------------------------------------ C1 Northbridge (xlsx)
def write_c1(t):
    fmt = lambda m: m.strftime("%b-%y")
    keys = ["payroll", "rent", "marketing", "utilities", "other", "da", "interest", "tax"]
    names = ["Wages & Salaries", "Store Rent & Rates", "Advertising & Promotion", "Utilities",
             "General & Admin", "Depreciation", "Interest Expense", "Income Tax"]

    def pl(sc):
        rev, cogs = S(t, sc, "rev_main"), S(t, sc, "cogs_main")
        rows = {"Net Sales": rev, "Cost of Sales": cogs, "Gross Profit": rev - cogs}
        for n, k in zip(names, keys):
            rows[n] = S(t, sc, k)
        rows["Net Profit"] = rev - cogs - sum(S(t, sc, k) for k in keys)
        return to_wide(rows, fmt)

    cf = to_wide({"Cash from Operations": C(t, "opcf"), "Capital Expenditure": -C(t, "capex"),
                  "Debt Repayments": -C(t, "fin"), "Closing Cash Balance": C(t, "cash")}, fmt)
    p, v = t["prod"], t["vend"]
    sales = pd.DataFrame({"Month": p.month.dt.to_timestamp(), "Category": p["product"], "Units Sold": p.units,
                          "Unit Price": p.price, "Net Sales": p.revenue, "Unit Cost": p.unit_cost})
    sup = pd.DataFrame({"Month": v.month.dt.to_timestamp(), "Supplier": v.vendor, "Spend": v.spend,
                        "On-Time %": (v.otd * 100).round(1), "Defect %": (v.defect * 100).round(2)})
    with pd.ExcelWriter(OUT / "co1_northbridge_mgmt_accounts.xlsx", engine="openpyxl") as xw:
        for sc in ("Actual", "Budget"):
            name = f"P&L {sc}"
            pl(sc).to_excel(xw, sheet_name=name, startrow=3, index_label="Line Item")
            ws = xw.sheets[name]
            ws["A1"] = f"Northbridge Retail Ltd - Management Accounts ({sc})"
            ws["A2"] = "USD, monthly. Costs shown as positive numbers."
        cf.to_excel(xw, sheet_name="Cash Flow", startrow=3, index_label="Line Item")
        xw.sheets["Cash Flow"]["A1"] = "Northbridge Retail Ltd - Cash Flow (Actual)"
        xw.sheets["Cash Flow"]["A2"] = "USD. Capex and debt repayments shown as positive outflows."
        sales.to_excel(xw, sheet_name="Sales by Category", index=False)
        sup.to_excel(xw, sheet_name="Supplier Scorecard", index=False)


# ------------------------------------------------------------------ C2 Helix (csv GL)
def write_c2(t):
    acct = {"rev_main": (4000, "Product Sales"), "cogs_main": (5000, "Materials & Direct Labour"),
            "payroll": (6100, "Salaries"), "rent": (6200, "Factory Rent"), "marketing": (6300, "Sales & Marketing"),
            "utilities": (6400, "Energy"), "other": (6500, "Admin & Other"), "da": (7000, "Depreciation"),
            "interest": (7100, "Bank Interest"), "tax": (7200, "Corporation Tax")}
    rows = []
    for r_ in t["pl"].itertuples():
        no, name = acct[r_.line]
        rows.append(dict(Period=r_.month.strftime("%Y%m"), Ver="A" if r_.scenario == "Actual" else "B",
                         AcctNo=no, AcctName=name + "  ",  # trailing whitespace on purpose
                         NetAmount=-r_.amount if r_.line == "rev_main" else r_.amount))
    gl = pd.DataFrame(rows)
    gl = pd.concat([gl, gl.sample(3, random_state=1)])  # 3 accidental duplicate rows
    gl.sort_values(["Period", "Ver", "AcctNo"], kind="stable").to_csv(OUT / "co2_helix_gl_export.csv", index=False)

    names = {"opcf": "Net cash from operating activities", "capex": "Purchase of PPE",
             "fin": "Loan repayments", "cash": "Cash at end of period"}
    cf = t["cf"].copy()
    cf["period"] = cf.month.dt.strftime("%Y%m")
    cf["line"] = cf.line.map(names)
    cf[["period", "line", "amount"]].to_csv(OUT / "co2_helix_cashflow.csv", index=False)

    p, v = t["prod"], t["vend"]
    pd.DataFrame({"period": p.month.dt.strftime("%Y%m"), "sku_group": p["product"], "qty": p.units,
                  "avg_price": p.price, "sales_gbp": p.revenue, "direct_cost_gbp": p.cogs}
                 ).to_csv(OUT / "co2_helix_sales_by_sku_group.csv", index=False)
    pd.DataFrame({"period": v.month.dt.strftime("%Y%m"), "supplier": v.vendor, "po_value": v.spend,
                  "on_time_pct": (v.otd * 100).round(1), "defect_rate_pct": (v.defect * 100).round(2)}
                 ).to_csv(OUT / "co2_helix_supplier_spend.csv", index=False)


# ------------------------------------------------------------------ C3 Solstice (xlsx)
def write_c3(t):
    col = lambda m, s: f"{m.strftime('%b %y')} | {s}"
    R = lambda sc: S(t, sc, "rev_sub") + S(t, sc, "rev_svc")
    CG = lambda sc: S(t, sc, "cogs_sub") + S(t, sc, "cogs_svc")
    OP = lambda sc: sum(S(t, sc, k) for k in ("payroll", "rent", "marketing", "utilities", "other"))
    G = lambda sc: R(sc) - CG(sc)
    E = lambda sc: G(sc) - OP(sc)
    N = lambda sc: E(sc) - S(t, sc, "da") - S(t, sc, "interest") - S(t, sc, "tax")
    both = lambda fn: {"Act": fn("Actual"), "Bud": fn("Budget")}
    neg = lambda k: both(lambda sc: -S(t, sc, k))
    layout = [
        ("REVENUE", None),
        ("   Subscription Revenue", both(lambda sc: S(t, sc, "rev_sub"))),
        ("   Professional Services Revenue", both(lambda sc: S(t, sc, "rev_svc"))),
        ("Total Revenue", both(R)),
        ("COST OF SALES", None),
        ("   Hosting & Support Costs", neg("cogs_sub")),
        ("   Services Delivery Costs", neg("cogs_svc")),
        ("Gross Profit", both(G)),
        ("OPERATING EXPENSES", None),
        ("   Staff Costs", neg("payroll")),
        ("   Office & Facilities", neg("rent")),
        ("   Marketing & Sales Programs", neg("marketing")),
        ("   Software & Utilities", neg("utilities")),
        ("   Other Operating Costs", neg("other")),
        ("Total Opex", both(lambda sc: -OP(sc))),
        ("EBITDA", both(E)),
        ("BELOW THE LINE", None),
        ("   Amortisation of Capitalised Software", neg("da")),
        ("   Finance Costs", neg("interest")),
        ("   Income Taxes", neg("tax")),
        ("Net Income", both(N)),
    ]
    cols = {}
    for m in MONTHS:
        for s in ("Act", "Bud"):
            cols[col(m, s)] = [np.nan if v is None else v[s].get(m) for _, v in layout]
    pl = pd.DataFrame(cols, index=[l for l, _ in layout]).round(2)

    cf = to_wide({"Operating Cash Flow": C(t, "opcf"), "Capex & Capitalised Dev": C(t, "capex"),
                  "Debt Service": C(t, "fin"), "Ending Cash": C(t, "cash")}, lambda m: m.strftime("%b %y"))
    p, v = t["prod"], t["vend"]
    plans = pd.DataFrame({"Month": p.month.dt.to_timestamp(), "Plan": p["product"], "Seats": p.units,
                          "ARPU (monthly)": p.price, "Revenue": p.revenue, "Cost to Serve": p.cogs})
    vend = pd.DataFrame({"Month": v.month.dt.to_timestamp(), "Vendor": v.vendor, "Spend": v.spend,
                         "SLA Met %": v.otd, "Incident Rate": v.defect})
    with pd.ExcelWriter(OUT / "co3_solstice_financials.xlsx", engine="openpyxl") as xw:
        pl.to_excel(xw, sheet_name="P&L", startrow=2, index_label="Account")
        xw.sheets["P&L"]["A1"] = "Solstice Software Inc. - Monthly P&L (USD)"
        cf.to_excel(xw, sheet_name="Cash Flow", startrow=2, index_label="Account")
        xw.sheets["Cash Flow"]["A1"] = "Solstice Software Inc. - Cash Flow (USD, actuals)"
        plans.to_excel(xw, sheet_name="Plans & Pricing", index=False)
        vend.to_excel(xw, sheet_name="Vendors", index=False)


# ------------------------------------------------------------------ C4 Kessler (German TB, JSON)
def write_c4(t):
    acct = {"rev_main": (8400, "Umsatzerlöse"), "cogs_main": (3400, "Wareneinsatz"),
            "payroll": (4100, "Löhne und Gehälter"), "rent": (4210, "Miete und Pacht"),
            "marketing": (4600, "Werbe- und Reisekosten"), "utilities": (4240, "Energie und Nebenkosten"),
            "other": (4900, "Sonstige betriebliche Aufwendungen"), "da": (4830, "Abschreibungen"),
            "interest": (2100, "Zinsaufwendungen"), "tax": (4950, "Steuern vom Einkommen")}
    rows = []
    for (m, sc), g in t["pl"].groupby(["month", "scenario"]):
        soll = haben = 0.0
        for r_ in g.itertuples():
            no, name = acct[r_.line]
            s, h = (0.0, r_.amount) if r_.line == "rev_main" else (r_.amount, 0.0)
            soll, haben = soll + s, haben + h
            rows.append(dict(Periode=m.strftime("%Y-%m"), Szenario="Ist" if sc == "Actual" else "Plan",
                             Konto=no, Bezeichnung=name, Soll=s, Haben=h))
        diff = haben - soll  # balancing account so the TB balances
        rows.append(dict(Periode=m.strftime("%Y-%m"), Szenario="Ist" if sc == "Actual" else "Plan",
                         Konto=1200, Bezeichnung="Bank und Forderungen",
                         Soll=max(diff, 0.0), Haben=max(-diff, 0.0)))
    pd.DataFrame(rows).to_csv(OUT / "co4_kessler_saldenliste.csv", sep=";", decimal=",", index=False)

    names = {"opcf": "Operativer Cashflow", "capex": "Investitionen", "fin": "Finanzierung", "cash": "Kassenbestand"}
    cf = t["cf"]
    pd.DataFrame({"Periode": cf.month.dt.strftime("%Y-%m"), "Kennzahl": cf.line.map(names), "Betrag": cf.amount}
                 ).to_csv(OUT / "co4_kessler_cashflow.csv", sep=";", decimal=",", index=False)

    p, v = t["prod"], t["vend"]
    recs = [dict(period=r_.month.strftime("%Y-%m"), produkt=r_.product, menge=int(r_.units),
                 preis_eur=r_.price, umsatz_eur=r_.revenue, wareneinsatz_eur=r_.cogs) for r_ in p.itertuples()]
    (OUT / "co4_kessler_umsatz_produkte.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame({"Periode": v.month.dt.strftime("%Y-%m"), "Lieferant": v.vendor, "Einkaufsvolumen": v.spend,
                  "Termintreue": v.otd, "Reklamationsquote": v.defect}
                 ).to_csv(OUT / "co4_kessler_lieferanten.csv", sep=";", decimal=",", index=False)


def write_fx():
    r = np.random.default_rng(99)
    gbp, eur, rows = 1.27, 1.08, []
    for m in MONTHS:
        gbp, eur = round(gbp + r.normal(0, 0.008), 4), round(eur + r.normal(0, 0.006), 4)
        d = m.to_timestamp().date().isoformat()
        rows += [dict(month=d, currency="USD", rate_to_usd=1.0), dict(month=d, currency="GBP", rate_to_usd=gbp),
                 dict(month=d, currency="EUR", rate_to_usd=eur)]
    pd.DataFrame(rows).to_csv(OUT / "fx_rates.csv", index=False)


if __name__ == "__main__":
    truth = {k: build_truth(c) for k, c in CFG.items()}
    write_c1(truth["C1"]); write_c2(truth["C2"]); write_c3(truth["C3"]); write_c4(truth["C4"]); write_fx()
    print("Source files written to", OUT)
