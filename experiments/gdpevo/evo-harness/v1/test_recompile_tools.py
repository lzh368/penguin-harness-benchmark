"""Compile-only synthetic evidence; never a score or model-effectiveness test."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import protocol
import recompile_tools


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return str(path)


class RecompileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        sys.path.insert(0, str(Path(os.environ['EVO_TOOL']).parent))
        self.addCleanup(sys.path.remove, str(Path(os.environ['EVO_TOOL']).parent))
        import evo, control
        self.evo, self.control = evo, control

    def test_three_compatible_h0_rounds_reused_without_mixing_old_final(self):
        cases=[f'case-{n}'for n in range(5)]
        old_cells=save(self.root/'old-h0-cells.json',[{'case_id':case,'run':n,'score':10*n}for n in (1,2,3)for case in cases])
        old_h0=save(self.root/'h0-matrix.json',{'cells':old_cells})
        old_final=save(self.root/'final-matrix.json',{'cells':save(self.root/'old-final-cells.json',[{'case_id':case,'run':1,'score':1}for case in cases])})
        save(self.root/'manifest.json',{'workspace':str(self.root),'kind':'training-recompilation-diagnostic-validation',
            'source_hashes':{},'config':save(self.root/'config.json',{}),'old_matrices':{'h0':old_h0,'final':old_final}})
        save(self.root/'freeze.json',{'h0_snapshot':'h0','final_snapshot':'final','test_case_ids':cases})
        for n in (1,2,3):
            for case in cases:save(self.root/'records'/f'r{n}-final-{case}.json',{'cell':{'case_id':case,'run':n,'score':40*n},'audit':{'audit_passed':True}})
        with patch.object(recompile_tools,'modules',return_value=(None,None,None,SimpleNamespace(snapshot=lambda p:{'files':{}}))):
            result=recompile_tools.main(self.root,'summary',[])
        self.assertTrue(result['complete'])
        self.assertEqual(result['conditions']['h0']['reused_rounds'],[1,2,3])
        self.assertEqual(result['conditions']['h0']['three_round_mean'],20)
        self.assertEqual(result['conditions']['final']['three_round_mean'],80)
        self.assertEqual(len(result['conditions']['final']['cells']),15)

    def test_same_candidates_compile_with_private_general_body_and_full_topic(self):
        contexts = [save(self.root / (f'train_{n:03d}.json'), {'split': 'train', 'task_id': f'train_{n:03d}',
            'attempt_id': 'attempt-1', 'negative': n == 1, 'inputs': [], 'trace': {'pairs': []}}) for n in range(1, 6)]
        candidate = {'id': 'train_001-attempt-1-c01', 'task_id': 'train_001', 'attempt_id': 'attempt-1',
            'lesson': 'SYNTHETIC method', 'trigger': 'SYNTHETIC inputs', 'scope_hint': 'SYNTHETIC only',
            'evidence': [{'kind': 'feedback', 'claim': 'SYNTHETIC negative aggregate; no field attribution', 'epistemic': 'observed'}]}
        decisions = [{'op': 'ADD', 'candidate_ids': [candidate['id']], 'reason': 'SYNTHETIC retain supported calculation, condition decision.',
            'entry': {'id': level, 'level': level, 'trigger': 'SYNTHETIC', 'scope': 'SYNTHETIC',
                'body': 'SYNTHETIC_PRIVATE_BODY_' + level,
                **({'topic': 'synthetic'} if level == 'topic' else {'description': 'SYNTHETIC self-contained bounded method.'})}} for level in ('general','topic')]
        m = {'context_paths': contexts, 'candidates': save(self.root/'candidates.json', [candidate]),
            'empty_library': save(self.root/'empty.json', []), 'decisions': save(self.root/'decisions.json', decisions)}
        receipt = recompile_tools.compile_draft(m, self.evo, self.root/'compiled')
        files = {p:(self.root/'compiled/skill'/p).read_bytes()for p in receipt['files']}
        self.assertNotIn(b'SYNTHETIC_PRIVATE_BODY_general', b''.join(files.values()))
        self.assertIn(b'SYNTHETIC_PRIVATE_BODY_topic', b''.join(files.values()))
        self.assertIn('SYNTHETIC_PRIVATE_BODY_general', (self.root/'compiled/library.json').read_text())
        self.assertEqual(len({self.evo.model_context(files)['sha256'] for _ in range(5)}), 1)
        data = json.loads(Path(contexts[0]).read_text());data['split']='test';save(Path(contexts[0]),data)
        with self.assertRaises(AssertionError):
            recompile_tools.compile_draft(m, self.evo, self.root/'invalid')

    def test_original_native_prompt_and_all_child_segments_are_bound(self):
        traces=self.root/'traces';traces.mkdir()
        parent=traces/'parent.jsonl'; child1=traces/'child_001.jsonl';child2=traces/'child_002.jsonl'
        prompt='SYNTHETIC complete authorized training inputs, observations, answers and feedback.'
        prompt_path=self.root/'evolver-prompt.txt';prompt_path.write_text(prompt)
        runtime={'provider':'fixture','model_id':'no-model','thinking_level':'xhigh'}
        call={**runtime,'description':'single Evolver','prompt_file':str(prompt_path),'prompt_sha256':self.evo.digest(prompt.encode()),'run_in_background':True}
        meta={'session_id':'parent','source':'cli','workspace':str(self.root),'agent_state':str(self.root/'agent_state'),'provider':'fixture','model_id':'no-model'}
        def write_rows(path,rows):path.write_text(''.join(json.dumps(row)+'\n'for row in rows))
        write_rows(parent,[{'type':'session_meta','payload':meta},{'type':'model_msg','payload':{'type':'tool_call','name':'run_subagent','arguments':json.dumps(call),'tool_call_id':'dispatch'}},
            {'type':'event_msg','payload':{'type':'subagent','session_id':'child'}},
            {'type':'model_msg','payload':{'type':'tool_call_output','tool_call_id':'dispatch','output':'subagent running','stop_reason':'completed'}}])
        childmeta={**meta,'session_id':'child','source':'subagent'}
        write_rows(child1,[{'type':'session_meta','payload':childmeta},{'type':'model_msg','payload':{'type':'text','role':'user','sender':'parent_agent','text':prompt}}])
        write_rows(child2,[{'type':'session_meta','payload':childmeta},{'type':'model_msg','payload':{'type':'tool_call','name':'write_file','arguments':'{}','tool_call_id':'write'}},
            {'type':'model_msg','payload':{'type':'tool_call_output','tool_call_id':'write','output':'SYNTHETIC written','stop_reason':'completed'}}])
        m={'workspace':str(self.root),'trace_root':str(traces),'evolver_prompt':str(prompt_path),'evolver_call':save(self.root/'evolver-call.json',call),'runtime':runtime}
        save(self.root/'launch-receipt.json',{'sessionId':'parent'})
        session,view=recompile_tools.original_child(m,self.evo,self.control)
        self.assertEqual(session,'child');self.assertEqual(len(view['sources']),2);self.assertEqual(len(view['pairs']),1)
        self.assertEqual(view['binding']['parent_trace'],str(parent))
        rows=[json.loads(line)for line in parent.read_text().splitlines()]
        actual={**call,'description':'原生显示标签','description_note':'display only',
                'description_note_placeholder':'ignored by frozen native tool','yield_time_ms':1000}
        rows[1]['payload']['arguments']=json.dumps(actual);write_rows(parent,rows)
        self.assertEqual(recompile_tools.original_child(m,self.evo,self.control)[0],'child')
        for key,value in [('prompt_sha256','0'*64),('thinking_level','low'),('agent_id','other'),('model_id','wrong')]:
            rows[1]['payload']['arguments']=json.dumps({**actual,key:value});write_rows(parent,rows)
            with self.subTest(changed=key),self.assertRaisesRegex(AssertionError,'dispatch/child event'):
                recompile_tools.original_child(m,self.evo,self.control)
        rows[1]['payload']['arguments']=json.dumps(actual)
        rows[2]['payload']['session_id']='other';write_rows(parent,rows)
        with self.assertRaisesRegex(AssertionError,'dispatch/child event'):
            recompile_tools.original_child(m,self.evo,self.control)
        rows[2]['payload']['session_id']='child'
        rows[3]['payload']['stop_reason']='fatal';write_rows(parent,rows)
        with self.assertRaisesRegex(AssertionError,'dispatch/child event'):
            recompile_tools.original_child(m,self.evo,self.control)
        rows[3]['payload']['stop_reason']='completed';write_rows(parent,rows)
        prompt_path.write_text('SYNTHETIC altered unauthorized prompt')
        with self.assertRaisesRegex(AssertionError,'complete prepared training prompt'):
            recompile_tools.original_child(m,self.evo,self.control)


if __name__=='__main__':unittest.main()
