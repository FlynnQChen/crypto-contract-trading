import time
import logging 
from typing import Dict, List, Optional, Tuple
from decimal import Decimal, getcontext
from datetime import datetime, timedelta
from ...exceptions import PositionManagementError
from ....api import BinanceFuturesAPI, OKXFuturesAPI 
 
class PositionManager:
    """
    多交易所智能仓位管理系统 
    功能：
    - 实时监控全仓风险敞口
    - 自动平衡跨交易所头寸
    - 动态止损止盈策略 
    """
 
    def __init__(self, config: Dict, apis: Dict[str, object]):
        """
        :param config: 从position_management.json 加载的配置 
        :param apis: 交易所API实例 {'binance': api, 'okx': api}
        """
        self.config  = config 
        self.apis  = apis 
        self.logger  = logging.getLogger('position_mgr') 
        self.positions  = {}  # 记录所有仓位 {'binance': {'BTCUSDT': {'size': 1.2, 'side': 'long'}}}
        
        # 初始化精度
        getcontext().prec = 8
        self.min_order_size  = {
            'BTC': Decimal('0.001'),
            'ETH': Decimal('0.01'),
            'SOL': Decimal('1')
        }
 
        # 加载初始仓位
        self._init_position_tracking()
 
    def _init_position_tracking(self):
        """初始化仓位跟踪"""
        for exchange, api in self.apis.items(): 
            self.positions[exchange]  = {}
            try:
                if exchange == 'binance':
                    positions = api.get_position_risk() 
                    for pos in positions:
                        if float(pos['positionAmt']) != 0:
                            self.positions[exchange][pos['symbol']]  = {
                                'size': Decimal(pos['positionAmt']),
                                'side': 'long' if float(pos['positionAmt']) > 0 else 'short',
                                'entry_price': Decimal(pos['entryPrice'])
                            }
                
                elif exchange == 'okx':
                    positions = api.get_positions(instType="SWAP") 
                    for pos in positions:
                        if pos['pos'] != '0':
                            self.positions[exchange][pos['instId']]  = {
                                'size': Decimal(pos['pos']),
                                'side': pos['posSide'].lower(),
                                'entry_price': Decimal(pos['avgPx'])
                            }
            
            except Exception as e:
                self.logger.error(f" 初始化仓位失败 {exchange}: {str(e)}")
 
    def get_net_exposure(self, symbol: str) -> Decimal:
        """计算净风险敞口 (跨交易所)"""
        net = Decimal('0')
        for exchange in self.positions.values(): 
            for sym, pos in exchange.items(): 
                if symbol in sym:  # 处理不同交易所的symbol格式差异
                    net += pos['size'] if pos['side'] == 'long' else -pos['size']
        return net
 
    def check_balance_conditions(self, symbol: str) -> Optional[Dict]:
        """
        检查仓位再平衡条件
        返回: {
            'direction': 'long'/'short',
            'amount': Decimal,
            'priority_exchange': str
        }
        """
        net_exposure = self.get_net_exposure(symbol) 
        threshold = Decimal(str(self.config['rebalance_thresholds']['position_imbalance'])) 
        
        if abs(net_exposure) > threshold:
            return {
                'direction': 'short' if net_exposure > 0 else 'long',
                'amount': abs(net_exposure) * Decimal('0.5'),  # 平衡50%
                'priority_exchange': self._select_hedge_exchange(symbol)
            }
        return None
 
    def _select_hedge_exchange(self, symbol: str) -> str:
        """选择最优对冲交易所"""
        # 基于流动性、费率、滑点等指标 
        liquidity_scores = {}
        for exchange, api in self.apis.items(): 
            try:
                if exchange == 'binance':
                    depth = api.get_orderbook(f"{symbol}")['bids'][0][1] 
                elif exchange == 'okx':
                    depth = api.get_orderbook(f"{symbol}-SWAP")['bids'][0][1] 
                liquidity_scores[exchange] = float(depth)
            except:
                liquidity_scores[exchange] = 0 
        
        return max(liquidity_scores.items(),  key=lambda x: x[1])[0]
 
    def execute_rebalance(self, symbol: str, rebalance_data: Dict):
        """执行仓位再平衡"""
        try:
            exchange = rebalance_data['priority_exchange']
            amount = rebalance_data['amount']
            
            # 调整订单规模 
            adjusted_amount = max(
                amount.quantize(self.min_order_size.get(symbol[:3],  Decimal('0.001'))),
                self.min_order_size.get(symbol[:3],  Decimal('0.001'))
            )
            
            # 确定合约代码 
            if exchange == 'okx':
                symbol = f"{symbol}-SWAP"
            
            # 下单 
            order_id = self.apis[exchange].place_order( 
                symbol=symbol,
                side=rebalance_data['direction'],
                order_type='market',
                quantity=float(adjusted_amount)
            )
            
            self.logger.info( 
                f"仓位再平衡 | {exchange} {symbol} | "
                f"方向: {rebalance_data['direction']} | "
                f"数量: {adjusted_amount}"
            )
            
            # 更新本地仓位记录 
            self._update_local_position(
                exchange=exchange,
                symbol=symbol,
                size_change=adjusted_amount if rebalance_data['direction'] == 'long' else -adjusted_amount 
            )
            
            return order_id
        
        except Exception as e:
            self.logger.error(f" 再平衡执行失败 {symbol}: {str(e)}")
            raise PositionManagementError(f"Rebalance failed: {str(e)}")
 
    def check_stop_conditions(self, symbol: str) -> Optional[Dict]:
        """检查止损止盈条件"""
        for exchange, positions in self.positions.items(): 
            if symbol not in positions and f"{symbol}-SWAP" not in positions:
                continue 
            
            pos_key = symbol if symbol in positions else f"{symbol}-SWAP"
            position = positions[pos_key]
            
            # 获取当前价格 
            if exchange == 'binance':
                mark_price = Decimal(self.apis[exchange].get_mark_price(symbol)['markPrice']) 
            else:
                mark_price = Decimal(self.apis[exchange].get_mark_price(f"{symbol}-SWAP")['markPx']) 
            
            # 计算盈亏
            pnl_ratio = (
                (mark_price - position['entry_price']) / position['entry_price'] 
                if position['side'] == 'long' else
                (position['entry_price'] - mark_price) / position['entry_price']
            )
            
            # 检查止盈
            if pnl_ratio >= Decimal(str(self.config['stop_conditions']['take_profit'])): 
                return {
                    'exchange': exchange,
                    'symbol': pos_key,
                    'side': 'sell' if position['side'] == 'long' else 'buy',
                    'amount': abs(position['size']),
                    'reason': 'take_profit',
                    'pnl': float(pnl_ratio)
                }
            
            # 检查止损
            elif pnl_ratio <= -Decimal(str(self.config['stop_conditions']['stop_loss'])): 
                return {
                    'exchange': exchange,
                    'symbol': pos_key,
                    'side': 'sell' if position['side'] == 'long' else 'buy',
                    'amount': abs(position['size']),
                    'reason': 'stop_loss',
                    'pnl': float(pnl_ratio)
                }
        
        return None
 
    def execute_stop(self, stop_data: Dict):
        """执行止损止盈"""
        try:
            order_id = self.apis[stop_data['exchange']].place_order( 
                symbol=stop_data['symbol'],
                side=stop_data['side'],
                order_type='market',
                quantity=float(stop_data['amount'])
            )
            
            self.logger.warning( 
                f"{stop_data['reason'].upper()}触发 | {stop_data['exchange']} {stop_data['symbol']} | "
                f"方向: {stop_data['side']} | 数量: {stop_data['amount']} | "
                f"盈亏: {stop_data['pnl']:.2%}"
            )
            
            # 更新本地仓位记录
            self._update_local_position(
                exchange=stop_data['exchange'],
                symbol=stop_data['symbol'],
                size_change=-stop_data['amount'] if stop_data['side'] == 'sell' else stop_data['amount']
            )
            
            return order_id 
        
        except Exception as e:
            self.logger.error(f" 止损执行失败: {str(e)}")
            raise PositionManagementError(f"Stop execution failed: {str(e)}")
 
    def run(self):
        """主控制循环"""
        last_check = time.time() 
        
        while True:
            try:
                # 更新仓位数据
                self._init_position_tracking()
                
                # 每30秒检查一次
                if time.time()  - last_check > 30:
                    for symbol in self.config['monitored_symbols']: 
                        # 检查再平衡条件 
                        rebalance_data = self.check_balance_conditions(symbol) 
                        if rebalance_data:
                            self.execute_rebalance(symbol,  rebalance_data)
                        
                        # 检查止损条件 
                        stop_data = self.check_stop_conditions(symbol) 
                        if stop_data:
                            self.execute_stop(stop_data) 
                    
                    last_check = time.time() 
                
                time.sleep(5) 
            
            except Exception as e:
                self.logger.critical(f" 主循环异常: {str(e)}", exc_info=True)
                time.sleep(30) 
 
    def _update_local_position(self, exchange: str, symbol: str, size_change: Decimal):
        """更新本地仓位记录"""
        if exchange not in self.positions: 
            self.positions[exchange]  = {}
        
        if symbol not in self.positions[exchange]: 
            self.positions[exchange][symbol]  = {
                'size': Decimal('0'),
                'side': 'long' if size_change > 0 else 'short',
                'entry_price': Decimal('0')
            }
        
        self.positions[exchange][symbol]['size']  += size_change 
        
        # 清理零仓位
        if abs(self.positions[exchange][symbol]['size'])  < self.min_order_size.get(symbol[:3],  Decimal('0.001')):
            del self.positions[exchange][symbol] 