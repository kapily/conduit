"""Fresh synthetic workspace/session enrollment through the real Harness endpoint."""
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid

class Stop(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None

class Backend:
    def __init__(self, origin, secret):
        if origin not in ('http://127.0.0.1:44771','http://127.0.0.1:43671') or not re.fullmatch(r'[A-Za-z0-9._~%=-]{16,512}', secret): raise ValueError('Expected synthetic local fixture')
        self.origin=origin
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Stop())
        try:self.opener.open(origin+'/?token='+secret,timeout=5)
        except urllib.error.HTTPError as error:
            if error.code!=303:raise RuntimeError('Local grant exchange failed.') from None
            self.cookie=error.headers['Set-Cookie'].split(';',1)[0]
            assert self.cookie.startswith('dsh-auth-')
        else:raise RuntimeError('Missing isolated grant.')
    def call(self, method, **args):
        rpc=str(uuid.uuid4())
        data={'type':'client-request','rpcId':rpc,'method':method,'payload':{'args':args}}
        req=urllib.request.Request(self.origin+'/api/'+method,json.dumps(data).encode(),headers={'Cookie':self.cookie,'Origin':self.origin,'Content-Type':'application/json'})
        try:
            with self.opener.open(req,timeout=8) as response:
                obj=json.load(response)
        except urllib.error.HTTPError as e:raise RuntimeError(f'Local {method} HTTP status {e.code}') from None
        result=obj.get('result',{})
        assert obj.get('type')=='server-response' and obj.get('rpcId')==rpc
        if result.get('ok') is not True:
            err=result.get('error',{})
            raise RuntimeError(f'Local {method} failed ({str(err.get("code","unknown"))[:85]}).')
        return result.get('value')

def sky_sessions(client,base):
    projects=base/'Sky notebooks';projects.mkdir()
    result={}
    for folder,title in [('Sky atlas','Blue hour observing plan'),('Lunar maps','Three crater sketches')]:
        target=projects/folder;target.mkdir()
        obj=client.call('workspace/create',request={'path':str(target)})
        workspace=obj['workspace']['workspaceId']
        session=client.call('session/create',request={'workspaceId':workspace})['sessionId']
        renamed=client.call('session/rename',request={'sessionId':session,'title':title})
        assert renamed['title']==title
        result[folder]=workspace
    return result

if __name__=='__main__':
    assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
    stage=Path(os.environ['QA_STAGE']).resolve()
    assert stage==Path(os.environ['RUNNER_TEMP']).resolve()/'garden-qa'
    b=Backend('http://127.0.0.1:43671',(stage/'token.private').read_text().strip())
    home=Path((stage/'workspace-home').read_text());assert home==Path('/tmp/sky-notebooks')
    sky_sessions(b,home)
    print('Two invented workspaces and their distinct server sessions were enrolled.')
