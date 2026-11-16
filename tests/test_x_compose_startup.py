"""The resolved Compose startup graph must keep X behind web's migration barrier."""
import json
import os
import subprocess
from pathlib import Path

from tests.conftest import require_tool


def test_x_startup_waits_for_migrated_web_service(tmp_path):
    require_tool('docker')
    root = Path(__file__).resolve().parents[1]
    (tmp_path / 'docker-compose.yml').write_text((root / 'docker-compose.yml').read_text())
    (tmp_path / '.env').write_text('')
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'DOCKER_CONFIG') if key in os.environ}
    env['POSTGRES_PASSWORD'] = 'compose-test-only'
    completed = subprocess.run(
        ['docker', 'compose', '--project-directory', str(tmp_path), '--profile', 'x', 'config', '--format', 'json'],
        env=env, check=True, capture_output=True, text=True, timeout=30,
    )
    services = json.loads(completed.stdout)['services']
    dependency = services['x'].get('depends_on', {}).get('web', {})
    assert dependency.get('condition') == 'service_healthy'
    assert services['web']['healthcheck']['test'][0] != 'NONE'
