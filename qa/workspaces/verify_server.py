import os
from pathlib import Path
from seed import Backend
assert os.environ['GITHUB_ACTIONS']=='true'
stage=Path(os.environ['QA_STAGE']).resolve()
assert stage==Path(os.environ['RUNNER_TEMP']).resolve()/'garden-qa'
root=Path((stage/'workspace-home').read_text())
assert root==Path('/tmp/sky-notebooks')
b=Backend('http://127.0.0.1:43671',(stage/'token.private').read_text().strip())
triplet=[]
for path in [root/'Sky notebooks/Sky atlas',root/'Sky notebooks/Lunar maps',root/'Meteor watch']:
    assert path.is_dir()
    existing=b.call('workspace/create',request={'path':str(path)})
    assert existing['created'] is False
    workspace=existing['workspace']
    assert len(workspace['sessionIds'])==1
    triplet.append(workspace['sessionIds'][0])
assert len(set(triplet))==3
assert (stage/'workspace-evidence/verified').read_text()==os.environ['QA_COMMIT']
(stage/'workspace-evidence/isolation').write_text('Each of the three server workspaces owns one distinct session.')
print('Private authenticated server confirmed unique membership for all three workspace sessions.')
