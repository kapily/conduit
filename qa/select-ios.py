"""Create a fresh phone from a compatible device/runtime combination on this runner."""
import json
import os
from pathlib import Path
import subprocess

stage = Path(os.environ['RUNNER_TEMP']) / 'garden-qa'
inventory = json.loads(subprocess.check_output(['xcrun', 'simctl', 'list', '-j']))
runtimes = [runtime for runtime in inventory['runtimes']
            if runtime.get('isAvailable') and '.iOS-' in runtime['identifier']
            and runtime['version'].split('.')[0] in ('26', '27')]
runtimes.sort(key=lambda runtime: tuple(map(int, runtime['version'].split('.'))), reverse=True)
preferred = 'com.apple.CoreSimulator.SimDeviceType.iPhone-17'
candidates = []
for runtime in runtimes:
    rid = runtime['identifier']
    listed = [d.get('deviceTypeIdentifier', '') for d in inventory['devices'].get(rid, [])
              if d.get('isAvailable') and d.get('name', '').startswith('iPhone')]
    supported = [d['identifier'] for d in runtime.get('supportedDeviceTypes', [])
                 if d.get('name', '').startswith('iPhone')]
    candidates.extend([(kind, rid) for kind in [*listed, *supported] if kind])
candidates = list(dict.fromkeys((kind, rid) for kind, rid in candidates))
candidates.sort(key=lambda pair: (pair[0] != preferred, 'iPhone-17' not in pair[0]))
candidates.extend((preferred, runtime['identifier']) for runtime in runtimes)
for kind, rid in dict.fromkeys(candidates):
    created = subprocess.run(['xcrun','simctl','create','Garden QA',kind,rid],capture_output=True,text=True)
    if created.returncode:continue
    (stage/'ios-device').write_text(created.stdout.strip())
    print('Dedicated compatible simulator:', kind.rsplit('.',1)[-1], rid.rsplit('.',1)[-1])
    break
else:
    raise SystemExit('Available iOS runtimes and iPhone types supplied no compatible pairing.')
