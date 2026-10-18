"""Unit tests for the model contract and configuration resolution."""

from __future__ import annotations

import unittest
from pathlib import Path

from cvtrain.config import (
    ALL_MODEL_KEYS,
    DEFAULT_MODELS,
    MANIFEST_NAME,
    MODEL_REGISTRY,
    MODEL_SPECS,
    PACKAGE_DIR,
    ModelRegistry,
    ModelSpec,
    TrainConfig,
    register_model_spec,
    resolve_models,
)


class ModelSpecTests(unittest.TestCase):
    def test_keys_match_spec_map(self) -> None:
        self.assertEqual(ALL_MODEL_KEYS, ("det", "seg", "track"))
        for key, spec in MODEL_SPECS.items():
            self.assertEqual(key, spec.key)

    def test_exported_names_are_unique(self) -> None:
        outputs = [spec.output for spec in MODEL_SPECS.values()]
        self.assertEqual(len(outputs), len(set(outputs)))

    def test_expected_contract_names(self) -> None:
        # These names are asserted against AppConstants.h in test_contract.py.
        self.assertEqual(MODEL_SPECS["det"].output, "yolo11n.onnx")
        self.assertEqual(MODEL_SPECS["seg"].output, "yolo11n-seg.onnx")
        self.assertEqual(MODEL_SPECS["track"].output, "yolo11x-tracking.onnx")


class ResolveModelsTests(unittest.TestCase):
    def test_empty_selection_defaults(self) -> None:
        self.assertEqual(resolve_models(()), DEFAULT_MODELS)
        self.assertEqual(resolve_models(None), DEFAULT_MODELS)

    def test_single_selection(self) -> None:
        self.assertEqual(resolve_models(["det"]), ("det",))
        self.assertEqual(resolve_models(["track"]), ("track",))

    def test_selection_is_canonically_ordered(self) -> None:
        self.assertEqual(resolve_models(["track", "det"]), ("det", "track"))

    def test_unknown_keys_are_ignored(self) -> None:
        self.assertEqual(resolve_models(["bogus"]), DEFAULT_MODELS)
        self.assertEqual(resolve_models(["seg", "bogus"]), ("seg",))


class TrainConfigTests(unittest.TestCase):
    def test_device_class_normalises_spelling(self) -> None:
        # The canonical spelling used as provenance: blank means "auto", and
        # whitespace/case around a comma list is not a different experiment.
        self.assertEqual(TrainConfig().device_class, "auto")
        self.assertEqual(TrainConfig(device="   ").device_class, "auto")
        self.assertEqual(TrainConfig(device="0, 1").device_class, "0,1")
        self.assertEqual(TrainConfig(device="CPU").device_class, "cpu")
        self.assertEqual(TrainConfig(device="cuda:0").device_class, "cuda:0")

    def test_the_quality_gate_is_opt_in(self) -> None:
        # No threshold by default: behaviour for every existing caller (the Qt
        # dialog included) must stay exactly what it was.
        self.assertEqual(TrainConfig().min_metrics, ())


class PathResolutionTests(unittest.TestCase):
    def test_weights_path_resolves_from_the_package_root_by_default(self) -> None:
        # preflight, the backend and the provenance digest must agree on where
        # checkpoints live, so all three resolve the path through TrainConfig.
        cfg = TrainConfig()
        for key, spec in MODEL_SPECS.items():
            with self.subTest(key=key):
                self.assertEqual(cfg.weights_path(spec), PACKAGE_DIR.parent / spec.ckpt)
                self.assertTrue(str(cfg.weights_path(spec)).endswith(spec.ckpt))

    def test_weights_dir_override_moves_the_checkpoint_lookup(self) -> None:
        # Inputs move like outputs: a caller that redirects models_dir/runs_dir
        # can redirect the checkpoints the run starts from too.
        spec = MODEL_SPECS["det"]
        cfg = TrainConfig(weights_dir=Path("C:/elsewhere/weights"))
        self.assertEqual(cfg.weights_path(spec), Path("C:/elsewhere/weights") / spec.ckpt)

    def test_output_path_honours_a_models_dir_override(self) -> None:
        spec = MODEL_SPECS["det"]
        cfg = TrainConfig(models_dir=Path("C:/elsewhere/Models"))
        self.assertEqual(cfg.output_path(spec), Path("C:/elsewhere/Models") / spec.output)

    def test_manifest_path_sits_beside_the_models(self) -> None:
        cfg = TrainConfig(models_dir=Path("C:/elsewhere/Models"))
        self.assertEqual(cfg.manifest_path, Path("C:/elsewhere/Models") / MANIFEST_NAME)


