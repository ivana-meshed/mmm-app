"""
Tests for run_dk_benchmark_all_configs.py — config loading, enrichment and
validation, including the dk_final_with_tv_config.json extra-config setup.
"""

import json
import sys
import unittest
from pathlib import Path

# Make scripts/ importable
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_dk_benchmark_all_configs import (  # noqa: E402
    DEFAULT_MANIFEST_FILE,
    DK_CONFIGS_DIR,
    REQUIRED_UPLOAD_FIELDS,
    enrich_config_for_upload,
    load_config,
    load_config_from_path,
    load_manifest,
    ordered_config_files,
    validate_config_for_upload,
)

TV_CONFIG_NAME = "dk_final_with_tv_config.json"
TV_CONFIG_PATH = DK_CONFIGS_DIR / TV_CONFIG_NAME


class TestDkFinalWithTvConfig(unittest.TestCase):
    """Tests specific to dk_final_with_tv_config.json."""

    def setUp(self):
        self.raw = load_config_from_path(TV_CONFIG_PATH)
        self.enriched = enrich_config_for_upload(self.raw)

    # ── File existence ────────────────────────────────────────────────────────

    def test_file_exists_in_configs_dir(self):
        """Config file must exist in the DK configs directory."""
        self.assertTrue(
            TV_CONFIG_PATH.exists(),
            f"{TV_CONFIG_NAME} not found in {DK_CONFIGS_DIR}",
        )

    def test_file_is_valid_json(self):
        """Config file must be parseable JSON."""
        with open(TV_CONFIG_PATH) as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)

    # ── Required media channel fields ────────────────────────────────────────

    def test_paid_media_spends_not_empty(self):
        self.assertGreater(len(self.raw["paid_media_spends"]), 0)

    def test_paid_media_vars_not_empty(self):
        self.assertGreater(len(self.raw["paid_media_vars"]), 0)

    def test_paid_media_spends_and_vars_same_length(self):
        self.assertEqual(
            len(self.raw["paid_media_spends"]),
            len(self.raw["paid_media_vars"]),
        )

    def test_var_to_spend_mapping_keys_match_paid_media_vars(self):
        mapping = self.raw["var_to_spend_mapping"]
        self.assertEqual(set(mapping.keys()), set(self.raw["paid_media_vars"]))

    def test_var_to_spend_mapping_values_match_paid_media_spends(self):
        mapping = self.raw["var_to_spend_mapping"]
        self.assertEqual(
            set(mapping.values()), set(self.raw["paid_media_spends"])
        )

    # ── TV / radio channels present ──────────────────────────────────────────

    def test_tv_grp_channel_in_paid_media_vars(self):
        """WBR_TOTAL_GRP (TV GRP) should be in paid_media_vars."""
        self.assertIn("WBR_TOTAL_GRP", self.raw["paid_media_vars"])

    def test_radio_grp_channel_in_paid_media_vars(self):
        """BAUER_GRP_FLOW_RADIO should be in paid_media_vars."""
        self.assertIn("BAUER_GRP_FLOW_RADIO", self.raw["paid_media_vars"])

    def test_tv_spend_channel_in_paid_media_spends(self):
        self.assertIn("WBR_TOTAL_SPEND", self.raw["paid_media_spends"])

    def test_radio_spend_channel_in_paid_media_spends(self):
        self.assertIn("BAUER_SPEND_FLOW_RADIO", self.raw["paid_media_spends"])

    # ── Config enrichment ────────────────────────────────────────────────────

    def test_enrich_adds_dep_var_from_selected_goal(self):
        self.assertEqual(self.enriched["dep_var"], self.raw["selected_goal"])

    def test_enrich_adds_dep_var_type_conversion(self):
        self.assertEqual(self.enriched["dep_var_type"], "conversion")

    def test_enrich_adds_date_var(self):
        self.assertEqual(self.enriched["date_var"], "date")

    def test_enrich_preserves_existing_fields(self):
        for key in self.raw:
            self.assertIn(key, self.enriched)
            self.assertEqual(self.enriched[key], self.raw[key])

    def test_enrich_does_not_overwrite_existing_dep_var(self):
        config_with_dep_var = dict(self.raw, dep_var="CUSTOM_KPI")
        enriched = enrich_config_for_upload(config_with_dep_var)
        self.assertEqual(enriched["dep_var"], "CUSTOM_KPI")

    # ── Validation ───────────────────────────────────────────────────────────

    def test_validate_enriched_config_passes(self):
        missing = validate_config_for_upload(self.enriched)
        self.assertEqual(
            missing,
            [],
            f"Enriched TV config is missing required fields: {missing}",
        )

    def test_validate_raw_config_missing_derived_fields(self):
        """Raw (pre-enrichment) config should be missing dep_var etc."""
        raw_without_dep_var = {
            k: v
            for k, v in self.raw.items()
            if k not in ("dep_var", "dep_var_type", "date_var")
        }
        missing = validate_config_for_upload(raw_without_dep_var)
        self.assertIn("dep_var", missing)


