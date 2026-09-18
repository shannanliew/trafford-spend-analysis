"""
spend_cube_mvp.py — spend cube for Trafford Council published supplier spend.

Input:  ./data/trafford-supplier-spend-2025-26.csv
Output: ./output/spend_cube.xlsx     summary, categories, suppliers, cube
        ./output/supplier_check.csv  name variants behind each supplier

Run:    python spend_cube_mvp.py
Needs:  pandas, openpyxl
"""

import glob
import io
import os
import zipfile

import numpy as np
import pandas as pd

# --- CONFIG ------------------------------------------------------------------

COLUMNS = {
    "BeneficiaryName": "supplier_name",
    "BeneficiaryOtherID": "supplier_id",
    "VATRegistrationNumber": "vat_number",
    "Amount": "amount",
    "PaymentDate": "date",
    "OrganisationalUnit": "business_unit",
    "ProclassLabel": "proclass",
    "Purpose": "purpose",
    "TransactionNumber": "transaction_id",
}
DATE_FORMAT = "%Y-%m-%d"

# Spend procurement cannot influence, matched against the Proclass top level.
# EMPTY ON PURPOSE — run once, read the category list the script prints, then
# fill this in with Trafford's actual wording. Guessing here is how you end up
# with a number you cannot defend.
NON_ADDRESSABLE = [
    # Proclass top levels procurement cannot influence. Add as you find them.
]

# Payees that are not suppliers: redacted individuals, tax, pensions, levies to
# other public bodies. Matched as substrings against the invoice name, uppercased.
# Each entry needs a reason you can state out loud.
NON_ADDRESSABLE_SUPPLIERS = {
    "REDACTED": "redacted personal data, not a supplier",
    "HMRC": "tax",
    "H M REVENUE": "tax",
    "PENSION": "statutory pension contributions",
    "SUPERANN": "statutory pension contributions",
    "COMBINED AUTHOR": "levy to another public body",
    "PRUDENTIAL": "employee pension contributions passed through, not a purchase",
    "WORK & PENSIONS": "payment to a government department",
}

# Proclass is applied inconsistently — the same category appears under several
# labels. Map the variants onto one name. Left side is what appears in the data.
CATEGORY_ALIASES = {
    "SC": "SOCIAL CARE",
    "SOCIAL CARE SERVICES": "SOCIAL CARE",
    "FM": "FACILITIES MGT",
    "FINANCIAL": "FINANCIAL SERVICES",
    "LEISURE": "LEISURE SERVICES",
    "HWY": "HIGHWAYS",
}

TAIL_THRESHOLD = 50_000


# --- 1. LOAD -----------------------------------------------------------------

def load():
    frames = []
    for path in sorted(glob.glob("data/*")):
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                for m in zf.namelist():
                    if m.lower().endswith(".csv") and not m.startswith("__MACOSX"):
                        frames.append(pd.read_csv(io.BytesIO(zf.read(m)), dtype=str,
                                                  encoding="latin-1", on_bad_lines="skip"))
        elif path.lower().endswith(".csv"):
            frames.append(pd.read_csv(path, dtype=str, encoding="latin-1", on_bad_lines="skip"))
    if not frames:
        raise SystemExit("Nothing in ./data/")
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(f"Columns not found: {missing}\nActual: {list(df.columns)}")
    return df.rename(columns=COLUMNS)[list(COLUMNS.values())]


# --- 2. CLEAN ----------------------------------------------------------------

def clean(df):
    amt = df["amount"].fillna("").astype(str).str.replace(r"[£,\s]", "", regex=True)
    negative = amt.str.match(r"^\(.*\)$")
    amt = pd.to_numeric(amt.str.replace(r"^\((.*)\)$", r"\1", regex=True), errors="coerce")
    df["amount"] = amt.where(~negative, -amt)

    df["date"] = pd.to_datetime(df["date"], format=DATE_FORMAT, errors="coerce")

    for col in ["supplier_name", "supplier_id", "vat_number", "business_unit", "proclass", "purpose"]:
        df[col] = df[col].fillna("").astype(str).str.strip()

    bad_amount = int(df["amount"].isna().sum())
    bad_date = int(df["date"].isna().sum())
    df = df[df["amount"].notna()].copy()
    df["month"] = df["date"].dt.to_period("M").astype(str)

    print(f"  {len(df):,} transactions "
          f"({bad_amount:,} unreadable amounts dropped, {bad_date:,} unreadable dates)")
    if df["date"].notna().any():
        print(f"  {df['date'].min():%d %b %Y} to {df['date'].max():%d %b %Y}")
    return df


