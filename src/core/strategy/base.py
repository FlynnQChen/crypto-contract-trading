#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化交易系统基础模块
包含：
1. 抽象接口定义
2. 核心数据结构
3. 通用工具函数 
4. 基础异常体系 
"""
 
import abc 
import asyncio
from dataclasses import dataclass, field 
from datetime import datetime, timedelta
from decimal import Decimal 
from enum import Enum, auto
from typing import (
    Any, Dict, List, Optional, Tuple, TypeVar, Generic, Callable, Coroutine
)
import aiohttp 
import pandas as pd
import numpy as np 
from typing_extensions import Self 
# 在 base.py  文件末尾添加（在 if __name__ == '__main__' 之前）
 
def run_default_strategy(exchange: BaseExchange):
    """默认策略运行函数"""
    class DefaultStrategy(BaseStrategy):
        async def on_bar(self, bar: Bar):
            # 这里实现您的默认策略逻辑
            print(f"Processing bar: {bar.close}") 
            
        async def on_order_update(self, order: Order):
            print(f"Order updated: {order.status}") 
    
    # 创建并运行策略 
    strategy = DefaultStrategy(exchange, {})
    return strategy.run()  
 
# 类型定义 
T = TypeVar('T')
Number = TypeVar('Number', int, float, Decimal)
Symbol = str
ExchangeID = str 
OrderID = str
Timestamp = float 
 
class Direction(Enum):
    """交易方向枚举"""
    LONG = auto()
    SHORT = auto()
    NET = auto()  # 净头寸
 
    @property
    def sign(self) -> int:
        """获取方向系数"""
        return 1 if self == Direction.LONG else -1
 
    @classmethod
    def from_str(cls, s: str) -> Self:
        """从字符串解析"""
        s = s.lower() 
        if s in ('long', 'buy'):
            return cls.LONG 
        elif s in ('short', 'sell'):
            return cls.SHORT
        raise ValueError(f"Invalid direction string: {s}")
 
class OrderType(Enum):
    """订单类型枚举"""
    MARKET = auto()
    LIMIT = auto()
    STOP = auto()
    STOP_LIMIT = auto()
    IOC = auto()  # 立即成交否则取消
    FOK = auto()  # 全部成交否则取消
 
class OrderStatus(Enum):
    """订单状态枚举"""
    NEW = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    CANCELED = auto()
    REJECTED = auto()
    EXPIRED = auto()
 
class TimeInForce(Enum):
    """订单有效期"""
    GTC = auto()  # 一直有效
    IOC = auto()  # 立即成交否则取消 
    FOK = auto()  # 全部成交否则取消 
    DAY = auto()  # 当日有效
 
@dataclass(frozen=True)
class Asset:
    """资产信息"""
    symbol: Symbol 
    free: Decimal  # 可用余额
    locked: Decimal  # 冻结余额 
    price: Decimal = Decimal('0')  # 标记价格 
 
    @property 
    def total(self) -> Decimal:
        """总余额"""
        return self.free  + self.locked  
 
@dataclass
class Bar:
    """K线数据"""
    timestamp: Timestamp 
    open: Decimal
    high: Decimal 
    low: Decimal
    close: Decimal 
    volume: Decimal
    complete: bool = True  # 是否完整K线 
 
    def to_series(self) -> pd.Series:
        """转换为Pandas Series"""
        return pd.Series({
            'timestamp': datetime.fromtimestamp(self.timestamp), 
            'open': float(self.open), 
            'high': float(self.high), 
            'low': float(self.low), 
            'close': float(self.close), 
            'volume': float(self.volume) 
        })
 
@dataclass
class OrderBook:
    """订单簿数据"""
    bids: List[Tuple[Decimal, Decimal]]  # (price, size)
    asks: List[Tuple[Decimal, Decimal]]
    timestamp: Timestamp 
 
    def get_spread(self) -> Decimal:
        """获取买卖价差"""
        return self.asks[0][0]  - self.bids[0][0] 
 
    def get_mid_price(self) -> Decimal:
        """获取中间价"""
        return (self.asks[0][0]  + self.bids[0][0])  / 2 
 
@dataclass
class Order:
    """订单信息"""
    order_id: OrderID 
    symbol: Symbol
    direction: Direction 
    order_type: OrderType
    price: Decimal 
    size: Decimal
    filled: Decimal = Decimal('0')
    status: OrderStatus = OrderStatus.NEW 
    time_in_force: TimeInForce = TimeInForce.GTC
    create_time: Timestamp = field(default_factory=lambda: datetime.now().timestamp()) 
    update_time: Timestamp = field(default_factory=lambda: datetime.now().timestamp()) 
 
    @property
    def remaining(self) -> Decimal:
        """未成交数量"""
        return self.size  - self.filled  
 
    def update(self, **kwargs) -> None:
        """更新订单状态"""
        for k, v in kwargs.items(): 
            if hasattr(self, k):
                setattr(self, k, v)
        self.update_time  = datetime.now().timestamp() 
 
@dataclass
class Position:
    """仓位信息"""
    symbol: Symbol 
    direction: Direction
    size: Decimal 
    entry_price: Decimal 
    mark_price: Decimal 
    liq_price: Decimal = Decimal('0')
    unrealized_pnl: Decimal = Decimal('0')
    leverage: Decimal = Decimal('1')
 
    @property
    def notional_value(self) -> Decimal:
        """名义价值"""
        return self.size  * self.mark_price 
 
    @property
    def margin(self) -> Decimal:
        """占用保证金"""
        return self.notional_value  / self.leverage 
 
    def calculate_pnl(self, exit_price: Decimal) -> Decimal:
        """计算平仓盈亏"""
        return self.size  * (
            (exit_price - self.entry_price)  * self.direction.sign 
        )
 
class BaseExchange(abc.ABC):
    """交易所抽象基类"""
 
    def __init__(self, config: Dict[str, Any]):
        self.config  = config
        self.session  = aiohttp.ClientSession()
        self._last_request_time = 0
        self._rate_limit = 1.0 / config.get('rate_limit',  10)  # 默认10次/秒
 
    @abc.abstractmethod  
    async def fetch_balance(self) -> Dict[Symbol, Asset]:
        """获取账户余额"""
        pass 
 
    @abc.abstractmethod 
    async def fetch_positions(self) -> List[Position]:
        """获取当前仓位"""
        pass
 
    @abc.abstractmethod  
    async def fetch_order_book(self, symbol: Symbol, depth: int = 20) -> OrderBook:
        """获取订单簿"""
        pass 
 
    @abc.abstractmethod 
    async def create_order(
        self,
        symbol: Symbol,
        direction: Direction,
        order_type: OrderType,
        size: Decimal,
        price: Optional[Decimal] = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        **kwargs 
    ) -> Order:
        """创建新订单"""
        pass 
 
    @abc.abstractmethod  
    async def cancel_order(self, order_id: OrderID) -> bool:
        """取消订单"""
        pass 
 
    @abc.abstractmethod  
    async def fetch_ohlcv(
        self,
        symbol: Symbol,
        timeframe: str = '1m',
        since: Optional[Timestamp] = None,
        limit: int = 1000 
    ) -> List[Bar]:
        """获取K线数据"""
        pass
 
    async def close(self) -> None:
        """关闭连接"""
        await self.session.close() 
 
    async def _rate_limit_sleep(self) -> None:
        """速率限制"""
        elapsed = time.time()  - self._last_request_time 
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit  - elapsed)
        self._last_request_time = time.time() 
 
class BaseStrategy(abc.ABC):
    """策略抽象基类"""
 
    def __init__(self, exchange: BaseExchange, config: Dict[str, Any]):
        self.exchange  = exchange
        self.config  = config
        self.orders:  Dict[OrderID, Order] = {}
        self.positions:  Dict[Symbol, Position] = {}
        self.balance:  Dict[Symbol, Asset] = {}
 
    @abc.abstractmethod  
    async def on_bar(self, bar: Bar) -> None:
        """K线回调"""
        pass
 
    @abc.abstractmethod  
    async def on_tick(self, tick: Dict[str, Any]) -> None:
        """行情tick回调"""
        pass
 
    @abc.abstractmethod  
    async def on_order_update(self, order: Order) -> None:
        """订单状态更新"""
        pass
 
    async def run(self) -> None:
        """主运行循环"""
        while True:
            await self._update_state()
            await asyncio.sleep(self.config.get('interval',  1))
 
    async def _update_state(self) -> None:
        """更新账户状态"""
        self.balance  = await self.exchange.fetch_balance() 
        self.positions  = {
            pos.symbol:  pos 
            for pos in await self.exchange.fetch_positions() 
            if pos.size  != 0
        }
 
class RiskManager(abc.ABC):
    """风险管理抽象基类"""
 
    @abc.abstractmethod  
    async def check_order_risk(self, order: Order) -> Tuple[bool, str]:
        """检查订单风险"""
        pass
 
    @abc.abstractmethod  
    async def adjust_position_size(
        self,
        symbol: Symbol,
        direction: Direction,
        size: Decimal 
    ) -> Decimal:
        """调整头寸规模"""
        pass
 
class DataFeed(abc.ABC):
    """数据源抽象基类"""
 
    @abc.abstractmethod 
    async def subscribe(self, symbols: List[Symbol]) -> None:
        """订阅行情数据"""
        pass
 
    @abc.abstractmethod  
    async def get_next_tick(self) -> Dict[str, Any]:
        """获取下一个tick数据"""
        pass
 
    @abc.abstractmethod  
    async def get_historical_bars(
        self,
        symbol: Symbol,
        timeframe: str,
        since: Timestamp,
        until: Timestamp 
    ) -> List[Bar]:
        """获取历史K线"""
        pass
 
class QuantError(Exception):
    """量化系统基础异常"""
    pass 
 
class ExchangeError(QuantError):
    """交易所异常"""
    pass
 
class NetworkError(ExchangeError):
    """网络异常"""
    pass 
 
class RiskCheckFailed(QuantError):
    """风控检查失败"""
    pass 
 
class InsufficientFunds(QuantError):
    """资金不足"""
    pass 
 
# 实用工具函数 
def decimal_from_str(s: str) -> Decimal:
    """安全创建Decimal"""
    try:
        return Decimal(s)
    except:
        return Decimal('0')
 
def timestamp_to_datetime(ts: Timestamp) -> datetime:
    """时间戳转datetime"""
    return datetime.fromtimestamp(ts  / 1000 if ts > 1e12 else ts)
 
def calculate_atr(bars: List[Bar], period: int = 14) -> Decimal:
    """计算平均真实波幅"""
    if len(bars) < period + 1:
        return Decimal('0')
    
    tr_values = []
    for i in range(1, len(bars)):
        high, low = bars[i].high, bars[i].low 
        prev_close = bars[i-1].close
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        tr_values.append(tr) 
    
    atr = sum(tr_values[-period:]) / Decimal(str(period))
    return atr
 
def create_task_safe(coro: Coroutine) -> asyncio.Task:
    """安全创建异步任务"""
    loop = asyncio.get_event_loop() 
    task = loop.create_task(coro) 
    task.add_done_callback(_handle_task_result) 
    return task 
 
def _handle_task_result(task: asyncio.Task) -> None:
    """处理任务异常"""
    try:
        task.result() 
    except asyncio.CancelledError:
        pass 
    except Exception as e:
        logging.error(f"Task  failed: {str(e)}", exc_info=True) 
 
if __name__ == '__main__':
    # 模块功能测试
    bar = Bar(
        timestamp=datetime.now().timestamp(), 
        open=Decimal('50000'),
        high=Decimal('50500'),
        low=Decimal('49900'),
        close=Decimal('50200'),
        volume=Decimal('100.5')
    )
    print(bar.to_series()) 