class ModelRegistryTests(unittest.TestCase):
    def test_empty_registry(self) -> None:
        reg = ModelRegistry()
        self.assertEqual(len(reg), 0)
        self.assertEqual(reg.keys(), ())
        self.assertEqual(reg.values(), ())
        self.assertEqual(reg.items(), ())
        self.assertEqual(reg.as_dict(), {})
        self.assertNotIn("det", reg)

    def test_register_and_lookup(self) -> None:
        reg = ModelRegistry()
        spec = ModelSpec("custom", "c.pt", "c.onnx", "c", "custom_role", "Custom")
        reg.register(spec)
        self.assertEqual(len(reg), 1)
        self.assertIn("custom", reg)
        self.assertEqual(reg.get("custom"), spec)
        self.assertEqual(reg["custom"], spec)
        self.assertEqual(reg.keys(), ("custom",))
        self.assertEqual(reg.values(), (spec,))
        self.assertEqual(reg.items(), (("custom", spec),))
        self.assertEqual(reg.as_dict(), {"custom": spec})

    def test_register_type_error(self) -> None:
        reg = ModelRegistry()
        with self.assertRaises(TypeError):
            reg.register("not_a_spec")  # type: ignore[arg-type]

    def test_register_duplicate_key_rejected(self) -> None:
        reg = ModelRegistry()
        spec1 = ModelSpec("det", "d1.pt", "d1.onnx", "d1", "role", "label")
        spec2 = ModelSpec("det", "d2.pt", "d2.onnx", "d2", "role", "label")
        reg.register(spec1)
        with self.assertRaises(ValueError):
            reg.register(spec2)

    def test_register_duplicate_output_rejected(self) -> None:
        reg = ModelRegistry()
        spec1 = ModelSpec("det1", "d1.pt", "same.onnx", "d1", "role", "label")
        spec2 = ModelSpec("det2", "d2.pt", "same.onnx", "d2", "role", "label")
        reg.register(spec1)
        with self.assertRaises(ValueError):
            reg.register(spec2)

    def test_get_unknown_key_raises_key_error(self) -> None:
        reg = ModelRegistry()
        with self.assertRaises(KeyError):
            reg.get("nonexistent")

    def test_register_model_spec_updates_globals(self) -> None:
        spec = ModelSpec("pose_test", "pose.pt", "pose.onnx", "pose", "pose", "Pose")
        try:
            register_model_spec(spec)
            self.assertIn("pose_test", MODEL_REGISTRY)
            self.assertIn("pose_test", MODEL_SPECS)
            self.assertEqual(MODEL_SPECS["pose_test"], spec)
        finally:
            MODEL_REGISTRY._specs.pop("pose_test", None)
            MODEL_SPECS.pop("pose_test", None)

    def test_resolve_models_with_custom_registry(self) -> None:
        spec = ModelSpec("custom", "c.pt", "c.onnx", "c", "role", "label")
        reg = ModelRegistry([spec])
        self.assertEqual(resolve_models(["custom"], registry=reg), ("custom",))
        self.assertEqual(resolve_models([], registry=reg), DEFAULT_MODELS)


if __name__ == "__main__":
    unittest.main()
