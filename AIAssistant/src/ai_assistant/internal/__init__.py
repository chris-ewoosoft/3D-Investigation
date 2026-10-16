"""Internal infrastructure adapters.

These are framework/engine wrappers that live outside the ``adapters/``
package because Clean Architecture forbids ``orchestration/`` from importing
``adapters/`` (ADR 0002). The orchestration layer reaches the engine
through these wrappers, and the composition root wires them in.
"""
