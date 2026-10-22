"""Wait for one original Harbor job. This reader never launches or retries work."""
import argparse
import json
from pathlib import Path
import time


def status(directory):
    directory = Path(directory)
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise ValueError('use the original absolute Harbor job directory')
    def read(path):
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return None
    job = read(directory / 'result.json')
    trials = []
    for path in sorted(directory.glob('*/result.json')):
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('trial result escapes its original job')
        value = read(path)
        trials.append({'trial': path.parent.name, 'result': str(path),
                       'finished_at': value.get('finished_at'),
                       'agent_execution': value.get('agent_execution'),
                       'verifier_result': value.get('verifier_result'),
                       'exception_info': value.get('exception_info')})
    # A completed worker reply or a terminal trial alone is not a terminal job.
    complete = bool(job and job.get('finished_at') and trials
                    and all(t['finished_at'] for t in trials)
                    and job.get('stats', {}).get('n_running_trials', 0) == 0)
    return {'job': str(directory), 'complete': complete, 'pending': not complete,
            'finished_at': job.get('finished_at') if job else None,
            'trials': trials,
            'next_action': ('inspect these original records; do not regrade because cleanup failed' if complete
                else 'await the same native process_id and this original job; do not redispatch or clean its environment')}


def wait(directory, timeout=30.0, poll=0.25):
    if not 0 <= timeout <= 60 or not 0 < poll <= 5:
        raise ValueError('wait timeout must be 0..60 seconds; poll must be >0 and <=5')
    deadline = time.monotonic() + timeout
    while True:
        result = status(directory)
        if result['complete'] or time.monotonic() >= deadline:
            return result
        time.sleep(min(poll, max(deadline-time.monotonic(), 0)))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('job'); p.add_argument('--timeout', type=float, default=30)
    args = p.parse_args()
    print(json.dumps(wait(args.job, args.timeout), ensure_ascii=False))
