from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .models import OrderIntent, Side


class LiveTradingDisabled(RuntimeError):
    pass


@dataclass(frozen=True)
class QmtPreflight:
    ok: bool
    reasons: tuple[str, ...]


class QmtBroker:
    """Guarded miniQMT adapter.

    Submission is impossible unless the environment gate and the per-call human
    confirmation are both set. It uses miniQMT's documented ``order_stock``
    signature; run ``preflight`` and paper/ shadow reconciliation first.
    """

    def __init__(self, settings: Settings, *, daily_loss: float = 0.0, daily_order_count: int = 0):
        self.settings = settings
        self.daily_loss = daily_loss
        self.daily_order_count = daily_order_count
        self.audit: list[dict] = []

    def preflight(self, *, data_fresh: bool, reconciled: bool, kill_switch: bool, max_daily_loss: float, max_orders: int) -> QmtPreflight:
        reasons: list[str] = []
        if not self.settings.qmt_live_enabled: reasons.append("ASQ_QMT_LIVE_ENABLED 未开启")
        if not self.settings.qmt_userdata_path or not self.settings.qmt_account_id: reasons.append("缺少 QMT userdata 路径或账户标识")
        if not data_fresh: reasons.append("行情数据不新鲜")
        if not reconciled: reasons.append("账户未完成对账")
        if kill_switch: reasons.append("kill switch 已触发")
        if self.daily_loss >= max_daily_loss: reasons.append("达到当日亏损上限")
        if self.daily_order_count >= max_orders: reasons.append("达到当日委托数量上限")
        return QmtPreflight(not reasons, tuple(reasons))

    def submit(self, intent: OrderIntent, *, confirm_live_order: bool, preflight: QmtPreflight) -> dict:
        if not preflight.ok:
            raise LiveTradingDisabled("；".join(preflight.reasons))
        if not confirm_live_order:
            raise LiveTradingDisabled("每笔实盘委托均须明确确认")
        if intent.quantity <= 0 or intent.quantity % self.settings.lot_size:
            raise ValueError("证券委托数量必须是100股整手")
        try:
            from xtquant import xtconstant
            from xtquant.xttrader import XtQuantTrader
            from xtquant.xttype import StockAccount
        except ImportError as exc:
            raise RuntimeError("xtquant 不可用；请在安装了 miniQMT 的 Windows 环境中运行") from exc
        trader = XtQuantTrader(str(Path(self.settings.qmt_userdata_path)), int(datetime.now().timestamp()))
        trader.start()
        if trader.connect() != 0:
            trader.stop()
            raise RuntimeError("miniQMT 连接失败")
        account = StockAccount(self.settings.qmt_account_id)
        side = xtconstant.STOCK_BUY if intent.side is Side.BUY else xtconstant.STOCK_SELL
        try:
            order_id = trader.order_stock(account, intent.symbol, side, intent.quantity, xtconstant.FIX_PRICE, intent.limit_price, intent.strategy, intent.client_order_id)
        finally:
            trader.stop()
        record = {"at": datetime.now().isoformat(timespec="seconds"), "client_order_id": intent.client_order_id, "broker_order_id": order_id, "symbol": intent.symbol, "side": intent.side, "quantity": intent.quantity, "price": intent.limit_price}
        self.audit.append(record)
        self.daily_order_count += 1
        return record


def make_order_intent(*, symbol: str, side: Side, quantity: int, limit_price: float, strategy: str, signal_date: str, reason: str) -> OrderIntent:
    return OrderIntent(uuid4().hex, symbol, side, quantity, limit_price, strategy, signal_date, reason)
