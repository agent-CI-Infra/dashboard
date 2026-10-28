from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gsb.history import History, encode
from gsb.sync import Sync, BudgetExhausted, stamp, date
from gsb.project import run_details, slim_run
from gsb.config import Config
from gsb.github import GitHubError
from gsb.incremental_project import build_snapshot, _executed, _persist_coverage_summaries, _daily_coverage_history, _coverage_artifact_eligible
from publish import publish_batch
from test_reports import archive

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
REPO = 'example/source'


def item(number, state='open'):
    return dict(number=number, title=f'Issue {number}', state=state, body='public body', user={'login':'person'},
                created_at='2026-01-01T00:00:00Z', updated_at='2026-09-21T00:00:00Z',
                closed_at=None if state=='open' else '2026-09-21T00:00:00Z', html_url=f'https://github.com/{REPO}/issues/{number}')


def run(number=1, attempt=1):
    return dict(id=number, run_attempt=attempt, name='E2E', workflow_id=7, event='push', status='completed', conclusion='success',
                head_branch='main', head_sha='a'*40, html_url=f'https://github.com/{REPO}/actions/runs/{number}',
                created_at=stamp(NOW-timedelta(seconds=number)), updated_at=stamp(NOW), run_started_at=stamp(NOW-timedelta(seconds=number)))


class Source:
    token = 'fake-source-secret'
    def __init__(self, issues=0, prs=0, runs=0):
        self.issues = [item(i) for i in range(1, issues+1)]
        self.prs = [dict(item(i), head={'sha':'a'*40}, base={}, merged_at=None) for i in range(1, prs+1)]
        self.runs = [run(i) for i in range(1, runs+1)]
        self.calls = []
    def get(self, path, params=None):
        p = params or {}; self.calls.append((path, deepcopy(p)))
        if path == '/repos/'+REPO:
            return dict(created_at='2026-01-01T00:00:00Z',html_url='https://github.com/'+REPO,default_branch='main',private=False)
        if path.endswith('/releases'):return []
        if path.endswith('/issues'):rows = self.issues
        elif path.endswith('/pulls'):rows = self.prs
        elif path.endswith('/actions/runs'):
            lo,hi=p['created'].split('..')
            rows=[r for r in self.runs if lo<=r['created_at']<=hi]
            start=(p['page']-1)*100
            return {'total_count':len(rows),'workflow_runs':deepcopy(rows[start:min(start+100,1000)])}
        elif path.endswith('/jobs'):return {'jobs':[]}
        elif path.endswith('/artifacts'):return {'artifacts':[]}
        else:raise AssertionError(path)
        start=(p['page']-1)*100
        return deepcopy(rows[start:start+100])
    def graphql(self, query, variables):
        return {'repository': {'pullRequest': {'number':variables['number'], 'reviewDecision':'APPROVED', 'reviews':{}, 'commits':{}}}}


class SyncTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def persist(self, sync):
        for name,content in sync.files().items():
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
    def test_backfill_resumes_beyond_old_limits_and_reopen_does_not_double_count(self):
        gh=Source(issues=625,prs=250)
        first=Sync(gh,self.root,REPO,now=NOW,requests=4).collect()
        self.assertFalse(first.progress()['complete']);self.persist(first)
        for i in range(4):
            sync=Sync(gh,self.root,REPO,now=NOW+timedelta(minutes=i+1)).collect();self.persist(sync)
        self.assertEqual(sync.history.manifest['totals']['issues'],625)
        self.assertEqual(sync.history.manifest['totals']['prs'],250)
        self.assertTrue(sync.progress()['complete'])
        sync.save_item('issues',item(1,'closed'))
        self.assertEqual(sync.history.aggregate['issues:state:closed'],1)
        sync.save_item('issues',item(1,'open'));sync.save_item('issues',item(1,'open'))
        self.assertEqual(sync.history.aggregate['issues:total'],625)
        self.assertEqual(sync.history.aggregate['issues:state:open'],625)
        self.assertNotIn('issues:state:closed',sync.history.aggregate)
    def test_filtered_run_api_splits_1000_limit_without_losing_history(self):
        gh=Source(runs=1205)
        sync=Sync(gh,self.root,REPO,now=NOW,requests=500);sync.meta=gh.get('/repos/'+REPO)
        for _ in range(30):
            sync.runs('backfill',pages=4)
            self.persist(sync)
            sync=Sync(gh,self.root,REPO,now=NOW,requests=500);sync.meta=gh.get('/repos/'+REPO)
            if sync.state['backfill']['runs']['done']:break
        self.assertTrue(sync.state['backfill']['runs']['done'])
        self.assertEqual(sync.history.manifest['totals']['runs'],1205)
        self.assertGreater(len({p['created'] for path,p in gh.calls if path.endswith('/actions/runs')}),1)
    def test_permission_error_does_not_advance_cursor_or_delete_known_items(self):
        sync=Sync(Source(issues=1),self.root,REPO,now=NOW).collect();self.persist(sync)
        old=deepcopy(sync.state['backfill']['issues'])
        class Failed(Source):
            def get(self,path,params=None):
                if path.endswith('/issues'):raise GitHubError('private detail',kind='forbidden',status=403)
                return super().get(path,params)
        failed=Sync(Failed(),self.root,REPO,now=NOW+timedelta(hours=2)).collect()
        self.assertEqual(failed.state['backfill']['issues'],old)
        self.assertEqual(failed.history.manifest['totals']['issues'],1)
        self.assertEqual(failed.state['errors']['incremental:issues']['kind'],'forbidden')
        self.assertNotIn('private detail',encode(failed.state))
    def test_body_update_changes_revision_and_only_affected_shards(self):
        h=History(self.root)
        for n in (1,201):h.put('issues',dict(item(n),url=item(n)['html_url']))
        for path,content in h.files().items():
            p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
        h=History(self.root);old=deepcopy(h.manifest)
        row=h.get('issues',1);row['body']='changed';h.put('issues',row)
        self.assertNotEqual(old['shards']['issues/000000000000']['revision'],h.manifest['shards']['issues/000000000000']['revision'])
        self.assertFalse(any('000000000002' in p for p in h.files()))
    def test_sparse_run_indexes_are_bundled_without_losing_record_paths(self):
        history=History(self.root)
        for n in (1000001,2000001,9000001):history.put('runs',slim_run(run(n),{}))
        files=history.files()
        self.assertEqual(len(history.manifest['shards']),3)
        self.assertEqual(len(history.manifest['catalogs']),1)
        catalog=json.loads(next(v for k,v in files.items() if '/catalog/' in k))
        self.assertEqual(len(catalog),3)
        self.assertEqual(len({row['record'] for row in catalog}),3)

    def test_existing_metrics_migrate_without_cases_or_false_full_history(self):
        old=slim_run(run(),{});old['tests']=[dict(artifact_id=1,name='e2e',layer='e2e',counts={'tests':2},cases=[{'name':'DO-NOT-PUBLISH'}])]
        path=self.root/'site/data/snapshot.json';path.parent.mkdir(parents=True);path.write_text(encode({'repository':{'name':REPO},'quality':{'runs':[old]}}))
        sync=Sync(Source(),self.root,REPO,now=NOW)
        self.assertFalse(sync.progress()['complete'])
        self.assertEqual(sync.history.get('runs','1-1')['tests'][0]['counts']['tests'],2)
        self.assertNotIn('DO-NOT-PUBLISH',str(sync.files()))
    def test_same_data_next_poll_does_not_change_snapshot_timestamp(self):
        sync=Sync(Source(issues=1),self.root,REPO,now=NOW).collect()
        with patch('gsb.incremental_project.public_ops',return_value={}),patch('gsb.incremental_project.public_tests',return_value={}):
            doc=build_snapshot(sync)
        self.persist(sync)
        path=self.root/'site/data/snapshot.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(encode(doc))
        sync=Sync(Source(issues=1),self.root,REPO,now=NOW+timedelta(minutes=10)).collect()
        with patch('gsb.incremental_project.public_ops',side_effect=AssertionError('must use cache')):
            self.assertEqual(build_snapshot(sync),doc)

    def test_same_day_new_run_refreshes_tests_but_not_daily_ops(self):
        sync=Sync(Source(runs=1),self.root,REPO,now=NOW).collect()
        with patch('gsb.incremental_project.public_ops',return_value={'marker':'old-ops'}), \
             patch('gsb.incremental_project.public_tests',return_value={'marker':'old-tests'}):
            doc=build_snapshot(sync)
        self.persist(sync)
        path=self.root/'site/data/snapshot.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(encode(doc))

        sync=Sync(Source(runs=2),self.root,REPO,now=NOW+timedelta(minutes=10)).collect()
        with patch('gsb.incremental_project.public_ops',side_effect=AssertionError('daily ops must stay cached')), \
             patch('gsb.incremental_project.public_tests',return_value={'marker':'new-tests'}) as refreshed:
            updated=build_snapshot(sync)

        refreshed.assert_called_once()
        self.assertEqual(updated['sections']['ops']['data']['marker'],'old-ops')
        self.assertEqual(updated['sections']['tests']['data']['marker'],'new-tests')

    def test_coverage_summaries_persist_on_runs_and_keep_latest_result_per_beijing_day(self):
        history=History(self.root)
        for number in (1,2,3,4,5):
            raw=run(number)
            if number == 3:
                # The UT/ST coverage can be complete even when a later E2E
                # job makes the overall Nightly run fail.
                raw.update(name='Nightly', event='schedule', conclusion='failure')
            if number == 4:
                raw.update(conclusion='failure')
            if number == 5:
                raw.update(name='Nightly', event='schedule', conclusion='failure')
            raw['run_started_at']=stamp(NOW+timedelta(hours=number-1))
            raw['created_at']=raw['run_started_at'];raw['updated_at']=stamp(NOW+timedelta(hours=number))
            record=slim_run(raw,{})
            if number in (3,5):
                record['jobs']=[{'name':f'Gate / {name}',
                                 'conclusion':'failure' if number == 5 and name == 'ST' else 'success'}
                                for name in ('UT','ST','Coverage')]
                record['jobs'].append({'name':'Gate / E2E (real, daily only)','conclusion':'failure'})
            history.put('runs',record)
        metric=lambda value:{'lines':{'covered':value,'total':100,'percentage':value}}
        entries=[
            dict(artifact='node-coverage-summary-push-one',run_id=1,created_at=stamp(NOW+timedelta(minutes=30)),sha='1'*40,kind='main full',totals=metric(80)),
            dict(artifact='node-coverage-summary-push-two',run_id=2,created_at=stamp(NOW+timedelta(hours=1,minutes=30)),sha='2'*40,kind='main full',totals=metric(82)),
            dict(artifact='node-coverage-summary-nightly-three',run_id=3,created_at=stamp(NOW+timedelta(hours=12,minutes=30)),sha='3'*40,kind='nightly',totals=metric(83)),
            dict(artifact='node-coverage-summary-push-four',run_id=4,created_at=stamp(NOW+timedelta(hours=13,minutes=30)),sha='4'*40,kind='main full',totals=metric(84)),
            dict(artifact='node-coverage-summary-nightly-five',run_id=5,created_at=stamp(NOW+timedelta(hours=14,minutes=30)),sha='5'*40,kind='nightly',totals=metric(85)),
        ]
        coverage={'languages':{'node':{'history':entries}}}

        lines=[{'key':'main','ref':'main','default':True}]
        _persist_coverage_summaries(history,coverage,lines[0],lines)
        daily=_daily_coverage_history(history,lines[0],lines)['node']

        self.assertEqual(len(daily),2)
        self.assertEqual(daily[0]['day'],'2026-09-22')
        self.assertEqual(daily[0]['totals']['lines']['percentage'],82)
        self.assertEqual(daily[1]['day'],'2026-09-23')
        self.assertEqual(daily[1]['kind'],'nightly')
        self.assertEqual(history.get('runs','2-1')['coverage_summaries'][0]['artifact'],entries[1]['artifact'])
        self.assertFalse(history.get('runs','4-1').get('coverage_summaries'))
        self.assertFalse(history.get('runs','5-1').get('coverage_summaries'))
        for entry in entries:
            self.assertEqual(_coverage_artifact_eligible({'name':entry['artifact'],'run_id':entry['run_id']},
                                                         history.get('runs',f"{entry['run_id']}-1")),
                             entry['run_id'] not in (4,5))

        for name,content in history.files().items():
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
        refreshed=run(2);refreshed['updated_at']=stamp(NOW+timedelta(days=2))
        sync=Sync(Source(),self.root,REPO,now=NOW+timedelta(days=2))
        sync.save_run(refreshed)
        self.assertEqual(sync.history.get('runs','2-1')['coverage_summaries'][0]['artifact'],entries[1]['artifact'])


    def test_run_first_seen_running_is_read_again_when_it_completes(self):
        class Attempts(Source):
            def get(self, path, params=None):
                if '/attempts/' in path:
                    return deepcopy(next(r for r in self.runs if f"/runs/{r['id']}/" in path))
                return super().get(path, params)
        gh = Attempts(runs=1)
        gh.runs[0].update(status='in_progress', conclusion=None)
        report = lambda _gh, _cfg, row, **_kw: row.update(reports_status='available', tests=[]) or row  # noqa: E731
        with patch('gsb.sync.run_details', side_effect=report):
            sync = Sync(gh, self.root, REPO, now=NOW).collect()
        job = sync.state['pending']['run:1-1']
        # One layer's report does not make a running workflow complete.
        self.assertEqual(job['due'], stamp(NOW + timedelta(seconds=120)))
        self.persist(sync)
        gh.runs[0].update(status='completed', conclusion='success')
        sync = Sync(gh, self.root, REPO, now=NOW + timedelta(seconds=30))
        sync.meta = gh.get('/repos/' + REPO)
        sync.state['pending']['run:1-1']['due'] = stamp(NOW + timedelta(days=1))
        sync.save_run(deepcopy(gh.runs[0]))
        self.assertEqual(sync.state['pending']['run:1-1']['due'], stamp(NOW + timedelta(seconds=30)))


