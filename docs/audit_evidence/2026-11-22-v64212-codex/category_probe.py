from tests import test_support
import ast,json
import config
from catchup_plan import plan_catchup
old_tree=ast.parse(open('baseline_config.py').read())
old={}
for node in old_tree.body:
    if isinstance(node,ast.Assign):
        for target in node.targets:
            if isinstance(target,ast.Name) and target.id.endswith('_CATEGORIES'):
                try: old[target.id]=ast.literal_eval(node.value)
                except (ValueError,TypeError): pass
masters=set(config.MASTER_CATEGORIES)
old_changed={};new_changed={};changes=[]
for name,previous in old.items():
    current=getattr(config,name,None)
    if not isinstance(previous,list) or not isinstance(current,list): continue
    if not all(isinstance(x,dict) and 'url' in x for x in previous): continue
    assert len(previous)==len(current),name
    for a,b in zip(previous,current):
        assert set(a)==set(b),name
        assert {k:v for k,v in a.items() if k!='master'}=={k:v for k,v in b.items() if k!='master'},name
        if a.get('master')!=b.get('master'):
            assert a['master'] not in masters and b['master'] in masters,(name,a,b)
            old_changed.setdefault(name,[]).append(a);new_changed.setdefault(name,[]).append(b)
            changes.append({'list':name,'old':a['master'],'new':b['master']})
assert len(changes)==44,len(changes)
args=dict(enabled_masters=masters,hot=set(),last_scans={},now=1800000000,wave_interval=9600,cycle=86400)
assert plan_catchup(old_changed,**args)=={}
plan=plan_catchup(new_changed,**args)
assert sum(map(len,plan.values()))==44
assert plan_catchup(new_changed,**dict(args,enabled_masters=set()))=={}
count=config.get_wave_plan()['total_waves'];union=set()
for i in range(count): union.update(config.get_wave_plan(wave_index=i)['active_categories'])
assert all(x['new'] in union for x in changes)
print(json.dumps({'changed':44,'previously_catchup_eligible':0,'now_catchup_eligible':44,'disabled_eligible':0,
 'all_reachable_in_wave_cycle':True,'wave_count':count,'by_list':{k:len(v) for k,v in new_changed.items()},'changes':changes},ensure_ascii=False))
