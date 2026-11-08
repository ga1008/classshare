"""Precisely coordinate a 15s apply lock and one explicit app restart."""
import argparse,json,os,re,shlex,subprocess,time
from pathlib import Path
def main(a):
    work=Path(a.work).resolve();runtime=a.runtime
    nonce=a.lab_nonce
    if re.fullmatch(r'[a-f0-9]{32}',nonce or '') is None or not re.fullmatch(r'/[A-Za-z0-9_./-]+',runtime) or Path(runtime).name != nonce or '..' in Path(runtime).parts:
        raise ValueError('Explicit nonce-owned absolute Linux runtime required')
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',a.ssh_host or '') is None:
        raise ValueError('Explicit SSH config alias required')
    config=Path(a.ssh_config).resolve(strict=True)
    if not config.is_file():raise ValueError('Pinned owned SSH configuration required')
    handoff=json.loads((work/'client-handoff/handoff.json').read_text())
    if handoff.get('synthetic') is not True or handoff.get('lab_nonce')!=nonce or handoff.get('release_id')!='capacity-linux-'+nonce:
        raise ValueError('Fault coordinator requires the matching synthetic client handoff')
    if any((work/name).exists() for name in ('fault-held','fault-acked','fault-restarted','fault-injection.json')):
        raise ValueError('Preserve an existing fault injection run')
    name='lanshare-capacity-'+nonce+'-app-1'
    ssh=['ssh','-F',str(config),a.ssh_host]
    def command(text,timeout=30):return subprocess.check_output([*ssh,text],timeout=timeout,text=True).strip()
    marker=json.loads(command('cat '+shlex.quote(runtime+'/lab-marker.json')))
    if marker.get('kind') != 'lanshare-capacity-linux-lab' or marker.get('synthetic') is not True or marker.get('lab_nonce') != nonce or marker.get('release_id') != 'capacity-linux-'+nonce or a.nginx_ip != marker.get('network',{}).get('addresses',{}).get('nginx'):
        raise ValueError('Remote marker differs from the explicit owned lab')
    deadline=time.monotonic()+1800
    while not (work/'fault-ready').exists() and time.monotonic()<deadline:time.sleep(.2)
    assert (work/'fault-ready').exists()
    old=json.loads(command('docker inspect '+name))[0]
    assert old['Config']['Labels']['io.lanshare.capacity-lab.nonce']==nonce
    log=(work/'fault-holder.log').open('w')
    hold=subprocess.Popen([*ssh,f'cd {runtime} && docker compose -f compose.json run --rm --no-deps --entrypoint python controller /app/tools/recorder_capacity_facts.py hold --seconds 15'],stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    poll=f"python3 -c \"from pathlib import Path;import time;p=Path('{runtime}/artifacts/apply-held');end=time.monotonic()+10;exec('while not p.exists() and time.monotonic()<end: time.sleep(.05)');assert p.exists();print(p.read_text())\""
    held=float(command(poll));held_observed_monotonic=time.monotonic();held_observed_client_epoch=time.time()
    (work/'fault-held').write_text(str(held))
    deadline=time.monotonic()+30
    while not (work/'fault-acked').exists() and time.monotonic()<deadline:time.sleep(.05)
    assert (work/'fault-acked').exists()
    # Stop/start only this explicitly owned app container. PG/queue volumes and
    # all other roles remain running. No general restart or production target.
    # The held timestamp belongs to the VM. Compare local monotonic clocks for
    # the restart guard; Windows and VM wall clocks need not be synchronized.
    restart_began=time.time();assert time.monotonic()-held_observed_monotonic<13,'Admission took too long to retain a blocked queue for restart'
    command('docker restart --time 5 '+name,timeout=30)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        try:
            status=command(f"curl -ks --max-time 2 -o /dev/null -w '%{{http_code}}' https://{a.nginx_ip}/api/internal/live",timeout=4)
            if status=='200':break
        except subprocess.SubprocessError:pass
        time.sleep(.2)
    assert status=='200'
    after=json.loads(command('docker inspect '+name))[0]
    assert old['Id']==after['Id'] and old['Image']==after['Image'] and old['State']['Pid']!=after['State']['Pid']
    (work/'fault-restarted').write_text(str(time.time()))
    assert hold.wait(timeout=40)==0;log.close()
    release=float(command('cat '+runtime+'/artifacts/apply-released'))
    report={'status':'injected','lab_nonce':nonce,'hold_acquired_server_epoch':held,'hold_released_server_epoch':release,'hold_seconds':release-held,
            'hold_observed_client_epoch':held_observed_client_epoch,
            'timestamp_domains':{'hold_acquired_server_epoch':'VM','hold_released_server_epoch':'VM','hold_observed_client_epoch':'external_client','planned_app_restart_began_epoch':'external_client','app_ready_epoch':'external_client'},
            'planned_app_restart_began_epoch':restart_began,'app_ready_epoch':float((work/'fault-restarted').read_text()),
            'same_container_and_image':True,'old_pid':old['State']['Pid'],'new_pid':after['State']['Pid'],'other_roles_restarted':False,'production_contacted':False}
    (work/'fault-injection.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--work',required=True);p.add_argument('--runtime',required=True);p.add_argument('--lab-nonce',required=True);p.add_argument('--ssh-config',required=True);p.add_argument('--ssh-host',required=True);p.add_argument('--nginx-ip',required=True);main(p.parse_args())
