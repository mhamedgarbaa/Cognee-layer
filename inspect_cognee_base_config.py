import subprocess

SCRIPT = r'''
import inspect
from cognee.infrastructure.config import base_config
import cognee.infrastructure.config.base_config as bc

cfg = base_config.get_base_config()
print('data_root_directory=', cfg.data_root_directory)
print('system_root_directory=', cfg.system_root_directory)
print('cache_root_directory=', cfg.cache_root_directory)
print('logs_root_directory=', cfg.logs_root_directory)
print('source_file=', inspect.getsourcefile(bc))

src_path = inspect.getsourcefile(bc)
with open(src_path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

print('\n=== potential env aliases ===')
for idx, line in enumerate(lines, 1):
    text = line.strip()
    if (
        'AliasChoices' in text
        or 'validation_alias' in text
        or 'Field(' in text
        or 'env=' in text
        or 'ROOT' in text
        or 'root_directory' in text
    ):
        if any(k in text.lower() for k in ['root', 'data', 'system', 'cache', 'logs', 'directory', 'cognee']):
            print(idx, text)
'''

result = subprocess.run(
    ['docker', 'exec', 'cognee_mcp_server', 'python', '-c', SCRIPT],
    capture_output=True,
    text=True,
    timeout=120,
)

print(result.stdout)
if result.stderr:
    print('STDERR:')
    print(result.stderr)
