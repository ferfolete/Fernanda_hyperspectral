"""
Unified Hyperspectral Scan Controller
======================================
Controls the GRBL table (OpenBuilds) and Ocean Optics spectrometer together,
replacing the need to manually coordinate OceanView and OpenBuilds.

Each spectrum is saved with position metadata (X, Y) embedded in the filename
and file header, so there is no ambiguity in the mapping.

Dependencies:
    pip install pyserial seabreeze numpy

For seabreeze backend:
    - cseabreeze (default, uses OmniDriver): works if OmniDriver is installed
    - pyseabreeze (pure Python): pip install seabreeze[pyseabreeze]
    Set SEABREEZE_BACKEND env var or edit BACKEND constant below.

Usage:
    python scan_controller.py --port COM3 --output ./output
    python scan_controller.py --port COM3 --x-end 10 --y-end 10 --step 0.2 --dwell 2.0
    python scan_controller.py --dry-run   # preview scan plan without hardware
"""

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    import serial
except ImportError:
    serial = None

# Set to "pyseabreeze" if OmniDriver is not installed
SEABREEZE_BACKEND = "cseabreeze"

try:
    import seabreeze
    seabreeze.use(SEABREEZE_BACKEND)
    from seabreeze.spectrometers import Spectrometer
    SEABREEZE_AVAILABLE = True
except Exception:
    SEABREEZE_AVAILABLE = False


# ---------------------------------------------------------------------------
# GRBL controller
# ---------------------------------------------------------------------------

class GRBLController:
    """Minimal GRBL serial interface."""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 60.0):
        if serial is None:
            raise ImportError("pyserial is required: pip install pyserial")
        self.port = port
        self._serial = serial.Serial(port, baudrate, timeout=1)
        print(f"  Connected to GRBL on {port}. Waiting for boot...")
        time.sleep(2)
        self._serial.flushInput()
        # Wake up GRBL
        self._serial.write(b"\r\n\r\n")
        time.sleep(2)
        self._serial.flushInput()
        self._timeout = timeout

    def send(self, command: str, wait_ok: bool = True) -> str:
        """Send a G-code command and optionally wait for 'ok'."""
        cmd = command.strip() + "\n"
        self._serial.write(cmd.encode())
        if wait_ok:
            return self._wait_ok()
        return ""

    def _wait_ok(self) -> str:
        lines = []
        start = time.time()
        while time.time() - start < self._timeout:
            raw = self._serial.readline()
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            lines.append(line)
            if line == "ok":
                return "\n".join(lines)
            if line.lower().startswith("error"):
                raise RuntimeError(f"GRBL error: {line}")
            if line.lower().startswith("alarm"):
                raise RuntimeError(f"GRBL alarm: {line}. Run $X to clear.")
        raise TimeoutError(f"GRBL did not respond 'ok' within {self._timeout}s. Last: {lines}")

    def move_to(self, x: float, y: float, feedrate: int = 1000):
        self.send(f"G01 X{x:.4f} Y{y:.4f} F{feedrate}")

    def set_absolute(self):
        self.send("G90")

    def unlock(self):
        """Clear GRBL alarm state."""
        self.send("$X", wait_ok=False)
        time.sleep(0.5)
        self._serial.flushInput()

    def close(self):
        self._serial.close()


# ---------------------------------------------------------------------------
# Spectrum I/O
# ---------------------------------------------------------------------------

def save_spectrum(
    wavelengths: np.ndarray,
    intensities: np.ndarray,
    x: float,
    y: float,
    index: int,
    output_dir: Path,
    prefix: str = "scan",
    spectrometer_info: dict = None,
) -> Path:
    """Save a spectrum in OceanView-compatible tab-separated format."""
    timestamp = datetime.now().strftime("%H-%M-%S-%f")[:12]  # HH-MM-SS-mmm
    filename = f"{prefix}__{index:04d}__{x:.3f}_{y:.3f}__{timestamp}.txt"
    filepath = output_dir / filename

    info = spectrometer_info or {}
    now = datetime.now().strftime("%a %b %d %H:%M:%S %Y")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Data from {filename} Node\n\n")
        f.write(f"Date: {now}\n")
        f.write(f"Position X (mm): {x:.4f}\n")
        f.write(f"Position Y (mm): {y:.4f}\n")
        f.write(f"Scan Index: {index}\n")
        if info.get("model"):
            f.write(f"Spectrometer: {info['model']} ({info.get('serial', '')})\n")
        if info.get("integration_time_ms"):
            f.write(f"Integration Time (sec): {info['integration_time_ms'] / 1000:.6E}\n")
        f.write(f"Number of Pixels in Spectrum: {len(wavelengths)}\n")
        f.write(">>>>>Begin Spectral Data<<<<<\n")
        for wl, intensity in zip(wavelengths, intensities):
            f.write(f"{wl:.3f}\t{intensity:.5f}\n")

    return filepath


# ---------------------------------------------------------------------------
# Scan logic
# ---------------------------------------------------------------------------

def build_grid(
    x_start: float, x_end: float, x_step: float,
    y_start: float, y_end: float, y_step: float,
    snake: bool = False,
) -> list[tuple[float, float]]:
    """Return ordered list of (x, y) positions for the raster scan."""
    xs = np.arange(x_start, x_end + x_step * 0.5, x_step)
    ys = np.arange(y_start, y_end + y_step * 0.5, y_step)
    positions = []
    for i, y in enumerate(ys):
        row = xs if not snake or i % 2 == 0 else xs[::-1]
        for x in row:
            positions.append((round(float(x), 6), round(float(y), 6)))
    return positions


