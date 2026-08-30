from __future__ import annotations
import argparse,json
from hot_leader_strategy.backtest import run_backtest
from hot_leader_strategy.runner import HotLeaderRunner

def main()->int:
    parser=argparse.ArgumentParser(description="Independent A-share hot-theme leader research/paper strategy")
    parser.add_argument("--refresh",action="store_true",help="Refresh only hot-leader isolated market cache")
    parser.add_argument("--signal-once",action="store_true",help="Generate independent research signal")
    parser.add_argument("--paper-once",action="store_true",help="Run independent local paper account only")
    parser.add_argument("--close-plan-once",action="store_true",help="Refresh the isolated cache and create one close plan for next-session paper execution")
    parser.add_argument("--execute-previous-plan-once",action="store_true",help="Execute at most one prior-session plan in the independent paper account")
    parser.add_argument("--intraday-once",action="store_true",help="Run one isolated intraday price-confirmation and risk-monitoring cycle")
    parser.add_argument("--account",action="store_true")
    parser.add_argument("--backtest",action="store_true",help="Point-in-time, next-open historical backtest")
    parser.add_argument("--start",default="20180101"); parser.add_argument("--end",default="20261231"); parser.add_argument("--limit",type=int)
    args=parser.parse_args(); runner=HotLeaderRunner()
    if args.refresh:result=runner.refresh(args.start,args.end,args.limit)
    elif args.close_plan_once:result=runner.close_plan_once(args.end if args.end!="20261231" else None)
    elif args.execute_previous_plan_once:result=runner.execute_previous_plan_once(args.end if args.end!="20261231" else None)
    elif args.intraday_once:result=runner.intraday_once()
    elif args.signal_once:result=runner.signal_once(args.end if args.end!="20261231" else None)
    elif args.paper_once:result=runner.paper_once(args.end if args.end!="20261231" else None)
    elif args.account:result=runner.account()
    elif args.backtest:result=run_backtest(runner.store,runner.settings,args.start,args.end)
    else:parser.print_help();return 0
    print(json.dumps(result,ensure_ascii=False,default=str));return 0
