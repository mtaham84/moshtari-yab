
import sys
sys.path.insert(0, '/app')
import json
from pathlib import Path
from datetime import datetime, timezone
from workers.x_collector.worker import XCollector, normalize_tweet
import tempfile

fixture = json.loads((Path('/app/workers/x_collector/tests/fixtures/x_tweets.json')).read_text(encoding='utf-8'))
fresh = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
for item in fixture:
    item['createdAtISO'] = fresh

print('Fixture count:', len(fixture))
for idx, f in enumerate(fixture):
    norm = normalize_tweet(f)
    print(f'Item {idx} normalized:', norm is not None)

td = tempfile.mkdtemp()
root = Path(td)
collector = XCollector(client=None, output_dir=root / 'collected', state_path=root / 'state.sqlite3')
res = collector.run(['query'], mock_records=fixture)
print('Result:', res)
