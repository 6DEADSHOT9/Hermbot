import math
import unittest
from decimal import Decimal

from backend import limits


MAC = {
    "cpus": 4, "memory_bytes": 8_320_299_008, "pid_max": 99_999,
    "threads_max": 63_765, "file_max": 811_881, "nr_open": 1_048_576,
    "pids_limit": True, "memory_limit": True, "swap_limit": True,
    "logging_driver": "json-file", "cgroup_version": "2", "rootless": False,
}
BOTS = [{"memory_mb": size} for size in (4096, 2048, 2048)]


class LimitTests(unittest.TestCase):
    def test_unchanged_database_cpu_value_is_grandfathered(self):
        limits.validate_resources(1024, 1.1, {'cpus': 1}, existing={'cpus': Decimal('1.1')})
        with self.assertRaises(ValueError):
            limits.validate_resources(1024, 1.2, {'cpus': 1}, existing={'cpus': Decimal('1.1')})

    def test_process_examples_separate_share_default_and_maximum(self):
        for ceiling, max_bots, default, maximum, capacity in (
            (63_765, 10, 1024, 4782, 93),
            (163_840, 10, 4096, 12288, 240),
            (32_768, 10, 512, 2457, 48),
            (12_000, 10, 512, 900, 17),
            (12_000, 30, 512, 512, 17),
        ):
            with self.subTest(ceiling=ceiling, max_bots=max_bots):
                machine = {"pid_max": ceiling, "threads_max": ceiling + 100}
                row = limits.describe(machine, max_bots)["pids_limit"]
                self.assertEqual(row["value"], default)
                self.assertEqual(row["hard"], [128, maximum])
                self.assertEqual(limits.capacity(machine, [], max_bots)["process_capacity"], capacity)

    def test_workspace_counts_do_not_change_container_defaults(self):
        for max_bots in (3, 10, 30):
            self.assertEqual(limits.resolved_values(MAC, max_bots), limits.resolved_values(MAC))

    def test_process_fallback_and_one_reading(self):
        for reading in (None, 0, -1, True, "63765", math.nan, math.inf, 10 ** 400):
            row = limits.describe({"pid_max": reading})["pids_limit"]
            self.assertEqual(row["value"], 512)
            self.assertTrue(row["uncertain"])
        self.assertEqual(limits.describe({"threads_max": 63765})["pids_limit"]["value"], 1024)

    def test_nofile_not_a_share_and_hard_bound_is_kernel_ceiling(self):
        for max_bots in (3, 10, 30):
            row = limits.describe(MAC, max_bots)["nofile_hard"]
            self.assertEqual(row["value"], 16384)
            self.assertEqual(row["hard"], [1024, 1048576])
        row = limits.describe({"nr_open": 8192})["nofile_hard"]
        self.assertEqual(row["value"], 8192)
        self.assertEqual(row["hard"], [1024, 8192])
        self.assertTrue(limits.describe({})["nofile_hard"]["uncertain"])

    def test_resolved_values_remain_plain_and_null_resets(self):
        values = limits.resolved_values(MAC, overrides={"pids_limit": 2048, "log_files": None})
        self.assertEqual(values, {"pids_limit": 2048, "nofile_hard": 16384, "allow_swap": False,
                                  "log_size_mb": 10, "log_files": 5})
        self.assertEqual(limits.describe(MAC, overrides={"pids_limit": 2048})["pids_limit"]["source"], "owner")
        self.assertEqual(limits.validate_overrides({"pids_limit": None}, MAC)["pids_limit"]["source"], "computed")

    def test_override_hard_bounds_and_types(self):
        for values in ({"missing": 1}, {"pids_limit": 127}, {"pids_limit": 4783},
                       {"pids_limit": True}, {"pids_limit": 512.0}, {"pids_limit": math.nan},
                       {"nofile_hard": 1023}, {"nofile_hard": 1048577}, {"allow_swap": 1},
                       {"log_size_mb": 0}, {"log_files": 21}, []):
            with self.subTest(values=values), self.assertRaises(ValueError):
                limits.validate_overrides(values, MAC)
        limits.validate_overrides({"pids_limit": 128, "nofile_hard": 1048576,
                                   "allow_swap": True, "log_files": 20}, MAC)

    def test_recommendation_warnings_are_separate(self):
        self.assertEqual(limits.override_warnings({"pids_limit": 512, "log_files": 5}, MAC), [])
        warnings = limits.override_warnings({"pids_limit": 128, "nofile_hard": 100000,
                                             "allow_swap": True, "log_files": 20}, MAC)
        self.assertEqual(len(warnings), 4)
        self.assertIn("allow_swap=True", warnings[2])

    def test_detected_support_is_not_claimed_as_verified(self):
        self.assertEqual(limits.describe(MAC)["pids_limit"]["enforcement"], "unverified")
        self.assertEqual(limits.describe({"pids_limit": False})["pids_limit"]["enforcement"], "not_enforced")
        self.assertEqual(limits.describe({"swap_limit": False})["allow_swap"]["enforcement"], "not_enforced")
        self.assertTrue(all(row["applies"] == "recreate" for row in limits.describe(MAC).values()))

    def test_machine_memory_and_cpu_bounds(self):
        bounds = limits.resource_bounds(MAC)
        self.assertEqual(bounds["memory_max_mb"], 6398)
        self.assertEqual(bounds["memory_default_mb"], 2048)
        self.assertEqual(bounds["memory_options_mb"], [1024, 2048, 4096])
        self.assertEqual(bounds["cpus_max"], 4)
        self.assertEqual(bounds["cpus_default"], 1.5)
        big = limits.resource_bounds({"memory_bytes": 64 * limits.GIB, "cpus": 32})
        self.assertEqual(big["memory_max_mb"], 8192)
        self.assertEqual(big["memory_default_mb"], 4096)
        self.assertEqual(big["cpus_max"], 32)

    def test_unknown_resource_defaults_and_uncertainty(self):
        for reading in (None, 0, -1, True, "8000000000", math.nan, math.inf, 10 ** 400):
            with self.subTest(reading=reading):
                bounds = limits.resource_bounds({"memory_bytes": reading, "cpus": reading})
                self.assertEqual(bounds["memory_default_mb"], 1024)
                self.assertEqual(bounds["cpus_default"], .25)
                self.assertTrue(bounds["memory_uncertain"])
                self.assertTrue(bounds["cpus_uncertain"])
        limits.validate_resources(8192, 4, {})

    def test_known_machine_below_floor_blocks_new_resources(self):
        machine = {"memory_bytes": 2 * limits.GIB, "cpus": 1}
        bounds = limits.resource_bounds(machine)
        self.assertEqual(bounds["memory_max_mb"], 512)
        self.assertEqual(bounds["memory_options_mb"], [])
        self.assertFalse(bounds["memory_uncertain"])
        with self.assertRaises(ValueError):
            limits.validate_resources(1024, 1, machine)

    def test_resource_validation_and_independent_grandfathering(self):
        existing = {"memory_mb": 512, "cpus": 8}
        limits.validate_resources(512, 8, MAC, existing)
        limits.validate_resources(512, 2, MAC, existing)
        limits.validate_resources(2048, 8, MAC, existing)
        for memory, cpus in ((512, 2), (6400, 2), (1024, 4.1), (1024, .1),
                             (True, 1), (1024.0, 1), (1024, True), (1024, math.nan),
                             (1024, math.inf), (1024, "1"), (1024, 10 ** 400)):
            with self.subTest(memory=memory, cpus=cpus), self.assertRaises(ValueError):
                limits.validate_resources(memory, cpus, MAC)

    def test_actual_bot_capacity_and_no_idle_double_counting(self):
        result = limits.capacity(MAC, BOTS)
        self.assertEqual(result["capacity"], 2)
        self.assertEqual(result["limiting_resources"], ["memory"])
        self.assertEqual(result["memory_today_mb"], 9.5 * 1024)
        self.assertAlmostEqual(result["memory_at_max_mb"], 12.3 * 1024)
        self.assertFalse(result["uncertain"])
        self.assertTrue(result["above_capacity"])
        for max_active, count in ((1, 6), (2, 2), (3, 2)):
            with self.subTest(max_active=max_active):
                self.assertEqual(limits.capacity(MAC, BOTS, max_active=max_active)["memory_capacity"], count)

    def test_hypothetical_bot_capacity_is_distinct(self):
        bots = [{"memory_mb": 2048}] * 3
        self.assertEqual(limits.capacity(MAC, bots)["memory_capacity"], 3)
        # At one run, new bots default to 4 GB. Eleven already configured 2 GB
        # bots fit, but three existing 2 GB bots plus new 4 GB bots fit only six.
        self.assertEqual(limits.capacity(MAC, bots, max_active=1)["memory_capacity"], 6)
        self.assertEqual(limits.capacity(MAC, bots * 4, max_active=1)["memory_capacity"], 11)
        self.assertNotEqual(limits.capacity(MAC, bots)["memory_today_mb"], limits.capacity(MAC, BOTS)["memory_today_mb"])

    def test_new_default_bots_can_be_larger_than_existing_legacy_bots(self):
        machine = {"memory_bytes": 8 * limits.GIB}
        bots = [{"memory_mb": 512}] * 3
        result = limits.capacity(machine, bots)
        self.assertEqual(result["memory_default_mb"], 2048)
        self.assertEqual(result["memory_capacity"], 5)

    def test_missing_capacity_is_explicit_and_process_impossibility_is_distinct(self):
        result = limits.capacity({}, BOTS)
        self.assertIsNone(result["capacity"])
        self.assertIsNone(result["above_capacity"])
        self.assertTrue(result["uncertain"])
        result = limits.capacity({"pid_max": 12000}, [], max_bots=30)
        self.assertTrue(result["above_capacity"])
        self.assertFalse(result["process_impossible"])
        self.assertTrue(result["uncertain"])
        self.assertTrue(limits.capacity({"pid_max": 256}, [], max_bots=3)["process_impossible"])

    def test_maximum_cannot_hide_existing_bots(self):
        bots = [{"memory_mb": 1024}] * 5
        result = limits.capacity(MAC, bots, max_bots=3)
        self.assertEqual(result["memory_at_max_mb"], result["memory_today_mb"])

    def test_counts_reject_invalid_types_and_bounds(self):
        for max_bots, max_active in ((True, 3), (2, 3), (31, 3), (10, 0), (10, 11), (10, math.nan)):
            with self.subTest(max_bots=max_bots, max_active=max_active), self.assertRaises(ValueError):
                limits.describe(MAC, max_bots, max_active)

    def test_disk_preview_never_enforces_and_missing_facts_are_uncertain(self):
        machine = {"disk_total_bytes": 911 * limits.GIB, "disk_free_bytes": 669 * limits.GIB}
        result = limits.disk_preview(machine)
        self.assertFalse(result["active"])
        self.assertEqual(result["default_gb"], 14)
        self.assertEqual(result["hard_max_gb"], 44)
        self.assertEqual(result["capacity"], 88)
        self.assertEqual(result["enforcement"], "not_active_until_a2")
        self.assertTrue(limits.disk_preview({})["uncertain"])
        self.assertTrue(limits.disk_preview({"disk_total_bytes": limits.GIB,
                                            "disk_free_bytes": 2 * limits.GIB})["uncertain"])


if __name__ == "__main__":
    unittest.main()
