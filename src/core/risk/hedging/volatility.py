import time 
import logging
import numpy as np 
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from decimal import Decimal, getcontext
from ...exceptions import VolatilityHedgeError
from ....api import BinanceFuturesAPI, OKXFuturesAPI 
 
class VolatilityHedger:
    """
    波动率驱动的对冲系统 
    功能：
    - 实时监测市场波动率
    - 动态调整对冲比例 
    - 多策略风险分散 
    """
 
    def __init__(self, config: Dict, apis: Dict[str, object]):
        """
        :param config: 从volatility_hedge.json 加载的配置 
        :param apis: 交易所API实例 {'binance': api, 'okx': api}
        """
        self.config  = config 
        self.apis  = apis 
        self.logger  = logging.getLogger('volatility_hedger') 
        self.hedge_positions  = {}
        
        # 初始化指标计算窗口 
        self.historical_data  = {
            symbol: {
                'prices': [],
                'timestamps': [],
                'atr': None,
                'rsi': None 
            }
            for symbol in config['instrument_selection']['preferred_pairs']
        }
 
        # 设置精度 
        getcontext().prec = 8 
        self.min_order_size  = {
            'BTC': Decimal('0.001'),
            'ETH': Decimal('0.01'),
            'SOL': Decimal('1')
        }
 
    def update_market_data(self, symbol: str): 
        """更新市场数据并计算指标"""
        try:
            # 获取最新K线数据 (这里简化处理，实际应使用WebSocket)
            klines = self.apis['binance'].get_klines( 
                symbol=f"{symbol}USDT",
                interval='1h',
                limit=24  # 24小时数据
            )
 
            # 存储价格数据 
            closes = [Decimal(str(k[4])) for k in klines]
            self.historical_data[symbol]['prices']  = closes 
            self.historical_data[symbol]['timestamps']  = [k[0] for k in klines]
 
            # 计算ATR (14周期)
            high_prices = [Decimal(str(k[2])) for k in klines]
            low_prices = [Decimal(str(k[3])) for k in klines]
            self.historical_data[symbol]['atr']  = self._calculate_atr(
                high_prices[-14:],
                low_prices[-14:],
                closes[-14:]
            )
 
            # 计算RSI (14周期)
            self.historical_data[symbol]['rsi']  = self._calculate_rsi(closes[-15:])
 
        except Exception as e:
            self.logger.error(f" 数据更新失败 {symbol}: {str(e)}")
 
    def _calculate_atr(self, highs: List[Decimal], lows: List[Decimal], closes: List[Decimal]) -> Decimal:
        """计算平均真实波幅(ATR)"""
        true_ranges = []
        for i in range(1, len(highs)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            true_ranges.append(max(hl,  hc, lc))
        return sum(true_ranges) / Decimal(str(len(true_ranges)))
 
    def _calculate_rsi(self, closes: List[Decimal]) -> Decimal:
        """计算相对强弱指数(RSI)"""
        deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains = sum(d for d in deltas if d > 0) / Decimal('14')
        losses = abs(sum(d for d in deltas if d < 0)) / Decimal('14')
        return Decimal('100') - (Decimal('100') / (Decimal('1') + (gains / losses if losses != 0 else Decimal('100'))))
 
    def assess_volatility(self, symbol: str) -> Dict:
        """评估当前波动率状态"""
        data = self.historical_data.get(symbol,  {})
        if not data or not data['atr']:
            return {'state': 'normal', 'score': 0}
 
        # 波动率评分 (0-1)
        atr_ratio = data['atr'] / Decimal(str(self.config['volatility_indicators']['thresholds']['high_vol'])) 
        volatility_score = min(float(atr_ratio), 1.0)
 
        # 确定状态
        if data['atr'] >= Decimal(str(self.config['volatility_indicators']['thresholds']['extreme_vol'])): 
            state = 'extreme'
        elif data['atr'] >= Decimal(str(self.config['volatility_indicators']['thresholds']['high_vol'])): 
            state = 'high'
        else:
            state = 'normal'
 
        return {
            'state': state,
            'score': volatility_score,
            'atr': float(data['atr']),
            'rsi': float(data['rsi']) if data['rsi'] else None
        }
 
    def calculate_hedge_ratio(self, symbol: str) -> Decimal:
        """计算动态对冲比例"""
        volatility = self.assess_volatility(symbol) 
        config = self.config['hedge_ratio_calculation'] 
 
        # 基础比例
        ratio = Decimal(str(config['base_ratio']))
 
        # RSI调整
        if volatility['rsi']:
            rsi_adj = max(0, volatility['rsi'] - 70) * Decimal(str(config['dynamic_adjustment']['rsi_factor']))
            ratio += rsi_adj
 
        # 波动率调整 
        vol_adj = Decimal(str(volatility['score'])) * Decimal('0.5')  # 波动率贡献最多50%
        ratio += vol_adj 
 
        # 限制最大比例
        return min(ratio, Decimal(str(config['max_ratio'])))
 
    def execute_hedge(self, symbol: str):
        """执行对冲操作"""
        try:
            current_ratio = self._get_current_hedge_ratio(symbol)
            target_ratio = self.calculate_hedge_ratio(symbol) 
            
            # 计算需要调整的头寸
            position = self._get_position_size(symbol)
            if not position:
                return 
 
            size_to_hedge = abs(position['size'] * (target_ratio - current_ratio))
            if size_to_hedge < float(self.min_order_size.get(symbol.split('-')[0],  Decimal('0'))):
                return 
 
            # 确定对冲方向
            side = 'sell' if position['size'] > 0 else 'buy'
            contract_type = 'SWAP' if 'SWAP' in symbol else 'FUTURES'
 
            # 执行对冲 (使用TWAP策略)
            order_id = self._twap_order(
                symbol=symbol,
                side=side,
                amount=Decimal(str(size_to_hedge)),
                minutes=self.config['execution_rules']['twap_minutes'] 
            )
 
            # 记录对冲仓位 
            self.hedge_positions[f"{symbol}_{datetime.now().timestamp()}"]  = {
                'order_id': order_id,
                'symbol': symbol,
                'side': side,
                'size': size_to_hedge,
                'target_ratio': float(target_ratio),
                'timestamp': datetime.now() 
            }
 
            self.logger.info( 
                f"波动率对冲 | {symbol} | 方向: {side} | "
                f"规模: {size_to_hedge:.4f} | 目标比例: {target_ratio:.2%} | "
                f"ATR: {self.historical_data[symbol]['atr']:.2f}" 
            )
 
        except Exception as e:
            self.logger.error(f" 对冲执行失败 {symbol}: {str(e)}", exc_info=True)
 
    def monitor_hedges(self):
        """监控并调整对冲仓位"""
        for hedge_id, hedge in list(self.hedge_positions.items()): 
            try:
                # 检查是否需要再平衡
                current_ratio = self._get_current_hedge_ratio(hedge['symbol'])
                deviation = abs(current_ratio - hedge['target_ratio'])
                
                if deviation > Decimal('0.05'):  # 偏离超过5%
                    self.execute_hedge(hedge['symbol']) 
 
                # 清理过期记录 (超过6小时)
                if (datetime.now()  - hedge['timestamp']) > timedelta(hours=6):
                    del self.hedge_positions[hedge_id] 
 
            except Exception as e:
                self.logger.error(f" 对冲监控异常 {hedge_id}: {str(e)}")
 
    def run(self):
        """启动主循环"""
        symbols = self.config['instrument_selection']['preferred_pairs'] 
        last_update = time.time() 
 
        while True:
            try:
                # 更新市场数据
                if time.time()  - last_update > 300:  # 5分钟更新一次
                    for symbol in symbols:
                        self.update_market_data(symbol) 
                    last_update = time.time() 
 
                # 监控现有对冲 
                self.monitor_hedges() 
 
                # 执行新对冲 
                for symbol in symbols:
                    vol_state = self.assess_volatility(symbol) 
                    if vol_state['state'] in ['high', 'extreme']:
                        self.execute_hedge(symbol) 
 
                time.sleep(10) 
 
            except Exception as e:
                self.logger.critical(f" 主循环异常: {str(e)}", exc_info=True)
                time.sleep(30) 