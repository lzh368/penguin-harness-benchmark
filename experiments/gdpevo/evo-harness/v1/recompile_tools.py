"""010 authorized compile-only data helpers. Penguin alone dispatches roles/trials."""
from pathlib import Path
import importlib.util
import json
import os
import re
import shutil
import statistics
import sys
import tarfile
import tempfile

sys.dont_write_bytecode = True


def read(path):
    return json.loads(Path(path).read_bytes())


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(path, value):
    raw = value if isinstance(value, bytes) else (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        assert path.read_bytes() == raw, 'immutable artifact differs; reconcile, do not rerun'
    else:
        with path.open('xb') as handle:
            handle.write(raw)
    return str(path)


def modules(m):
    scripts = Path(m['new_method_scripts'])
    sys.path.insert(0, str(scripts))
    import evo
    import control
    protocol = load(m['protocol'], 'old_frozen_protocol')
    acceptance = protocol.helper(Path(m['protocol']).with_name('acceptance.py'))
    return evo, control, protocol, acceptance


def compile_draft(m, evo, directory):
    if m.get('kind') == 'export-repair-diagnostic-validation':
        assert evo.digest(Path(m['source_library']).read_bytes()) == m['source_library_sha256']
        library = Path(directory) / 'library.json'
        library.parent.mkdir(parents=True, exist_ok=True)
        library.write_bytes(Path(m['source_library']).read_bytes())
        return evo.export_skill({'library': str(library), 'limits': {'general': 5, 'topic': 5},
            'max_bytes': 16000, 'general_storage': 'description-only', 'output': str(Path(directory) / 'skill')})
    contexts = {(v['task_id'], v['attempt_id']): v for v in map(read, m['context_paths'])}
    assert len(contexts) == 5 and all(v['split'] == 'train' for v in contexts.values())
    req = {'candidates': m['candidates'], 'library': m['empty_library'],
           'decisions': m['decisions'], 'limits': {'general': 5, 'topic': 5}, 'max_bytes': 16000,
           'output': str(Path(directory) / 'library.json')}
    evo.compile_validated_contexts(req, contexts)
    package = evo.export_skill({'library': req['output'], 'limits': req['limits'],
        'max_bytes': 16000, 'general_storage': 'description-only', 'output': str(Path(directory) / 'skill')})
    return package


def original_child(m, evo, control):
    launch = read(Path(m['workspace']) / 'launch-receipt.json')
    root = Path(m['trace_root'])
    prompt = Path(m['evolver_prompt']).read_text()
    sessions, matched = {}, set()
    for path in root.rglob('*.jsonl'):
        meta, _, rows = control.trace_context(path)
        if meta.get('source') != 'subagent':
            continue
        sessions.setdefault(meta['session_id'], []).append((path, meta))
        if any(not row.get('origin') and row.get('payload', {}).get('type') == 'text'
               and row['payload'].get('sender') == 'parent_agent' and row['payload'].get('text') == prompt for row in rows):
            matched.add(meta['session_id'])
    assert len(matched) == 1, 'exactly one actual Evolver with complete prepared training prompt required'
    session = next(iter(matched))
    values = sessions[session]
    meta = values[0][1]
    dispatch = read(m['evolver_call'])
    assert all(dispatch[k] == m['runtime'][k] for k in ('provider', 'model_id', 'thinking_level'))
    # The frozen native runtime records parentage in the parent's subagent event,
    # not a parent_trace field on child session_meta. Bind that event to the
    # completed dispatch call, rather than inventing or rewriting child metadata.
    # These are the arguments consumed by frozen 11d82df run-subagent.ts which
    # affect child identity/input or execution. Descriptions and arbitrary UI
    # annotations are not consumed; yield_time_ms only changes the wait window.
    functional = {'prompt', 'prompt_file', 'prompt_sha256', 'agent_id',
                  'provider', 'model_id', 'thinking_level', 'run_in_background'}
    def semantic(value):
        result = {k: v for k, v in value.items() if k in functional}
        # Native run_subagent omitting agent_id self-spawns the parent Agent.
        # Explicitly naming that same Agent has the same input and State; a
        # different Agent remains a functional identity mismatch.
        if m.get('controller_id'):
            result.setdefault('agent_id', m['controller_id'])
        return result
    links = []
    for parent in sorted(root.rglob(launch['sessionId'] + '*.jsonl')):
        parent_rows = [json.loads(line) for line in parent.read_text().splitlines() if line]
        assert any(row.get('type') == 'session_meta' and row['payload']['session_id'] == launch['sessionId']
                   and not row.get('origin') for row in parent_rows)
        for index, row in enumerate(parent_rows):
            call = row.get('payload', {})
            if row.get('origin') or call.get('type') != 'tool_call' or call.get('name') != 'run_subagent':
                continue
            if semantic(json.loads(call['arguments'])) != semantic(dispatch):
                continue
            tail = parent_rows[index + 1:]
            end = next((i for i, value in enumerate(tail) if not value.get('origin')
                        and value.get('payload', {}).get('type') == 'tool_call_output'
                        and value['payload'].get('tool_call_id') == call['tool_call_id']), None)
            if end is None or tail[end]['payload'].get('stop_reason') != 'completed':
                continue
            events = [value['payload']['session_id'] for value in tail[:end] if not value.get('origin')
                      and value.get('type') == 'event_msg' and value.get('payload', {}).get('type') == 'subagent']
            if events == [session]:
                links.append(str(parent))
    assert len(links) == 1, 'original native dispatch/child event differs or is ambiguous'
    binding = {k: meta[k] for k in ('session_id', 'source', 'agent_state', 'workspace', 'provider', 'model_id')}
    binding['parent_trace'] = links[0]
    assert binding['provider'] == m['runtime']['provider'] and binding['model_id'] == m['runtime']['model_id']
    view = evo.trace_view([str(p) for p, _ in values], binding)
    return session, view


def main(workspace, op, args):
    workspace = Path(workspace).resolve()
    m = read(workspace / 'manifest.json')
    assert m['workspace'] == str(workspace) and m['kind'] in (
        'training-recompilation-diagnostic-validation', 'export-repair-diagnostic-validation')
    evo, control, protocol, acceptance = modules(m)
    assert all(evo.digest(Path(path).read_bytes()) == sha for path, sha in m['source_hashes'].items()), 'frozen source changed'
    c = read(m['config'])
    if op == 'check':
        return {'complete': True, 'training_validation': read(m['training_validation']),
                'one_evolver_only': True, 'new_training_calls': 0, 'test_material_given_to_evolver': False}
    if op in ('validate', 'seal'):
        with tempfile.TemporaryDirectory(prefix='recompile-draft-') as temp:
            try:
                package = compile_draft(m, evo, temp)
            except (ValueError, KeyError, TypeError) as exc:
                return {'complete': False, 'recoverable_draft': True, 'reason': str(exc), 'output_path': m['decisions']}
        receipt = {'file_path': m['decisions'], 'sha256': evo.digest(Path(m['decisions']).read_bytes()),
                   'session_id': os.environ.get('PENGUIN_SESSION_ID'), 'package_sha256': package['sha256']}
        if op == 'seal':
            assert receipt['session_id'], 'seal must execute in original native Evolver'
            save(workspace / 'evolver-seal.json', receipt)
        return {'complete': True, **receipt}
    if op == 'publish':
        if (workspace / 'freeze.json').exists():
            return {'complete': True, 'freeze': read(workspace / 'freeze.json'), 'already_published': True}
        session, view, seal = None, {'sources': []}, None
        if m['kind'] == 'training-recompilation-diagnostic-validation':
            session, view = original_child(m, evo, control)
            seal = read(workspace / 'evolver-seal.json')
            assert seal['session_id'] == session and seal['sha256'] == evo.digest(Path(m['decisions']).read_bytes())
            assert any(seal.items() <= value.items() for pair in view['pairs']
                       for value in evo.json_values(pair['observation']['payload'].get('output', '')) if isinstance(value, dict)), 'original seal observation missing'
        package = compile_draft(m, evo, workspace / 'compiled')
        if seal is not None:
            assert package['sha256'] == seal['package_sha256']
        prior = read(m['old_freeze'])
        initial = acceptance.snapshot(prior['h0_snapshot'])['files']
        state = Path(prior['target_state'])
        current = acceptance.transported_filemap(state, evo)
        allowed = [acceptance.snapshot(prior[ph + '_snapshot'])['files'] for ph in ('h0', 'final')]
        assert current in [{k: evo.digest(v) for k, v in item.items()} for item in allowed], 'Target has unrelated changes or a live prior phase'
        files = dict(initial)
        for name in list(files):
            if acceptance.learned_state_file(name):
                del files[name]
        package_files = {p: (workspace / 'compiled/skill' / p).read_bytes() for p in package['files']}
        files.update(evo.package_state_files(package_files))
        version, rest = acceptance.config_version(files['system_config.yaml'])
        assert version == prior['h0_version']
        next_version = max(int(p.name[1:-7]) for p in state.parent.glob('snapshots/v*.tar.gz')) + 1
        files['system_config.yaml'] = files['system_config.yaml'].replace(('version: ' + str(version) + '\n').encode(), ('version: ' + str(next_version) + '\n').encode(), 1)
        with tempfile.TemporaryDirectory(prefix='recompiled-state-', dir=state.parent) as temporary:
            staged = Path(temporary) / 'agent_state'
            for name, raw in files.items():
                path = staged / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            evo.install_context({'state': str(staged)})
            final_files = {name: (staged / name).read_bytes() for name in evo.filemap(staged)}
            assert acceptance.package_from_snapshot(final_files, evo) == package
            assert acceptance.config_version(final_files['system_config.yaml'])[1] == rest
            old_plain, _ = evo.split_context(initial['AGENTS.md'].decode())
            new_plain, _ = evo.split_context(final_files['AGENTS.md'].decode())
            assert old_plain == new_plain
            assert {k:v for k,v in initial.items() if k not in ('AGENTS.md','system_config.yaml') and not acceptance.learned_state_file(k)} == {k:v for k,v in final_files.items() if k not in ('AGENTS.md','system_config.yaml') and not acceptance.learned_state_file(k)}
            snapshot = state.parent / 'snapshots' / ('v' + str(next_version) + '.tar.gz')
            assert not snapshot.exists()
            with tarfile.open(snapshot, 'w:gz') as archive:
                archive.add(staged, arcname='agent_state')
            backup = workspace / 'prior-live-state'
            assert not backup.exists()
            state.rename(backup)
            staged.rename(state)
        freeze = {'h0_snapshot': prior['h0_snapshot'], 'final_snapshot': str(snapshot),
                  'h0_version': prior['h0_version'], 'final_version': next_version,
                  'target_state': str(state), 'test_case_ids': prior['test_case_ids'],
                  'package': package, 'evolver_session': session,
                  'evolver_trace_sha256': {p['path']:p['sha256'] for p in view['sources']}}
        save(workspace / 'freeze.json', freeze)
        return {'complete': True, 'freeze': freeze, 'diagnostic_after_test_inspection': True}
    freeze = read(workspace / 'freeze.json')
    snapshots = {ph: acceptance.snapshot(freeze[ph + '_snapshot'])['files'] for ph in ('h0', 'final')}
    if op == 'restore':
        phase, = args
        state = Path(freeze['target_state'])
        assert acceptance.transported_filemap(state, evo) in [{k:evo.digest(v)for k,v in raw.items()}for raw in snapshots.values()]
        with tempfile.TemporaryDirectory(prefix='fixed-test-restore-', dir=state.parent) as temporary:
            staged = Path(temporary) / 'agent_state'
            for name, raw in snapshots[phase].items():
                path = staged / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            state.rename(Path(temporary) / 'prior')
            staged.rename(state)
        return {'complete': True, 'phase': phase, 'expected_version': freeze[phase + '_version']}
    if op == 'record':
        phase, number, case, trial = args
        number = int(number)
        assert phase in ('h0','final') and number in ((2,3) if phase == 'h0' else (1,2,3)) and case in freeze['test_case_ids']
        identity = {**read(m['old_matrices'][phase])['identity'], 'expected_version': freeze[phase + '_version'], 'case_id': case, 'run': number}
        injection = protocol.helper(m['injection_auditor'])
        record = injection.canonical_worker_cell({'identity': identity, 'session_id': trial, 'benchmark': m['benchmark'],
            'snapshot': freeze[phase + '_snapshot'], 'runtime': c['runtime'], 'config_value': c,
            'launch': read(Path(m['benchmark']) / case / 'statement/harbor-launch.json')}, evo, acceptance)
        if record['pending']:
            return record
        save(workspace / 'records' / f'r{number}-{phase}-{case}.json', record)
        return {'complete': True, 'cell': record['cell'], 'audit_passed': record['audit'].get('audit_passed', True)}
    if op == 'summary':
        output = {'kind': m['kind'], 'diagnostic_after_test_inspection': True, 'complete': True, 'conditions': {}}
        for phase in ('h0','final'):
            original = read(m['old_matrices'][phase])
            cells = read(original['cells']) if phase == 'h0' else []
            records = [read(p) for p in sorted((workspace / 'records').glob('r*-' + phase + '-*.json'))]
            cells += [v['cell'] for v in records]
            expected = {(case, n)for case in freeze['test_case_ids']for n in (1,2,3)}
            keys = [(cell['case_id'],cell['run'])for cell in cells]
            assert len(keys) == len(set(keys)), 'duplicate semantic measurement'
            complete = set(keys) == expected
            output['complete'] &= complete
            means = {str(n): statistics.mean(v['score']for v in cells if v['run']==n) for n in (1,2,3)if sum(v['run']==n for v in cells)==5}
            output['conditions'][phase] = {'cells': cells, 'round_means': means,
                'three_round_mean': statistics.mean(means.values())if len(means)==3 else None,
                'round_mean_sd': statistics.stdev(means.values())if len(means)==3 else None,
                'audit_passed': all(v['audit'].get('audit_passed',True)for v in records),
                'reused_rounds': sorted({v['run'] for v in read(original['cells'])})if phase=='h0'else [],
                'per_case': {case: {'scores': [v['score']for v in sorted(cells,key=lambda x:x['run'])if v['case_id']==case],
                    'mean': statistics.mean(v['score']for v in cells if v['case_id']==case),
                    'sd': statistics.stdev(v['score']for v in cells if v['case_id']==case)if sum(v['case_id']==case for v in cells)==3 else None}for case in freeze['test_case_ids'] if any(v['case_id']==case for v in cells)}}
        save_path = workspace / 'summary.json'
        save_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
        return output
    raise ValueError('unknown deterministic recompile operation')


if __name__ == '__main__':
    workspace, operation, *args = sys.argv[1:]
    print(json.dumps(main(workspace, operation, args), ensure_ascii=False, allow_nan=False))
