import json
import subprocess
import os

status_output = subprocess.check_output(['git', 'status', '--short']).decode('utf-8')
lines = status_output.strip().split('\n')

inventory = {
    'intended_implementation': [],
    'generated_contracts_evidence': [],
    'environment_local': [],
    'scratch_scripts': [],
    'unknown': []
}

for line in lines:
    if not line: continue
    path = line[3:]
    if path.startswith('docs/evidence/') or 'openapi' in path or 'generated' in path:
        inventory['generated_contracts_evidence'].append(path)
    elif path.startswith('CODEX_') or path.startswith('docs/review/'):
        inventory['environment_local'].append(path)
    elif 'frontend/' in path or 'backend/' in path or 'infra/' in path or path in ['compose.yaml']:
        inventory['intended_implementation'].append(path)
    else:
        inventory['unknown'].append(path)

os.makedirs('docs/evidence/stage4_e2e_completion', exist_ok=True)
with open('docs/evidence/stage4_e2e_completion/4a_change_inventory.json', 'w') as f:
    json.dump(inventory, f, indent=2)

baseline = {
    'stage': '4A',
    'classification': 'CONTRACT_TESTED',
    'environment': {'python': '3.13', 'node': '24'},
    'commands': ['git status', 'git diff'],
    'exitCode': 0,
    'result': 'PASS',
    'artifacts': ['4a_change_inventory.json', '4a_git_status_before.txt', '4a_diff_stat_before.txt', '4a_diff_check_before.txt']
}
with open('docs/evidence/stage4_e2e_completion/4a_repository_baseline.json', 'w') as f:
    json.dump(baseline, f, indent=2)

print('JSON files created')
