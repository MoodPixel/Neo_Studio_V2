from __future__ import annotations

import unittest

from neo_app.image.krea2_contract import check_krea2_compatibility, classify_krea2_vae


class Krea2CompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.family = "krea2"
        self.model = "krea2_raw_fp8.safetensors"
        self.encoder = "qwen3vl_4b.safetensors"

    def test_official_qwen_vae_is_verified(self) -> None:
        result = check_krea2_compatibility(
            self.family,
            self.model,
            self.encoder,
            "qwen_image_vae.safetensors",
        )
        self.assertTrue(result.compatible)
        self.assertEqual(result.vae_kind, "qwen_image_vae")

    def test_flux2_vae_is_experimental_and_allowed_to_runtime(self) -> None:
        self.assertEqual(classify_krea2_vae("flux2-vae.safetensors"), "experimental_flux2_vae")
        result = check_krea2_compatibility(
            self.family,
            self.model,
            self.encoder,
            "flux2-vae.safetensors",
        )
        self.assertIsNone(result.compatible)
        self.assertEqual(result.vae_kind, "experimental_flux2_vae")
        self.assertIn("ComfyUI", result.message)

    def test_flux2_underscore_alias_is_detected(self) -> None:
        self.assertEqual(classify_krea2_vae("flux_2_vae_fp16.safetensors"), "experimental_flux2_vae")

    def test_generic_foreign_flux_vae_remains_blocked_by_default(self) -> None:
        result = check_krea2_compatibility(
            self.family,
            self.model,
            self.encoder,
            "flux_ae.safetensors",
        )
        self.assertFalse(result.compatible)
        self.assertEqual(result.vae_kind, "foreign_flux_ae")

    def test_explicit_override_allows_generic_foreign_vae_as_warning(self) -> None:
        result = check_krea2_compatibility(
            self.family,
            self.model,
            self.encoder,
            "flux_ae.safetensors",
            allow_experimental_vae=True,
        )
        self.assertIsNone(result.compatible)
        self.assertIn("override", result.message.lower())

    def test_experimental_vae_does_not_bypass_wrong_text_encoder(self) -> None:
        result = check_krea2_compatibility(
            self.family,
            self.model,
            "qwen2.5_vl_7b.safetensors",
            "flux2-vae.safetensors",
            allow_experimental_vae=True,
        )
        self.assertFalse(result.compatible)
        self.assertEqual(result.text_encoder_kind, "qwen2_family")


if __name__ == "__main__":
    unittest.main()
