"""Real public split package metadata with SYNTHETIC runtime/preflight; no model."""
import json
from pathlib import Path
import tomllib
import unittest

import protocol
import registration
import test_registration


class SplitPackageTests(unittest.TestCase):
    def setUp(self):
        self.f = test_registration.RegistrationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.config["versions"].update(skill="2026.10.23.6",
            benchmark_source_commit="73457bde7376f42de49d384c603933fe18d97000")
        self.f.config.update(benchmark_layout="gdpevo-domain-packages-v1",
            context_transport="agents-md-v1", reflection_evidence="observed-v1",
            receipt_policy="native-trial-v1", candidate_edit="native-draft-v1",
            solver_stage="bound-post-judge-v1", test_repeats=1,
            resource_gate={"script":"/synthetic/queue.py", "policy":"/synthetic/policy.json"})

    def group(self, group):
        domain = protocol.DOMAINS[group]
        c = self.f.config
        c.update(group=group, test_ids=[f"tg{group}-test-{i:03d}" for i in range(1, 6)],
            benchmark_id=f"penguinharness-benchmark-gdpevo-{domain}-train",
            test_benchmark_id=f"penguinharness-benchmark-gdpevo-{domain}-test")
        self.f.preflight["task_ids"] = [f"tg{group}-train", *c["test_ids"]]
        self.f.save_preflight()
        self.f.save_config()

    def test_all_six_domains_register_real_packages_once_with_bound_launches(self):
        for group, domain in protocol.DOMAINS.items():
            with self.subTest(group=group):
                self.group(group)
                result = self.f.run_prepare()
                self.assertEqual(len(result["training"]["case_ids"]), 1)
                self.assertEqual(len(result["test"]["case_ids"]), 5)
                for phase in ("training", "test"):
                    package_id = result[phase]["benchmark_id"]
                    original = self.f.repo / "packages" / package_id
                    installed = self.f.output / package_id
                    meta = tomllib.loads((installed / "benchmark_config.toml").read_text())
                    self.assertEqual(meta["id"], package_id)
                    self.assertEqual(meta["runs"], 1)
                    self.assertEqual(meta["origin"]["commit"], self.f.config["versions"]["benchmark_source_commit"])
                    for case in result[phase]["case_ids"]:
                        self.assertEqual((installed/case/"statement/package-source.md").read_bytes(),
                                         (original/case/"statement/README.md").read_bytes())
                        self.assertEqual((installed/case/"rubric/README.md").read_bytes(),
                                         (original/case/"rubric/README.md").read_bytes())
                        launch = json.loads((installed/case/"statement/harbor-launch.json").read_bytes())
                        self.assertEqual(launch["source_package"]["id"], package_id)
                        self.assertEqual(launch["argv_template"][:5],
                            ["python3", "-B", "/synthetic/queue.py", "/synthetic/policy.json", "--"])
                        task_path = launch["argv_template"][launch["argv_template"].index("-p") + 1]
                        self.assertIn(f"benchmarks/gdpevo-{domain}/tasks/task_group_{group}/", task_path)
                        self.assertEqual(launch["solving_timeout_sec"], 21600 if phase == "training" else 14400)

    def test_wrong_domain_and_three_retests_are_rejected_before_writes(self):
        self.group("003")
        self.f.config["test_benchmark_id"] = "penguinharness-benchmark-gdpevo-erp-test"
        self.f.save_config()
        with self.assertRaisesRegex(ValueError, "package id"):
            self.f.run_prepare()
        self.group("003")
        self.f.config["test_repeats"] = 3
        self.f.save_config()
        with self.assertRaisesRegex(ValueError, "once"):
            self.f.run_prepare()
        self.assertEqual(list(self.f.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
