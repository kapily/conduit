#!/usr/bin/env python3
"""Prepare an empty private Harness profile and its synthetic model on loopback."""
import datetime, json, os, re, secrets, subprocess, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

src=Path(__file__).resolve().parent
stage=Path(os.environ['QA_STAGE']).resolve()
stage.mkdir(parents=True,exist_ok=True)
os.umask(0o077)
assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
home=Path('/tmp/sky-notebooks')
home.mkdir(mode=0o700)
(stage/'workspace-home').write_text(str(home))
garden=home/'Horizon notes'
for p in (home/'profiles/web',home/'storages',garden,stage/'private-tmp'): p.mkdir(parents=True,exist_ok=True)
assert not (home/'storages/workspace.json').exists(), 'Expected a fresh QA profile'
(home/'profiles/web/cordis.patch.yml').write_text('''
- id: llm-pi-ai
  config:
    providers:
      greenhouse:
        displayName: Greenhouse
        api: openai-completions
        apiKeyEnv: GARDEN_MODEL_KEY
        baseURL: http://127.0.0.1:43672/v1
        models:
          - id: field-notes
            name: Field Notes
            contextWindow: 131072
            maxTokens: 4096
            input: [text]
- id: agent-default-model
  config:
    provider: greenhouse
    model: field-notes
- id: workspace-controller
  config:
    documentsDirectory: '''+json.dumps(str(garden))+'''
- id: ui-settings-general
  name: "@deepseek-ai/dsh-client-ui-settings-general"
  config:
    welcomeNoticeVersion: 2026-09-28.1
''')
key=uuid.uuid4().hex
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
(home/'storages/workspace.json').write_text(json.dumps({'unit':{'name':'workspace','version':2},'global':{'initialized':True,'workspaceIds':[key],'defaultWorkspaceId':key,'archivedSessionIds':[],'pinnedSessionIds':[]},'tables':{'workspaces':{key:{'path':str(garden),'title':'Horizon notes','createdAt':now,'updatedAt':now,'sessionIds':[]}}}}))
def launch(name,cmd):
 env={'PATH':os.environ['PATH'],'HOME':str(home),'DSH_HOME':str(home),'TMPDIR':str(stage/'private-tmp'),'GARDEN_MODEL_KEY':secrets.token_hex(18),'DSH_PERMISSION_MODE':'read-only','SSH_TTY':'/dev/pts/fixture','DO_NOT_TRACK':'1','CI':'true','LANG':'en_US.UTF-8','NO_COLOR':'1','npm_config_cache':str(stage/'npm-cache')}
 with (stage/(name+'.private.log')).open('wb') as log:
  p=subprocess.Popen(cmd,cwd=garden,env=env,stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True)
 (stage/(name+'.pid')).write_text(str(p.pid));return p
model=launch('model',[sys.executable,str(src/'model.py')])
host=launch('server',[os.environ['QA_DSH'],'web','--no-open','--host','127.0.0.1','--port','43671'])
for _ in range(350):
 if model.poll() is not None or host.poll() is not None: raise SystemExit('Synthetic service failed to start.')
 m=re.search(r'http://127\.0\.0\.1:43671/\?token=([A-Za-z0-9._~%=-]+)',(stage/'server.private.log').read_text(errors='ignore'))
 if m:break
 time.sleep(.2)
else:raise SystemExit('Synthetic host did not issue a launch link.')
(stage/'token.private').write_text(m.group(1))
class Stop(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args):return None
try:urllib.request.build_opener(Stop).open(m.group(0),timeout=5)
except urllib.error.HTTPError as e:
 assert e.code==303
 cookie=e.headers['Set-Cookie'].split(';',1)[0]
 req=urllib.request.Request('http://127.0.0.1:43671/',headers={'Cookie':cookie},method='HEAD')
 assert urllib.request.urlopen(req,timeout=5).status==200
else:raise SystemExit('Unexpected browser-grant result.')
print('Official Harness web grant verified with isolated garden model and workspace.')
