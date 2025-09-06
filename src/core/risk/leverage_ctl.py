import time 
import logging
from typing import Dict, List, Optional
from decimal import Decimal, getcontext
from datetime import datetime 
from ...exceptions import LeverageError 
from ....api import BinanceFuturesAPI, OKXFuturesAPI
 
class LeverageController:
    """
    多交易所杠杆智能管理系统 
    功能：
    - 动态调整杠杆倍数 
    - 基于波动率的风险控制 
    - 跨交易所杠杆同步
    """
 
    def __init__(self, config: Dict, apis: Dict[str, object]):
        """
        :param config: 从leverage_control.json 加载的配置 
        :param apis: 交易所API实例 {'binance': api, 'okx': api}
        """
        self.config  = config 
        self.apis  = apis 
        self.logger  = logging.getLogger('leverage_ctl') 
        self.current_leverages  = {}  # 记录当前杠杆 {'binance': {'BTCUSDT': 5}}
        
        # 初始化精度
        getcontext().prec = 6
        self.min_leverage  = {
            'binance': Decimal('1'),
            'okx': Decimal('3')
        }
        self.max_leverage  = {
            'binance': Decimal('125'),
            'okx': Decimal('100')
        }
 
        # 加载初始杠杆设置 
        self._init_leverage_settings()
 
    def _init_leverage_settings(self):
        """初始化各交易所的杠杆设置"""
        for exchange, api in self.apis.items(): 
            self.current_leverages[exchange]  = {}
            for symbol in self.config['monitored_pairs']: 
                try:
                    if exchange == 'binance':
                        resp = api.change_leverage(symbol,  self.config['default_leverage']) 
                        self.current_leverages[exchange][symbol]  = Decimal(str(resp['leverage']))
                    elif exchange == 'okx':
                        resp = api.set_leverage( 
                            instId=f"{symbol}-SWAP",
                            lever=str(self.config['default_leverage']), 
                            mgnMode="cross" if self.config['cross_margin']  else "isolated"
                        )
                        self.current_leverages[exchange][symbol]  = Decimal(resp['lever'])
                except Exception as e:
                    self.logger.error(f" 初始化杠杆失败 {exchange} {symbol}: {str(e)}")
 
    def calculate_optimal_leverage(self, symbol: str, volatility: float) -> Dict[str, Decimal]:
        """
        计算各交易所最优杠杆
        返回: {'binance': Decimal(10), 'okx': Decimal(8)}
        """
        # 获取基础配置 
        base_leverage = Decimal(str(self.config['base_leverage'])) 
        max_risk = Decimal(str(self.config['max_risk_per_trade'])) 
        
        # 波动率调整因子 (volatility是ATR百分比值)
        vol_adjustment = min(
            Decimal('1.0') / (Decimal(str(volatility)) * Decimal('2.0')),  # 1/(2*ATR)
            Decimal('3.0')  # 最大放大3倍 
        )
        
        # 计算理论杠杆 
        raw_leverage = base_leverage * vol_adjustment
        
        # 应用交易所限制
        results = {}
        for exchange in self.apis.keys(): 
            clamped_leverage = max(
                self.min_leverage[exchange], 
                min(
                    raw_leverage * Decimal(str(self.config['exchange_adjustments'].get(exchange,  '1.0'))),
                    self.max_leverage[exchange] 
                )
            )
            results[exchange] = clamped_leverage.quantize(Decimal('1.')) 
        
        return results 
 
    def adjust_leverage(self, symbol: str, new_leverages: Dict[str, Decimal]):
        """调整各交易所杠杆"""
        for exchange, leverage in new_leverages.items(): 
            if exchange not in self.apis: 
                continue
            
            current = self.current_leverages[exchange].get(symbol,  Decimal('1'))
            if abs(current - leverage) < Decimal('0.5'):  # 避免微小调整
                continue
            
            try:
                if exchange == 'binance':
                    self.apis['binance'].change_leverage( 
                        symbol=symbol,
                        leverage=int(leverage)
                    )
                elif exchange == 'okx':
                    self.apis['okx'].set_leverage( 
                        instId=f"{symbol}-SWAP",
                        lever=str(leverage),
                        mgnMode="cross" if self.config['cross_margin']  else "isolated"
                    )
                
                self.current_leverages[exchange][symbol]  = leverage
                self.logger.info( 
                    f"杠杆调整 | {exchange} {symbol} | "
                    f"{current}x → {leverage}x"
                )
                
            except Exception as e:
                self.logger.error(f" 杠杆调整失败 {exchange} {symbol}: {str(e)}")
 
    def check_liquidation_risk(self, symbol: str) -> Optional[Dict]:
        """检查爆仓风险"""
        risks = {}
        for exchange, api in self.apis.items(): 
            try:
                if exchange == 'binance':
                    position = next(
                        (p for p in api.get_position_risk()  if p['symbol'] == symbol),
                        None
                    )
                    if position:
                        risks[exchange] = {
                            'price': Decimal(position['markPrice']),
                            'liq_price': Decimal(position['liquidationPrice']),
                            'margin_ratio': Decimal(position['marginRatio'])
                        }
                
                elif exchange == 'okx':
                    positions = api.get_positions(instType="SWAP") 
                    position = next(
                        (p for p in positions if p['instId'] == f"{symbol}-SWAP"),
                        None
                    )
                    if position:
                        risks[exchange] = {
                            'price': Decimal(position['markPx']),
                            'liq_price': Decimal(position['liqPx']),
                            'margin_ratio': Decimal(position['mgnRatio'])
                        }
            
            except Exception as e:
                self.logger.error(f" 获取爆仓风险失败 {exchange}: {str(e)}")
        
        return risks if risks else None 
 
    def auto_hedge_liq_risk(self, symbol: str, risks: Dict):
        """自动对冲爆仓风险"""
        # 找出风险最高的交易所 
        high_risk_exchange = max(
            risks.items(), 
            key=lambda x: (x[1]['margin_ratio'], abs(x[1]['price'] - x[1]['liq_price']))
        )[0]
        
        # 在另一交易所建立对冲头寸 
        hedge_exchange = 'okx' if high_risk_exchange == 'binance' else 'binance'
        
        try:
            position_size = self._get_position_size(high_risk_exchange, symbol)
            if not position_size:
                return
            
            # 计算对冲量 (保守策略)
            hedge_size = position_size * Decimal('0.5')  # 50%对冲 
            
            # 确定方向 (反向对冲)
            side = 'sell' if position_size > 0 else 'buy'
            
            self.apis[hedge_exchange].place_order( 
                symbol=symbol,
                side=side,
                order_type='market',
                quantity=float(hedge_size),
                reduce_only=False 
            )
            
            self.logger.warning( 
                f"爆仓对冲执行 | {symbol} | {high_risk_exchange}→{hedge_exchange} | "
                f"对冲量: {hedge_size:.4f}"
            )
            
        except Exception as e:
            self.logger.error(f" 爆仓对冲失败 {symbol}: {str(e)}")
 
    def run(self):
        """主控制循环"""
        last_vol_check = time.time() 
        
        while True:
            try:
                # 每5分钟检查一次
                if time.time()  - last_vol_check > 300:
                    for symbol in self.config['monitored_pairs']: 
                        # 获取波动率数据 (实际应从其他模块获取)
                        volatility = self._get_volatility(symbol)
                        
                        # 计算最优杠杆 
                        new_leverages = self.calculate_optimal_leverage(symbol,  volatility)
                        self.adjust_leverage(symbol,  new_leverages)
                        
                        # 检查爆仓风险 
                        risks = self.check_liquidation_risk(symbol) 
                        if risks and any(r['margin_ratio'] > Decimal('0.9') for r in risks.values()): 
                            self.auto_hedge_liq_risk(symbol,  risks)
                    
                    last_vol_check = time.time() 
                
                time.sleep(10) 
            
            except Exception as e:
                self.logger.critical(f" 主循环异常: {str(e)}", exc_info=True)
                time.sleep(30) 