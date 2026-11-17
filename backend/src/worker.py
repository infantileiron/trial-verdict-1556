import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Decision,DpRelease
from src.services.analysis import calculate
from src.services.privacy import process_release
r=redis.Redis.from_url(os.environ['REDIS_URL'])
with Session() as recovery:
 for pending in recovery.query(Decision).filter(Decision.state.in_(['queued','running'])).all():pending.state='queued';r.rpush('trial:decisions',pending.id)
 # 隐私发布任务：扣费已提交，恢复时重新入队；结果由持久化种子确定性重算，噪声不变。
 for dp in recovery.query(DpRelease).filter(DpRelease.status.in_(['queued','running'])).all():dp.status='queued';r.rpush('trial:privacy',dp.id)
 recovery.commit()
while True:
 Path('/tmp/heartbeat').touch()
 try:
  item=r.blpop(['trial:decisions','trial:privacy'],timeout=3)
  if not item:continue
  queue,raw=item
  if queue==b'trial:privacy':
   with Session() as s:
    row=s.get(DpRelease,int(raw))
    if not row or row.status not in ('queued','running'):continue
    process_release(s,row)
    if row.status=='done' and row.result:
     # 产物落盘失败不影响已提交的结果，下次仍可按库内结果外发。
     try:Path('/artifacts/dp-channel-'+row.sample_ref+'.json').write_text(json.dumps(row.result,ensure_ascii=False))
     except OSError:logging.warning('artifact write skipped for privacy release %s',row.id)
   continue
  with Session() as s:
   row=s.get(Decision,int(raw))
   if not row or row.state!='queued':continue
   row.state='running';s.commit()
   try:
    row.result=calculate(row.snapshot);row.state='done'
    Path('/artifacts/trial-verdict-'+str(row.id)+'.json').write_text(json.dumps(row.result,ensure_ascii=False))
   except Exception as e:row.state='failed';row.error=str(e)
   s.commit()
 except Exception:logging.exception('worker');time.sleep(2)
