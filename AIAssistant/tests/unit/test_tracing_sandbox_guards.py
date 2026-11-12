"""Guard-path coverage for the tracer cache, the OTLP exporter wiring and the sandbox front door.

The OTel tracer resolves once and is cached: the second call must take the
cache-hit arm, and the first call must handle both an enabled SDK and a
missing one (simulated by halting the import, which raises ``ImportError``
exactly like an uninstalled package). Module-global tracer state is saved
and restored so test order cannot matter. The exporter arms (endpoint set /
package missing / no endpoint) are pinned with fake modules so the wiring is
verified whether or not ``opentelemetry-exporter-otlp`` is installed.

The sandbox arms are its two cheapest rejections: an interpreter flag the
policy has never heard of, and a command line ``shlex`` itself refuses.
"""
from __future__ import annotations

import socket
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ai_assistant.observability.tracing as tracing
from ai_assistant.tools.sandbox import _inline_code_requested, run


class TracerCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = tracing._tracer

    def tearDown(self) -> None:
        tracing._tracer = self._saved

    def test_a_cached_tracer_is_returned_without_rechecking(self):
        tracing._tracer = False
        self.assertIs(tracing._get_tracer(), False)

    def test_a_disabled_sdk_caches_the_disabled_sentinel(self):
        tracing._tracer = None
        with patch.object(tracing, "observability_enabled", return_value=False):
            self.assertIs(tracing._get_tracer(), False)
        self.assertIs(tracing._tracer, False)

    def test_a_missing_opentelemetry_package_caches_disabled(self):
        """``None`` in ``sys.modules`` makes the import raise ``ImportError``."""
        tracing._tracer = None
        with (
            patch.object(tracing, "observability_enabled", return_value=True),
            patch.dict(sys.modules, {"opentelemetry": None}),
        ):
            self.assertIs(tracing._get_tracer(), False)

    def test_an_installed_sdk_yields_a_cached_tracer(self):
        """A fake SDK proves the success arm without needing the package."""
        from types import SimpleNamespace

        fake_trace = SimpleNamespace(get_tracer=lambda name: "tracer!")
        fake_otel = SimpleNamespace(trace=fake_trace)
        tracing._tracer = None
        with (
            patch.object(tracing, "observability_enabled", return_value=True),
            patch.object(tracing, "otlp_exporter_endpoint", return_value=None),
            patch.dict(sys.modules, {"opentelemetry": fake_otel,
                                     "opentelemetry.trace": fake_trace}),
        ):
            self.assertEqual(tracing._get_tracer(), "tracer!")
        self.assertEqual(tracing._tracer, "tracer!")

    def test_a_lost_lock_race_returns_the_cached_sentinel(self):
        """``None`` at the outer check but set on entry means another thread
        won the race — the inner check must take the cache-hit exit."""

        class _PreemptingLock:
            def __enter__(self) -> None:
                tracing._tracer = False

            def __exit__(self, *args) -> bool:
                return False

        tracing._tracer = None
        with (
            patch.object(tracing, "_tracer_lock", _PreemptingLock()),
            patch.object(tracing, "observability_enabled", return_value=True),
        ):
            self.assertIs(tracing._get_tracer(), False)


