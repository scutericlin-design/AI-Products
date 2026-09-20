"""Run once inside the existing cloud app. Never send a test alert."""
import json
from pathlib import Path
from sqlalchemy import select
from app.config import settings
from app.database import SessionLocal
from app.models import PlatformSetting,KnowledgeJob
from app.trading.personal_plan import PERSONAL_PLAN_KEY,PersonalPlanStore
from app.trading.durable_plan import VERSION
with SessionLocal() as db:
 old=PersonalPlanStore().get(db)
 if old.get('version')==VERSION:
  print('already_migrated')
 else:
  assert old['available_cash']==200000 and not old['holdings'], 'Account changed; migration stopped'
  backup='personal_buy_plan_backup_20260920'
  assert db.get(PlatformSetting,backup) is None, 'Backup exists; inspect before retry'
  db.add(PlatformSetting(key=backup,value=json.dumps(old,ensure_ascii=False)))
  plan=json.loads(Path('research/buy-plan-2026-09-20/plan.json').read_text())
  chat=db.scalar(select(KnowledgeJob.chat_id).where(KnowledgeJob.chat_id.is_not(None)).order_by(KnowledgeJob.created_at.desc(),KnowledgeJob.id.desc()).limit(1))
  assert settings.trading_feishu_webhook_url or (chat and settings.feishu_bot_app_id and settings.feishu_bot_app_secret), 'No configured Feishu destination'
  plan['feishu_chat_id']=chat
  PersonalPlanStore().put(db,plan)
  print('migrated',plan['version'],'candidates',len(plan['candidates']),'cash',plan['available_cash'],'notification_policy',plan['notification_policy'])
