"""Deterministic acceptance data. Never publish these synthetic runs to Pages."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from publish import export_site

root=Path(__file__).resolve().parents[1]
time='2026-09-20T12:00:00Z'
repo='ScienceDiscovery/sciencediscovery'
base='https://github.com/'+repo
item=dict(number=1,title='处理超时问题 <script>alert(1)</script>',url=base+'/issues/1',author='maintainer',labels=[{'name':'bug'}],assignees=[],age_days=4,updated_at=time,milestone='v1.0')
pr=dict(item,number=3,title='修复工作流',url=base+'/pull/3',draft=False,review_decision='CHANGES_REQUESTED',head_sha='a'*40,ci={'state':'failure','checks':[{'name':'E2E','conclusion':'failure','url':base+'/actions/runs/10'}]})
runs=[]
for idx,kind in enumerate(('gate','daily','release')):
    counts={'tests':10,'passed':7,'failed':1,'skipped':1,'flaky':1}
    if kind=='release': counts={'tests':10,'passed':10,'failed':0,'skipped':0,'flaky':0}
    runs.append(dict(id=10+idx,workflow_id=idx,name={'gate':'CI','daily':'Daily','release':'Release'}[kind],channel=kind,event={'gate':'pull_request','daily':'schedule','release':'release'}[kind],status='completed',conclusion='failure' if kind!='release' else 'success',branch='main',sha='a'*40,attempt=2,updated_at=time,url=base+'/actions/runs/'+str(10+idx),reports_status='available',jobs=[{'name':'E2E','status':'completed','conclusion':'failure','url':base+'/actions/runs/10','failed_steps':['Run browser journeys']}],tests=[{'name':'e2e-results','layer':'e2e','status':'available','counts':counts,'cases':[{'name':'创建项目','file':'test/create.spec.ts','project':'chromium','status':'passed'},{'name':'恢复会话','file':'test/session.spec.ts','project':'chromium','status':'flaky'}]}]))
doc={'schema_version':1,'generated_at':time,'repository':{'name':repo,'url':base,'description':'浏览器验收数据','default_branch':'main'},'issues':{'open_count':1,'unassigned_count':1,'stale_count':0,'counts':{'opened_30d':4,'closed_30d':3},'items':[item]},'prs':{'open_count':1,'waiting_review_count':1,'draft_count':0,'merged_30d':2,'median_time_to_merge_h':3,'items':[pr]},'quality':{'runs':runs,'required_checks':['E2E'],'branch_protected':True},'releases':[{'tag':'v1.0','url':base+'/releases/tag/v1.0','sha':'a'*40,'published_at':time,'prerelease':False,'validation_run_ids':[12]},{'tag':'v0.9','url':base+'/releases/tag/v0.9','sha':'b'*40,'published_at':time,'prerelease':False,'validation_run_ids':[]}],'notices':[{'message':'部分补充信息不可读取，请以 GitHub 原页面为准。'}]}

# Keep all ten pages exercised without contacting GitHub during acceptance tests.
from datetime import datetime, timezone
from unittest.mock import patch
from gsb.board import BoardStore
from gsb.config import Config
from gsb.collectors import Context
from gsb.public_sections import public_tests, score_history
from gsb.lines import configured_lines

item.update(state='open',created_at=time,comments=0,idle_days=4,body='## Reproduction\nDetails for #1. <script>alert(2)</script>')
pr.update(state='open',created_at=time,comments=1,idle_days=4,body='Fixes #1',head='fix-timeout',base='releases/v0.3.0.beta',requested_reviewers=['reviewer'],reviews=[],waiting_review=True,mergeable='CONFLICTING',linked_issues=[1])
issues=doc['issues']; issues.update(unlabeled_count=0,no_response_count=1,median_age_days=4,oldest_age_days=4,stale_days_threshold=30,labels=[dict(name='bug',color='aa3333',count=1)],unused_labels=[],aging=[dict(bucket='0–7 天',count=1)],assignee_load=[],milestones=[dict(name='v1.0',count=1)],recent=[item],stale=[],oldest=[item],closed_recent=[])
issues['counts'].update(closed_total=3,opened_7d=1,closed_7d=0)
prs=doc['prs'];prs.update(review_sla_days=3,ci_states={'failure':1},review_decisions={'CHANGES_REQUESTED':1},closed_unmerged_30d=0,p90_time_to_merge_h=5,median_time_to_merge_h_human=3,merged_human_count=2,reviewer_load=[dict(login='reviewer',count=1)],authors=[dict(login='maintainer',count=1)],recent_merged=[],recent_closed_unmerged=[])
# 120 days of synthetic Issue / PR flow, turned into series by the collector's own trend code.
from datetime import date, timedelta
from gsb.work_trend import daily_trend
flow={}
for i in range(120):
    day=(date(2026,5,24)+timedelta(days=i)).isoformat()
    merged=[1,1,0,2,1,1,0][(i+4)%7]
    for key,value in (('issues:created_at',[0,1,2,3,1,0,4][i%7]+(i%11==0)),('issues:closed_at',[1,0,1,2,0,1,1][i%7]),
                      ('prs:created_at',[2,1,0,3,2,1,1][i%7]),('prs:merged_at',merged),('prs:closed_at',merged+(i%9==0))):
        if value: flow[f'{key}:{day}']=value
issues['trend']=daily_trend(flow,'issues','2026-09-20')
prs['trend']=daily_trend(flow,'prs','2026-09-20',complete=False)
assert min(issues['trend']['open'])>=0 and min(prs['trend']['open'])>=0
for r in runs:
    r.update(title=r['name'],created_at=time,duration_s=120,actor='maintainer')
    r['jobs'][0].update(duration_s=120)
    r['tests'][0].update(artifact_id=r['id']+100,url=r['url']+'/artifacts/100',created_at=time)
real_scores=[]
for case,value,seconds in [('DRB-59',0.5433,2796),('DRB-64',0.5595,2310),('DRB-58',0.5413,1722),('DRB-62',0.5297,2670),('DRB-75',0.5376,4248)]:
    real_scores.append(dict(case=case,family='deepresearchbench',delivery='passed',quality_status='passed',duration_ms=seconds*1000,metrics=[dict(label='RACE',value=value,unit='ratio'),dict(label='Citation accuracy',value=82.4,unit='percent'),dict(label='Verification coverage',value=91.7,unit='percent')]))
real_scores += [
    dict(case='BiomniBench-da-13-3',family='biomnibench',delivery='passed',quality_status='scored',duration_ms=672000,metrics=[dict(label='Rubric',value=87,unit='score100')]),
    dict(case='BiomniBench-da-14-1',family='biomnibench',delivery='passed',quality_status='scored',duration_ms=870000,metrics=[dict(label='Rubric',value=100,unit='score100')]),
    dict(case='TC-E2E-01',family='research-team',delivery='passed',quality_status='scored',duration_ms=2850000,metrics=[dict(label='Judge total',value=86.25,unit='score100')]),
    dict(case='PUCT-COMPRESS',family='evolve-compression',delivery='passed',quality_status='scored',duration_ms=534000,metrics=[dict(label='Held-out test',value=0.695296,unit='ratio'),dict(label='LLM judge',value=86.25,unit='score100')]),
]
latest_scores=json.loads(json.dumps(real_scores))
latest_scores[0]['delivery'] = 'failed'
latest_scores[0]['metrics'][0]['value'] = None
latest_scores[0]['journey'] = dict(source='run-report',
    goal='Research DRB-59 and evaluate both delivery and report quality.',
    preconditions=['live generator', 'isolated Swarm stack', 'configured judges and source access'],
    step_summary='本次报告没有记录独立的用户步骤。', steps=[],
    metadata=dict(type='real', model='Live generator and configurable judges',
                  credentials='E2E_API_TOKEN（仅变量名）', cost_side_effects='Billable research and Judge calls'))
runs[1]['tests'].insert(0,dict(name='real-e2e-results',layer='e2e',status='available',counts=dict(tests=9,passed=9,failed=0,skipped=0,flaky=0),cases=[],scores=latest_scores,artifact_id=210,url=runs[1]['url']+'/artifacts/210',created_at=time))
runs[1]['tests'][0]['counts'].update(passed=8, failed=1)
class GH:
    def get_text_file(self,*args): return '{"scripts":{"test":"node --test"}}'
    def paginate(self,*args,**kwargs): return []
cfg=Config(repo=repo)
ctx=Context(GH(),cfg,datetime.now(timezone.utc),{'default_branch':'main'})
with patch('gsb.public_sections._tree_paths',return_value=(['services/core/src/index.ts','services/core/tests/test_main.py','test/session.spec.ts','services/empty/index.ts'],'github:git-tree')):
    tests=public_tests(ctx,runs)
# Synthetic history exists only in the browser acceptance fixture. Real history
# comes from recorded Actions attempts and is never inferred from one score.
historical=[]
for day,shift in ((4,-0.03),(6,-0.024),(8,-0.021),(10,-0.012),(12,-0.015),(14,-0.009),(15,-0.018),(16,-0.007),(17,0.004),(18,-0.002),(19,0.008)):
    when=f'2026-09-{day:02d}T12:00:00Z'
    scores=json.loads(json.dumps(real_scores))
    for score in scores:
        score['duration_ms'] = max(60000, score['duration_ms'] + (day - 15) * 47000 + (day % 3) * 81000)
        for metric in score['metrics']:
            if metric['value'] is not None:
                value=metric['value']+shift*(100 if metric['unit'] in ('percent','score100') else 1)
                if metric['unit'] in ('percent','score100'):
                    value=max(0,min(100,value))
                elif metric['unit']=='ratio':
                    value=max(0,min(1,value))
                metric['value']=round(value,4)
    if day==17:
        scores[-1]['metrics'][-1].update(value=None,status='error')
    if day==4:
        scores[-2]['delivery']='failed'
        scores[-2]['metrics'][0]['value']=None
    historical.append(dict(id=800+day,attempt=1,created_at=when,url=base+f'/actions/runs/{800+day}',tests=[
        dict(name='real-e2e-results',url=base+f'/actions/runs/{800+day}/artifacts/{900+day}',scores=scores)]))
tests['score_history']=score_history([runs[1],*historical])
tests['score_runs']=[dict(run_id=r['id'],attempt=r['attempt'],created_at=r['created_at'],url=r['url'],
    conclusion=r.get('conclusion','success'),duration_s=r.get('duration_s'),artifact_status='available') for r in [runs[1],*reversed(historical)]]
node_totals=dict(lines=dict(covered=48878,total=58864,percentage=83.04),branches=dict(covered=13987,total=17548,percentage=79.71),functions=dict(covered=3928,total=4701,percentage=83.56))
python_totals=dict(lines=dict(covered=5845,total=8932,percentage=65.44),branches=dict(covered=1507,total=3086,percentage=48.83))
# Per-file totals in nested directories exercise the coverage tree.
FILES={'node':[('src/index.ts',40,50),('src/util/strings.ts',9,10),('src/util/numbers.ts',2,10),('src/api/client.ts',30,60)],
       'python':[('src/sciencediscovery_evolve/auth.py',18,20),('src/sciencediscovery_evolve/candidates.py',10,40)]}
def coverage_language(name,totals,group):
    baseline=dict(artifact=f'{name}-coverage-summary-main-incremental-mainsha',created_at=time,kind='main full',sha='a'*40,totals=totals,groups=[dict(name=group,files=4,totals=totals)])
    current=dict(kind='authoritative',totals=totals,groups=[dict(name=group,files=4,totals=totals,source_sha='a'*40,updated_at=time,update_kind='full baseline')],increments=[],sources=[dict(path=f'{group}/{path}',totals={k:dict(covered=c,total=t,percentage=round(c*100/t,2)) for k,(c,t) in zip(totals,[(covered,total)]*len(totals))}) for path,covered,total in FILES[name]])
    percentages=[totals['lines']['percentage']-2.8,totals['lines']['percentage']-2.4,totals['lines']['percentage']-2.0,totals['lines']['percentage']-1.8,totals['lines']['percentage']-1.2,totals['lines']['percentage']-0.4,totals['lines']['percentage']]
    history=[]
    for day,percentage in zip((11,12,17,18,19,20,21),percentages):
        historical_totals=json.loads(json.dumps(totals))
        historical_totals['lines']['percentage']=round(percentage,2)
        historical_totals['lines']['covered']=round(historical_totals['lines']['total']*percentage/100)
        history.append(dict(day=f'2026-09-{day:02d}',artifact=f'{name}-coverage-summary-nightly-{day}',run_id=day,created_at=f'2026-09-{day:02d}T12:00:00Z',sha='a'*40,kind='nightly',totals=historical_totals))
    return dict(language=name,source='artifact:'+baseline['artifact'],scope='fixture coverage scope',baseline=baseline,current=current,history=history,pull_requests=[dict(number=3,branch='fix-timeout',created_at=time,sha='a'*40,groups=current['groups'],totals=totals)])
combined=dict(lines=dict(covered=54723,total=67796,percentage=80.72),branches=dict(covered=15494,total=20634,percentage=75.09),functions=node_totals['functions'])
tests['coverage']=dict(source='Actions coverage summaries',value=dict(format='sciencediscovery-summary',lines_pct=80.72,branches_pct=75.09,functions_pct=83.56),current=dict(kind='authoritative',totals=combined),languages=dict(node=coverage_language('node',node_totals,'packages/core'),python=coverage_language('python',python_totals,'services/evolve')),attempts=[dict(step='Actions 覆盖率摘要',ok=True,detail='Node.js + Python')])
# Tagged catalog: default-branch PR and Daily plans share one rule, Release has not run.
import io, zipfile
from gsb.tagged import TaggedStore, extract
tag_schema={'groups':{'category':{'multiple':False,'values':['ut','st','e2e']},'os':{'multiple':True,'values':['linux','macos','windows']},'arch':{'multiple':True,'values':['amd64','arm64']},'npu':{'multiple':False,'values':['none','required'],'default':'none'},'model':{'multiple':False,'values':['none','mock','real'],'default':'none'},'judge':{'multiple':False,'values':['none','llm'],'default':'none'},'status':{'multiple':False,'values':['reviewed','external','legacy','unreviewed'],'default':'reviewed'},'sandbox':{'multiple':False,'values':['none','bubblewrap','seatbelt'],'default':'none'}}}
policy='(category:ut or category:st or category:e2e) and os:linux and arch:amd64 and npu:none and (model:none or model:mock) and judge:none and status:reviewed'
groups_of={'ut':[(280,'packages/core/src/unit.test.ts',['os:linux','os:macos','arch:amd64','arch:arm64']),(24,'services/runner/src/sandbox.test.ts',['os:linux','arch:amd64','arch:arm64','sandbox:bubblewrap']),(12,'services/paper/tests/test_external.py',['os:linux','arch:amd64','status:external']),(3,'services/runner/src/seatbelt.test.ts',['os:macos','arch:arm64','sandbox:seatbelt'])],
  'st':[(2,'test/api/agent_loop_smoke.ts',['os:linux','arch:amd64','model:mock']),(1,'test/api/agent_loop_real_smoke.ts',['os:linux','arch:amd64','model:real']),(1,'services/runner/workloads/npu-smoke-test.py',['os:linux','arch:amd64','npu:required'])],
  'e2e':[(18,'test/journey-first-run.spec.ts',['os:linux','arch:amd64','model:mock','sandbox:bubblewrap']),(6,'test/legacy-console.spec.ts',['os:linux','arch:amd64','sandbox:bubblewrap','status:legacy']),(2,'test/journey-real-model.spec.ts',['os:linux','arch:amd64','model:real','sandbox:bubblewrap'])]}
tag_store=TaggedStore()
# The jiuwen line has only UT and ST cases, from a manual run on its branch.
for profile_name,created,event,branch in (('pr','2026-09-20T10:00:00Z','push','main'),('daily','2026-09-19T18:00:00Z','schedule','main'),('pr','2026-09-19T08:00:00Z','workflow_dispatch','releases/v0.3.0.beta')):
    found=[]
    for part,groups in groups_of.items():
        if branch!='main' and part=='e2e': continue
        catalog=[{'id':f'{source}::case {i+1}','source':source,'sourceHash':'0'*64,'runner':'node','tags':[f'category:{part}',*tags]} for count,source,tags in groups for i in range(count)]
        planned=sum(count for count,_,tags in groups if not any(t in tags for t in ('status:external','status:legacy','model:real','npu:required')) and 'os:linux' in tags)
        blob=io.BytesIO()
        with zipfile.ZipFile(blob,'w') as archive:
            archive.writestr(f'{part}/tagged/catalog.json',json.dumps(catalog))
            archive.writestr(f'{part}/tagged/plan.json',json.dumps({'profile':profile_name,'revision':'a'*40,'selector':f'{policy} and (category:{part})','targets':[{'os':'linux','arch':'amd64'}],'entries':[{}]*planned}))
            archive.writestr(f'{part}/tagged/summary.json',json.dumps({'status':'PASS','planned':planned,'executed':planned,'passed':planned,'failed':0,'skipped':0}))
        with zipfile.ZipFile(blob) as archive: found+=extract(archive)
    tag_store.observe(dict(id=900+6*(profile_name=='daily')+(branch!='main'),attempt=1,url=base+'/actions/runs/900',created_at=created,branch=branch,event=event),found,'main',('main','releases/v0.3.0.beta'))
tag_store.refresh_schema(lambda *args: json.dumps(tag_schema),repo)
tests['tagged']=tag_store.view('main')
runs[0]['jobs'].append(dict(name='Coverage',status='completed',conclusion='success',url=runs[0]['url']+'/job/coverage',failed_steps=[],duration_s=385))
rate=dict(success_rate=50,total=2,success=1,failure=1,cancelled=0,median_duration_s=120)
ci=dict(default_branch='main',main=rate,pull_request=rate,red_streak_main=1,failures_7d=2,runs_sampled=3,job_history_runs=2,main_timeline=runs,latest_main=dict(run=runs[0],jobs=runs[0]['jobs']),workflows=[dict(name='CI',url=runs[0]['url'],path='.github/workflows/ci.yml',state='active',all=rate,main=rate,pull_request=rate,failures_7d=2,last_run=runs[0])],job_health=[dict(name='E2E',success_rate=50,runs=2,success=1,failure=1,cancelled=0,median_duration_s=120,last=runs[0],top_failed_steps=[dict(step='Run browser journeys',count=1)]),dict(name='Coverage',success_rate=100,runs=1,success=1,failure=0,cancelled=0,median_duration_s=385,last=runs[0],top_failed_steps=[])],recent_runs=runs)
# CI lanes: 13 PR runs on the newest day (more than one column shows), manual and push
# runs on main, one nightly per evening, a queue run, hidden categories and no release.
from gsb.ci_lanes import LANES, build_lanes
def lane_run(ident,name,event,created,branch,conclusion='success',pull_requests=()):
    # Finished runs take between 3 and 28 minutes; a running one has no time yet.
    return dict(id=ident,attempt=1,name=name,workflow_id={'CI':1,'Nightly':2}.get(name,3),event=event,status='in_progress' if conclusion is None else 'completed',conclusion=conclusion,branch=branch,sha=f'{ident:040x}',created_at=created,url=f'{base}/actions/runs/{ident}',pull_requests=list(pull_requests),title=f'{name} run {ident}',duration_s=None if conclusion is None else 180+(ident*37)%1500)
outcomes=['success','failure','cancelled','timed_out',None,'success','failure','success','action_required','success','failure','success','success']
lane_runs=[lane_run(500+i,'CI','pull_request',f'2026-09-19T{16+i//4:02d}:{(i%4)*15+10:02d}:00Z','fix-timeout',c,pull_requests=[3] if i%2 else []) for i,c in enumerate(outcomes)]
lane_runs+=[lane_run(480,'CI','pull_request','2026-09-17T03:00:00Z','fix-timeout','failure'),lane_run(481,'CI','pull_request','2026-09-17T05:30:00Z','fix-timeout')]
lane_runs+=[lane_run(470,'CI','push','2026-09-18T02:00:00Z','main'),lane_run(471,'CI','workflow_dispatch','2026-09-20T01:00:00Z','main','failure')]
lane_runs+=[lane_run(400+d,'Nightly','schedule',f'2026-09-{d:02d}T18:00:00Z','main','failure' if d==16 else 'success') for d in range(12,20)]
lane_runs.append(lane_run(399,'CI','workflow_call','2026-09-19T18:00:05Z','main'))
lane_runs.append(lane_run(398,'Maintenance','push','2026-09-19T18:00:06Z','main'))
lane_runs.append(lane_run(397,'CI','merge_group','2026-09-20T05:00:00Z','gh-readonly-queue/main/pr-3'))
lane_runs.append(lane_run(396,'CI',None,'2026-09-20T05:01:00Z','main'))
lanes=build_lanes(lane_runs,default_branch='main',now=datetime(2026,9,20,12,tzinfo=timezone.utc),rules=json.loads((root/'test/fixtures/board-config.json').read_text())['workflows'],prs=[pr])
ci['lanes']=lanes
# E2E histories also use these run IDs to identify their branch line.
ci['recent_runs']=[*runs,*lane_runs]
# Release branch line: PRs, a failing manual gate, Nightly and hidden categories.
swarm_runs=[lane_run(600,'CI','pull_request','2026-09-19T09:00:00Z','swarm-fix','success',pull_requests=[8]),lane_run(601,'CI','pull_request','2026-09-20T02:00:00Z','swarm-fix','failure',pull_requests=[8]),
            lane_run(602,'CI','workflow_dispatch','2026-09-20T03:00:00Z','releases/v0.3.0.beta','failure')]
swarm_runs += [lane_run(37845266203,'Nightly','workflow_dispatch','2026-09-20T04:00:00Z','releases/v0.3.0.beta'),
               lane_run(603,'CI','workflow_call','2026-09-20T04:01:00Z','releases/v0.3.0.beta'),
               lane_run(604,'Maintenance','push','2026-09-20T04:02:00Z','releases/v0.3.0.beta')]
swarm_rate=dict(success_rate=0,total=1,success=0,failure=1,cancelled=0,median_duration_s=300)
swarm_recent=[dict(r,title=r['title'],created_at=r['created_at'],duration_s=300,actor='maintainer') for r in swarm_runs]
swarm_ci=dict(default_branch='releases/v0.3.0.beta',main=swarm_rate,pull_request=dict(swarm_rate,success_rate=50,total=2,success=1),red_streak_main=1,failures_7d=2,runs_sampled=6,job_history_runs=1,
              main_timeline=[swarm_recent[2]],latest_main=dict(run=swarm_recent[2],jobs=[dict(name='UT',conclusion='failure',url=base+'/actions/runs/602',duration_s=300,failed_steps=['Run unit tests'])]),
              workflows=[dict(name='CI',url=base+'/actions/workflows/ci.yml',path='.github/workflows/ci.yml',state='active',all=swarm_rate,main=swarm_rate,pull_request=swarm_rate,failures_7d=2,last_run=swarm_recent[2])],
              job_health=[],recent_runs=swarm_recent,
              lanes=build_lanes(swarm_runs,default_branch='releases/v0.3.0.beta',now=datetime(2026,9,20,12,tzinfo=timezone.utc),rules=json.loads((root/'test/fixtures/board-config.json').read_text())['workflows'],prs=[pr],lanes=tuple((key,'releases/v0.3.0.beta' if key=='main' else label) for key,label in LANES)))
# Its summaries predate per-file totals: groups only, the newest one from a later commit.
swarm_cov=json.loads(json.dumps(tests['coverage']))
for dataset in swarm_cov['languages'].values():
    dataset['current']['sources']=[];dataset['pull_requests']=[];dataset['history']=[]
web=dict(lines=dict(covered=50,total=100,percentage=50.0),branches=dict(covered=10,total=40,percentage=25.0),functions=dict(covered=5,total=10,percentage=50.0))
swarm_cov['languages']['node']['current']['groups'].append(dict(name='apps/web',files=12,totals=web,source_sha='c'*40,updated_at='2026-09-20T13:00:00Z',update_kind='full baseline'))
swarm_tests=dict(json.loads(json.dumps(tests)),tagged=tag_store.view('releases/v0.3.0.beta'),executed=[],coverage=swarm_cov)
legacy_run=lane_run(610,'CI','push','2026-09-18T06:00:00Z','legacy')
legacy_recent=dict(legacy_run,title=legacy_run['title'],created_at=legacy_run['created_at'],duration_s=300,actor='maintainer')
legacy_rate=dict(success_rate=100,total=1,success=1,failure=0,cancelled=0,median_duration_s=300)
legacy_ci=dict(default_branch='legacy',main=legacy_rate,pull_request=dict(legacy_rate,total=0,success=0,success_rate=None),
               red_streak_main=0,failures_7d=0,runs_sampled=1,job_history_runs=0,main_timeline=[legacy_recent],
               latest_main=dict(run=legacy_recent,jobs=[]),
               workflows=[dict(name='CI',url=base+'/actions/workflows/ci.yml',path='.github/workflows/ci.yml',state='active',
                               all=legacy_rate,main=legacy_rate,pull_request=legacy_rate,failures_7d=0,last_run=legacy_recent)],
               job_health=[],recent_runs=[legacy_recent],
               lanes=build_lanes([legacy_run,lane_run(611,'Nightly','schedule','2026-09-19T18:00:00Z','legacy')],default_branch='legacy',now=datetime(2026,9,20,12,tzinfo=timezone.utc),
                                 rules=json.loads((root/'test/fixtures/board-config.json').read_text())['workflows'],lanes=tuple((key,'legacy' if key=='main' else label) for key,label in LANES)))
legacy_tests=dict(json.loads(json.dumps(tests)),tagged=None,executed=[],coverage=dict(source=None,value=None,languages={},attempts=[]))
ops=dict(releases=dict(latest=None,count=0,items=[],tags=[],total_downloads=0,cadence_days=None,unreleased=None),branches=dict(default='main',protection=dict(enabled=True,required_reviews=1,required_checks=['E2E']),rulesets=[],items=[],count=1,stale=[]),public_advisories=[],community=dict(health_percentage=75,missing=['contributing'],files=dict(readme=True,contributing=False)),activity=dict(weeks=[dict(week=1789819200,total=10)],commits_4w=10,commits_52w=10),recent_commits=[],commits_7d=3,stale_automation=dict(workflow=None))
wrap=lambda value:dict(status='ok',data=value,notes=[],error=None)
doc.update(repo=repo,repo_url=base,config=dict(artifact_names=['ut-results','e2e-results','real-e2e-results'],pr_idle_days=14),sections={k:wrap(v) for k,v in dict(repo=dict(description='浏览器验收数据',stars=10,forks=2,language='Python',license='MIT',default_branch='main',pushed_at=time),issues=issues,prs=prs,ci=ci,tests=tests,ops=ops).items()})
doc['lines']=configured_lines(json.loads((root/'test/fixtures/board-config.json').read_text()),'main')
doc['line_sections']={'legacy':dict(ci=wrap(legacy_ci),tests=wrap(legacy_tests)),
                      'release':dict(ci=wrap(swarm_ci),tests=wrap(swarm_tests))}
doc['board']=BoardStore(cfg,persist=False).payload(doc)
doc['details']={'issue:1':dict(body=item['body'],cross_references=[]),'pr:3':dict(body=pr['body'],cross_references=[])}
export_site(root/'.e2e/site/github-status-board',doc)
# E2E run records through the real collector and Pages attachment, offline: yesterday's
# collection read histories across multiple runs and branch lines. An expired
# artifact disappears; fork and oversized artifacts have no hosted HTML.
import base64
import shutil
from datetime import timedelta
from gsb import e2e_records
records_now=datetime(2026,9,20,12,tzinfo=timezone.utc)
step=lambda title,ms,*children,error=None:dict(title=title,duration=ms,steps=list(children),**({'error':dict(message=error)} if error else {}))
journeys={'suites':[{'title':'journey-first-run.spec.ts','file':'journey-first-run.spec.ts','suites':[{'title':'J1 首次运行','file':'journey-first-run.spec.ts','specs':[
    dict(title='后台执行完成后显示运行时提示而不是伪装成用户消息，并且保留项目保存结果和完整的任务执行记录供后续查看',file='journey-first-run.spec.ts',line=18,tests=[dict(projectName='mocked',status='unexpected',results=[dict(status='failed',duration=9150,
        errors=[dict(message='\x1b[31mError: expect(locator).toBeVisible() failed\x1b[39m\n\nLocator: getByText(\'已保存\')\nExpected: visible\nTimeout: 5000ms\n    at journey-first-run.spec.ts:41:7')],
        steps=[step('1. 打开控制台',1830,step('page.goto /console',1400),step('等待项目列表',380)),
               step('2. 保存项目',7200,step('点击保存',90),step('等待已保存提示',5010,error='Timed out 5000ms waiting for getByText(\'已保存\')'),error='Error: expect(locator).toBeVisible() failed')])])]),
    dict(title='恢复会话',file='journey-first-run.spec.ts',line=52,tests=[dict(projectName='mocked',status='flaky',results=[
        dict(status='failed',duration=820,errors=[dict(message='Error: socket hang up')],steps=[step('重新连接',820)]),dict(status='passed',duration=640,steps=[step('重新连接',640)])])]),
    dict(title='导出报告',file='journey-first-run.spec.ts',line=70,tests=[dict(projectName='mocked',status='skipped',results=[dict(status='skipped',duration=0)])]),
    dict(title='模型设置可以保存',file='journey-first-run.spec.ts',line=88,tests=[dict(projectName='mocked',status='expected',results=[dict(status='passed',duration=2310,steps=[step('打开设置',400),step('保存',120)])])])]}]}]}
journey_dir = 'journey-reports/issue-77-wake-notice/后台执行完成后显示运行时提示而不是伪装成用户消息'
case_html = 'data/40072e79cd3d0cda7a79c6bad7501851b4babf54.html'
shots = ['01-跑一个后台任务并等它完成.png', '02-对话页把唤醒记成运行时提示.png', '03-提示本身说明了完成了什么.png']
journeys['suites'][0]['suites'][0]['specs'][0]['tests'][0]['results'][-1]['attachments'] = [dict(
    name='journey report', contentType='text/html', path='/home/runner/work/project/project/e2e/test-results/wake/attachments/report.html')]
def records_zip(ident):
    label=f'artifact {ident}'
    report=json.loads(json.dumps(journeys))
    specs=report['suites'][0]['suites'][0]['specs']
    case=specs[0]; test=case['tests'][0]
    if ident == 7003:
        case['line']=218  # Moving source lines must not split the case's history.
        test.update(status='expected',results=[dict(status='passed',duration=2310,steps=[step('确认运行时提示',2310)])])
    elif ident == 7004:
        case['line']=45
        test['status']='flaky'
        test['results'].append(dict(status='passed',duration=640,steps=[step('重试后完成',640)],attachments=test['results'][0]['attachments']))
    elif ident == 7005:
        case['line']=77
        test.update(status='skipped',results=[dict(status='skipped',duration=0)])
    if ident == 7001:
        # Same title in another file is a different case.
        specs.append(dict(title=case['title'],file='journey-other.spec.ts',line=18,tests=[dict(status='expected',results=[dict(status='passed',duration=800)])]))
    buf=io.BytesIO()
    html = '<!doctype html><meta charset="utf-8"><h1>后台执行完成后显示运行时提示</h1>' + ''.join(f'<img src="{name}">' for name in shots)
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jT1sAAAAASUVORK5CYII=')
    with zipfile.ZipFile(buf,'w') as archive:
        prefix='mocked-standard/e2e/'
        archive.writestr(prefix+'test-results/results.json',json.dumps(report))
        archive.writestr(prefix+'test-results/wake/attachments/report.html',html)
        archive.writestr(prefix+'playwright-report/index.html',f'<!doctype html><title>{label}</title><h1>Playwright Report fixture</h1>')
        archive.writestr(prefix+'playwright-report/'+case_html,html)
        archive.writestr(prefix+journey_dir+'/report.html',html)
        for name in shots:
            archive.writestr(prefix+journey_dir+'/'+name,png)
        # Another slice without HTML must still show cases, with a GitHub fallback.
        archive.writestr('mocked-literature/e2e/test-results/results.json',json.dumps({'suites':[{'specs':[
            dict(title='文献查询完成后显示引用来源',tests=[dict(status='expected',results=[dict(status='passed',steps=[step('查询文献',120)])])]),
            dict(title=case['title'],file=case['file'],tests=[dict(status='expected',results=[dict(status='passed',duration=100)])])]}]}))
    return buf.getvalue()

def listed(ident,run_id,expires,fork=False):
    return dict(id=ident,name='e2e-results',size_in_bytes=4096,expired=False,created_at=f'2026-09-19T{16 if ident == 7003 else ident-6990:02d}:00:00Z',expires_at=expires,
                workflow_run=dict(id=run_id,repository_id=7,head_repository_id=8 if fork else 7,head_branch='main',head_sha='a'*40))
class RecordSource:
    artifacts=[listed(7001,10,'2026-10-04T08:00:00Z'),listed(7002,11,'2026-09-20T06:00:00Z'),listed(7003,12,'2026-10-04T09:00:00Z',fork=True),
               listed(7004,470,'2026-10-04T10:00:00Z'),listed(7005,471,'2026-10-04T11:00:00Z'),
               listed(7006,602,'2026-10-04T12:00:00Z'),dict(listed(7007,480,'2026-10-04T13:00:00Z'),size_in_bytes=e2e_records.ARTIFACT_BYTES+1)]
    def get(self,path,params=None): return dict(artifacts=self.artifacts)
    def download_artifact(self,repo,ident,*,max_bytes): return records_zip(ident)
checkout=root/'.e2e/records-checkout'
shutil.rmtree(checkout,ignore_errors=True)
collected=e2e_records.refresh(RecordSource(),repo,checkout,doc,records_now-timedelta(days=1),bundle=4242,downloads=6)
site=root/'.e2e/site/github-status-board'
for path,content in collected.files.items():
    target=site/path.removeprefix('site/'); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(content)
collected.write_bundle(checkout/'bundle')
bundle=io.BytesIO()
with zipfile.ZipFile(bundle,'w') as archive:
    for file in (checkout/'bundle').rglob('*'):
        if file.is_file(): archive.write(file,file.relative_to(checkout/'bundle').as_posix())
class BoardRuns:
    def get(self,path,params=None):
        return dict(artifacts=[dict(id=1,name='e2e-html',expired=False)]) if path.endswith('/artifacts') else dict(path='.github/workflows/collect.yml',head_branch='main',status='completed')
    def download_artifact(self,repo,ident,*,max_bytes): return bundle.getvalue()
e2e_records.attach(BoardRuns(),'ScienceDiscovery/github-status-board',site,records_now,sleep=lambda seconds:None)
shutil.rmtree(checkout,ignore_errors=True)
empty=json.loads(json.dumps(doc));empty['quality']['runs']=[];empty['releases']=[];empty['issues']=None;empty['prs']=None;empty['notices']=[{'message':'GitHub 数据不可读取，结果未知。'}]
empty['sections']={k:dict(status='error',data=None,notes=[],error=dict(kind='error',message='结果未知')) for k in empty['sections']}
empty['board']['items']=[];empty['details']={};empty['line_sections']={}
export_site(root/'.e2e/site/empty',empty)

# GitCode sync page: a bot response with failures, divergence, a hostile title and
# credential-shaped error text, passed through the same public_document() as publish.py.
from gsb import gitcode_sync
FAKE_TOKEN = 'gitcode-e2e-fake-token-0123456789'
gh_pr = lambda n: f'https://github.com/openJiuwen-ai/sciencediscovery/pull/{n}'
mr = lambda n: f'https://gitcode.com/openJiuwen/sciencediscovery/merge_requests/{n}'
def sync_record(ident, minute, pr, action, status, summary, *, mr_number=None, code=None, error=None, sha='a' * 40, title='feat(reader): stream long PDFs'):
    return dict(id=ident, time=f'2026-10-07T06:{minute:02d}:00.000Z', pr=pr, pr_url=gh_pr(pr), title=title, action=action, head_sha=sha,
                mr=mr_number, mr_url=mr(mr_number) if mr_number else None, status=status, summary=summary, error_code=code, error=error)
bot_payload = dict(ok=True, enabled=True, source='openJiuwen-ai/sciencediscovery', target='openJiuwen/sciencediscovery', check_name='CodeCheck (GitCode)', records=[
    sync_record('s1', 1, 119, 'opened', 'success', '已推送原始 head 1111111，已创建 GitCode MR !9', mr_number=9, sha='1' * 40),
    sync_record('s2', 5, 119, 'merged', 'success', '已在 GitHub 合并；已关闭 GitCode MR !9（未调用合并接口）', mr_number=9, sha='1' * 40),
    sync_record('s3', 10, 120, 'opened', 'success', '已推送原始 head aaaaaaa，已创建 GitCode MR !11', mr_number=11),
    sync_record('s4', 20, 121, 'synchronize', 'error', '同步失败（第 1 次，已停止重试）', code='permission_denied', sha='b' * 40, title='<script>alert(1)</script> fix sync',
                error=f'GitCode receive-pack returned HTTP 403: credentials rejected token={FAKE_TOKEN} via https://sync-bot:{FAKE_TOKEN}@gitcode.com/x.git'),
    sync_record('s5', 30, 122, 'synchronize', 'error', '已推送原始 head ccccccc，已更新 GitCode MR !12；两边历史不一致，GitCode diff 可能包含本 PR 以外的提交', mr_number=12,
                code='history_diverged', sha='c' * 40, error='GitCode MR !12 lists 9 commits but GitHub PR #122 has 3; the GitCode diff may include commits outside this PR'),
    sync_record('s6', 40, 120, 'codecheck', 'error', 'CodeCheck 未通过（ci-failed），已写 GitHub Check', mr_number=11, code='codecheck_failed', error='GitCode labelled the merge request ci-failed'),
], pulls=[
    dict(pr=122, pr_url=gh_pr(122), title='refactor: split runner', base='main', head_sha='c' * 40, mr=12, mr_url=mr(12), sync_status='diverged', check='pending',
         error_code='history_diverged', error='GitCode MR !12 lists 9 commits but GitHub PR #122 has 3; the GitCode diff may include commits outside this PR', updated_at='2026-10-07T06:30:00Z', pending=True),
    dict(pr=121, pr_url=gh_pr(121), title='<script>alert(1)</script> fix sync', base='main', head_sha='b' * 40, mr=None, mr_url=None, sync_status='failed', check='failure',
         error_code='permission_denied', error=f'Authorization: Bearer {FAKE_TOKEN} rejected', updated_at='2026-10-07T06:20:00Z', pending=False),
    dict(pr=120, pr_url=gh_pr(120), title='feat(reader): stream long PDFs', base='main', head_sha='a' * 40, mr=11, mr_url=mr(11), sync_status='synced', check='failure',
         error_code=None, error=None, updated_at='2026-10-07T06:10:00Z', pending=False),
    dict(pr=119, pr_url=gh_pr(119), title='docs: tidy', base='main', head_sha='1' * 40, mr=9, mr_url=mr(9), sync_status='merged', check='cancelled',
         error_code=None, error=None, updated_at='2026-10-07T06:05:00Z', pending=False),
])
sync_doc = gitcode_sync.public_document(bot_payload, None, '2026-10-07T07:00:00Z')
assert FAKE_TOKEN not in gitcode_sync.encode(sync_doc)
(site / 'data/gitcode-sync.json').write_text(gitcode_sync.encode(sync_doc), encoding='utf-8')
