import unittest

from enviroplus_homeassistant.gas import apply_gas_indices, build_gas_baselines


class GasBaselineTests(unittest.TestCase):
    def test_build_gas_baselines_rejects_non_positive_values(self):
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            build_gas_baselines(oxidising=0)

    def test_apply_gas_indices_uses_expected_direction_per_channel(self):
        baselines = build_gas_baselines(oxidising=10.0, reducing=20.0, nh3=40.0)
        readings = {
            "gas_oxidising": 12.0,
            "gas_reducing": 10.0,
            "gas_nh3": 20.0,
        }

        enriched = apply_gas_indices(readings, baselines)

        self.assertEqual(enriched["gas_no2_index"], 1.2)
        self.assertEqual(enriched["gas_co_index"], 2.0)
        self.assertEqual(enriched["gas_nh3_index"], 2.0)

    def test_apply_gas_indices_leaves_unconfigured_channels_unpublished(self):
        baselines = build_gas_baselines(oxidising=10.0)
        readings = {
            "gas_oxidising": 10.0,
            "gas_reducing": 5.0,
            "gas_nh3": 5.0,
        }

        enriched = apply_gas_indices(readings, baselines)

        self.assertIn("gas_no2_index", enriched)
        self.assertNotIn("gas_co_index", enriched)
        self.assertNotIn("gas_nh3_index", enriched)


if __name__ == "__main__":
    unittest.main()
