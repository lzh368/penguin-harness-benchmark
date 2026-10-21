"""Run with: uvx --from harbor==0.23.0 python -B tools/test_penguin_limits.py.

No model calls or Docker containers: exercise the adapter and its real run.sh with
stub executables, including completion, timeout cleanup and exported traces.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import yaml
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig
from harbor.models.task.config import TaskConfig
from harbor.trial.trial import Trial

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'agents'), str(ROOT / 'tools/gdpevo')]
from penguin_agent.agent import PenguinAgent, PenguinOptions
from packages import load_job_caps, task_caps


class LimitsTests(unittest.TestCase):
    def make_agent(self, root, run_timeout='unlimited', **kwargs):
        agent = PenguinAgent(logs_dir=root / 'agent', model_name='deepseek/deepseek-flash',
                             run_timeout=run_timeout, max_turns=-1, **kwargs)
        task = root / 'task'
        task.mkdir(exist_ok=True)
        (task / 'task.toml').write_text('[agent]\n')
        config = {'agent': {}, 'task': {'path': str(task)}}
        agent._trial_config = lambda: config
        return agent, config, task

    def test_unlimited_removes_both_adapter_and_harbor_deadlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, config, task = self.make_agent(Path(tmp))
            self.assertIsNone(agent.run_timeout_sec())
            self.assertIsNone(agent.harbor_agent_timeout_sec()[0])
            agent.check_timeout_budget()
            probe = SimpleNamespace(
                config=SimpleNamespace(agent=SimpleNamespace(override_timeout_sec=None)),
                task=SimpleNamespace(config=TaskConfig.model_validate(tomllib.loads((task / 'task.toml').read_text()))))
            self.assertIsNone(Trial._compute_agent_timeout_sec(probe))

    def test_unlimited_rejects_a_remaining_outer_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, config, task = self.make_agent(Path(tmp))
            config['agent']['override_timeout_sec'] = 1200
            with self.assertRaisesRegex(ValueError, 'conflicts with Harbor'):
                agent.check_timeout_budget()
            config['agent'].clear()
            (task / 'task.toml').write_text('[agent]\ntimeout_sec = 1200\n')
            with self.assertRaisesRegex(ValueError, 'conflicts with Harbor'):
                agent.check_timeout_budget()

    def test_finite_defaults_and_cleanup_budget_still_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, config, _ = self.make_agent(Path(tmp), run_timeout=None)
            config['agent']['override_timeout_sec'] = 1200
            self.assertEqual(agent.run_timeout_sec(), 990)
            agent.check_timeout_budget()
            agent.options.run_timeout = '15m'
            self.assertEqual(agent.run_timeout_sec(), 900)
            agent.check_timeout_budget()
            config['agent']['override_timeout_sec'] = 900
            with self.assertRaisesRegex(ValueError, 'needs at least'):
                agent.check_timeout_budget()

    def test_unlimited_is_explicit_and_zero_is_not_an_alias(self):
        self.assertEqual(PenguinOptions(run_timeout='unlimited', max_turns=-1).max_turns, -1)
        self.assertEqual(PenguinOptions(run_timeout='task').run_timeout, 'task')
        for value in ['0', '-1', 'none']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                PenguinOptions(run_timeout=value)
        with self.assertRaises(ValueError):
            PenguinOptions(max_turns=0)

    def test_run_passes_unlimited_settings_to_the_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, _, _ = self.make_agent(Path(tmp))
            agent.exec_as_root = AsyncMock(return_value=ExecResult(return_code=0))
            agent.exec_as_agent = AsyncMock(return_value=ExecResult(return_code=0))
            asyncio.run(agent.run('Solve the task.', SimpleNamespace(upload_file=AsyncMock()), AgentContext()))
            env = agent.exec_as_agent.call_args.kwargs['env']
            self.assertEqual(env['PB_TIMEOUT'], '')
            self.assertEqual(env['PB_MAX_TURNS'], '-1')

    def test_task_mode_uses_the_full_budget_and_respects_harbor_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, config, task = self.make_agent(Path(tmp), run_timeout='task')
            self.assertIsNone(agent.run_timeout_sec())
            agent.check_timeout_budget()
            (task / 'task.toml').write_text('[agent]\ntimeout_sec = 14610\n')
            self.assertEqual(agent.run_timeout_sec(), 14400)
            agent.check_timeout_budget()
            config['agent']['override_timeout_sec'] = 21810
            self.assertEqual(agent.run_timeout_sec(), 21600)
            config['agent']['max_timeout_sec'] = 14610
            self.assertEqual(agent.run_timeout_sec(), 14400)
            config['agent_timeout_multiplier'] = 2
            self.assertEqual(agent.run_timeout_sec(), 29010)
            config['agent'].clear()
            config['agent_timeout_multiplier'] = 1
            (task / 'task.toml').write_text('[agent]\ntimeout_sec = 210\n')
            with self.assertRaisesRegex(ValueError, 'needs at least'):
                agent.check_timeout_budget()

    def test_task_mode_does_not_treat_unreadable_config_as_unlimited(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, _, task = self.make_agent(Path(tmp), run_timeout='task')
            (task / 'task.toml').unlink()
            with self.assertRaisesRegex(ValueError, 'readable task.toml'):
                agent.run_timeout_sec()
            agent._trial_config = lambda: None
            with self.assertRaisesRegex(ValueError, 'readable Harbor trial config'):
                agent.run_timeout_sec()

    def test_run_passes_four_hour_task_budget_to_the_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, _, task = self.make_agent(Path(tmp), run_timeout='task')
            (task / 'task.toml').write_text('[agent]\ntimeout_sec = 14610\n')
            agent.exec_as_root = AsyncMock(return_value=ExecResult(return_code=0))
            agent.exec_as_agent = AsyncMock(return_value=ExecResult(return_code=0))
            asyncio.run(agent.run('Solve the task.', SimpleNamespace(upload_file=AsyncMock()), AgentContext()))
            env = agent.exec_as_agent.call_args.kwargs['env']
            self.assertEqual(env['PB_TIMEOUT'], '14400s')
            self.assertEqual(env['PB_MAX_TURNS'], '-1')

    def test_gdpevo_tasks_and_package_commands_use_their_own_budgets(self):
        raw = yaml.safe_load((ROOT / 'benchmarks/gdpevo/job.yaml').read_text())
        job = JobConfig.model_validate(raw)
        self.assertEqual(load_job_caps(), ('task', -1))
        self.assertIsNone(job.agents[0].override_timeout_sec)
        self.assertEqual(job.agents[0].kwargs['max_turns'], -1)
        configs = list((ROOT / 'benchmarks/gdpevo/tasks').rglob('task.toml'))
        self.assertEqual(len(configs), 66)
        with tempfile.TemporaryDirectory() as tmp:
            for path in configs:
                with self.subTest(task=str(path.relative_to(ROOT))):
                    data = tomllib.loads(path.read_text())
                    task = TaskConfig.model_validate(data)
                    expected = (21600 if data['metadata']['task_id'] == 'train_group' else
                                14400 if data['metadata']['split'] == 'test' else None)
                    probe = SimpleNamespace(config=job, task=SimpleNamespace(config=task))
                    probe.config = SimpleNamespace(agent=job.agents[0], timeout_multiplier=1.0,
                                                   agent_timeout_multiplier=None)
                    probe._resolve_timeout_sec = MethodType(Trial._resolve_timeout_sec, probe)
                    hard = None if expected is None else expected + 210
                    self.assertEqual(Trial._compute_agent_timeout_sec(probe), hard)
                    agent = PenguinAgent(logs_dir=Path(tmp)/'agent', model_name='deepseek/deepseek-flash',
                                         run_timeout='task', max_turns=-1)
                    agent._trial_config = lambda: {'agent': {}, 'task': {'path': str(path.parent)}}
                    self.assertEqual(agent.run_timeout_sec(), expected)
                    agent.check_timeout_budget()
                    command_timeout, description = task_caps(data, 'task', -1)
                    self.assertEqual(command_timeout, 'unlimited' if expected is None else f'{expected//3600}h')
                    self.assertIn('no turn limit', description)
                    self.assertGreater(task.verifier.timeout_sec, 0)
                    self.assertGreater(task.environment.build_timeout_sec, 0)

    def run_shell(self, root, timeout, status):
        prefix, home, logs = root / 'prefix', root / 'home', root / 'logs'
        (prefix / 'node/bin').mkdir(parents=True)
        (prefix / 'bin').mkdir()
        (prefix / 'instruction.md').write_text('Solve the fixture task.')
        traces = home / 'project/agents/solver/traces'
        traces.mkdir(parents=True)
        (traces / 'trace.jsonl').write_text('{"fixture":true}\n')
        helper = prefix / 'node/bin/node'
        helper.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys, time
from pathlib import Path
command, args = sys.argv[2], sys.argv[3:]
if command == 'now': print(int(time.time()*1000))
elif command == 'ready': print('{"ready":true,"http_status":200}')
elif command == 'set-max-turns': print(json.dumps({'max_turns':int(args[-1])}))
elif command == 'run-status':
    result=json.loads(Path(args[0]).read_text()); print(result['status'],result['sessionId'])
elif command == 'abort': print('{"aborted":true,"status":"idle"}')
elif command == 'outcome': Path(args[0]).write_text(args[1])
elif command == 'redact': print('{"redacted":0}')
else: raise SystemExit('unexpected helper command: '+command)
''')
        penguin = prefix / 'bin/penguin'
        penguin.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
if args == ['server']: pass
elif args[:2] == ['server','status']: print('{"running":true}')
elif args[:2] == ['server','stop']: print('{"stopped":true}')
elif args[0] == 'run':
    Path(os.environ['FIXTURE_ARGS']).write_text(json.dumps(args))
    print(json.dumps({'sessionId':'fixture-session','status':os.environ['FIXTURE_STATUS']}))
elif args[0] == 'cost': print('{"groups":[]}')
else: raise SystemExit('unexpected penguin command: '+repr(args))
''')
        helper.chmod(0o755)
        penguin.chmod(0o755)
        env = dict(os.environ, PENGUIN_HOME=str(home), PB_PREFIX=str(prefix), PB_LOGS=str(logs),
                   PB_PROJECT='project', PB_AGENT='solver', PB_PROVIDER='fixture', PB_MODEL='fixture',
                   PB_THINKING='max', PB_TIMEOUT=timeout, PB_ABORT_WAIT='0', PB_MAX_TURNS='-1',
                   FIXTURE_ARGS=str(root / 'args.json'), FIXTURE_STATUS=status)
        subprocess.run(['bash', str(ROOT / 'agents/penguin_agent/run.sh')], cwd=root, env=env,
                       check=True, capture_output=True, text=True, timeout=10)
        return logs, json.loads((root / 'args.json').read_text())

    def test_unlimited_completion_exports_results_without_aborting(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs, args = self.run_shell(Path(tmp), '', 'completed')
            self.assertNotIn('--timeout', args)
            self.assertFalse((logs / 'penguin/abort.json').exists())
            self.assertEqual(json.loads((logs / 'penguin/max-turns.json').read_text())['max_turns'], -1)
            self.assertFalse(json.loads((logs / 'penguin-outcome.json').read_text())['timed_out'])
            for path in ['penguin/traces/solver/trace.jsonl', 'penguin/server-stop.json',
                         'penguin-cost-by-session.json', 'penguin/redaction.json']:
                self.assertTrue((logs / path).is_file(), path)

    def test_finite_timeout_still_aborts_and_collects_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs, args = self.run_shell(Path(tmp), '900s', 'running')
            self.assertEqual(args[args.index('--timeout') + 1], '900s')
            self.assertTrue(json.loads((logs / 'penguin/abort.json').read_text())['aborted'])
            self.assertTrue(json.loads((logs / 'penguin-outcome.json').read_text())['timed_out'])
            self.assertTrue((logs / 'penguin/traces/solver/trace.jsonl').is_file())


if __name__ == '__main__':
    unittest.main()
