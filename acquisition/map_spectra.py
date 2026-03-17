"""
Spectra-to-Position Mapper (Post-processor)
=============================================
Maps existing OceanView spectrum files to their XY table positions by
matching the sequential index in each filename to the ordered list of
positions in the G-code file.

The OceanView filename format is assumed to be:
    <prefix>__<index>__<HH-MM-SS-ms>.txt

The G-code positions are extracted in the same order they were executed.
Index 0 -> first G-code position, index 1 -> second, etc.

Output:
  - index.csv   : comma-separated table of (index, x_mm, y_mm, original_file, renamed_file)
  - Copies (or symlinks) of each spectrum file renamed as:
      X<x>_Y<y>__<index>__<original_timestamp>.txt

Usage:
    # Map TESTE 2 spectra using the scan G-code:
    python map_spectra.py "runs/TESTE 2" "edited-gcode 1,0cm x 1,0cm delta 2seg" -o mapped/

    # Only create the CSV index, don't copy files:
    python map_spectra.py "runs/TESTE 2" "edited-gcode 1,0cm x 1,0cm delta 2seg" --no-copy

    # Show a report without writing anything:
    python map_spectra.py "runs/TESTE 2" "edited-gcode 1,0cm x 1,0cm delta 2seg" --report
"""

import argparse
import csv
import re
import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
# G-code parsing
# ---------------------------------------------------------------------------

