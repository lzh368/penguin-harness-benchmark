"""Real local subprocess completion and async cleanup; no Docker or model calls."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import worker_job

class WorkerJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.job=Path(self.tmp.name)/'original-job';self.job.mkdir()

    def test_worker_return_and_trial_finish_do_not_finish_original_job(self):
        # Real running process writes a trial before the terminal job record.
        script='''import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]);t=p/'trial';t.mkdir()
(t/'result.json').write_text(json.dumps({'finished_at':'trial-end','verifier_result':{'rewards':{'reward':0.5}}}))
(p/'result.json').write_text(json.dumps({'finished_at':None,'stats':{'n_running_trials':1}}))
time.sleep(0.5)
(p/'result.json').write_text(json.dumps({'finished_at':'job-end','stats':{'n_running_trials':0}}))
'''
        process=subprocess.Popen([sys.executable,'-c',script,str(self.job)])
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        deadline=time.monotonic()+3
        while not (self.job/'result.json').exists() and time.monotonic()<deadline:time.sleep(0.01)
        first=worker_job.wait(self.job,0)
        self.assertTrue(first['pending']);self.assertIsNone(process.poll())
        self.assertEqual(first['trials'][0]['verifier_result']['rewards']['reward'],0.5)
        final=worker_job.wait(self.job,3);process.wait(timeout=3)
        self.assertTrue(final['complete']);self.assertEqual(len(list(self.job.glob('*/result.json'))),1)

    def test_score_is_retained_with_cleanup_exception_without_regrading(self):
        t=self.job/'trial';t.mkdir()
        (t/'result.json').write_text(json.dumps({'finished_at':'end','verifier_result':{'rewards':{'reward':0.5}},'exception_info':{'exception_type':'RuntimeError','exception_message':'no running event loop'}}))
        (self.job/'result.json').write_text(json.dumps({'finished_at':'end','stats':{'n_running_trials':0}}))
        result=worker_job.status(self.job);self.assertTrue(result['complete'])
        self.assertEqual(result['trials'][0]['verifier_result']['rewards']['reward'],0.5)
        self.assertIsNotNone(result['trials'][0]['exception_info'])

    def test_dead_process_with_incomplete_job_never_authorizes_retry(self):
        (self.job/'result.json').write_text('{"finished_at":null}')
        result=worker_job.wait(self.job,0)
        self.assertTrue(result['pending']);self.assertIn('do not redispatch',result['next_action'])
        self.assertFalse(any(self.job.glob('*/result.json')))


try:
    from harbor.trial.trial import Trial
except ImportError:
    Trial = None

@unittest.skipIf(Trial is None, 'run with cached Harbor 0.23 interpreter for real cleanup regression')
class HarborLoopLifecycleTests(unittest.TestCase):
    class Environment:
        def __init__(self): self.applied=[]
        async def set_network_policy(self, policy):
            process=await asyncio.create_subprocess_exec(sys.executable,'-c','pass')
            await process.wait();self.applied.append(policy)

    def test_real_harbor_cleanup_preserves_primary_exception_while_loop_is_alive(self):
        async def run():
            environment=self.Environment()
            with self.assertRaisesRegex(RuntimeError,'PRIMARY agent cancellation'):
                async with Trial._phase_network_policy(object(),environment,baseline_policy='baseline',phase_policy='agent'):
                    raise RuntimeError('PRIMARY agent cancellation')
            self.assertEqual(environment.applied,['agent','baseline'])
        asyncio.run(run())

    def test_real_harbor_generator_cleanup_after_loop_close_reproduces_secondary_error(self):
        environment=self.Environment();loop=asyncio.new_event_loop()
        context=Trial._phase_network_policy(object(),environment,baseline_policy='baseline',phase_policy='agent')
        loop.run_until_complete(context.__aenter__());loop.close()
        closing=context.gen.athrow(GeneratorExit())
        with self.assertRaisesRegex(RuntimeError,'no running event loop') as error:
            closing.send(None)
        self.assertIsInstance(error.exception.__context__,GeneratorExit)
        self.assertEqual(environment.applied,['agent'])

if __name__=='__main__':unittest.main()
