import pyvisa
import numpy as np
import time
import h5py

INSTRUMENT_IP = "169.254.192.100" 
# For raw TCPIP sockets, adding 'SOCKET' at the end ensures PyVISA treats it properly
VISA_RESOURCE_STR = f"TCPIP0::{INSTRUMENT_IP}::9001::SOCKET"

def read_trace(device, trace=1):
    """Read ':TRACe:DATA? n', handling the IEEE-488.2 definite-length block
    (#<n><len><payload>) in either ASCII or 32-bit float binary form."""
    device.write(f":TRACe:DATA? {trace}")
    first = device.read_bytes(1)
    if first == b"#":
        n_digits = int(device.read_bytes(1))
        length = int(device.read_bytes(n_digits))
        payload = device.read_bytes(length)
        try:
            device.read_bytes(1)  # trailing LF
        except pyvisa.VisaIOError:
            pass
    else:
        payload = first + device.read_raw()

    text = payload.decode("ascii", errors="replace").strip()
    if text and all(c in "0123456789+-.,eE \r\n" for c in text):
        return np.array([float(v) for v in text.replace("\r", "").replace("\n", "").split(",") if v.strip()])
    return np.frombuffer(payload, dtype="<f4").astype(float)

def wait_for_sweep(device, max_wait_s=60):
    """Poll ':STATus:OPERation?' until bit 8 (256, sweep complete) is set.
    Falls back to a fixed delay if the instrument does not answer the query."""
    t0 = time.time()
    while time.time() - t0 < max_wait_s:
        try:
            if int(float(device.query(":STATus:OPERation?"))) & 256:
                return
        except (pyvisa.VisaIOError, ValueError):
            print("STAT:OPER? not answered, falling back to fixed delay")
            time.sleep(3)
            flush_input(device)  # drop any late reply so later reads stay in sync
            return
        time.sleep(0.2)
    print(f"Sweep did not report complete within {max_wait_s} s, reading anyway")

ATTEN_STEPS = list(range(0, 70, 5))  # 0, 5, ..., 65 dB

def flush_input(device, wait_ms=300):
    """Discard any unread (late) replies so the next query reads its own answer.
    A query that times out may still be answered later; without this, every
    following query would read the previous one's reply."""
    old = device.timeout
    device.timeout = wait_ms
    try:
        while True:
            device.read_raw()
    except pyvisa.VisaIOError:
        pass
    finally:
        device.timeout = old

def query_or_none(device, cmd, timeout_ms=2000):
    """Query a setting back from the instrument; return None if it does not answer."""
    old = device.timeout
    device.timeout = timeout_ms
    try:
        return device.query(cmd).strip()
    except pyvisa.VisaIOError:
        device.timeout = old
        flush_input(device)
        return None
    finally:
        device.timeout = old

def connect():
    """Open the raw-socket connection to the analyzer."""
    rm = pyvisa.ResourceManager()
    print(f"Connecting to Anritsu MS2720T at {INSTRUMENT_IP}...")
    device = rm.open_resource(VISA_RESOURCE_STR)

    # The MS2720T terminates its responses with LF only. Expecting '\r\n'
    # caused "read string doesn't end with termination characters" and hangs.
    device.write_termination = '\n'
    device.read_termination = '\n'
    device.timeout = 10000  # 10 Seconds

    # Clear hardware registers
    device.write("*CLS")
    time.sleep(0.1)

    device.idn = device.query("*IDN?").strip()
    print(f"Connected to: {device.idn}")
    return device

