"""Drop-detect placeholder.

Goal: when the operator places a liquid sample on the EmStat4T's electrode,
the cell impedance / open-circuit current changes sharply.  By holding the
cell at a small bias and watching the WE current (or by reading OCP), we can
auto-trigger a CV / SWV scan without needing a button press.

This module exposes the *signature* of that future trigger so the rest of
`cv_app` can already use it:

    detector = DropDetector(threshold_a=10e-9, settle_s=0.3)
    detector.wait_for_drop()         # blocks until the threshold is crossed
    run_cv(params, ...)

For now, `wait_for_drop()` is a stub that simply waits for the user to press
ENTER.  The hardware-watching version can be slotted in later by replacing
`wait_for_drop()` and reusing the rest of the pipeline.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

LOG = logging.getLogger(__name__)


@dataclass
class DropDetector:
    """Placeholder API for an automatic drop-detect trigger.

    Parameters are kept here so the same interface works for the future
    hardware version.
    """

    bias_potential_v: float = 0.0
    threshold_a: float = 10e-9       # current change that signals a drop
    settle_s: float = 0.3            # wait this long after threshold to stabilise
    poll_interval_s: float = 0.05
    timeout_s: Optional[float] = None  # None = wait forever
    use_hardware: bool = False        # set True once implemented

    def wait_for_drop(self) -> None:
        """Block until a drop is detected (or the user simulates one).

        Today this is a manual gate — once the actual MethodSCRIPT-based drop
        detection is wired up (e.g. using `MSExample016-Drop_detect.mscr` as a
        starting point), implement it inside the `if self.use_hardware` branch.
        """
        if not self.use_hardware:
            LOG.info("Drop-detect placeholder: press ENTER once you've dropped "
                     "the sample on the sensor...")
            try:
                input()
            except EOFError:
                # Non-interactive context (e.g. piped) — just continue.
                pass
            time.sleep(self.settle_s)
            return

        # ------------------------------------------------------------------
        # FUTURE: real hardware-based drop detection
        # ------------------------------------------------------------------
        # 1. Open the device.
        # 2. Run a small MethodSCRIPT that holds the cell at `bias_potential_v`
        #    and streams WE current at e.g. 5 Hz.
        # 3. Compare each new current sample to a rolling baseline; if the
        #    absolute change exceeds `threshold_a`, return.
        # 4. On timeout, raise TimeoutError.
        raise NotImplementedError(
            "Hardware drop detection isn't implemented yet — set "
            "use_hardware=False to keep using the manual gate."
        )
