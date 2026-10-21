# rf-instrument-daq

Python scripts for remote control and data acquisition from RF test instruments
(spectrum analyzers, signal generators, SDRs) over PyVISA/SCPI, with HDF5 logging
and simple viewers for the recorded data.

Each instrument has its own folder with a standalone acquisition script.
Recordings are written to HDF5 so they can be opened with the included viewer
or any HDF5 tool (h5py, MATLAB, HDFView).

## Supported instruments

| Instrument | Type | Interface | Script | Status |
|---|---|---|---|---|
| Anritsu MS2720T (fw 4.80) | Handheld spectrum analyzer | Ethernet, raw socket (port 9001) | `anritsu/ms2720t/anritsuMS2720T_acq.py` | Working |

## Repository layout

    rf-instrument-daq/
    ├── anritsu/
    │   └── ms2720t/
    │       ├── anritsuMS2720T_acq.py   # configure, single sweep, continuous recording
    │   └── spectrum_viewer.py      # GUI to view recorded .h5 files
    ├── common/                         # shared helpers (planned)
    ├── data/                           # local recordings (gitignored)
    ├── requirements.txt
    └── README.md

## Installation

    git clone https://github.com/<user>/rf-instrument-daq.git
    cd rf-instrument-daq
    pip install -r requirements.txt

`requirements.txt`:

    pyvisa
    pyvisa-py      # pure-Python VISA backend; not needed if NI-VISA/Keysight VISA is installed
    numpy
    h5py
    matplotlib

The viewer also needs Tkinter (bundled with most Python installs; on Debian/Ubuntu: `sudo apt install python3-tk`).

## Anritsu MS2720T

### Connection

- Connect the analyzer by Ethernet and set its IP address. The scripts assume
  `169.254.192.100`; change `INSTRUMENT_IP` at the top of the script if yours differs.
- The scripts use a raw socket: `TCPIP0::<ip>::9001::SOCKET`.
- The instrument ends its replies with LF (`\n`) only.

### Single sweep

    from anritsuMS2720T_acq import acquire_spectrum_data

    freqs, amps = acquire_spectrum_data(
        start_freq=88, stop_freq=108,  # MHz
        rbw=100,                       # kHz
        pre_amp=True,                  # preamplifier ON/OFF
        attenuation="auto",            # "auto" or 0-65 dB in 5 dB steps
        ref_level="auto",              # "auto" or dBm, e.g. -60
        scale_db_div=10,               # dB/div
    )

`freqs` is in Hz and `amps` in dBm. Each sweep returns 551 points.

### Continuous recording

    from anritsuMS2720T_acq import record_data

    record_data(acq_duration_s=600, interval_s=1.0,
                start_freq=88, stop_freq=108, rbw=100, pre_amp=True,
                attenuation=0, ref_level=-60, scale_db_div=10)

Or run the script directly: `python anritsuMS2720T_acq.py`.

- Connects and configures the instrument once, then repeats single sweeps
  every `interval_s` seconds for `acq_duration_s` seconds.
- Writes to `spectrum_YYYYMMDD_HHMMSS.h5` (or a name passed as `filename=`).
- The file is flushed after every sweep, so the data survives a crash or Ctrl+C.
- Press Ctrl+C to stop early; the instrument is returned to continuous sweep.

### HDF5 file format

| Dataset | Shape | Units |
|---|---|---|
| `frequencies` | (N,) | Hz |
| `amplitudes` | (n_sweeps, N) | dBm |
| `timestamps` | (n_sweeps,) | Unix seconds (start of each sweep) |

File attributes hold the requested settings (`req_*`), the values read back
from the instrument (`inst_*`) and the instrument ID (`instrument`).

    import h5py
    with h5py.File("spectrum_20261021_123438.h5") as f:
        freqs = f["frequencies"][:]
        amps = f["amplitudes"][:]
        times = f["timestamps"][:]
        print(dict(f.attrs))

### Viewer

    python spectrum_viewer.py spectrum_20261021_123438.h5

- Top panel: one sweep, or a summary across sweeps (mean in linear power,
  highest value per frequency, lowest value per frequency, median), with the peak marked.
- Bottom panel: waterfall of power over frequency and elapsed time; click a row
  to select that sweep.
- Sweep slider, adjustable colour range, file-attribute view, and **Reload** for
  files that are still being recorded.

### Known quirks (MS2720T, fw 4.80)

- `DISP:POIN?` is not supported; the point count is taken from the trace length.
- `INIT;*OPC?` gets no reply over the socket. The scripts send `INIT` and poll
  `:STATus:OPERation?` (bit 8 = sweep complete) instead.
- `SYST:ERR?` may not be answered; the scripts treat it as optional.
- A query that times out may be answered late, so later replies arrive one query behind.
  The scripts discard any leftover input after a timeout.
- The auto reference level command (`DISP:WIND:TRAC:Y:SCAL:RLEV:AUTO`) is
  unverified; check the settings printout (instrument readback) after configuration.

## Adding a new instrument

1. Create `<vendor>/<model>/` with an acquisition script.
2. Use the same HDF5 layout (`frequencies`, `amplitudes`, `timestamps` + attributes)
   so the existing viewer works unchanged.
3. Document the connection string, tested firmware and any SCPI quirks in this README,
   and add a row to the *Supported instruments* table.

## License

MIT (or your choice).
