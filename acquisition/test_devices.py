"""
Device test helpers — load in a Python console and call the functions.

    >>> exec(open('test_devices.py').read())   # or: from test_devices import *

    >>> test_table()                  # tiny 1 mm move on COM3
    >>> test_table(port='COM5')       # different port
    >>> test_spectrometer()           # read one spectrum
    >>> test_spectrometer(backend='pyseabreeze')
"""

PORT = 'COM3'   # <-- change this if your GRBL is on a different port


# ---------------------------------------------------------------------------
# Table test
# ---------------------------------------------------------------------------

def test_table(port=PORT, move_mm=1.0):
    """
    Connect to GRBL, move +move_mm on X, then move back to 0.
    Total displacement: move_mm mm and return.
    Home the stage in OpenBuilds FIRST, then close OpenBuilds before calling this.
    """
    import time
    try:
        import serial
    except ImportError:
        print("ERROR: pyserial not installed.  Run:  pip install pyserial")
        return

    def send(ser, cmd, timeout=10):
        ser.write((cmd.strip() + '\n').encode())
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = ser.readline().decode(errors='replace').strip()
            if line:
                print(f'  GRBL: {line}')
            if line == 'ok':
                return
            if line.lower().startswith(('error', 'alarm')):
                raise RuntimeError(f'GRBL: {line}')

    print(f'Connecting to {port}...')
    ser = serial.Serial(port, 115200, timeout=1)
    time.sleep(2); ser.flushInput()
    ser.write(b'\r\n\r\n'); time.sleep(2); ser.flushInput()

    print('Unlocking...')
    ser.write(b'$X\n'); time.sleep(0.5); ser.flushInput()

    print('Setting absolute positioning...')
    send(ser, 'G90')

    print(f'Moving to X={move_mm:.1f}...')
    send(ser, f'G01 X{move_mm:.3f} Y0.000 F500')
    time.sleep(1.5)

    print('Returning to X=0...')
    send(ser, f'G01 X0.000 Y0.000 F500')
    time.sleep(1.5)

    ser.close()
    print('Table test OK.')


# ---------------------------------------------------------------------------
# Go to origin
# ---------------------------------------------------------------------------

def go_to_origin(port=PORT):
    """Move the stage to X=0, Y=0 at a safe feedrate."""
    import time
    try:
        import serial
    except ImportError:
        print("ERROR: pyserial not installed.  Run:  pip install pyserial")
        return

    def send(ser, cmd, timeout=15):
        ser.write((cmd.strip() + '\n').encode())
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = ser.readline().decode(errors='replace').strip()
            if line:
                print(f'  GRBL: {line}')
            if line == 'ok':
                return
            if line.lower().startswith(('error', 'alarm')):
                raise RuntimeError(f'GRBL: {line}')

    print(f'Connecting to {port}...')
    ser = serial.Serial(port, 115200, timeout=1)
    time.sleep(2); ser.flushInput()
    ser.write(b'\r\n\r\n'); time.sleep(2); ser.flushInput()

    ser.write(b'$X\n'); time.sleep(0.5); ser.flushInput()
    send(ser, 'G90')

    print('Moving to origin X=0 Y=0...')
    send(ser, 'G01 X0.000 Y0.000 F500')
    time.sleep(2)

    ser.close()
    print('At origin.')


# ---------------------------------------------------------------------------
# Spectrometer test
# ---------------------------------------------------------------------------

def test_spectrometer(backend='cseabreeze', integration_ms=100):
    """
    Connect to the first available Ocean Optics spectrometer, read one spectrum,
    print wavelength range and peak, then disconnect.
    Use backend='pyseabreeze' if OmniDriver is not installed.
    """
    try:
        import seabreeze
        seabreeze.use(backend)
        from seabreeze.spectrometers import Spectrometer
    except ImportError:
        print("ERROR: seabreeze not installed.  Run:  pip install seabreeze")
        return

    print(f'Looking for spectrometer (backend={backend})...')
    try:
        s = Spectrometer.from_first_available()
    except Exception as e:
        print(f'ERROR: Could not connect: {e}')
        return

    print(f'  Model : {s.model}')
    print(f'  Serial: {s.serial_number}')

    s.integration_time_micros(int(integration_ms * 1000))
    wl = s.wavelengths()
    it = s.intensities(correct_dark_counts=True, correct_nonlinearity=False)

    peak_idx = it.argmax()
    print(f'  Wavelengths  : {wl[0]:.1f} – {wl[-1]:.1f} nm  ({len(wl)} pixels)')
    print(f'  Max intensity: {it.max():.1f}  at {wl[peak_idx]:.1f} nm')
    print(f'  Min intensity: {it.min():.1f}')

    s.close()
    print('Spectrometer test OK.')


# ---------------------------------------------------------------------------
# Quick help on load
# ---------------------------------------------------------------------------

print("test_devices.py loaded.")
print("  test_table()          -> 1 mm move on", PORT)
print("  test_table(port='COMx', move_mm=2)")
print("  test_spectrometer()   -> read one spectrum")
print("  test_spectrometer(backend='pyseabreeze')")