class TestLoadConfigHelpers(unittest.TestCase):
    """Tests for load_config / load_config_from_path / load_manifest."""

    def test_load_config_tv_by_filename(self):
        cfg = load_config(TV_CONFIG_NAME)
        self.assertEqual(cfg["country"], "dk")
        self.assertEqual(cfg["name"], "dk_final_with_tv")

    def test_load_config_from_path_tv(self):
        cfg = load_config_from_path(TV_CONFIG_PATH)
        self.assertIn("paid_media_spends", cfg)

    def test_load_manifest_default(self):
        manifest = load_manifest()
        self.assertIn("files", manifest)

    def test_ordered_config_files_returns_list(self):
        files = ordered_config_files()
        self.assertIsInstance(files, list)
        self.assertGreater(len(files), 0)

    def test_all_ordered_config_files_exist(self):
        for filename in ordered_config_files():
            path = DK_CONFIGS_DIR / filename
            self.assertTrue(
                path.exists(),
                f"Config file in manifest not found on disk: {filename}",
            )


class TestExtraConfigIntegration(unittest.TestCase):
    """
    Integration tests for the --extra-config code path.

    Simulates the runtime logic of main() that resolves and validates extra
    configs, using dk_final_with_tv_config.json as the fixture.
    """

    def _resolve_extra_config(self, raw: str) -> Path:
        """Replicate the path resolution logic from main()."""
        candidate = Path(raw)
        if candidate.exists():
            return candidate.resolve()
        return DK_CONFIGS_DIR / candidate.name

    def test_bare_filename_resolves_to_configs_dir(self):
        resolved = self._resolve_extra_config(TV_CONFIG_NAME)
        self.assertEqual(resolved, TV_CONFIG_PATH)

    def test_absolute_path_resolves_directly(self):
        resolved = self._resolve_extra_config(str(TV_CONFIG_PATH))
        self.assertEqual(resolved, TV_CONFIG_PATH)

    def test_extra_config_file_found_after_resolution(self):
        resolved = self._resolve_extra_config(TV_CONFIG_NAME)
        self.assertTrue(resolved.exists())

    def test_extra_config_loads_and_enriches_without_error(self):
        resolved = self._resolve_extra_config(TV_CONFIG_NAME)
        cfg = load_config_from_path(resolved)
        enriched = enrich_config_for_upload(cfg)
        missing = validate_config_for_upload(enriched)
        self.assertEqual(
            missing,
            [],
            f"TV extra config validation failed for fields: {missing}",
        )

    def test_extra_config_label_is_name_field(self):
        """The label shown in logs should come from the 'name' field."""
        cfg = load_config_from_path(TV_CONFIG_PATH)
        label = cfg.get("name") or TV_CONFIG_PATH.stem
        self.assertEqual(label, "dk_final_with_tv")


if __name__ == "__main__":
    unittest.main()
