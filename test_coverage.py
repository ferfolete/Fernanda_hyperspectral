"""
Coverage Test for gcode_generator.py
=====================================
Generates scan positions for 0.5 cm x 0.5 cm and 1 cm x 1 cm areas and
produces a PNG image confirming every grid cell is visited exactly once.

Each image cell represents one step x step "pixel". If step == sensor pixel
size, a fully green image means 100% coverage with no gaps or duplicates.

Color key:
  green  — visited exactly once  (correct)
  red    — never visited          (gap / missed pixel)
  blue   — visited more than once (overlap)

Usage:
    python test_coverage.py                 # default 0.2 mm step, saves PNGs to output/
    python test_coverage.py --step 0.5      # change step size
    python test_coverage.py --show          # open interactive window instead of saving
    python test_coverage.py --snake         # test snake (boustrophedon) pattern too
"""

import argparse
import re
import sys
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from gcode_generator import generate_gcode


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

def check_coverage(
    x_end: float,
    y_end: float,
    step: float,
    snake: bool = False,
    output_dir: Path = Path("output"),
    show: bool = False,
) -> bool:
    """
    Generate G-code, parse the positions back out, build a coverage grid,
    and save (or show) a PNG visualisation.

    Returns True if every cell is covered exactly once.
    """
    gcode, total = generate_gcode(
        x_start=0.0, x_end=x_end, x_step=step,
        y_start=0.0, y_end=y_end, y_step=step,
        dwell_time=1,
        snake=snake,
    )

    # Parse positions from the generated G-code (same path a real controller sees).
    # Skip the first G01 move: it is the "move to start / settle" command that
    # precedes the initial G04 Pn dwell and is NOT a scan measurement point.
    move_re = re.compile(r"G01\s+X([+-]?[\d.]+)\s+Y([+-]?[\d.]+)", re.IGNORECASE)
    all_moves = [
        (float(m.group(1)), float(m.group(2)))
        for m in move_re.finditer(gcode)
    ]
    positions = all_moves[1:]  # drop the settle move

    # Expected grid axes
    xs = np.arange(0, x_end + step * 0.5, step)
    ys = np.arange(0, y_end + step * 0.5, step)
    nx, ny = len(xs), len(ys)

    # Build hit-count grid
    grid = np.zeros((ny, nx), dtype=int)
    out_of_bounds = 0
    for x, y in positions:
        ix = round(x / step)
        iy = round(y / step)
        if 0 <= ix < nx and 0 <= iy < ny:
            grid[iy, ix] += 1
        else:
            out_of_bounds += 1

    missed     = int(np.sum(grid == 0))
    duplicated = int(np.sum(grid > 1))
    ok = missed == 0 and duplicated == 0 and out_of_bounds == 0

    # --- build RGB image ---
    # 0 → red, 1 → green, 2+ → blue
    clamped = np.clip(grid, 0, 2)
    palette = np.array([[0.85, 0.12, 0.12],   # red   — missed
                        [0.18, 0.72, 0.18],   # green — correct
                        [0.15, 0.45, 0.85]],  # blue  — duplicated
                       dtype=float)
    rgb = palette[clamped]  # shape (ny, nx, 3)

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.imshow(
        rgb,
        origin="lower",
        extent=[-step / 2, x_end + step / 2, -step / 2, y_end + step / 2],
        aspect="equal",
        interpolation="nearest",
    )

    area_label  = f"{x_end:.0f} mm × {y_end:.0f} mm"
    pattern_lbl = "snake" if snake else "raster"
    title_top   = f"{area_label}  |  step = {step} mm  |  {nx}×{ny} = {total} pts  ({pattern_lbl})"
    if ok:
        title_bot = "PASS — all cells covered exactly once"
        title_color = "green"
    else:
        parts = []
        if missed:     parts.append(f"{missed} missed")
        if duplicated: parts.append(f"{duplicated} duplicated")
        if out_of_bounds: parts.append(f"{out_of_bounds} out-of-bounds")
        title_bot = "FAIL — " + ", ".join(parts)
        title_color = "red"

    ax.set_title(f"{title_top}\n{title_bot}", color=title_color, fontsize=9)
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")

    legend_patches = [
        mpatches.Patch(color=palette[1], label="covered (×1)"),
        mpatches.Patch(color=palette[0], label="missed (×0)"),
        mpatches.Patch(color=palette[2], label="duplicated (>×1)"),
    ]
    ax.legend(handles=legend_patches, loc="upper right", fontsize=8)
    fig.tight_layout()

    status_str = "PASS" if ok else "FAIL"
    print(f"  [{status_str}]  {area_label}  step={step} mm  pattern={pattern_lbl}")
    if not ok:
        if missed:        print(f"         missed     : {missed} cell(s)")
        if duplicated:    print(f"         duplicated : {duplicated} cell(s)")
        if out_of_bounds: print(f"         out-of-bounds: {out_of_bounds} point(s)")

    if show:
        plt.show()
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        pattern_suffix = "_snake" if snake else ""
        fname = output_dir / f"coverage_{x_end:.0f}x{y_end:.0f}mm_step{step}mm{pattern_suffix}.png"
        fig.savefig(fname, dpi=150)
        print(f"         saved  -> {fname}")

    plt.close(fig)
    return ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Visual pixel-coverage test for gcode_generator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--step",  type=float, default=0.2, metavar="MM",
                        help="Step size in mm (= one pixel)")
    parser.add_argument("--show",  action="store_true",
                        help="Open interactive plot window instead of saving PNGs")
    parser.add_argument("--snake", action="store_true",
                        help="Also test the snake (boustrophedon) pattern")
    parser.add_argument("-o", "--output", default="output",
                        help="Directory to save PNG images")
    args = parser.parse_args()

    output_dir = Path(args.output)
    areas = [(5.0, 5.0), (10.0, 10.0)]   # 0.5 cm x 0.5 cm and 1 cm x 1 cm
    patterns = [False, True] if args.snake else [False]

    print(f"\nCoverage test  —  step = {args.step} mm\n")
    all_ok = True
    for snake in patterns:
        for x_end, y_end in areas:
            ok = check_coverage(
                x_end, y_end, args.step,
                snake=snake,
                output_dir=output_dir,
                show=args.show,
            )
            all_ok = all_ok and ok

    print()
    if all_ok:
        print("All tests PASSED.")
    else:
        print("Some tests FAILED — check the images above.")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