def configure(device, start_freq=88, stop_freq=108, rbw=100, pre_amp=True,
              attenuation="auto", ref_level="auto", scale_db_div=10):
    """Send the measurement setup and put the analyzer in single-sweep mode.
    start_freq, stop_freq in MHz; rbw in kHz.
    pre_amp:      True (ON) / False (OFF)
    attenuation:  "auto" or 0..65 dB in 5 dB steps
    ref_level:    "auto" or reference level in dBm (e.g. -20)
    scale_db_div: display scale in dB/div (default 10)
    Returns a dict of the settings read back from the instrument."""
    if attenuation != "auto" and attenuation not in ATTEN_STEPS:
        raise ValueError(f"attenuation must be 'auto' or one of {ATTEN_STEPS}")
    if ref_level != "auto":
        ref_level = float(ref_level)
    if start_freq >= stop_freq:
        raise ValueError("start_freq must be lower than stop_freq")

    print("\n--- Starting Parameter Configuration ---")

    # Send parameters individually with brief delays to allow the instrument to buffer
    print("Sending Start Freq...")
    device.write(f"SENS:FREQ:STAR {start_freq} MHz")
    time.sleep(0.2)

    print("Sending Stop Freq...")
    device.write(f"SENS:FREQ:STOP {stop_freq} MHz")
    time.sleep(0.2)

    print("Sending RBW configuration...")
    device.write(f"BAND:RES {rbw} kHz")
    time.sleep(0.2)

    print(f"Setting preamplifier {'ON' if pre_amp else 'OFF'}...")
    device.write(f"SENS:POW:RF:GAIN:STAT {'ON' if pre_amp else 'OFF'}")
    time.sleep(0.2)  # Give the internal attenuator/relay time to settle

    if attenuation == "auto":
        print("Setting attenuation to AUTO...")
        device.write("SENS:POW:RF:ATT:AUTO ON")
    else:
        print(f"Setting attenuation to {attenuation} dB...")
        device.write("SENS:POW:RF:ATT:AUTO OFF")
        time.sleep(0.1)
        device.write(f"SENS:POW:RF:ATT {attenuation}")
    time.sleep(0.2)

    if ref_level == "auto":
        print("Setting reference level to AUTO...")
        # Equivalent of the front-panel 'Auto Reference Level' key
        device.write("DISP:WIND:TRAC:Y:SCAL:RLEV:AUTO")
    else:
        print(f"Setting reference level to {ref_level} dBm...")
        device.write(f"DISP:WIND:TRAC:Y:SCAL:RLEV {ref_level}")
    time.sleep(0.2)

    print(f"Setting scale to {scale_db_div} dB/div...")
    device.write(f"DISP:WIND:TRAC:Y:SCAL:PDIV {scale_db_div}")
    time.sleep(0.2)

    print("Switching to Single Sweep mode...")
    device.write("INIT:CONT OFF")
    time.sleep(15)

    # Read the settings back so the actual instrument state is visible
    print("\n--- Instrument Settings (readback) ---")
    readback = {}
    for label, cmd in [("preamp", "SENS:POW:RF:GAIN:STAT?"),
                       ("atten_auto", "SENS:POW:RF:ATT:AUTO?"),
                       ("attenuation_dB", "SENS:POW:RF:ATT?"),
                       ("ref_level_dBm", "DISP:WIND:TRAC:Y:SCAL:RLEV?"),
                       ("scale_dB_div", "DISP:WIND:TRAC:Y:SCAL:PDIV?"),
                       ("rbw_Hz", "BAND:RES?"),
                       ("start_freq_Hz", "SENS:FREQ:STAR?"),
                       ("stop_freq_Hz", "SENS:FREQ:STOP?")]:
        readback[label] = query_or_none(device, cmd)
        print(f"  {label:16s}: {readback[label]}")
    print()
    flush_input(device)  # make sure no stray reply is left before sweeping
    return readback

def single_sweep(device):
    """Trigger one sweep, wait for it and return (frequencies [Hz], amplitudes [dBm])."""
    # The MS2720T does not answer 'INIT;*OPC?' over the socket, so trigger
    # the sweep on its own and poll the operation status register instead.
    device.write("INIT")
    wait_for_sweep(device, max_wait_s=60)

    start_freq = float(device.query("SENS:FREQ:STAR?"))
    stop_freq = float(device.query("SENS:FREQ:STOP?"))
    amplitudes = read_trace(device, 1)

    # 'DISP:POIN?' is not a valid MS2720T command; the point count is
    # simply the length of the returned trace.
    frequencies = np.linspace(start_freq, stop_freq, len(amplitudes))
    return frequencies, amplitudes