# --- 3. SUPPLIERS ------------------------------------------------------------

def add_suppliers(df):
    """
    Three levels of identity, weakest to strongest:
      supplier_name  what was typed on the invoice
      supplier_id    the council's own vendor record
      vat_number     the legal entity — several vendor records can share one
    Group on VAT where present, fall back to the vendor record.
    """
    df["supplier_id"] = df["supplier_id"].replace("", np.nan)
    df["vat_number"] = (df["vat_number"].str.upper()
                        .str.replace(r"[^A-Z0-9]", "", regex=True).replace("", np.nan))

    # A vendor record often carries a VAT number on some invoices and not others.
    # Resolve one VAT per vendor record first, then apply it to all of its rows —
    # otherwise the same supplier splits in two and the rollup increases the count.
    id_to_vat = (df.dropna(subset=["vat_number"])
                   .groupby("supplier_id")["vat_number"].agg(lambda s: s.mode().iat[0]))
    df["vat_resolved"] = df["supplier_id"].map(id_to_vat).fillna(df["vat_number"])

    df["supplier"] = df["vat_resolved"].fillna(df["supplier_id"]).fillna(df["supplier_name"])

    # Readable label: the name carrying the most spend in each group.
    label = (df.groupby(["supplier", "supplier_name"])["amount"].sum()
               .reset_index().sort_values("amount", ascending=False)
               .drop_duplicates("supplier").set_index("supplier")["supplier_name"])
    df["supplier_label"] = df["supplier"].map(label)

    # Flag payees that are not suppliers.
    upper = df["supplier_name"].str.upper()
    df["is_supplier"] = True
    df["exclusion_reason"] = ""
    for token, reason in NON_ADDRESSABLE_SUPPLIERS.items():
        hit = upper.str.contains(token, regex=False, na=False)
        df.loc[hit, "is_supplier"] = False
        df.loc[hit, "exclusion_reason"] = reason

    multi = df.groupby("supplier")["supplier_name"].nunique()
    print(f"  {df['supplier_name'].nunique():,} invoice names | "
          f"{df['supplier_id'].nunique():,} vendor records | "
          f"{df['supplier'].nunique():,} suppliers after VAT rollup")
    print(f"  {(multi > 1).sum():,} suppliers appear under more than one name")
    print(f"  {df['vat_resolved'].isna().mean():.0%} of rows still have no VAT after resolution")
    excluded = df.loc[~df["is_supplier"], "amount"].sum()
    print(f"  excluded {(~df['is_supplier']).sum():,} rows / £{excluded:,.0f} as not-a-supplier")
    return df


# --- 4. CATEGORIES -----------------------------------------------------------

def add_categories(df):
    """Proclass reads 'LEVEL 1 : Level 2'. Blank and 'Not Classified' both mean unclassified."""
    p = df["proclass"].replace("", "Not Classified")
    unclassified = p.str.strip().str.lower().isin(["not classified", "nan"])
    parts = p.str.split(":", n=1, expand=True)
    l2 = parts[1] if parts.shape[1] > 1 else pd.Series("", index=df.index)

    l1 = parts[0].str.strip().str.upper()
    l1 = l1.replace(CATEGORY_ALIASES)
    df["category_l1"] = l1.where(~unclassified, "UNCLASSIFIED")
    df["category_l2"] = (l2.fillna("(none)").str.strip()
                         .where(~unclassified, "Unclassified").replace("", "(none)"))

    df["addressable"] = df["is_supplier"]
    if NON_ADDRESSABLE:
        pattern = "|".join(NON_ADDRESSABLE)
        df.loc[df["category_l1"].str.upper().str.contains(pattern, regex=True, na=False),
               "addressable"] = False
    share = df.loc[df["addressable"], "amount"].sum() / df["amount"].sum()
    print(f"  {share:.1%} of net spend is addressable")

    classified = df.loc[df["category_l1"] != "UNCLASSIFIED", "amount"].sum() / df["amount"].sum()
    print(f"  {classified:.1%} of spend by value carries a Proclass category")
    return df


# --- 5. THE ANALYSES ---------------------------------------------------------

