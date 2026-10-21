"""Opt-in compiled native Server startup/restart, with the Task entry mocked.

PENGUIN_NATIVE_READY_CHECKOUT=/absolute/compiled/penguin python3 -B -m unittest \
  discover -s tools -p test_penguin_native_ready.py -v

Only local loopback access; no credentials, model request or Session is created.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / "agents/penguin_agent/helper.mjs"
SOURCE = os.environ.get("PENGUIN_NATIVE_READY_CHECKOUT")


@unittest.skipUnless(SOURCE and shutil.which("node"), "compiled native checkout opt-in required")
class NativeReadinessTests(unittest.TestCase):
    def test_fresh_root_and_old_token_restart_use_single_put_and_mock_task(self):
        cli = Path(SOURCE).resolve()/"packages/cli/dist/penguin.js"
        self.assertTrue(cli.is_file(), "build the native CLI first")
        with tempfile.TemporaryDirectory(prefix="evo-native-ready-") as temporary:
            root = Path(temporary)
            home, prefix = root/"data", root/"prefix"
            home.mkdir()
            (prefix/"node/bin").mkdir(parents=True)
            (prefix/"bin").mkdir()
            (prefix/"node/bin/node").symlink_to(shutil.which("node"))
            shutil.copyfile(HELPER, prefix/"helper.mjs")
            (prefix/"instruction.md").write_text("SYNTHETIC task must be mocked")
            guard = root/"fetch-guard.mjs"
            guard.write_text('''import fs from 'node:fs';
const original=globalThis.fetch;
globalThis.fetch=async(input,options={})=>{
  const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
  const method=(options.method??input?.method??'GET').toUpperCase();
  if(!['localhost','127.0.0.1','[::1]'].includes(url.hostname)||
      (method!=='GET'&&/sessions|messages|tasks/.test(url.pathname))){
    fs.appendFileSync(process.env.FIXTURE_HTTP,JSON.stringify({blocked:true})+'\\n');
    throw Error('nonlocal/model/task request forbidden');
  }
  const response=await original(input,options);
  fs.appendFileSync(process.env.FIXTURE_HTTP,JSON.stringify({method,path:url.pathname,status:response.status})+'\\n');
  return response;
};
''')
            wrapper = prefix/"bin/penguin"
            wrapper.write_text("#!"+sys.executable+"\n"+'''
import json, os, sys
from pathlib import Path
if sys.argv[1:2]==['run'] and sys.argv[2:]!=['--help']:
    with Path(os.environ['FIXTURE_HTTP']).open('a') as file:
        file.write(json.dumps({'method':'MOCK_TASK','path':None,'status':None})+'\\n')
    print('{"status":"completed","sessionId":"-"}')
else:
    os.execv(os.environ['FIXTURE_NODE'],[os.environ['FIXTURE_NODE'],os.environ['FIXTURE_CLI'],*sys.argv[1:]])
''')
            wrapper.chmod(0o755)
            previous_token = None
            for attempt in ("fresh", "restart_with_old_token"):
                with self.subTest(attempt=attempt):
                    trace = root/(attempt+"-http.jsonl")
                    logs = root/(attempt+"-logs")
                    env = dict(PATH=os.environ["PATH"], HOME=str(root/"isolated-user-home"),
                        PENGUIN_HOME=str(home), PB_PREFIX=str(prefix), PB_LOGS=str(logs),
                        PB_PROJECT="default_project", PB_AGENT="default_agent", PB_PROVIDER="synthetic",
                        PB_MODEL="no-model", PB_THINKING="high", PB_TIMEOUT="5s", PB_ABORT_WAIT="5", PB_MAX_TURNS="-1",
                        NO_PROXY="localhost,127.0.0.1", NODE_OPTIONS="--import "+str(guard),
                        FIXTURE_HTTP=str(trace), FIXTURE_NODE=shutil.which("node"), FIXTURE_CLI=str(cli))
                    if previous_token is not None:
                        self.assertEqual((home/"api-token").read_text(), previous_token)
                    try:
                        result = subprocess.run(["bash",str(HELPER.with_name("run.sh"))],cwd=root,env=env,
                                                capture_output=True,text=True,timeout=90)
                    finally:
                        # On a test timeout, stop only this new private root;
                        # never leave its native server behind after temp cleanup.
                        if (home/"server.lock").exists():
                            subprocess.run([str(wrapper),"server","stop","--root",str(home)],cwd=prefix,env=env,
                                           capture_output=True,text=True,timeout=15)
                    token = (home/"api-token").read_text()
                    # No arbitrary native output is included in a test failure.
                    self.assertNotIn(token.strip(),result.stdout+result.stderr)
                    self.assertEqual(result.returncode,0,"native adapter failed; private temp logs retained during assertion only")
                    if previous_token is not None:self.assertNotEqual(token,previous_token)
                    previous_token=token
                    events=[json.loads(line) for line in trace.read_text().splitlines()]
                    self.assertFalse(any(event.get("blocked") for event in events))
                    writes=[event for event in events if event['method']=="PUT"]
                    config="/api/projects/default_project/agents/default_agent/config"
                    self.assertEqual(writes,[{'method':'PUT','path':config,'status':200}])
                    task_positions=[i for i,e in enumerate(events) if e['method']=="MOCK_TASK"]
                    self.assertEqual(len(task_positions),1)
                    put_position=next(i for i,e in enumerate(events) if e['method']=="PUT")
                    self.assertTrue(any(e['method']=="GET" and e['path']==config and e['status']==200 for e in events[:put_position]))
                    self.assertTrue(any(e['method']=="GET" and e['path']==config and e['status']==200 for e in events[put_position+1:task_positions[0]]))
                    ready=json.loads((logs/"penguin/server-ready.json").read_text())
                    self.assertTrue(ready['ready'])
                    self.assertEqual(ready['stage'],'agent_config')
                    idle=json.loads((logs/"penguin/quiescence.json").read_text())
                    self.assertTrue(idle['native_sessions_idle'])
                    self.assertEqual(idle['sessions_checked'],0)
                    self.assertEqual((logs/"penguin/server.log").stat().st_mode & 0o777,0o600)
                    self.assertFalse((home/"server.lock").exists())


if __name__ == "__main__":unittest.main()
