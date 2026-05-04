"""Live PSTrace-style plot.

Designed to work cleanly when called from the main thread (the common case
for a CLI / Jupyter run).  Uses matplotlib's interactive mode and only
calls `plt.pause` to flush the GUI event loop, so it stays portable across
backends.

If matplotlib isn't available or the run is headless, callers can simply pass
`live_plot=False` to `run_cv` / `run_swv`.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from .params import Sample

LOG = logging.getLogger(__name__)


class LivePlot:
    """Update-as-you-go matplotlib plot of (potential, current).

    Multi-scan CV samples are drawn with separate line segments per scan, so
    the cycles are visually distinguishable (similar to PSTrace).
    """

    def __init__(self,
                 title: str = "Voltammogram",
                 xlabel: str = "Potential (V)",
                 ylabel: str = "Current (A)",
                 ylim_pad: float = 1.2,
                 update_every: int = 1,
                 refresh_min_interval_s: float = 0.04):
        """
        update_every: redraw at most every Nth sample (still bounded by
            refresh_min_interval_s so a fast stream doesn't kill performance,
            and so a slow stream still draws every point).
        refresh_min_interval_s: never redraw faster than this many seconds
            (default 40 ms ≈ 25 fps).  Set to 0 to disable throttling.
        """
        self.title = title
        self.xlabel = xlabel
        self.ylabel = ylabel
        self.ylim_pad = ylim_pad
        self.update_every = max(1, update_every)
        self.refresh_min_interval_s = max(0.0, refresh_min_interval_s)

        self._scans_x: List[List[float]] = []
        self._scans_y: List[List[float]] = []
        self._current_scan: int = -1
        self._lines = []
        self._fig = None
        self._ax = None
        self._counter = 0
        self._last_refresh_t: float = 0.0

    # ------------------------------------------------------------------
    def start(self):
        try:
            import matplotlib
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "matplotlib is required for live plotting. "
                "`pip install matplotlib`."
            ) from exc

        # The default MacOSX backend has a known issue where it buffers
        # draws and only flushes them when the script ends, so the curve
        # appears all at once at the end instead of streaming live.
        # Force TkAgg first (it pumps events on every flush_events call),
        # falling back to QtAgg, then to whatever the user has.
        current = matplotlib.get_backend().lower()
        if "agg" in current and "tk" not in current and "qt" not in current:
            LOG.warning(
                "Non-interactive backend %s detected — the plot window won't "
                "appear. Unset MPLBACKEND or install Tk.", current,
            )
        elif current.startswith(("macosx", "module://")):
            for candidate in ("TkAgg", "QtAgg", "Qt5Agg"):
                try:
                    matplotlib.use(candidate, force=True)
                    LOG.info("Switched matplotlib backend %s -> %s for "
                             "real-time streaming.", current, candidate)
                    break
                except Exception:
                    continue

        # Import pyplot AFTER setting the backend.
        import matplotlib.pyplot as plt
        self._plt = plt
        self._matplotlib = matplotlib

        plt.ion()
        self._fig, self._ax = plt.subplots(figsize=(8, 5))
        self._ax.set_title(self.title)
        self._ax.set_xlabel(self.xlabel)
        self._ax.set_ylabel(self.ylabel)
        self._ax.grid(True, which="major", linestyle="-", alpha=0.5)
        self._ax.minorticks_on()
        self._ax.grid(True, which="minor", linestyle="--", alpha=0.3)
        self._fig.tight_layout()
        # Show + warm the GUI loop.
        #
        # First-run cold-start on macOS: the Tk/Qt window manager needs a few
        # hundred ms to actually realize the window, build the matplotlib
        # font cache, and start pumping events.  If we begin streaming
        # samples before that's done, the early redraws get dropped and the
        # plot only "wakes up" at the end of the script (which is exactly
        # what you saw on the first run).  On the second invocation the
        # caches are warm and it works fine.
        #
        # Fix: bring the window up, then keep flushing events for ~0.6 s so
        # the OS / backend has finished initializing before the first sample
        # arrives.
        self._fig.canvas.draw()
        self._plt.show(block=False)
        self._raise_window()

        warmup_deadline = time.monotonic() + 0.6
        while time.monotonic() < warmup_deadline:
            try:
                self._fig.canvas.flush_events()
            except Exception:
                pass
            time.sleep(0.02)

        self._last_refresh_t = time.monotonic()

    # ------------------------------------------------------------------
    def _raise_window(self):
        """Best-effort 'bring this window to front' for the active backend."""
        try:
            mgr = self._fig.canvas.manager
            # Tk
            win = getattr(mgr, "window", None)
            if win is not None and hasattr(win, "lift"):
                win.lift()
                if hasattr(win, "attributes"):
                    win.attributes("-topmost", True)
                    win.after_idle(win.attributes, "-topmost", False)
                return
            # Qt
            if hasattr(mgr, "show"):
                mgr.show()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _ensure_scan(self, scan_idx: int):
        while scan_idx > self._current_scan:
            self._current_scan += 1
            self._scans_x.append([])
            self._scans_y.append([])
            (line,) = self._ax.plot([], [], marker=".", linestyle="-",
                                    label=f"scan {self._current_scan + 1}")
            self._lines.append(line)
            self._ax.legend(loc="best", fontsize=8)

    def add_sample(self, sample: Sample):
        if self._ax is None:
            return  # plot not started
        self._ensure_scan(sample.scan)

        xs = self._scans_x[sample.scan]
        ys = self._scans_y[sample.scan]
        xs.append(sample.potential_v)
        ys.append(sample.current_a)
        self._lines[sample.scan].set_data(xs, ys)

        self._counter += 1
        if self._counter % self.update_every != 0:
            return
        # Throttle redraws by wall-clock time so we don't burn CPU when data
        # comes in fast, but still hit ~25 fps when it doesn't.
        now = time.monotonic()
        if now - self._last_refresh_t < self.refresh_min_interval_s:
            return
        self._last_refresh_t = now
        self._refresh()

    def _refresh(self):
        try:
            # Recompute axis limits based on all samples seen so far.
            all_x = [v for line in self._scans_x for v in line]
            all_y = [v for line in self._scans_y for v in line]
            if all_x and all_y:
                xmin, xmax = min(all_x), max(all_x)
                ymin, ymax = min(all_y), max(all_y)
                if xmax > xmin:
                    self._ax.set_xlim(xmin, xmax)
                if ymax > ymin:
                    pad = (ymax - ymin) * (self.ylim_pad - 1) / 2
                    self._ax.set_ylim(ymin - pad, ymax + pad)
            # draw_idle() schedules; flush_events() forces the GUI to
            # actually paint NOW.  This is what makes the plot stream live
            # instead of only appearing at the end of the run.
            self._fig.canvas.draw_idle()
            self._fig.canvas.flush_events()
        except Exception as exc:  # pragma: no cover
            LOG.debug("LivePlot refresh failed: %s", exc)

    def finish(self, keep_open: bool = True):
        """Final redraw.  If `keep_open` is True (default), keeps the figure
        on screen until the user closes it; otherwise closes immediately."""
        if self._ax is None:
            return
        self._refresh()
        if keep_open:
            try:
                self._plt.ioff()
                self._plt.show()
            except Exception:  # pragma: no cover
                pass
        else:
            self._plt.close(self._fig)
