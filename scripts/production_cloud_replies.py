"""Read current production reply profiles through pinned SFS SSH and mTLS.

Credentials and raw profile identifiers stay in memory inside the cluster.
The only exec program is our fixed, bounded /work/runtime-logs reader.
"""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REMOTE = r'''
import json,os,re,ssl,sys,urllib.request,urllib.parse

class ReplyNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def collect_runtime_replies(payload):
    credentials=payload['credentials']
    ctx=ssl.create_default_context(cadata=credentials['ca'])
    fds=[]
    try:
        for name in ('cert','key'):
            fd=os.memfd_create('production-read-'+name,os.MFD_CLOEXEC)
            os.write(fd,credentials[name].encode());fds.append(fd)
        ctx.load_cert_chain('/proc/self/fd/'+str(fds[0]),'/proc/self/fd/'+str(fds[1]))
    finally:
        for fd in fds:os.close(fd)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),ReplyNoRedirect(),
                                     urllib.request.HTTPSHandler(context=ctx))
    req=urllib.request.Request('https://192.168.7.18:5443/api/v1/namespaces/adx-jiuwen/pods?labelSelector=app%3Dadx-node&limit=100')
    with opener.open(req,timeout=30) as response:
        raw=response.read(4_000_001)
    if len(raw)>4_000_000:raise ValueError('pod inventory exceeds bound')
    inventory=json.loads(raw)
    if inventory.get('metadata',{}).get('continue'):raise ValueError('incomplete node inventory')
    nodes=sorted(item['metadata']['name'] for item in inventory['items']
                 if re.fullmatch(r'adx-node-[0-9]+',item['metadata']['name']))
    if not nodes or len(nodes)>32:raise ValueError('invalid runtime node inventory')
    environments=payload['environments']
    node_source=payload['node_source']+'\nenvironments='+repr(environments)+'\nprint(json.dumps(read_node_replies(environments)))\n'
    events=[];matched=set();coverage={'expected_nodes':len(nodes),'read_nodes':0,
                                    'expected_bindings':len(environments),'files':0,'bytes':0,'read_bytes':0}
    for node in nodes:
        result=audit_exec(ctx,node,node_source)
        events.extend(result['events']);matched.update(result['matched']);coverage['read_nodes']+=1
        for key in ('files','bytes','read_bytes'):coverage[key]+=result['coverage'][key]
        if len(events)>REPLY_MAX_EVENTS:raise ValueError('reply event count exceeds bound')
    coverage['matched_environments']=len(matched)
    return merge_reply_events(payload.get('previous'),events,payload['salt'],coverage=coverage)

try:
    output={'ok':True,'data':collect_runtime_replies(json.load(sys.stdin))}
except Exception:
    output={'ok':False}
print(json.dumps(output,separators=(',',':')))
'''


def collect_cloud_replies(token, previous, *, operation, binding_reader, credentials=None):
    credentials = credentials or json.loads(os.environ['PRODUCTION_KUBE_READ_CREDENTIALS'])
    if (not isinstance(credentials, dict) or set(credentials) != {'ca', 'cert', 'key'}
            or any(not isinstance(v, str) or not 0 < len(v) < 65536 for v in credentials.values())):
        raise ValueError('invalid production read credentials')
    environments = {}
    for item in binding_reader(token, operation=operation):
        binding = item.get('binding') or {}
        scope = binding.get('scope') or {}
        if (scope.get('tenant') != 'jiuwen' or scope.get('template') != 'digitalmate'
                or (scope.get('template_ref') or {}).get('alias') != 'production'):
            continue
        environment = (binding.get('current') or {}).get('environment_id')
        identity = scope.get('binding_id')
        if not isinstance(environment, str) or not isinstance(identity, str) or not identity:
            raise ValueError('incomplete production binding')
        if environment in environments and environments[environment] != identity:
            raise ValueError('ambiguous production environment')
        environments[environment] = identity
    if not environments:
        raise ValueError('no production environments')
    metrics_source = (HERE / 'production_reply_metrics.py').read_text(encoding='utf-8')
    transport_source = (HERE / 'production_cluster_exec.py').read_text(encoding='utf-8')
    result = operation({'credentials': credentials, 'environments': environments,
                        'salt': token, 'previous': previous, 'node_source': metrics_source},
                       remote_source=metrics_source + '\n' + transport_source + '\n' + REMOTE)
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise RuntimeError('Production runtime reply collection failed; previous snapshot retained')
    return result['data']
