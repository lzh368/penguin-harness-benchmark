"""SYNTHETIC final-only replay; actual State/raw trial files, zero models.

Only Penguin's separately tested native learning/source seal boundary is stubbed.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tarfile
import unittest
from unittest.mock import Mock, patch
import acceptance
import offline_replay as replay
import protocol
import test_injection
import test_protocol
import test_registration
save = test_injection.save


class ReplayTests(unittest.TestCase):
    def setUp(self):
        registration = test_registration.RegistrationTests()
        registration.setUp()
        self.addCleanup(registration.doCleanups)
        self.registration = registration
        self.root, self.config = registration.root, registration.config
        self.tool = os.environ['EVO_TOOL']
        self.evo = protocol.helper(self.tool)
        source = save(self.root / 'source.json', {'schema': 'SYNTHETIC-source-gate-tested-in-P'})
        self.config.update(protocol=replay.PROTOCOL, test_repeats=1, test_phase_order=['final_test'],
            limits={'general': 5, 'topic': 5}, max_bytes=16000, budget_usd=10, paid_authorized=True,
            source_manifest=str(source), source_manifest_sha256=protocol.sha(source),
            test_agent_id='create: synthetic-target')
        registration.save_config()
        self.base = {'config': str(registration.config_path), 'config_sha256': protocol.sha(registration.config_path), 'evo_tool': self.tool}
        self.registered = replay.execute({**self.base, 'op': 'prepare-benchmarks'})
        self.benchmark = registration.output / self.config['test_benchmark_id']
        self.target = registration.project / 'agents/synthetic-target/agent_state'
        self.target.mkdir(parents=True)
        (self.target / 'AGENTS.md').write_text('SYNTHETIC native instructions\n')
        (self.target / 'system_config.yaml').write_text('version: 1\n' + test_protocol.SYNTHETIC_OUTPUT_TOOLS)
        self.library = save(self.root / 'library.json', [])
        self.empty_package = self.root / 'empty-package'
        self.evo.export_skill({'library': str(self.library), 'output': str(self.empty_package), 'limits': self.config['limits'], 'max_bytes': 16000})
        test_protocol.install_package(self.evo, self.empty_package, self.target)
        self.initial = self.snapshot('initial.tar.gz')
        self.package = self.root / 'learned-package'
        save(self.library, [{'id': 'synthetic-general', 'level': 'general', 'trigger': 'synthetic records',
            'scope': 'synthetic', 'description': 'SYNTHETIC general description.', 'body': 'SYNTHETIC general body not loaded.'},
            {'id': 'synthetic-topic', 'level': 'topic', 'topic': '003', 'trigger': 'synthetic rows', 'scope': 'synthetic',
             'body': 'SYNTHETIC topic body used by five tests.'}])
        self.evo.export_skill({'library': str(self.library), 'output': str(self.package), 'limits': self.config['limits'], 'max_bytes': 16000})
        test_protocol.install_package(self.evo, self.package, self.target)
        (self.target / 'system_config.yaml').write_text('version: 2\n' + test_protocol.SYNTHETIC_OUTPUT_TOOLS)
        self.final = self.snapshot('final.tar.gz')
        self.seal = save(self.root / 'learning-seal.json', {'synthetic': True})
        self.penguin = Mock()
        self.penguin.verify_source.return_value = {'complete': True, 'source_manifest_sha256': self.config['source_manifest_sha256']}
        self.sealed()
        boundary = patch.object(replay, 'method', return_value=(self.evo, self.penguin))
        boundary.start()
        self.method_patch = boundary
        self.addCleanup(boundary.stop)
        self.freeze_request = {**self.base, 'op': 'verify-freeze', 'learning_seal': str(self.seal),
            'initial_snapshot': str(self.initial), 'final_snapshot': str(self.final), 'target_state': str(self.target)}
        self.freeze_path = save(self.root / 'freeze-request.json', self.freeze_request)
        self.cells = [{'protocol_version': 1, 'agent_id': 'synthetic-target', 'expected_version': 2,
            **self.config['runtime'], 'case_id': case, 'run': 1, 'status': 'ok', 'score': 50,
            'cost': 0, 'duration_ms': 1, 'session_id': f'harbor:synthetic-replay-{i}'}
            for i, case in enumerate(self.registered['test']['case_ids'], 1)]
        self.cells_path = save(self.root / 'cells.json', self.cells)
        self.frozen = acceptance.snapshot(self.final)['files']
        self.trials = [test_injection.write_trial(self.benchmark, cell, self.frozen, self.config['runtime']) for cell in self.cells]
        self.round_request = {**self.base, 'op': 'verify-round', 'freeze_request': str(self.freeze_path), 'cells': str(self.cells_path), 'round': 1}

    def snapshot(self, name):
        path = self.root / name
        with tarfile.open(path, 'w:gz') as archive:
            archive.add(self.target, arcname='agent_state')
        return path

    def sealed(self):
        self.penguin.verify_learning_seal.return_value = {'complete': True, 'package_path': str(self.package),
            'package_sha256': self.evo.validate_package(self.package)['sha256'], 'source_manifest_sha256': self.config['source_manifest_sha256']}

    def test_test_only_registration_and_five_raw_trials_complete(self):
        self.assertFalse((self.registration.output / self.config['benchmark_id']).exists())
        result = replay.execute(self.round_request)
        self.assertTrue(result['complete'])
        self.assertEqual(result['cells'], self.cells)
        self.assertEqual(len(result['trials']), 5)
        self.assertEqual(result['final']['version'], 2)
        self.assertEqual(result['matrix']['evaluation']['score'], 50)
        self.assertEqual(len({row['skill_sha256'] for row in result['trials']}), 1)
        self.assertEqual(len({str(row['loaded_skill_files']) for row in result['trials']}), 1)
        self.assertEqual(replay.execute({**self.round_request, 'op': 'verify-test-matrix'}), result)
        self.assertEqual(result['new_training_calls'], 0)
        self.assertEqual(result['new_baseline_calls'], 0)
        statement = next(self.benchmark.glob('CASE-*/statement/README.md')).read_text()
        self.assertIn('No new training run, baseline or additional test round', statement)

    def test_additional_round_and_baseline_phase_rejected(self):
        with self.assertRaisesRegex(ValueError, 'only final test round 1'):
            replay.execute({**self.round_request, 'round': 2})
        for field, value in (('test_repeats', 3), ('test_phase_order', ['h0_test', 'final_test'])):
            with self.assertRaisesRegex(ValueError, 'one final-only'):
                replay.validate_replay_config({**self.config, field: value}, self.registration.repo)

    def test_frozen_source_hash_and_original_learning_seal_required(self):
        original = Path(self.config['source_manifest']).read_bytes()
        Path(self.config['source_manifest']).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'source manifest changed'):
            replay.execute(self.freeze_request)
        Path(self.config['source_manifest']).write_bytes(original)
        self.penguin.verify_learning_seal.side_effect = ValueError('original native seal evidence missing')
        with self.assertRaisesRegex(ValueError, 'native seal evidence missing'):
            replay.execute(self.freeze_request)

    def test_missing_original_trial_cannot_complete_synthetic_table(self):
        shutil.rmtree(self.trials[2][0])
        with self.assertRaisesRegex(ValueError, 'original trial missing'):
            replay.execute(self.round_request)

    def test_original_trial_reward_runtime_and_state_remain_authoritative(self):
        path = self.trials[0][0] / 'result.json'
        original = protocol.read(path)
        for mutate, expected in (
            (lambda d: d['verifier_result']['rewards'].update(reward=0.1), 'original verifier'),
            (lambda d: d['agent_result']['metadata'].update(thinking='low'), 'runtime differs')):
            changed = copy.deepcopy(original)
            mutate(changed)
            save(path, changed)
            with self.assertRaisesRegex(ValueError, expected):
                replay.execute(self.round_request)
        save(path, original)
        state_path = self.trials[0][0] / 'agent/penguin-agent-state-ready.json'
        state = protocol.read(state_path)
        state['files']['AGENTS.md'] = '0' * 64
        save(state_path, state)
        with self.assertRaisesRegex(ValueError, 'State changed during native startup'):
            replay.execute(self.round_request)

    def test_missing_method_page_is_not_full_reader_loading(self):
        trace = self.trials[0][1]
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        rows = [row for row in rows if row.get('payload', {}).get('tool_call_id') != 'method-1']
        trace.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
        with self.assertRaisesRegex(ValueError, 'Skill not fully observed'):
            replay.execute(self.round_request)

    def test_pending_cost_keeps_valid_reward_and_pending_reservation(self):
        self.cells[1]['cost'] = None
        save(self.cells_path, self.cells)
        path = self.trials[1][0] / 'result.json'
        raw = protocol.read(path)
        raw['agent_result']['cost_usd'] = None
        raw['agent_result']['metadata']['cost_status'] = 'pending'
        save(path, raw)
        result = replay.execute(self.round_request)
        self.assertTrue(result['complete'])
        self.assertEqual(result['matrix']['evaluation']['score'], 50)
        self.assertFalse(result['costs']['cost_complete'])
        self.assertEqual(result['costs']['pending_test_cells'], [self.cells[1]['case_id']])
        self.assertIsNone(result['cells'][1]['cost'])

    def test_empty_learning_still_runs_final_only_five_not_baseline_reuse(self):
        self.package = self.empty_package
        self.sealed()
        test_protocol.install_package(self.evo, self.package, self.target)
        (self.target / 'system_config.yaml').write_text('version: 1\n' + test_protocol.SYNTHETIC_OUTPUT_TOOLS)
        self.snapshot('final.tar.gz')
        frozen = acceptance.snapshot(self.final)['files']
        for cell, (trial, _) in zip(self.cells, self.trials):
            cell['expected_version'] = 1
            shutil.rmtree(trial)
            test_injection.write_trial(self.benchmark, cell, frozen, self.config['runtime'])
        save(self.cells_path, self.cells)
        self.assertTrue(replay.execute(self.freeze_request)['equivalent_initial'])
        self.assertEqual(len(replay.execute(self.round_request)['trials']), 5)

    def test_foreign_state_mutation_and_budget_change_refused_before_test(self):
        (self.target / 'AGENTS.md').write_text('changed outside compiled package\n')
        self.snapshot('final.tar.gz')
        with self.assertRaisesRegex(ValueError, 'outside learned Skill'):
            replay.execute(self.freeze_request)
        with self.assertRaisesRegex(ValueError, 'learning limits changed'):
            replay.validate_replay_config({**self.config, 'max_bytes': 16001}, self.registration.repo)

    def test_missing_worker_rows_are_incomplete_not_zero(self):
        save(self.cells_path, self.cells[:4])
        result = replay.execute(self.round_request)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['matrix']['score'])
        self.assertIsNone(result['matrix']['evaluation'])


    def test_real_penguin_source_and_native_learning_seal_through_raw_trials(self):
        """Reuse P's native-shaped six-role evidence, with no seal-boundary mock."""
        ptest = Path(self.tool).resolve().parents[3] / 'tests/test_replay.py'
        spec = importlib.util.spec_from_file_location('synthetic_penguin_replay_test', ptest)
        fixture_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture_module)
        parent = fixture_module.ReplayTests()
        parent.setUp()
        self.addCleanup(parent.doCleanups)
        self.method_patch.stop()
        with patch.object(fixture_module.control, 'TOOL', Path(self.tool)):
            # Reinitialize only synthetic pre-dispatch fixture configuration.
            # No original or running experiment artifact is involved.
            shutil.rmtree(parent.scratch)
            self.config.update(source_manifest=parent.manifest,
                source_manifest_sha256=protocol.sha(parent.manifest),
                replay_reserves=parent.config['replay_reserves'])
            self.registration.save_config()
            self.base['config_sha256'] = protocol.sha(self.registration.config_path)
            parent.config = self.config
            parent.configpath = str(self.registration.config_path)
            parent.meta['provider'] = self.config['runtime']['provider']
            parent.meta['model_id'] = self.config['runtime']['model_id']
            parent.runtime = self.config['runtime']
            parent.write_trace(parent.trace, [fixture_module.row('session_meta', **parent.meta)])
            fixture_module.write(parent.skill/'replay.json', {'config': parent.configpath,
                'config_sha256': self.base['config_sha256']})
            fixture_module.replay.bootstrap({'trace_path':str(parent.trace)},str(parent.state),parent.meta,parent.scratch,None)
            parent.mode = fixture_module.evo.read(parent.scratch/fixture_module.control.MARKER)
            parent.all_learners()
            ids = [parent.candidate(t)[0]['id'] for t in parent.ids if t != parent.ids[1]]
            decisions = [{'op':'ADD','candidate_ids':ids,'reason':'Retain a supported key-based join procedure.',
                'entry':{'id':'join-records','level':'topic','topic':'crm','trigger':'Need to link entity records',
                    'scope':'Use the task-defined key and time basis',
                    'body':'Find the task-defined entity key. Query authorized records, join on that key, restrict the required time interval and aggregate at the requested record grain.'}}]
            parent.run_role('evolver', None, decisions)
            learned = fixture_module.replay.learning(parent.mode, parent.config)
            seal = fixture_module.write(fixture_module.replay.private(parent.mode)/'learning.json', learned)
            test_protocol.install_package(self.evo, learned['package_path'], self.target)
            self.snapshot('final.tar.gz')
            self.freeze_request.update(self.base, learning_seal=seal)
            save(self.freeze_path, self.freeze_request)
            self.round_request.update(self.base)
            frozen = acceptance.snapshot(self.final)['files']
            for cell,(trial,_) in zip(self.cells,self.trials):
                shutil.rmtree(trial)
                test_injection.write_trial(self.benchmark,cell,frozen,self.config['runtime'])
            actual = replay.execute(self.round_request)
            self.assertTrue(actual['complete'])
            self.assertEqual(actual['frozen_package_sha256'],learned['package']['sha256'])
            self.assertEqual(len(actual['trials']),5)
            self.assertEqual(len(learned['learners']),5)
            self.assertFalse(learned['original_solver_reflection'])


if __name__ == '__main__':
    unittest.main()
