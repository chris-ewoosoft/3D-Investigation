"""What counts as a metric: one predicate, shared by both sides of the gate.

The backend coerces the framework's result object and the quality gate reads a
manifest a hand may have edited; both must agree on which values are
measurements, or a run could pass one and fail the other on the same number.
This module is a leaf on purpose: policy (``gate``) and the framework adapter
(``backend``) can share it without either depending on the other.
"""

from __future__ import annotations

import math
import numbers

__all__ = ["coerce_metric"]


def coerce_metric(value: object) -> float | None:
    """``value`` as a finite float, or ``None`` when it is not a measurement.

    Three things are deliberately rejected:

    * ``bool`` — it subclasses ``int`` and ``float(True) == 1.0``, which would
      let a flag clear any threshold;
    * non-finite values — ``NaN`` compares false against every threshold (so a
      metric that met no bar can slip through), and ``Infinity`` is not JSON;
    * values that overflow ``float`` — ``float(10**400)`` raises
      :class:`OverflowError`, and a metric the gate cannot read is "not
      reported", never a crash halfway through a run.

    ``numbers.Real`` — not ``float`` — is the test, so numpy scalar types
    frameworks like to report are kept instead of silently vanishing: a metric
    that disappears would turn a ``--min-metric`` request into an
    unexplainable "not reported" failure.
    """

    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