def run_scan(
    grbl_port: str,
    positions: list[tuple[float, float]],
    dwell_time: float,
    output_dir: Path,
    feedrate: int = 1000,
    integration_time_ms: float = 100.0,
    prefix: str = "scan",
    initial_dwell: float = 4.0,
    dry_run: bool = False,
):
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(positions)

    print(f"\nScan plan:")
    print(f"  Positions  : {total}")
    print(f"  Dwell time : {dwell_time}s")
    print(f"  Feedrate   : {feedrate} mm/min")
    print(f"  Integration: {integration_time_ms} ms")
    print(f"  Output     : {output_dir}")
    print(f"  Est. time  : {total * (dwell_time + 0.3) / 60:.1f} min\n")

    if dry_run:
        print("[DRY RUN] First 5 and last 5 positions:")
        for i, (x, y) in enumerate(positions[:5]):
            print(f"  [{i:04d}] X={x:.3f}  Y={y:.3f}")
        if total > 10:
            print("  ...")
        for i, (x, y) in enumerate(positions[max(5, total - 5):], start=max(5, total - 5)):
            print(f"  [{i:04d}] X={x:.3f}  Y={y:.3f}")
        return

    # --- Connect spectrometer ---
    spec = None
    spec_info = {}
    if SEABREEZE_AVAILABLE:
        try:
            spec = Spectrometer.from_first_available()
            spec.integration_time_micros(int(integration_time_ms * 1000))
            spec_info = {
                "model": spec.model,
                "serial": spec.serial_number,
                "integration_time_ms": integration_time_ms,
            }
            print(f"Spectrometer: {spec.model} s/n {spec.serial_number}")
        except Exception as e:
            print(f"WARNING: Could not connect to spectrometer: {e}")
            print("         Spectra will be filled with zeros.")
    else:
        print("WARNING: seabreeze not available. Install it: pip install seabreeze")
        print("         Spectra will be filled with zeros.")

    # --- Connect GRBL ---
    print(f"Connecting to GRBL on {grbl_port}...")
    grbl = GRBLController(grbl_port)
    grbl.unlock()
    grbl.set_absolute()

    # Move to first position and wait for initial settle
    x0, y0 = positions[0]
    print(f"Moving to start position X={x0:.3f} Y={y0:.3f}...")
    grbl.move_to(x0, y0, feedrate)
    time.sleep(initial_dwell)
    print("Starting scan...\n")

    try:
        for index, (x, y) in enumerate(positions):
            grbl.move_to(x, y, feedrate)
            time.sleep(dwell_time)

            # Acquire spectrum
            if spec:
                try:
                    wavelengths = spec.wavelengths()
                    intensities = spec.intensities(
                        correct_dark_counts=True,
                        correct_nonlinearity=False,
                    )
                except Exception as e:
                    print(f"\nWARNING: Spectrum acquisition failed at [{index}]: {e}")
                    wavelengths = np.linspace(180, 1100, 3648)
                    intensities = np.zeros(3648)
            else:
                wavelengths = np.linspace(180, 1100, 3648)
                intensities = np.zeros(3648)

            filepath = save_spectrum(
                wavelengths, intensities, x, y, index, output_dir, prefix, spec_info
            )

            pct = (index + 1) / total * 100
            print(
                f"\r  [{pct:5.1f}%] {index+1}/{total}  X={x:6.3f}  Y={y:6.3f}  -> {filepath.name}",
                end="",
                flush=True,
            )

        print("\n\nScan complete!")

    except KeyboardInterrupt:
        print(f"\n\nScan interrupted at index {index} (X={x:.3f} Y={y:.3f}).")
        print(f"  {index} spectra saved to {output_dir}")

    finally:
        grbl.close()
        if spec:
            spec.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Unified hyperspectral scan controller (GRBL + Ocean Optics spectrometer)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", default="COM3", help="GRBL serial port (e.g. COM3, COM5)")
    parser.add_argument("--x-start", type=float, default=0.0, metavar="MM")
    parser.add_argument("--x-end",   type=float, default=10.0, metavar="MM")
    parser.add_argument("--x-step",  type=float, default=0.2,  metavar="MM")
    parser.add_argument("--y-start", type=float, default=0.0,  metavar="MM")
    parser.add_argument("--y-end",   type=float, default=10.0, metavar="MM")
    parser.add_argument("--y-step",  type=float, default=0.2,  metavar="MM")
    parser.add_argument("--step",    type=float, default=None,
                        help="Set both --x-step and --y-step at once")
    parser.add_argument("--dwell",   type=float, default=2.0,
                        help="Dwell time per position in seconds")
    parser.add_argument("--feedrate", type=int, default=1000, metavar="MM/MIN")
    parser.add_argument("--integration", type=float, default=100.0, metavar="MS",
                        help="Spectrometer integration time in milliseconds")
    parser.add_argument("--output", default="./output",
                        help="Output directory for spectrum files")
    parser.add_argument("--prefix", default="scan",
                        help="Filename prefix for spectrum files")
    parser.add_argument("--snake", action="store_true",
                        help="Use snake (boustrophedon) scan pattern instead of always left-to-right")
    parser.add_argument("--initial-dwell", type=float, default=4.0,
                        help="Extra settle time at the very first position (seconds)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show scan plan without connecting to any hardware")

    args = parser.parse_args()

    x_step = args.step if args.step is not None else args.x_step
    y_step = args.step if args.step is not None else args.y_step

    positions = build_grid(
        args.x_start, args.x_end, x_step,
        args.y_start, args.y_end, y_step,
        snake=args.snake,
    )

    run_scan(
        grbl_port=args.port,
        positions=positions,
        dwell_time=args.dwell,
        output_dir=Path(args.output),
        feedrate=args.feedrate,
        integration_time_ms=args.integration,
        prefix=args.prefix,
        initial_dwell=args.initial_dwell,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