class OtlpExporterWiringTests(unittest.TestCase):
    """Spans reach a collector only when an endpoint is actually configured.

    The exporter package may or may not be installed, so all three arms are
    pinned with fake modules in ``sys.modules``: endpoint wired, endpoint set
    but package missing (one warning, spans stay local), no endpoint (nothing
    configured at all).
    """

    def setUp(self) -> None:
        self._saved = tracing._tracer
        self.addCleanup(setattr, tracing, "_tracer", self._saved)
        tracing._tracer = None

    def _fake_modules(self, exporter_installed: bool = True) -> dict:
        from types import ModuleType

        recorded: dict[str, object] = {}

        trace_mod = ModuleType("opentelemetry.trace")
        trace_mod.get_tracer = lambda name: "tracer!"  # type: ignore[attr-defined]
        trace_mod.set_tracer_provider = (  # type: ignore[attr-defined]
            lambda provider: recorded.setdefault("provider", provider)
        )

        sdk_trace = ModuleType("opentelemetry.sdk.trace")

        class _Provider:
            def __init__(self) -> None:
                self.processors: list = []

            def add_span_processor(self, processor: object) -> None:
                self.processors.append(processor)

        sdk_trace.TracerProvider = _Provider  # type: ignore[attr-defined]
        export_mod = ModuleType("opentelemetry.sdk.trace.export")
        export_mod.BatchSpanProcessor = (  # type: ignore[attr-defined]
            lambda span_exporter: ("processor", span_exporter)
        )
        exporter_mod = ModuleType(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
        exporter_mod.OTLPSpanExporter = (  # type: ignore[attr-defined]
            lambda endpoint=None: ("exporter", endpoint)
        )

        top = ModuleType("opentelemetry")
        top.trace = trace_mod  # type: ignore[attr-defined]
        modules: dict = {
            "opentelemetry": top,
            "opentelemetry.trace": trace_mod,
            "opentelemetry.sdk": ModuleType("opentelemetry.sdk"),
            "opentelemetry.sdk.trace": sdk_trace,
            "opentelemetry.sdk.trace.export": export_mod,
            "opentelemetry.exporter": ModuleType("opentelemetry.exporter"),
            "opentelemetry.exporter.otlp": ModuleType("opentelemetry.exporter.otlp"),
            "opentelemetry.exporter.otlp.proto": ModuleType(
                "opentelemetry.exporter.otlp.proto"
            ),
            "opentelemetry.exporter.otlp.proto.http": ModuleType(
                "opentelemetry.exporter.otlp.proto.http"
            ),
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": exporter_mod,
        }
        if not exporter_installed:
            # ``None`` in sys.modules makes the import raise ImportError,
            # exactly like an uninstalled package.
            modules["opentelemetry.exporter.otlp.proto.http.trace_exporter"] = None
        self._recorded = recorded
        return modules

    def test_an_endpoint_wires_the_exporter_into_the_provider(self) -> None:
        url = "http://collector:4318/v1/traces"
        with (
            patch.object(tracing, "observability_enabled", return_value=True),
            patch.object(tracing, "otlp_exporter_endpoint", return_value=url),
            patch.dict(sys.modules, self._fake_modules()),
        ):
            self.assertEqual(tracing._get_tracer(), "tracer!")

        processors = self._recorded["provider"].processors
        self.assertEqual(len(processors), 1)
        tag, exporter = processors[0]
        self.assertEqual(tag, "processor")
        self.assertIsInstance(exporter, tracing._ResilientOtlpExporter)
        self.assertEqual(exporter._inner, ("exporter", url))
        self.assertEqual(exporter._endpoint, url)

    def test_a_missing_exporter_package_degrades_to_local_spans(self) -> None:
        with (
            patch.object(tracing, "observability_enabled", return_value=True),
            patch.object(tracing, "otlp_exporter_endpoint", return_value="http://c:1/v1/traces"),
            patch.dict(sys.modules, self._fake_modules(exporter_installed=False)),
            self.assertLogs("ai_assistant.observability.tracing", level="WARNING") as caught,
        ):
            self.assertEqual(tracing._get_tracer(), "tracer!")

        self.assertIn("not installed", caught.output[0])
        self.assertNotIn("provider", self._recorded)

    def test_without_an_endpoint_no_exporter_is_configured(self) -> None:
        with (
            patch.object(tracing, "observability_enabled", return_value=True),
            patch.object(tracing, "otlp_exporter_endpoint", return_value=None),
            patch.dict(sys.modules, self._fake_modules()),
        ):
            self.assertEqual(tracing._get_tracer(), "tracer!")

        self.assertNotIn("provider", self._recorded)


class OtlpReachabilityTests(unittest.TestCase):
    def test_rejects_non_http_and_malformed_endpoints(self) -> None:
        for bad in ("ftp://collector:4318/v1/traces", "http:///v1/traces", "http://collector:bad/v1/traces"):
            self.assertFalse(tracing._is_otlp_endpoint_reachable(bad, timeout=0.05))

    def test_returns_false_when_dns_resolution_fails(self) -> None:
        with patch.object(tracing.socket, "getaddrinfo", side_effect=OSError()):
            self.assertFalse(
                tracing._is_otlp_endpoint_reachable("http://collector:4318/v1/traces", timeout=0.05)
            )

    def test_defaults_ports_for_http_and_https_and_returns_false_when_all_connects_fail(self) -> None:
        seen_ports: list[int] = []

        def _fake_getaddrinfo(host, port, type=0):
            seen_ports.append(port)
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", port, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", port)),
            ]

        class _RefusingSocket:
            def __init__(self, family, *_args) -> None:
                self.family = family

            def __enter__(self) -> "_RefusingSocket":
                return self

            def __exit__(self, *args) -> bool:
                return False

            def settimeout(self, _timeout: float) -> None:
                pass

            def connect(self, _addr) -> None:
                raise ConnectionRefusedError()

        with (
            patch.object(tracing.socket, "getaddrinfo", side_effect=_fake_getaddrinfo),
            patch.object(tracing.socket, "socket", side_effect=_RefusingSocket),
        ):
            self.assertFalse(tracing._is_otlp_endpoint_reachable("http://collector/v1/traces"))
            self.assertFalse(tracing._is_otlp_endpoint_reachable("https://collector/v1/traces"))

        self.assertEqual(seen_ports, [80, 443])

    def test_detects_listening_loopback_collector(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertTrue(
                tracing._is_otlp_endpoint_reachable(
                    f"http://127.0.0.1:{port}/v1/traces",
                    timeout=0.5,
                )
            )


class _FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _StubExporter:
    def __init__(self, result: object = "SUCCESS", exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.exported: list[object] = []
        self.flushed = 0
        self.shut_down = False

    def export(self, spans: object) -> object:
        self.exported.append(spans)
        if self.exc is not None:
            raise self.exc
        return self.result

    def force_flush(self, _timeout_millis: int = 30000) -> bool:
        self.flushed += 1
        return True

    def shutdown(self) -> None:
        self.shut_down = True


class ResilientOtlpExporterTests(unittest.TestCase):
    def test_unreachable_collector_pauses_export_without_warning_and_recovers(self) -> None:
        clock = _FakeClock()
        probe_results = iter([False, False, True])
        probe_calls = 0

        def _probe(_endpoint: str, _timeout: float) -> bool:
            nonlocal probe_calls
            probe_calls += 1
            return next(probe_results)

        inner = _StubExporter(result="SUCCESS")
        exporter = tracing._ResilientOtlpExporter(
            inner,
            "http://localhost:4318/v1/traces",
            failure_result="FAILURE",
            cooldown_seconds=60.0,
            probe=_probe,
            clock=clock,
        )

        # First batch: probe fails -> logs once at INFO, skips inner exporter.
        with self.assertLogs("ai_assistant.observability.tracing", level="INFO") as logs:
            self.assertEqual(exporter.export(["s1"]), "FAILURE")
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelname, "INFO")
        self.assertIn("unreachable", logs.output[0])
        self.assertEqual(inner.exported, [])

        # During cooldown: skips probe, skips inner exporter, flushes cleanly.
        clock.now += 10.0
        self.assertEqual(exporter.export(["s2"]), "FAILURE")
        self.assertTrue(exporter.force_flush())
        self.assertEqual(probe_calls, 1)
        self.assertEqual(inner.flushed, 0)

        # After cooldown, still unreachable -> no duplicate INFO log.
        clock.now += 60.0
        with self.assertNoLogs("ai_assistant.observability.tracing", level="INFO"):
            self.assertEqual(exporter.export(["s3"]), "FAILURE")
        self.assertEqual(probe_calls, 2)

        # After next cooldown, collector comes back -> logs resume and exports.
        clock.now += 60.0
        with self.assertLogs("ai_assistant.observability.tracing", level="INFO") as resumed:
            self.assertEqual(exporter.export(["s4"]), "SUCCESS")
        self.assertIn("resuming", resumed.output[0])
        self.assertEqual(inner.exported, [["s4"]])

        # Subsequent healthy batches skip probing.
        self.assertEqual(exporter.export(["s5"]), "SUCCESS")
        self.assertEqual(probe_calls, 3)
        self.assertTrue(exporter.force_flush())
        self.assertEqual(inner.flushed, 1)

    def test_inner_failure_or_exception_trips_circuit_until_cooldown(self) -> None:
        clock = _FakeClock()
        probe_calls = 0

        def _probe(_endpoint: str, _timeout: float) -> bool:
            nonlocal probe_calls
            probe_calls += 1
            return True

        inner = _StubExporter(result="FAILURE")
        exporter = tracing._ResilientOtlpExporter(
            inner,
            "http://localhost:4318/v1/traces",
            failure_result="FAILURE",
            cooldown_seconds=30.0,
            probe=_probe,
            clock=clock,
        )
        self.assertEqual(exporter.export(["s1"]), "FAILURE")
        # Circuit tripped by FAILURE result -> next batch skips probe & inner.
        self.assertEqual(exporter.export(["s2"]), "FAILURE")
        self.assertEqual(probe_calls, 1)
        self.assertEqual(len(inner.exported), 1)

        # Advance past cooldown and simulate an exception from inner.export.
        clock.now += 31.0
        inner.exc = RuntimeError("broken pipe")
        with self.assertLogs("ai_assistant.observability.tracing", level="INFO") as caught:
            self.assertEqual(exporter.export(["s3"]), "FAILURE")
        self.assertIn("resuming", caught.output[0])
        self.assertIn("broken pipe", "\n".join(caught.output))
        self.assertEqual(exporter.export(["s4"]), "FAILURE")
        self.assertEqual(probe_calls, 2)

    def test_shutdown_blocks_subsequent_export_and_force_flush(self) -> None:
        inner = _StubExporter(result="SUCCESS")
        exporter = tracing._ResilientOtlpExporter(
            inner,
            "http://localhost:4318/v1/traces",
            failure_result="FAILURE",
            probe=lambda _e, _t: True,
        )
        exporter.shutdown()
        self.assertTrue(inner.shut_down)
        self.assertEqual(exporter.export(["s1"]), "FAILURE")
        self.assertFalse(exporter.force_flush())
        self.assertEqual(inner.exported, [])


class InlineCodeFlagTests(unittest.TestCase):
    def test_an_unknown_double_dash_flag_is_inline_code(self):
        self.assertTrue(_inline_code_requested(["python", "--bogus=1"]))

    def test_a_safe_double_dash_flag_with_a_value_is_allowed(self):
        self.assertFalse(_inline_code_requested(["python", "--help=x", "job.py"]))

    def test_a_script_path_is_not_inline_code(self):
        self.assertFalse(_inline_code_requested(["python", "job.py"]))

    def test_a_non_interpreter_ignores_its_flags(self):
        self.assertFalse(_inline_code_requested(["ruff", "--bogus=1"]))

    def test_stdin_and_terminator_markers_are_inline_code(self):
        self.assertTrue(_inline_code_requested(["python", "-"]))
        self.assertTrue(_inline_code_requested(["python", "job.py", "--"]))

    def test_safe_short_flags_are_allowed(self):
        self.assertFalse(_inline_code_requested(["python", "-B", "job.py"]))

    def test_short_options_are_inline_code(self):
        """``-c``/``-m`` (attached or not) turn the interpreter into ``eval``."""
        self.assertTrue(_inline_code_requested(["python", "-c", "print(1)"]))
        self.assertTrue(_inline_code_requested(["python", "-mmod"]))


class SandboxFrontDoorTests(unittest.TestCase):
    def test_an_unparseable_command_is_rejected_without_running(self):
        # The local runtime is requested explicitly: the isolation gate is on
        # by default (ADR 0023), and it would otherwise answer before the
        # parser is reached — which is a different refusal than the one this
        # case pins.
        settings = SimpleNamespace(
            sandbox_enabled=True,
            sandbox_runtime="local",
            sandbox_image="agent:latest",
            max_write_bytes=1024 * 1024,
            sandbox_require_isolation=False,
        )
        with patch("ai_assistant.tools.sandbox.load_agent_runtime_settings", return_value=settings):
            outcome = run("git 'abc", ".", 10)
        self.assertIn("error", outcome)
        self.assertIn("Invalid command", outcome["error"])


if __name__ == "__main__":
    unittest.main()