def acquire_spectrum_data(**settings):
    """Connect, configure, take one sweep and disconnect.
    Accepts the same keyword arguments as configure()."""
    device = None
    try:
        device = connect()
        configure(device, **settings)
        freqs, amps = single_sweep(device)
        print(f"Processing Complete: Extracted {len(amps)} entries.")
        return freqs, amps
    except pyvisa.VisaIOError as e:
        print(f"\n[VISA Error Intercepted] {e}")
        return None, None
    finally:
        if device is not None:
            try:
                device.write("INIT:CONT ON")  # Restore normal live display tracking
                device.close()
            except pyvisa.VisaIOError:
                pass

def record_data(acq_duration_s=100, interval_s=1.0, filename=None, **settings):
    """Record sweeps continuously for acq_duration_s seconds into one HDF5 file.
    interval_s: target time between the start of consecutive sweeps.
    settings:   same keyword arguments as configure().
    File layout: 'frequencies' (N,), 'amplitudes' (n_sweeps, N), 'timestamps'
    (n_sweeps,) in Unix seconds; settings are stored as file attributes.
    Stop early with Ctrl+C; everything recorded so far is kept."""
    if filename is None:
        filename = time.strftime("spectrum_%Y%m%d_%H%M%S.h5")

    device = None
    n_saved = 0
    try:
        # Connect and configure ONCE, then reuse the same connection for every sweep
        device = connect()
        readback = configure(device, **settings)

        with h5py.File(filename, "w") as hf:
            for key, val in settings.items():
                hf.attrs[f"req_{key}"] = str(val)
            for key, val in readback.items():
                hf.attrs[f"inst_{key}"] = str(val)
            hf.attrs["instrument"] = device.idn

            dset_amps = dset_time = None
            num_points = None

            print(f"Recording to {filename} for {acq_duration_s} s (Ctrl+C to stop)...")
            start_time = time.time()
            while time.time() - start_time < acq_duration_s:
                t_sweep = time.time()
                try:
                    freqs, amps = single_sweep(device)
                except (pyvisa.VisaIOError, ValueError) as e:
                    print(f"[{time.strftime('%H:%M:%S')}] Sweep failed ({e}), retrying")
                    flush_input(device)
                    time.sleep(1)
                    continue

                # Create the datasets from the first sweep, when the point count is known
                if dset_amps is None:
                    num_points = len(amps)
                    hf.create_dataset("frequencies", data=freqs)
                    # maxshape=(None, N) lets the time axis grow without limit
                    dset_amps = hf.create_dataset("amplitudes", shape=(0, num_points),
                                                  maxshape=(None, num_points),
                                                  dtype="float32", chunks=(1, num_points))
                    dset_time = hf.create_dataset("timestamps", shape=(0,), maxshape=(None,),
                                                  dtype="float64", chunks=(1,))
                    print(f"Created HDF5 datasets with {num_points} points per sweep")

                if len(amps) != num_points:
                    print(f"Skipping sweep with {len(amps)} points (expected {num_points})")
                    continue

                # Append the new sweep as one row
                dset_amps.resize((n_saved + 1, num_points))
                dset_time.resize((n_saved + 1,))
                dset_amps[n_saved, :] = amps
                dset_time[n_saved] = t_sweep
                n_saved += 1
                hf.flush()  # keep the file valid even if the run is interrupted

                print(f"[{time.strftime('%H:%M:%S')}] Sweep #{n_saved}, "
                      f"peak: {np.max(amps):.2f} dBm")

                # Wait out the rest of the interval
                remaining = interval_s - (time.time() - t_sweep)
                if remaining > 0:
                    time.sleep(remaining)

    except KeyboardInterrupt:
        print("\nRecording stopped by user.")
    except pyvisa.VisaIOError as e:
        print(f"\n[VISA Error Intercepted] {e}")
    finally:
        if device is not None:
            try:
                device.write("INIT:CONT ON")  # Restore normal live display tracking
                device.close()
            except pyvisa.VisaIOError:
                pass
        print(f"Saved {n_saved} sweeps to {filename}")
    return filename


if __name__ == "__main__":
    record_data(acq_duration_s=100, interval_s=1.0,
                start_freq=88, stop_freq=108, rbw=100, pre_amp=True,
                attenuation=0, ref_level=-60, scale_db_div=10)
