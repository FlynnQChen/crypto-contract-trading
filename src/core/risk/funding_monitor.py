import time 
import logging 
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from ..exceptions import FundingRiskWarning 
from ...api import BinanceFuturesAPI, OKXFuturesAPI 
 
class FundingMonitor:
    """
    多交易所资金费率实时监控与风控系统
    功能：
    - 实时监控资金费率异常
    - 自动触发对冲策略
    - 分级风险处置 
    """
 
    def __init__(self, exchanges: Dict[str, object], config: Dict):
        """
        :param exchanges: 交易所API实例字典 {'binance': binance_api, 'okx': okx_api}
        :param config: 风控配置 (从funding_protection.json 加载)
        """
        self.exchanges  = exchanges
        self.config  = config
        self.logger  = logging.getLogger('funding_monitor') 
        self.last_checked  = {}
        self.hedge_positions  = {}
 
        # 初始化交易所特定参数 
        self.exchange_params  = {
            'binance': {
                'symbol_mapping': lambda s: s.replace('-',  ''),
                'get_rate_method': self._get_binance_funding_rate 
            },
            'okx': {
                'symbol_mapping': lambda s: f"{s}-SWAP",
                'get_rate_method': self._get_okx_funding_rate
            }
        }
 
    def _get_binance_funding_rate(self, symbol: str) -> float:
        """获取Binance当前资金费率"""
        data = self.exchanges['binance'].get_funding_rate_history(symbol,  limit=1)
        return float(data[0]['fundingRate'])
 
    def _get_okx_funding_rate(self, symbol: str) -> float:
        """获取OKX当前预测资金费率"""
        data = self.exchanges['okx'].get_mark_price(symbol) 
        return float(data['fundingRate'])
 
    def check_funding_rates(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        多交易所资金费率检查 
        返回结构: {'BTC': {'binance': 0.0001, 'okx': 0.0002, 'spread': 0.0001}}
        """
        results = {}
        for symbol in symbols:
            rates = {}
            for exchange_name, api in self.exchanges.items(): 
                try:
                    mapped_symbol = self.exchange_params[exchange_name]['symbol_mapping'](symbol) 
                    rates[exchange_name] = self.exchange_params[exchange_name]['get_rate_method'](mapped_symbol) 
                except Exception as e:
                    self.logger.error(f" 获取{exchange_name}费率失败: {str(e)}")
                    continue 
 
            if len(rates) > 1:
                rates['spread'] = abs(list(rates.values())[0]  - list(rates.values())[1]) 
            results[symbol] = rates
        return results
 
    def evaluate_risk(self, symbol: str, rate: float) -> Optional[str]:
        """评估风险等级并触发相应操作"""
        thresholds = self.config['exchanges']['binance']['tiered_thresholds']   # 默认使用binance阈值 
        
        if abs(rate) >= thresholds['extreme']:
            self._trigger_extreme_measures(symbol, rate)
            return 'extreme'
        elif abs(rate) >= thresholds['action']:
            self._trigger_protection_actions(symbol, rate)
            return 'action'
        elif abs(rate) >= thresholds['warning']:
            self._send_warning_alert(symbol, rate)
            return 'warning'
        return None 
 
    def _trigger_protection_actions(self, symbol: str, rate: float):
        """触发保护性操作"""
        actions = self.config['exchanges']['binance']['protection_actions']['action'] 
        self.logger.warning(f" 执行保护操作 | 交易对: {symbol} | 费率: {rate:.6f}")
 
        # 撤销所有挂单 
        if actions.get('cancel_pending',  False):
            for exchange in self.exchanges.values(): 
                exchange.cancel_all_orders(symbol) 
 
        # 切换只减仓模式
        if actions.get('reduce_only',  False):
            self._set_reduce_only_mode(symbol, True)
 
        # 执行对冲 (如果配置)
        if rate > 0 and self.config['global_hedging']['cross_exchange']['enabled']: 
            self._hedge_position(symbol, rate)
 
    def _trigger_extreme_measures(self, symbol: str, rate: float):
        """触发极端情况处理"""
        actions = self.config['exchanges']['binance']['protection_actions']['extreme'] 
        self.logger.critical(f"!!!  极端费率触发 !!! | 交易对: {symbol} | 费率: {rate:.6f}")
 
        # 暂停新订单 
        if 'pause_new_orders' in actions:
            pause_duration = actions['pause_new_orders']['duration']
            self._pause_trading(symbol, pause_duration, actions['pause_new_orders']['whitelist'])
 
        # 强制对冲
        if 'force_hedge' in actions and actions['force_hedge']['enabled']:
            self._hedge_position(symbol, rate, forced=True)
 
    def _hedge_position(self, symbol: str, rate: float, forced: bool = False):
        """执行跨交易所对冲"""
        max_ratio = self.config['global_hedging']['cross_exchange']['max_ratio'] 
        if symbol in self.hedge_positions  and not forced:
            return
 
        # 确定对冲方向 (费率正负决定)
        side = 'sell' if rate > 0 else 'buy'
        self.logger.info(f" 开始对冲 {symbol} | 方向: {side} | 最大比例: {max_ratio}")
 
        # 在实际应用中实现对冲逻辑
        # 此处简化示例:
        self.hedge_positions[symbol]  = {
            'time': datetime.now(), 
            'side': side,
            'ratio': max_ratio
        }
 
    def run(self, interval: Optional[int] = None):
        """启动监控循环"""
        check_interval = interval or self.config['exchanges']['binance']['check_intervals']['normal'] 
        symbols = self.config['global_hedging']['cross_exchange']['allowed_pairs'] 
 
        while True:
            start_time = time.time() 
            
            try:
                rates = self.check_funding_rates(symbols) 
                for symbol, rate_data in rates.items(): 
                    current_rate = rate_data.get('binance',  0)  # 以Binance为主 
                    risk_level = self.evaluate_risk(symbol,  current_rate)
 
                    # 记录监控日志 
                    self._log_monitoring_data(symbol, rate_data, risk_level)
 
            except Exception as e:
                self.logger.error(f" 监控循环异常: {str(e)}", exc_info=True)
 
            # 精确间隔控制 
            elapsed = time.time()  - start_time 
            sleep_time = max(0, check_interval - elapsed)
            time.sleep(sleep_time) 
 
    def _log_monitoring_data(self, symbol: str, rate_data: Dict, risk_level: Optional[str]):
        """记录监控数据到Prometheus和日志"""
        log_msg = f"监控更新 | {symbol} | Binance: {rate_data.get('binance',  0):.6f}"
        if 'okx' in rate_data:
            log_msg += f" | OKX: {rate_data['okx']:.6f} | 价差: {rate_data.get('spread',  0):.6f}"
        if risk_level:
            log_msg += f" | 风险等级: {risk_level.upper()}" 
        
        self.logger.info(log_msg) 
 
        # Prometheus指标上报 (示例)
        if hasattr(self, 'prometheus'):
            self.prometheus.gauge('funding_rate',  rate_data.get('binance',  0), 
                                tags={'symbol': symbol, 'exchange': 'binance'})