def parse_gcode_positions(gcode_path: Path) -> list[tuple[float, float]]:
    """
    Extract ordered (x, y) positions from a G-code file.
    Skips comment lines and only reads G01/G1 move commands.
    """
    positions = []
    pattern = re.compile(
        r"G0?1\s+X([+-]?[\d.]+)\s+Y([+-]?[\d.]+)", re.IGNORECASE
    )
    with open(gcode_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.split(";")[0].strip()  # strip inline comments
            m = pattern.search(line)
            if m:
                positions.append((float(m.group(1)), float(m.group(2))))
    return positions


# ---------------------------------------------------------------------------
# Spectrum file parsing
# ---------------------------------------------------------------------------

_INDEX_RE = re.compile(r"__(\d+)__[\d-]+\.txt$", re.IGNORECASE)


def get_file_index(path: Path) -> int | None:
    """Extract the sequence index from an OceanView filename."""
    m = _INDEX_RE.search(path.name)
    return int(m.group(1)) if m else None


def collect_spectrum_files(spectrum_dir: Path) -> list[Path]:
    """
    Return all .txt spectrum files sorted by their embedded sequence index.
    Files without a parseable index are sorted by name as a fallback.
    """
    files = list(spectrum_dir.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"No .txt files found in {spectrum_dir}")

    def sort_key(p: Path):
        idx = get_file_index(p)
        return (0, idx) if idx is not None else (1, p.name)

    return sorted(files, key=sort_key)


# ---------------------------------------------------------------------------
# Core mapping
# ---------------------------------------------------------------------------

def map_spectra_to_positions(
    spectrum_dir: Path,
    gcode_path: Path,
    output_dir: Path | None = None,
    copy_files: bool = True,
    report_only: bool = False,
) -> list[dict]:
    """
    Map spectrum files to G-code positions.

    Returns a list of dicts with keys:
        index, x_mm, y_mm, original_file, renamed_file, warning
    """
    positions = parse_gcode_positions(gcode_path)
    files = collect_spectrum_files(spectrum_dir)

    n_pos = len(positions)
    n_files = len(files)
    n = min(n_pos, n_files)

    print(f"G-code positions : {n_pos}")
    print(f"Spectrum files   : {n_files}")

    if n_pos != n_files:
        print(
            f"WARNING: count mismatch ({n_files} files vs {n_pos} positions). "
            f"Mapping first {n} pairs."
        )

    rows = []
    for i in range(n):
        path = files[i]
        x, y = positions[i]
        file_index = get_file_index(path)
        warning = ""

        if file_index is not None and file_index != i:
            warning = f"expected index {i}, file has index {file_index}"

        # Build new filename: keep original timestamp suffix for traceability
        suffix_match = re.search(r"__(\d+-\d+-\d+-\d+)\.txt$", path.name)
        timestamp_part = suffix_match.group(1) if suffix_match else f"{i:04d}"
        renamed = f"X{x:.3f}_Y{y:.3f}__{i:04d}__{timestamp_part}.txt"

        rows.append({
            "index": i,
            "x_mm": x,
            "y_mm": y,
            "original_file": path.name,
            "renamed_file": renamed,
            "source_path": path,
            "warning": warning,
        })

    if report_only:
        _print_report(rows, n_pos, n_files)
        return rows

    if output_dir is None:
        raise ValueError("output_dir required when not in report_only mode")

    output_dir.mkdir(parents=True, exist_ok=True)

    warnings = []
    for row in rows:
        if copy_files:
            dest = output_dir / row["renamed_file"]
            shutil.copy2(row["source_path"], dest)
        if row["warning"]:
            warnings.append(f"  [{row['index']:04d}] {row['warning']}")

        if row["index"] % 200 == 0:
            print(
                f"  [{row['index']:04d}/{n-1}]  X={row['x_mm']:.3f}  Y={row['y_mm']:.3f}"
                f"  {row['original_file']} -> {row['renamed_file']}"
            )

    # Write CSV index
    csv_path = output_dir / "index.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["index", "x_mm", "y_mm", "original_file", "renamed_file", "warning"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in writer.fieldnames})

    print(f"\nMapped {n} spectra.")
    if copy_files:
        print(f"Renamed copies saved to: {output_dir}")
    print(f"Index CSV: {csv_path}")

    if warnings:
        print(f"\n{len(warnings)} index warnings (check index.csv):")
        for w in warnings[:10]:
            print(w)
        if len(warnings) > 10:
            print(f"  ... and {len(warnings) - 10} more.")

    return rows


def _print_report(rows: list[dict], n_pos: int, n_files: int):
    print(f"\n--- Mapping Report ---")
    print(f"Positions in G-code : {n_pos}")
    print(f"Spectrum files found : {n_files}")
    print(f"Mappable pairs       : {len(rows)}")
    print()
    print(f"{'Index':>6}  {'X (mm)':>8}  {'Y (mm)':>8}  {'File'}")
    print("-" * 70)
    sample = rows[:5] + (rows[-5:] if len(rows) > 10 else [])
    shown = set()
    for row in rows:
        if len(shown) >= 10:
            break
        if row["index"] < 5 or row["index"] >= len(rows) - 5:
            if row["index"] not in shown:
                shown.add(row["index"])
                warn = f"  [!] {row['warning']}" if row["warning"] else ""
                print(
                    f"{row['index']:>6}  {row['x_mm']:>8.3f}  {row['y_mm']:>8.3f}"
                    f"  {row['original_file']}{warn}"
                )
            if row["index"] == 4 and len(rows) > 10:
                print("  ...")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Map OceanView spectrum files to XY positions using a G-code scan file",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "spectrum_dir",
        help="Directory containing OceanView .txt spectrum files",
    )
    parser.add_argument(
        "gcode_file",
        help="G-code file that was used for the scan",
    )
    parser.add_argument(
        "-o", "--output",
        default="./mapped_spectra",
        help="Output directory for renamed copies and index.csv",
    )
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Only create index.csv without copying/renaming files",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print a mapping report without writing any files",
    )

    args = parser.parse_args()

    spectrum_dir = Path(args.spectrum_dir)
    gcode_path = Path(args.gcode_file)

    if not spectrum_dir.is_dir():
        print(f"ERROR: spectrum directory not found: {spectrum_dir}")
        return
    if not gcode_path.is_file():
        print(f"ERROR: G-code file not found: {gcode_path}")
        return

    map_spectra_to_positions(
        spectrum_dir=spectrum_dir,
        gcode_path=gcode_path,
        output_dir=None if args.report else Path(args.output),
        copy_files=not args.no_copy,
        report_only=args.report,
    )


if __name__ == "__main__":
    main()