class MetricTests(unittest.TestCase):
    def test_latest_real_e2e_report_keeps_scores_for_test_page(self):
        score = dict(case='TC-E2E-01', metrics=[dict(label='Judge total', value=86.25, unit='score100')])
        row = dict(id=1, sha='a'*40, attempt=1, branch='main', updated_at='2026-09-20T12:00:00Z',
                   tests=[dict(name='real-e2e-results', layer='e2e', counts=None,
                               url='https://github.com/example/source/actions/runs/1/artifacts/7', scores=[score])])
        report = _executed([row])[0]
        self.assertEqual(report['scores'], [score])
        self.assertEqual(report['url'], row['tests'][0]['url'])

    def test_parsed_artifact_not_downloaded_again_and_expiration_preserves_counts(self):
        artifact=dict(id=9,name='e2e-results',created_at='2026-09-22T12:00:00Z',expired=False,digest='sha256:abc')
        class GH:
            downloads=0
            def paginate(self,path,**kw):return [] if path.endswith('/jobs') else [artifact]
            def download_artifact(self,*args,**kw):
                self.downloads+=1
                return archive({'dashboard-summary.json':json.dumps(dict(tests=2,passed=1,failed=1,skipped=0,flaky=0))})
        gh=GH();r=slim_run(run(),{});cfg=Config(repo=REPO)
        run_details(gh,cfg,r,cache={},parser_version=1)
        self.assertEqual(gh.downloads,1)
        run_details(gh,cfg,r,cache=deepcopy(r),parser_version=1)
        self.assertEqual(gh.downloads,1)
        artifact['expired']=True
        run_details(gh,cfg,r,cache=deepcopy(r),parser_version=2)
        self.assertEqual(r['tests'][0]['counts']['tests'],2)
        self.assertEqual(gh.downloads,1)
    def test_historical_attempt_does_not_consume_new_attempt_artifact(self):
        class GH:
            def paginate(self,path,**kw):return [] if path.endswith('/jobs') else [dict(id=9,name='e2e-results',created_at='2026-09-22T12:00:00Z')]
            def download_artifact(self,*a,**kw):raise AssertionError('future attempt')
        r=slim_run(run(),{});r['next_started_at']='2026-09-22T11:59:00Z'
        run_details(GH(),Config(repo=REPO),r,cache={},parser_version=1)
        self.assertEqual(r['tests'],[])


