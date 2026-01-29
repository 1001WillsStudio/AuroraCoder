import os
import pandas as pd


def sheet_to_markdown(file_path: str, sheet_name: str | int | None = None, delimiter: str | None = None) -> str:
    """
    Convert a single‑sheet CSV, TSV, or Excel file to a markdown table.

    Parameters
    ----------
    file_path : str
        Path to the input file.
    sheet_name : str | int | None, optional
        Excel sheet name or index (ignored for CSV/TSV). Defaults to the first sheet.
    delimiter : str | None, optional
        Delimiter override for CSV/TSV files. If None, infers from file extension.

    Returns
    -------
    str
        Markdown‑formatted table.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(file_path, delimiter=delimiter or ",")
    elif ext in {".tsv", ".txt"}:
        df = pd.read_csv(file_path, delimiter=delimiter or "\t")
    elif ext in {".xlsx", ".xlsm", ".xls"}:
        df = pd.read_excel(file_path, sheet_name=sheet_name or 0)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    if df.empty:
        return ""

    header = "| " + " | ".join(map(str, df.columns)) + " |"
    divider = "|" + "|".join(["---"] * len(df.columns)) + "|"
    body = "\n".join(
        "| " + " | ".join(map(lambda x: "" if pd.isna(x) else str(x), row)) + " |"
        for row in df.itertuples(index=False, name=None)
    )
    return "\n".join([header, divider, body])


if __name__ == "__main__":
    # Example usage
    markdown_output = sheet_to_markdown("example.xlsx")  # supports .csv, .tsv, .xlsx, etc.
    print(markdown_output)
