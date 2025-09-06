import time
import logging 
from decimal import Decimal, getcontext 
from datetime import datetime, timedelta 
from typing import Dict, List, Optional, Tuple
from ...exceptions import SpreadTradingError 
from ....api import OKXFuturesAPI, BinanceFuturesAPI
 
class SpreadEngine:
    """
    跨期合约价差交易系统 
    功能：
    - 监控当季/次季合约价差
    - 自动执行价差回归交易 
    - 动态保证金管理
    """
 
    def __init__(self, config: Dict, api: OKXFuturesAPI):
        """
        :param config: 从calendar_spread.json 加载的配置
        :param api: 交易所API实例 (OKX/Binance)
        """
        self.config  = config 
        self.api  = api 
        self.logger  = logging.getLogger('spread_engine') 
        self.active_spreads  = {}  # 记录活跃价差交易 
        
        # 精度设置
        getcontext().prec = 6
        self.min_size  = {
            'BTC': Decimal('0.01'),
            'ETH': Decimal('0.1'),
            'SOL': Decimal('1')
        }
 
    def get_contract_pairs(self, symbol: str) -> Optional[Tuple[str, str]]:
        """
        获取当前和次季合约代码 
        返回: (front_month_contract, back_month_contract)
        """
        contracts = self.api.list_instruments(instType="SWAP") 
        if not contracts:
            return None
 
        # 按到期日排序 
        sorted_contracts = sorted(
            [c for c in contracts if c['instId'].startswith(symbol)],
            key=lambda x: x['expiry']
        )
 
        if len(sorted_contracts) < 2:
            return None 
 
        return (sorted_contracts[0]['instId'], sorted_contracts[1]['instId'])
 
    def calculate_basis(self, front: str, back: str) -> Optional[Dict]:
        """
        计算跨期价差和指标 
        返回: {
            'raw_spread': Decimal,  # 原始价差(back - front)
            'annualized': Decimal,  # 年化收益率 
            'liquidity': Decimal,   # 流动性得分(0-1)
            'fair_value': Decimal   # 理论合理价差 
        }
        """
        try:
            # 获取双合约数据 
            front_data = self.api.get_mark_price(front) 
            back_data = self.api.get_mark_price(back) 
            
            if not all([front_data, back_data]):
                return None 
 
            # 计算基本价差 
            front_price = Decimal(front_data['markPrice'])
            back_price = Decimal(back_data['markPrice'])
            raw_spread = back_price - front_price 
 
            # 计算年化收益率 
            days_to_expiry = (datetime.strptime(front_data['expiry'],  '%Y-%m-%d') - datetime.now()).days 
            if days_to_expiry <= 0:
                return None
            annualized = (raw_spread / front_price) * (365 / days_to_expiry)
 
            # 流动性评估 (盘口深度加权)
            front_depth = self._calculate_liquidity_score(front)
            back_depth = self._calculate_liquidity_score(back)
            liquidity_score = min(front_depth, back_depth)
 
            # 理论价差 (资金费率推导)
            fair_value = self._calculate_fair_value(
                front_data['fundingRate'],
                back_data['fundingRate'],
                days_to_expiry 
            )
 
            return {
                'raw_spread': raw_spread,
                'annualized': annualized,
                'liquidity': liquidity_score,
                'fair_value': fair_value,
                'front_price': front_price,
                'back_price': back_price
            }
 
        except Exception as e:
            self.logger.error(f" 价差计算失败 {front}/{back}: {str(e)}")
            return None 
 
    def _calculate_liquidity_score(self, instrument: str) -> Decimal:
        """计算合约流动性得分 (0-1)"""
        orderbook = self.api.get_orderbook(instrument) 
        if not orderbook:
            return Decimal('0')
        
        # 计算前五档深度 (单位：BTC等值)
        total = sum(
            Decimal(price) * Decimal(amount) 
            for price, amount in orderbook['bids'][:5] + orderbook['asks'][:5]
        )
        return total / Decimal('1000000')  # 每100万美元得1分，最高1分 
 
    def _calculate_fair_value(self, front_rate: str, back_rate: str, days: int) -> Decimal:
        """计算理论合理价差"""
        front_daily = Decimal(front_rate) / Decimal('3')  # 8小时费率转日率 
        back_daily = Decimal(back_rate) / Decimal('3')
        return (front_daily - back_daily) * Decimal(str(days))
 
    def check_spread_opportunity(self, symbol: str) -> Optional[Dict]:
        """
        检查价差交易机会 
        返回: {
            'type': 'contango'/'backwardation',
            'symbol': str,
            'front': str,
            'back': str,
            'raw_spread': Decimal,
            'annualized': Decimal,
            'deviation': Decimal  # 偏离理论值程度 
        }
        """
        contracts = self.get_contract_pairs(symbol) 
        if not contracts:
            return None 
 
        basis = self.calculate_basis(*contracts) 
        if not basis:
            return None 
 
        # 检查升水机会 
        if basis['raw_spread'] >= Decimal(str(self.config['spread_conditions']['entry_thresholds']['contango'])): 
            return {
                'type': 'contango',
                'symbol': symbol,
                'front': contracts[0],
                'back': contracts[1],
                'raw_spread': basis['raw_spread'],
                'annualized': basis['annualized'],
                'deviation': basis['raw_spread'] - basis['fair_value']
            }
 
        # 检查贴水机会 
        if basis['raw_spread'] <= Decimal(str(self.config['spread_conditions']['entry_thresholds']['backwardation'])): 
            return {
                'type': 'backwardation',
                'symbol': symbol,
                'front': contracts[0],
                'back': contracts[1],
                'raw_spread': basis['raw_spread'],
                'annualized': basis['annualized'],
                'deviation': basis['raw_spread'] - basis['fair_value']
            }
 
        return None 
 
    def execute_spread_trade(self, opportunity: Dict) -> bool:
        """执行价差交易"""
        symbol = opportunity['symbol']
        config = self.config['execution_rules'] 
 
        try:
            # 计算头寸规模 
            balance = self._get_available_balance()
            position_size = min(
                Decimal(str(balance)) * Decimal(str(config['size_limit']['per_trade'])),
                Decimal(str(self.min_size.get(symbol,  '0.1')))
            )
 
            if position_size <= Decimal('0'):
                raise SpreadTradingError("可用保证金不足")
 
            # 确定交易方向 
            if opportunity['type'] == 'contango':
                front_side, back_side = 'sell', 'buy'
            else:
                front_side, back_side = 'buy', 'sell'
 
            # TWAP下单 
            front_order = self._twap_order(
                inst_id=opportunity['front'],
                side=front_side,
                size=position_size,
                minutes=config['twap_minutes']
            )
 
            back_order = self._twap_order(
                inst_id=opportunity['back'],
                side=back_side,
                size=position_size,
                minutes=config['twap_minutes']
            )
 
            # 记录活跃交易 
            self.active_spreads[f"{symbol}_{datetime.now().timestamp()}"]  = {
                'front_order': front_order,
                'back_order': back_order,
                'symbol': symbol,
                'type': opportunity['type'],
                'size': float(position_size),
                'entry_spread': float(opportunity['raw_spread']),
                'timestamp': datetime.now() 
            }
 
            self.logger.info( 
                f"价差交易执行 | {symbol} | 类型: {opportunity['type']} | "
                f"规模: {position_size} | 年化: {opportunity['annualized']:.2%}"
            )
            return True 
 
        except Exception as e:
            self.logger.error(f" 价差交易失败: {str(e)}", exc_info=True)
            # 此处应添加订单撤销逻辑
            return False
 
    def monitor_spreads(self):
        """监控并平仓价差交易"""
        for trade_id, trade in list(self.active_spreads.items()): 
            try:
                current_basis = self.calculate_basis(trade['front_order']['instId'],  
                                                   trade['back_order']['instId'])
                if not current_basis:
                    continue
 
                # 检查退出条件
                exit_condition = False
                if trade['type'] == 'contango':
                    if current_basis['raw_spread'] <= Decimal(str(
                        self.config['spread_conditions']['exit_thresholds']['profit'])): 
                        exit_condition = True
                    elif current_basis['raw_spread'] >= Decimal(str(
                        self.config['spread_conditions']['exit_thresholds']['loss'])): 
                        exit_condition = True 
                else:
                    if current_basis['raw_spread'] >= -Decimal(str(
                        self.config['spread_conditions']['exit_thresholds']['profit'])): 
                        exit_condition = True 
                    elif current_basis['raw_spread'] <= -Decimal(str(
                        self.config['spread_conditions']['exit_thresholds']['loss'])): 
                        exit_condition = True
 
                # 执行平仓
                if exit_condition:
                    self._close_spread_trade(trade_id)
 
                # 移仓检查 (到期前3天)
                if (datetime.strptime(trade['front_order']['instId'].split('-')[2],  '%Y%m%d') - datetime.now()).days  <= 3:
                    self._rollover_position(trade_id)
 
            except Exception as e:
                self.logger.error(f" 价差监控异常 {trade_id}: {str(e)}")
 
    def run(self):
        """启动价差交易主循环"""
        symbols = self.config['contract_selection']['allowed_pairs'] 
        last_check = time.time() 
 
        while True:
            try:
                # 监控现有仓位 
                self.monitor_spreads() 
 
                # 扫描新机会 
                if time.time()  - last_check >= self.config['monitoring']['basis_monitor']['interval']: 
                    for symbol in symbols:
                        opportunity = self.check_spread_opportunity(symbol) 
                        if opportunity and self._check_risk_limits(): 
                            self.execute_spread_trade(opportunity) 
                    last_check = time.time() 
 
                time.sleep(1) 
 
            except Exception as e:
                self.logger.critical(f" 主循环异常: {str(e)}", exc_info=True)
                time.sleep(10) 