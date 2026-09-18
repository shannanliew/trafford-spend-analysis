"""
inspect_data.py — look at the raw files before configuring anything.

Put the Trafford .zip files in ./data/ and run:
    python inspect_data.py

Reads CSVs straight out of the zips, prints the schema of each distinct layout,
sample values, null rates, and a suggested column mapping for spend_cube.py.
Copy the whole output and send it back.
"""

from __future__ import annotations

import glob
import io
import os
import zipfile

import pandas as pd

DATA_DIR = "data"
SAMPLE_ROWS = 400

# Header keywords -> canonical name used by spend_cube.py. First match wins.
GUESS_RULES = [
    ("supplier_raw", ["supplier name", "vendor name", "supplier", "vendor", "payee", "beneficiary"]),
    ("amount", ["amount", "net amount", "value", "gross", "total", "spend"]),
    ("date", ["date", "payment date", "invoice date", "transaction date", "posting"]),
    ("business_unit", ["directorate", "department", "service area", "body", "division"]),
    ("sub_unit", ["service", "cost centre", "cost center", "responsible unit", "team"]),
    ("expense_type", ["expense type", "expenditure type", "category", "subjective", "spend type"]),
    ("expense_detail", ["detailed expense", "expense detail", "description", "narrative", "purpose"]),
    ("transaction_id", ["transaction", "invoice number", "document", "reference", "payment ref"]),
]


def iter_tables():
    """Yield (label, DataFrame) for every CSV/XLSX in ./data, including inside zips."""
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*"))):
        low = path.lower()
        if low.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                for member in zf.namelist():
                    if member.endswith("/") or member.startswith("__MACOSX"):
                        continue
                    if not member.lower().endswith((".csv", ".xlsx", ".xls")):
                        continue
                    raw = zf.read(member)
                    label = f"{os.path.basename(path)} :: {member}"
                    try:
                        if member.lower().endswith(".csv"):
                            df = pd.read_csv(io.BytesIO(raw), dtype=str, nrows=SAMPLE_ROWS,
                                             encoding="latin-1", on_bad_lines="skip")
                            full = pd.read_csv(io.BytesIO(raw), dtype=str, encoding="latin-1",
                                               on_bad_lines="skip", usecols=[0])
                            n = len(full)
                        else:
                            df = pd.read_excel(io.BytesIO(raw), dtype=str, nrows=SAMPLE_ROWS)
                            n = None
                        yield label, df, n
                    except Exception as exc:
                        print(f"  ! could not read {label}: {exc}")
        elif low.endswith((".csv", ".xlsx", ".xls")):
            try:
                if low.endswith(".csv"):
                    df = pd.read_csv(path, dtype=str, nrows=SAMPLE_ROWS,
                                     encoding="latin-1", on_bad_lines="skip")
                    n = sum(1 for _ in open(path, encoding="latin-1")) - 1
                else:
                    df = pd.read_excel(path, dtype=str, nrows=SAMPLE_ROWS)
                    n = None
                yield os.path.basename(path), df, n
            except Exception as exc:
                print(f"  ! could not read {path}: {exc}")


def guess_mapping(columns: list[str]) -> dict[str, str]:
    """Suggest THEIR header -> our canonical name."""
    out: dict[str, str] = {}
    used: set[str] = set()
    lowered = {c: str(c).strip().lower() for c in columns}
    for canonical, keywords in GUESS_RULES:
        best = None
        for col, low in lowered.items():
            if col in used:
                continue
            for rank, kw in enumerate(keywords):
                if kw in low:
                    score = (rank, len(low))
                    if best is None or score < best[0]:
                        best = (score, col)
                    break
        if best:
            out[best[1]] = canonical
            used.add(best[1])
    return out


def describe(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in df.columns:
        s = df[col]
        non_null = s.dropna().astype(str).str.strip()
        non_null = non_null[non_null != ""]
        examples = " | ".join(non_null.drop_duplicates().head(3).tolist())
        rows.append({
            "column": str(col)[:40],
            "filled_%": f"{len(non_null) / max(len(s), 1):.0%}",
            "distinct": non_null.nunique(),
            "examples": examples[:70],
        })
    return pd.DataFrame(rows)


def main() -> None:
    if not os.path.isdir(DATA_DIR):
        raise SystemExit(f"No ./{DATA_DIR}/ folder. Create it and put the zip files inside.")

    seen_layouts: dict[tuple, list[str]] = {}
    total_rows = 0

    for label, df, n in iter_tables():
        key = tuple(str(c).strip() for c in df.columns)
        seen_layouts.setdefault(key, []).append(f"{label}" + (f"  ({n:,} rows)" if n else ""))
        if n:
            total_rows += n

    if not seen_layouts:
        raise SystemExit(f"Found no readable files in ./{DATA_DIR}/")

    print(f"\n{'=' * 78}\n{len(seen_layouts)} distinct layout(s), ~{total_rows:,} rows total\n{'=' * 78}")

    for i, (cols, files) in enumerate(seen_layouts.items(), 1):
        print(f"\n--- LAYOUT {i} — {len(files)} file(s) ---")
        for f in files[:12]:
            print(f"    {f}")
        if len(files) > 12:
            print(f"    … and {len(files) - 12} more")

        # re-read one representative file for the sample
        for label, df, _ in iter_tables():
            if tuple(str(c).strip() for c in df.columns) == cols:
                break

        print()
        print(describe(df).to_string(index=False))

        print("\n  Suggested mapping for spend_cube.py:")
        print('        "columns": {')
        for their, ours in guess_mapping(list(df.columns)).items():
            print(f'            "{their}": "{ours}",')
        print("        },")

        unmapped = [c for c in df.columns if c not in guess_mapping(list(df.columns))]
        if unmapped:
            print(f"  Not mapped: {', '.join(str(c) for c in unmapped)}")

        print("\n  First 3 rows:")
        print(df.head(3).to_string(index=False, max_colwidth=28))

    print(f"\n{'=' * 78}\nSend this whole output back.\n{'=' * 78}\n")


if __name__ == "__main__":
    main()
