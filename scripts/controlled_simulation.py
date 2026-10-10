"""Generate strictly simulated contract evidence; never issue a GO decision."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.simulation.harness import demonstration

if __name__ == '__main__':
    output = ROOT / 'artifacts' / 'controlled_simulation.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    report = demonstration()
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'mode':report['mode'], 'simulated_events':len(report['events']),
                      'production_acceptance':report['production_acceptance'], 'report':str(output)}))