class AtomicTests(unittest.TestCase):
    def test_stale_checkout_rejected_before_any_write(self):
        class GH:
            token='fake-write-secret'
            def get(self,path):return {'object':{'sha':'b'*40}} if '/git/' in path else {'private':False}
            def _request(self,*a,**kw):raise AssertionError('must not write')
        with self.assertRaises(ValueError):publish_batch(GH(),'example/board','main','a'*40,{'.sync/state.json':'{}'})
    def test_one_commit_contains_both_progress_and_site_with_no_force(self):
        writes=[]
        class GH:
            token='fake-write-secret'
            def get(self,path):
                if '/commits/' in path:return {'tree':{'sha':'tree'}}
                return {'object':{'sha':'a'*40}} if '/git/' in path else {'private':False}
            def _url(self,path,_):return path
            def _request(self,method,path,body):
                writes.append((method,path,body));return ({'sha':'new'},None,200)
        large='x'*(300*1024)
        publish_batch(GH(),'example/board','main','a'*40,{'.sync/state.json':'{}','site/data/snapshot.json':'{}','site/data/history/manifest.json':large})
        tree=next(b for _,p,b in writes if p.endswith('/trees'))
        self.assertEqual({r['path'] for r in tree['tree']},{'.sync/state.json','site/data/snapshot.json','site/data/history/manifest.json'})
        # Small files ride in the tree request; only the large one costs a blob request.
        self.assertEqual([p for _,p,_ in writes if p.endswith('/blobs')],['/repos/example/board/git/blobs'])
        self.assertEqual({r['path']:r.get('content') for r in tree['tree'] if 'content' in r},{'.sync/state.json':'{}','site/data/snapshot.json':'{}'})
        self.assertEqual(len(writes),4)
        self.assertEqual(writes[-1][2],{'sha':'new','force':False})
