"""Read-only cgroup/VM sampling; only explicitly labelled owned lab containers."""
import argparse,json,os,re,subprocess,time
from pathlib import Path
def processes(cg):
    result=[]
    try: pids=(cg/'cgroup.procs').read_text().splitlines()
    except OSError: return result
    for pid in pids:
        try:
            raw=Path('/proc',pid,'status').read_text(); fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in raw.splitlines() if ':' in line}
            try: descriptors=len(list(Path('/proc',pid,'fd').iterdir()))
            except OSError: descriptors=None
            result.append({'pid':int(pid),'name':fields.get('Name'),'threads':int(fields.get('Threads',0)),'rss_kib':int(fields.get('VmRSS','0 kB').split()[0]),'rss_anon_kib':int(fields.get('RssAnon','0 kB').split()[0]),'file_descriptors':descriptors,'descriptor_table_capacity':int(fields.get('FDSize',0))})
        except (OSError,ValueError): pass
    return result
def number(path):
    try:return int(path.read_text().strip())
    except (OSError,ValueError):return None

def main(args):
    root=Path(args.runtime).resolve(strict=True)
    if os.name != 'posix' or re.fullmatch(r'[a-f0-9]{32}', args.lab_nonce or '') is None:
        raise ValueError('Linux and explicit owned nonce required')
    marker=json.loads((root/'lab-marker.json').read_text());nonce=marker['lab_nonce']
    if root.name != nonce or nonce != args.lab_nonce or marker.get('synthetic') is not True or marker.get('kind') != 'lanshare-capacity-linux-lab':
        raise ValueError('Owned Linux lab marker required')
    if os.cpu_count()!=2:
        raise ValueError('This evidence profile requires the entire VM to have 2 CPUs')
    records=json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q','--filter','label=io.lanshare.capacity-lab.nonce='+nonce],text=True).split()],text=True))
    containers=[]
    for record in records:
        assert record['Config']['Labels']['io.lanshare.capacity-lab.nonce']==nonce
        pid=record['State']['Pid'];group=Path('/proc')/str(pid)/'cgroup';cg=Path('/sys/fs/cgroup')/group.read_text().split('0::',1)[1].strip().lstrip('/')
        containers.append((record['Config']['Labels'].get('com.docker.compose.service',record['Config']['Labels'].get('io.lanshare.capacity-lab.role','observer')),record['Id'],cg))
    with (root/'artifacts/recorder-host-observations.jsonl').open('x') as out:
        deadline=time.monotonic()+args.seconds
        while time.monotonic()<deadline and not (root/'artifacts/stop-recorder-monitor').exists():
            mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
            values=[]
            for role,cid,cg in containers:
                values.append({'role':role,'id':cid,'pids':number(cg/'pids.current'),'memory':number(cg/'memory.current'),
                    'processes':len((cg/'cgroup.procs').read_text().splitlines()) if (cg/'cgroup.procs').exists() else None,'rss_processes':processes(cg),
                    'cpu_stat':(cg/'cpu.stat').read_text() if (cg/'cpu.stat').exists() else None,'memory_events':(cg/'memory.events').read_text() if (cg/'memory.events').exists() else None})
            out.write(json.dumps({'epoch':time.time(),'mem_available':mem['MemAvailable'],'swap_total':mem['SwapTotal'],'cpu_stat':Path('/proc/stat').read_text().splitlines()[0],'containers':values})+'\n');out.flush();time.sleep(1)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',required=True)
    parser.add_argument('--lab-nonce',required=True)
    parser.add_argument('--seconds',type=int,default=1200)
    args=parser.parse_args()
    if not 60 <= args.seconds <= 7200:
        raise SystemExit('Bounded 60..7200 second sampling required')
    main(args)
