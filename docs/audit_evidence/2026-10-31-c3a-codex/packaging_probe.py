"""Auxiliary packaging-excluded sensitivity check; no database or labels are modified."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from scripts import c2_quality_report as report

names=('retained','dropped_mixed','dropped_both_k2','added')
rows=[{'label':'same','strata':[n]} for n in names]
rows.append({'label':'packaging','strata':['added']})
sizes=dict.fromkeys(names,100)
fresh=report.weighted_estimate(rows,sizes,None)
known=report.weighted_estimate(rows,sizes,None,{'added':['packaging']})
out={'fresh_packaging':fresh['layers']['added'],'known_packaging':known['layers']['added']}
print(json.dumps(out,indent=2))
