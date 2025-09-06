import time 
import logging
from decimal import Decimal, getcontext
from typing import Dict, List, Optional, Tuple
from datetime import datetime 
from ...exceptions import ArbitrageError
from ....api import BinanceFuturesAPI, OKXFuturesAPI 
 
class Arbitrator:
    """
    跨交易所价差套利系统 
    功能：
    - 实时监测多交易所价差
    - 自动执行对冲交易 
    - 动态风险控制
    """
 
    def __init__(self, config: Dict, apis: Dict[str, object]):
        """
        :param config: 从basic_arbitrage.json 加载的配置 
        :param apis: 交易所API实例 {'binance': api, 'okx': api}
        """
        self.config  = config 
        self.apis  = apis 
        self.logger  = logging.getLogger('arbitrator') 
        self.active_positions  = {}
        
        # 设置Decimal精度
        getcontext().prec = 8
        self.min_profit  = Decimal('0.001')  # 最小盈利阈值0.1%
 
        # 初始化交易所适配器 
        self.exchange_adapters  = {
            'binance': {
                'symbol_mapping': lambda s: s.replace('-',  ''),
                'get_orderbook': self._get_binance_orderbook,
                'make_order': self._binance_make_order 
            },
            'okx': {
                'symbol_mapping': lambda s: f"{s}-SWAP",
                'get_orderbook': self._get_okx_orderbook,
                'make_order': self._okx_make_order 
            }
        }
 
    def _get_binance_orderbook(self, symbol: str) -> Dict:
        """获取Binance深度数据"""
        return self.apis['binance'].get_orderbook(symbol) 
 
    def _get_okx_orderbook(self, symbol: str) -> Dict:
        """获取OKX深度数据"""
        return self.apis['okx'].get_orderbook(symbol) 
 
    def _binance_make_order(self, symbol: str, side: str, amount: Decimal, price: Decimal) -> str:
        """Binance下单"""
        return self.apis['binance'].place_order( 
            symbol=symbol,
            side=side.upper(), 
            order_type='LIMIT',
            quantity=float(amount),
            price=float(price)
        )['orderId']
 
    def _okx_make_order(self, symbol: str, side: str, amount: Decimal, price: Decimal) -> str:
        """OKX下单"""
        return self.apis['okx'].place_order( 
            symbol=symbol,
            side=side.lower(), 
            order_type='limit',
            size=str(amount),
            price=str(price)
        )['ordId']
 
    def calculate_spread(self, symbol: str) -> Optional[Dict]:
        """
        计算交易所间价差
        返回: {
            'bid_gap': Decimal,  # 买单价差
            'ask_gap': Decimal,  # 卖单价差 
            'mid_gap': Decimal,  # 中间价差 
            'liquidity': Decimal # 可用流动性
        }
        """
        try:
            # 获取双交易所深度数据
            binance_symbol = self.exchange_adapters['binance']['symbol_mapping'](symbol) 
            okx_symbol = self.exchange_adapters['okx']['symbol_mapping'](symbol) 
            
            binance_ob = self.exchange_adapters['binance']['get_orderbook'](binance_symbol) 
            okx_ob = self.exchange_adapters['okx']['get_orderbook'](okx_symbol) 
 
            # 计算最佳买卖价差 
            best_bid_gap = Decimal(okx_ob['bids'][0][0]) - Decimal(binance_ob['asks'][0][0])
            best_ask_gap = Decimal(binance_ob['bids'][0][0]) - Decimal(okx_ob['asks'][0][0])
            mid_gap = ((Decimal(binance_ob['asks'][0][0]) + Decimal(binance_ob['bids'][0][0])) / 2 - 
                      (Decimal(okx_ob['asks'][0][0]) + Decimal(okx_ob['bids'][0][0])) / 2)
 
            # 计算可用流动性 (取三档深度最小值)
            liquidity = min(
                sum(Decimal(amt) for _, amt in binance_ob['asks'][:3]),
                sum(Decimal(amt) for _, amt in okx_ob['bids'][:3]),
                sum(Decimal(amt) for _, amt in binance_ob['bids'][:3]),
                sum(Decimal(amt) for _, amt in okx_ob['asks'][:3])
            )
 
            return {
                'bid_gap': best_bid_gap,
                'ask_gap': best_ask_gap,
                'mid_gap': mid_gap,
                'liquidity': liquidity 
            }
 
        except Exception as e:
            self.logger.error(f" 价差计算失败 {symbol}: {str(e)}")
            return None 
 
    def check_arbitrage_conditions(self, symbol: str) -> Optional[Dict]:
        """检查套利条件是否满足"""
        spread_data = self.calculate_spread(symbol) 
        if not spread_data:
            return None 
 
        # 获取配置阈值 
        min_gap = Decimal(str(self.config['trigger_conditions']['price_gap'])) 
        min_liquidity = Decimal(str(self.config['trigger_conditions']['min_liquidity'])) 
 
        # 确定套利方向 (正向/反向)
        direction = None 
        if spread_data['bid_gap'] >= min_gap:
            direction = 'buy_binance_sell_okx'
            profit = spread_data['bid_gap']
        elif spread_data['ask_gap'] >= min_gap:
            direction = 'buy_okx_sell_binance'
            profit = spread_data['ask_gap']
        else:
            return None 
 
        # 检查流动性 
        if spread_data['liquidity'] < min_liquidity:
            self.logger.warning(f" 流动性不足 {symbol}: {spread_data['liquidity']} < {min_liquidity}")
            return None 
 
        # 计算理论收益率 
        fee_rate = Decimal('0.0004')  # 双边手续费0.04%
        net_profit = profit - fee_rate * 2
        if net_profit < self.min_profit: 
            return None 
 
        return {
            'direction': direction,
            'symbol': symbol,
            'potential_profit': float(net_profit * 100),  # 转换为百分比 
            'liquidity': float(spread_data['liquidity'])
        }
 
    def execute_arbitrage(self, opportunity: Dict) -> bool:
        """执行套利交易"""
        symbol = opportunity['symbol']
        direction = opportunity['direction']
        max_size = Decimal(str(self.config['execution']['size_calculation']['max_per_trade'])) 
        
        try:
            # 获取当前账户余额 
            balance = self._get_available_balance()
            position_size = min(
                Decimal(str(balance)) * max_size,
                Decimal(str(opportunity['liquidity'])) * Decimal('0.1')  # 不超过流动性的10%
            )
 
            if position_size <= Decimal('0'):
                raise ArbitrageError("可用资金不足")
 
            # 根据方向确定交易对和价格 
            if direction == 'buy_binance_sell_okx':
                buy_exchange = 'binance'
                sell_exchange = 'okx'
                buy_price = self._get_ask_price('binance', symbol)
                sell_price = self._get_bid_price('okx', symbol)
            else:
                buy_exchange = 'okx'
                sell_exchange = 'binance'
                buy_price = self._get_ask_price('okx', symbol)
                sell_price = self._get_bid_price('binance', symbol)
 
            # 计算实际数量 (考虑合约面值)
            adjusted_size = self._adjust_position_size(symbol, position_size)
 
            # 同步下单 (实际生产环境应使用异步)
            buy_order_id = self.exchange_adapters[buy_exchange]['make_order']( 
                symbol=self.exchange_adapters[buy_exchange]['symbol_mapping'](symbol), 
                side='buy',
                amount=adjusted_size,
                price=buy_price * Decimal('1.001')  # 加0.1%确保成交
            )
 
            sell_order_id = self.exchange_adapters[sell_exchange]['make_order']( 
                symbol=self.exchange_adapters[sell_exchange]['symbol_mapping'](symbol), 
                side='sell',
                amount=adjusted_size,
                price=sell_price * Decimal('0.999')  # 减0.1%确保成交
            )
 
            # 记录活跃仓位
            self.active_positions[f"{symbol}_{datetime.now().timestamp()}"]  = {
                'buy_order_id': buy_order_id,
                'sell_order_id': sell_order_id,
                'symbol': symbol,
                'size': float(adjusted_size),
                'direction': direction,
                'timestamp': datetime.now() 
            }
 
            self.logger.info( 
                f"套利执行 | {symbol} | 方向: {direction} | "
                f"规模: {adjusted_size} | 预期利润: {opportunity['potential_profit']:.2f}%"
            )
            return True 
 
        except Exception as e:
            self.logger.error(f" 套利执行失败: {str(e)}", exc_info=True)
            # 这里应添加订单撤销逻辑
            return False
 
    def monitor_positions(self):
        """监控并平仓已收敛的套利仓位"""
        for position_id, position in list(self.active_positions.items()): 
            try:
                current_spread = self.calculate_spread(position['symbol']) 
                if not current_spread:
                    continue 
 
                # 检查价差是否收敛
                if position['direction'] == 'buy_binance_sell_okx': 
                    if current_spread['bid_gap'] <= Decimal(str(self.config['spread_conditions']['exit_thresholds']['profit'])): 
                        self._close_position(position_id)
                else:
                    if current_spread['ask_gap'] <= Decimal(str(self.config['spread_conditions']['exit_thresholds']['profit'])): 
                        self._close_position(position_id)
 
                # 检查超时 (30分钟未平仓强制平仓)
                if (datetime.now()  - position['timestamp']).total_seconds() > 1800:
                    self.logger.warning(f" 强制平仓超时仓位 {position_id}")
                    self._close_position(position_id, force=True)
 
            except Exception as e:
                self.logger.error(f" 仓位监控异常 {position_id}: {str(e)}")
 
    def run(self):
        """启动套利主循环"""
        symbols = list(self.config['exchange_pairs'][0]['symbol_mapping'].keys()) 
        last_check = time.time() 
 
        while True:
            try:
                # 检查现有仓位
                self.monitor_positions() 
 
                # 按配置间隔扫描机会 
                if time.time()  - last_check >= self.config['check_intervals']['normal']: 
                    for symbol in symbols:
                        opportunity = self.check_arbitrage_conditions(symbol) 
                        if opportunity and self._check_risk_limits():
                            self.execute_arbitrage(opportunity) 
                    last_check = time.time() 
 
                time.sleep(1) 
 
            except Exception as e:
                self.logger.critical(f" 主循环异常: {str(e)}", exc_info=True)
                time.sleep(10) 