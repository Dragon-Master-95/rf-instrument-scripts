"""GUI viewer for HDF5 spectrum files written by anritsuMS2720T_acq.record_data().

File layout expected:
    frequencies (N,)          Hz
    amplitudes  (n_sweeps, N) dBm
    timestamps  (n_sweeps,)   Unix seconds
    attrs                     requested / instrument settings

Usage:
    python spectrum_viewer.py [file.h5]
"""
import sys
import datetime as dt
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import h5py
import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk


def load_h5(path):
    """Return (freqs_Hz, amps_dBm, timestamps, attrs) from a recorded file."""
    with h5py.File(path, "r") as hf:
        if "amplitudes" not in hf:
            raise ValueError("File contains no sweeps ('amplitudes' dataset missing)")
        freqs = hf["frequencies"][:]
        amps = hf["amplitudes"][:].astype(float)
        times = hf["timestamps"][:]
        attrs = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in hf.attrs.items()}
    if amps.shape[0] == 0:
        raise ValueError("File contains 0 sweeps")
    return freqs, amps, times, attrs


class SpectrumViewer(tk.Tk):
    def __init__(self, path=None):
        super().__init__()
        self.title("MS2720T Spectrum Viewer")
        self.geometry("1200x800")
        self.path = None
        self.freqs = self.amps = self.times = None
        self._colorbar = None

        self._build_controls()
        self._build_plots()
        if path:
            self.open_file(path)

    # ---------------------------------------------------------------- layout
    def _build_controls(self):
        bar = ttk.Frame(self, padding=4)
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(bar, text="Open...", command=self.ask_open).pack(side=tk.LEFT)
        ttk.Button(bar, text="Reload", command=self.reload).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="Info", command=self.show_info).pack(side=tk.LEFT)

        ttk.Label(bar, text="  Trace:").pack(side=tk.LEFT)
        self.mode = tk.StringVar(value="Single sweep")
        mode_box = ttk.Combobox(bar, textvariable=self.mode, state="readonly", width=14,
                                values=["Single sweep", "Mean", "Max hold", "Min hold", "Median"])
        mode_box.pack(side=tk.LEFT)
        mode_box.bind("<<ComboboxSelected>>", lambda e: self.update_spectrum())

        self.show_stats = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Overlay mean/max", variable=self.show_stats,
                        command=self.update_spectrum).pack(side=tk.LEFT, padx=6)

        ttk.Label(bar, text="  Colour range [dBm]:").pack(side=tk.LEFT)
        self.vmin = tk.StringVar()
        self.vmax = tk.StringVar()
        for var in (self.vmin, self.vmax):
            e = ttk.Entry(bar, textvariable=var, width=7)
            e.pack(side=tk.LEFT, padx=1)
            e.bind("<Return>", lambda ev: self.update_waterfall())
        ttk.Button(bar, text="Auto", command=self.auto_clim).pack(side=tk.LEFT, padx=2)

        sbar = ttk.Frame(self, padding=(4, 0))
        sbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(sbar, text="Sweep:").pack(side=tk.LEFT)
        self.idx = tk.IntVar(value=0)
        self.slider = ttk.Scale(sbar, from_=0, to=0, orient=tk.HORIZONTAL,
                                command=self._on_slider)
        self.slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.idx_label = ttk.Label(sbar, text="-", width=40)
        self.idx_label.pack(side=tk.LEFT)

        self.status = tk.StringVar(value="Open an HDF5 file to begin")
        ttk.Label(self, textvariable=self.status, relief=tk.SUNKEN,
                  anchor=tk.W).pack(side=tk.BOTTOM, fill=tk.X)

    def _build_plots(self):
        self.fig = Figure(figsize=(10, 7), constrained_layout=True)
        self.ax_spec = self.fig.add_subplot(2, 1, 1)
        self.ax_wf = self.fig.add_subplot(2, 1, 2, sharex=self.ax_spec)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        NavigationToolbar2Tk(self.canvas, self).update()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Click on the waterfall to jump to that sweep
        self.canvas.mpl_connect("button_press_event", self._on_click)

    # ------------------------------------------------------------- file I/O
    def ask_open(self):
        path = filedialog.askopenfilename(title="Open spectrum file",
                                          filetypes=[("HDF5", "*.h5 *.hdf5"), ("All", "*")])
        if path:
            self.open_file(path)

    def open_file(self, path):
        try:
            self.freqs, self.amps, self.times, self.attrs = load_h5(path)
        except (OSError, KeyError, ValueError) as e:
            messagebox.showerror("Cannot open file", f"{path}\n\n{e}")
            return
        self.path = path
        n = self.amps.shape[0]
        self.slider.configure(to=max(n - 1, 0))
        self.idx.set(min(self.idx.get(), n - 1))
        self.slider.set(self.idx.get())
        self.title(f"MS2720T Spectrum Viewer - {path}")
        t0, t1 = self.times[0], self.times[-1]
        self.status.set(f"{path}  |  {n} sweeps x {self.amps.shape[1]} points  |  "
                        f"{self.freqs[0]/1e6:.3f}-{self.freqs[-1]/1e6:.3f} MHz  |  "
                        f"{dt.datetime.fromtimestamp(t0):%Y-%m-%d %H:%M:%S} - "
                        f"{dt.datetime.fromtimestamp(t1):%H:%M:%S} ({t1 - t0:.1f} s)")
        self.auto_clim(redraw=False)
        self.update_waterfall()
        self.update_spectrum()

    def reload(self):
        """Re-read the file, e.g. while a recording is still adding sweeps."""
        if self.path:
            self.open_file(self.path)

    def show_info(self):
        if not self.path:
            return
        lines = [f"{k} = {v}" for k, v in sorted(self.attrs.items())]
        win = tk.Toplevel(self)
        win.title("File attributes")
        txt = tk.Text(win, width=70, height=min(30, len(lines) + 2))
        txt.insert("1.0", "\n".join(lines) or "(no attributes)")
        txt.configure(state=tk.DISABLED)
        txt.pack(fill=tk.BOTH, expand=True)

    # -------------------------------------------------------------- plotting
    def auto_clim(self, redraw=True):
        if self.amps is None:
            return
        lo, hi = np.nanpercentile(self.amps, [1, 99.9])
        self.vmin.set(f"{lo:.1f}")
        self.vmax.set(f"{hi:.1f}")
        if redraw:
            self.update_waterfall()

    def _clim(self):
        try:
            return float(self.vmin.get()), float(self.vmax.get())
        except ValueError:
            return None, None

    def update_waterfall(self):
        if self.amps is None:
            return
        ax = self.ax_wf
        ax.clear()
        f_mhz = self.freqs / 1e6
        rel_t = self.times - self.times[0]
        # Extent maps rows to elapsed time; origin='lower' puts first sweep at the bottom
        t_end = rel_t[-1] if len(rel_t) > 1 else 1.0
        vmin, vmax = self._clim()
        im = ax.imshow(self.amps, aspect="auto", origin="lower", cmap="viridis",
                       extent=[f_mhz[0], f_mhz[-1], 0, t_end],
                       vmin=vmin, vmax=vmax, interpolation="nearest")
        if self._colorbar is None:
            self._colorbar = self.fig.colorbar(im, ax=ax, label="Power [dBm]")
        else:
            self._colorbar.update_normal(im)
        ax.set_xlabel("Frequency [MHz]")
        ax.set_ylabel("Elapsed time [s]")
        ax.set_title("Waterfall (click to select sweep)")
        self._cursor = ax.axhline(rel_t[self.idx.get()], color="r", lw=0.8)
        self.canvas.draw_idle()

    def update_spectrum(self):
        if self.amps is None:
            return
        ax = self.ax_spec
        xlim = ax.get_xlim() if ax.lines else None  # keep zoom across updates
        ax.clear()
        f_mhz = self.freqs / 1e6
        i = self.idx.get()
        mode = self.mode.get()

        if mode == "Single sweep":
            y, label = self.amps[i], f"Sweep #{i + 1}"
        elif mode == "Mean":
            # Average in linear power (mW), not in dB
            y, label = 10 * np.log10(np.mean(10 ** (self.amps / 10), axis=0)), "Mean (linear avg)"
        elif mode == "Max hold":
            y, label = self.amps.max(axis=0), "Max hold"
        elif mode == "Min hold":
            y, label = self.amps.min(axis=0), "Min hold"
        else:
            y, label = np.median(self.amps, axis=0), "Median"

        ax.plot(f_mhz, y, lw=0.9, label=label)
        if self.show_stats.get():
            ax.plot(f_mhz, 10 * np.log10(np.mean(10 ** (self.amps / 10), axis=0)),
                    lw=0.7, alpha=0.8, label="Mean")
            ax.plot(f_mhz, self.amps.max(axis=0), lw=0.7, alpha=0.8, label="Max hold")

        k = int(np.argmax(y))
        ax.plot(f_mhz[k], y[k], "rv")
        ax.annotate(f"{f_mhz[k]:.3f} MHz\n{y[k]:.1f} dBm", (f_mhz[k], y[k]),
                    textcoords="offset points", xytext=(6, -4), fontsize=8, va="top")

        ax.set_ylabel("Power [dBm]")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=8)
        ax.set_title(label)
        if xlim:
            ax.set_xlim(xlim)
        ax.tick_params(labelbottom=True)

        self.idx_label.configure(
            text=f"#{i + 1}/{self.amps.shape[0]}  "
                 f"{dt.datetime.fromtimestamp(self.times[i]):%Y-%m-%d %H:%M:%S}")
        if hasattr(self, "_cursor"):
            self._cursor.set_ydata([self.times[i] - self.times[0]] * 2)
        self.canvas.draw_idle()

    # ---------------------------------------------------------------- events
    def _on_slider(self, value):
        i = int(round(float(value)))
        if self.amps is None or i == self.idx.get():
            return
        self.idx.set(i)
        if self.mode.get() != "Single sweep":
            self.mode.set("Single sweep")
        self.update_spectrum()

    def _on_click(self, event):
        if event.inaxes is not self.ax_wf or self.amps is None or event.ydata is None:
            return
        if self.canvas.toolbar.mode:  # ignore clicks while zooming/panning
            return
        rel_t = self.times - self.times[0]
        i = int(np.argmin(np.abs(rel_t - event.ydata)))
        self.idx.set(i)
        self.slider.set(i)
        self.mode.set("Single sweep")
        self.update_spectrum()


if __name__ == "__main__":
    SpectrumViewer(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