def build(df):
    cube = (df.groupby(["month", "business_unit", "category_l1", "category_l2",
                        "addressable", "supplier", "supplier_label"], as_index=False)
              .agg(spend=("amount", "sum"), transactions=("amount", "size"))
              .sort_values("spend", ascending=False))

    base = df[df["addressable"] & (df["amount"] > 0)]
    suppliers = (base.groupby(["supplier", "supplier_label"], as_index=False)
                 .agg(spend=("amount", "sum"), transactions=("amount", "size"),
                      departments=("business_unit", "nunique"),
                      categories=("category_l2", "nunique"))
                 .sort_values("spend", ascending=False))
    suppliers["cum_share"] = suppliers["spend"].cumsum() / suppliers["spend"].sum()
    suppliers["abc"] = np.select([suppliers["cum_share"] <= 0.80,
                                  suppliers["cum_share"] <= 0.95], ["A", "B"], default="C")
    suppliers["avg_invoice"] = suppliers["spend"] / suppliers["transactions"]

    categories = (base.groupby("category_l1", as_index=False)
                  .agg(spend=("amount", "sum"), transactions=("amount", "size"),
                       suppliers=("supplier", "nunique"),
                       departments=("business_unit", "nunique"))
                  .sort_values("spend", ascending=False))
    categories["spend_share"] = categories["spend"] / categories["spend"].sum()

    tail = suppliers[suppliers["spend"] < TAIL_THRESHOLD]
    a_class = suppliers[suppliers["abc"] == "A"]
    unclassified = df.loc[df["category_l1"] == "UNCLASSIFIED", "amount"].sum()

    summary = pd.DataFrame({"metric": [
        "Transactions", "Gross spend", "Credit notes", "Net spend",
        "Invoice name strings", "Vendor records", "Suppliers after VAT rollup",
        "Spend with no Proclass category", "…as a share of net spend",
        "Non-supplier spend excluded", "Addressable spend", "…as a share of net spend",
        "Suppliers making up 80% of spend", "…as a share of all suppliers",
        "…their share of transactions",
        f"Suppliers under £{TAIL_THRESHOLD:,}", "…their share of spend",
        "…their share of transactions", "…their average invoice",
    ], "value": [
        len(df), df.loc[df["amount"] > 0, "amount"].sum(),
        df.loc[df["amount"] < 0, "amount"].sum(), df["amount"].sum(),
        df["supplier_name"].nunique(), df["supplier_id"].nunique(), df["supplier"].nunique(),
        unclassified, unclassified / df["amount"].sum(),
        df.loc[~df["is_supplier"], "amount"].sum(),
        df.loc[df["addressable"], "amount"].sum(),
        df.loc[df["addressable"], "amount"].sum() / df["amount"].sum(),
        len(a_class), len(a_class) / len(suppliers),
        a_class["transactions"].sum() / suppliers["transactions"].sum(),
        len(tail), tail["spend"].sum() / suppliers["spend"].sum(),
        tail["transactions"].sum() / suppliers["transactions"].sum(),
        tail["spend"].sum() / max(tail["transactions"].sum(), 1),
    ]})
    return cube, suppliers, categories, summary


# --- 6. EXPORT ---------------------------------------------------------------

def export(df, cube, suppliers, categories, summary):
    os.makedirs("output", exist_ok=True)
    (df.groupby(["supplier", "supplier_label", "supplier_name"], as_index=False)
       .agg(spend=("amount", "sum"), transactions=("amount", "size"))
       .sort_values("spend", ascending=False)
       .to_csv("output/supplier_check.csv", index=False))

    (df[~df["is_supplier"]].groupby(["supplier_label", "exclusion_reason"], as_index=False)
       .agg(spend=("amount", "sum"), transactions=("amount", "size"))
       .sort_values("spend", ascending=False)
       .to_csv("output/exclusions.csv", index=False))

    with pd.ExcelWriter("output/spend_cube.xlsx", engine="openpyxl") as xl:
        summary.to_excel(xl, sheet_name="summary", index=False)
        categories.to_excel(xl, sheet_name="categories", index=False)
        suppliers.to_excel(xl, sheet_name="suppliers", index=False)
        cube.to_excel(xl, sheet_name="cube", index=False)
    print("  wrote output/spend_cube.xlsx, supplier_check.csv, exclusions.csv")


if __name__ == "__main__":
    df = add_categories(add_suppliers(clean(load())))
    cube, suppliers, categories, summary = build(df)
    export(df, cube, suppliers, categories, summary)

    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")
    print("\n=== SUMMARY ===")
    print(summary.to_string(index=False))

    print("\n=== PROCLASS TOP LEVEL BY SPEND — build NON_ADDRESSABLE from this ===")
    print(categories[["category_l1", "spend", "spend_share", "suppliers", "transactions"]]
          .head(30).to_string(index=False))

    print("\n=== TOP 20 SUPPLIERS ===")
    print(suppliers[["supplier_label", "spend", "transactions", "departments", "categories", "abc"]]
          .head(20).to_string(index=False))
