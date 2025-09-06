#!/usr/bin/env python3 
# -*- coding: utf-8 -*-
"""
智能流动性管理系统 
核心功能：
1. 实时监控多交易所爆仓风险 
2. 自动分级处置高风险仓位
3. 跨交易所最优路径清算 
4. 自动对冲剩余风险敞口 
"""
 
import asyncio 
import logging
from decimal import Decimal, getcontext
from typing import Dict, List, Optional, Tuple 
from dataclasses import dataclass 
from datetime import datetime, timedelta 
from enum import Enum, auto 
import aiohttp 
import pandas as pd 
from ..exceptions import LiquidationError 
from ..utils import calculate_atr, get_symbol_correlation 
 
# 配置Decimal精度
getcontext().prec = 8 
 
class RiskLevel(Enum):
    """风险等级划分"""
    CRITICAL = auto()  # 距离爆仓价<1%
    HIGH = auto()      # 距离爆仓价1-3% 
    MEDIUM = auto()    # 距离爆仓价3-5%
    LOW = auto()       # 距离爆仓价>5%
 
@dataclass
class PositionRisk:
    """仓位风险数据结构"""
    exchange: str 
    symbol: str
    size: Decimal 
    side: str
    entry_price: Decimal
    mark_price: Decimal
    liq_price: Decimal 
    margin_ratio: Decimal
    risk_distance: Decimal
    notional_value: Decimal 
 
class SmartLiquidator:
    def __init__(self, config: Dict, apis: Dict[str, object]):
        """
        Args:
            config: 风险配置字典 
            apis: 交易所API适配器字典 
        """
        self.config  = config 
        self.apis  = apis 
        self.logger  = logging.getLogger('liquidator') 
        self.risk_positions:  Dict[str, List[PositionRisk]] = {}
        self.liquidation_history  = pd.DataFrame(
            columns=['timestamp', 'exchange', 'symbol', 'side', 
                   'amount', 'price', 'is_hedge', 'status']
        )
        
        # 初始化市场数据 
        self.symbol_info  = self._load_symbol_config()
        self.correlations  = {}  # 品种相关性缓存 
        
        # 启动后台任务
        self._running = True 
        self.task  = asyncio.create_task(self._run_monitor()) 
 
    async def shutdown(self):
        """优雅关闭"""
        self._running = False 
        await self.task 
 
    async def _run_monitor(self):
        """异步风险监控主循环"""
        while self._running:
            try:
                start_time = datetime.now() 
                
                # 并行获取所有交易所风险数据 
                risks = await self._fetch_all_risk_data()
                
                # 分析风险并生成处置策略 
                strategy = await self._analyze_risk(risks)
                
                # 执行处置策略 
                if strategy:
                    await self._execute_strategy(strategy)
                
                # 控制循环间隔 
                elapsed = (datetime.now()  - start_time).total_seconds()
                await asyncio.sleep( 
                    max(0, self.config['monitor_interval']  - elapsed)
                )
                
            except Exception as e:
                self.logger.error(f" 监控循环异常: {str(e)}", exc_info=True)
                await asyncio.sleep(10) 
 
    async def _fetch_all_risk_data(self) -> Dict[str, List[PositionRisk]]:
        """并行获取所有交易所仓位风险数据"""
        tasks = {
            exchange: self._fetch_exchange_risk(exchange) 
            for exchange in self.apis.keys() 
        }
        results = await asyncio.gather(*tasks.values(),  return_exceptions=True)
        
        risks = {}
        for exchange, result in zip(tasks.keys(),  results):
            if isinstance(result, Exception):
                self.logger.error(f" 获取{exchange}风险数据失败: {str(result)}")
                risks[exchange] = []
            else:
                risks[exchange] = result
        return risks 
 
    async def _fetch_exchange_risk(self, exchange: str) -> List[PositionRisk]:
        """获取单个交易所的风险仓位"""
        positions = await self.apis[exchange].fetch_positions() 
        risk_positions = []
        
        for pos in positions:
            # 跳过零仓位 
            if pos['size'] == 0:
                continue
                
            # 计算风险指标
            risk_distance = (
                (pos['mark_price'] - pos['liq_price']) / pos['mark_price']
                if pos['side'] == 'long' else
                (pos['liq_price'] - pos['mark_price']) / pos['mark_price']
            )
            
            # 只监控高风险仓位 
            if risk_distance < self.config['risk_threshold']: 
                risk_positions.append(PositionRisk( 
                    exchange=exchange,
                    symbol=pos['symbol'],
                    size=pos['size'],
                    side=pos['side'],
                    entry_price=pos['entry_price'],
                    mark_price=pos['mark_price'],
                    liq_price=pos['liq_price'],
                    margin_ratio=pos['margin_ratio'],
                    risk_distance=risk_distance,
                    notional_value=abs(pos['size'] * pos['mark_price'])
                ))
        
        return risk_positions
 
    async def _analyze_risk(self, risks: Dict[str, List[PositionRisk]]) -> Optional[Dict]:
        """分析风险并生成处置策略"""
        if not any(risks.values()): 
            return None
 
        # 找出风险最高的仓位 
        highest_risk = max(
            (pos for exchange_risks in risks.values()  for pos in exchange_risks),
            key=lambda x: (x.margin_ratio,  -x.risk_distance) 
        )
 
        # 确定风险等级
        risk_level = self._classify_risk(highest_risk.risk_distance) 
 
        # 生成基础清算策略
        strategy = {
            'primary': {
                'exchange': highest_risk.exchange, 
                'symbol': highest_risk.symbol, 
                'side': 'sell' if highest_risk.side  == 'long' else 'buy',
                'amount': self._calculate_liquidation_amount(
                    highest_risk.size,  
                    risk_level
                ),
                'order_type': 'market' if risk_level == RiskLevel.CRITICAL else 'limit',
                'urgency': risk_level.name  
            },
            'hedges': []
        }
 
        # 添加跨交易所对冲
        if self.config['cross_hedge']['enabled']: 
            await self._add_hedge_orders(strategy, highest_risk)
 
        # 添加相关性对冲 
        if self.config['correlation_hedge']['enabled']: 
            await self._add_correlation_hedge(strategy, highest_risk)
 
        return strategy 
 
    async def _add_hedge_orders(self, strategy: Dict, risk: PositionRisk):
        """添加跨交易所对冲订单"""
        hedge_exchanges = [
            e for e in self.apis.keys()  
            if e != risk.exchange  
        ]
        
        for exchange in hedge_exchanges:
            # 获取对冲合约符号
            hedge_symbol = self._get_hedge_symbol(risk.symbol,  exchange)
            if not hedge_symbol:
                continue
                
            # 计算对冲量
            hedge_amount = abs(risk.size)  * Decimal(
                self.config['cross_hedge']['ratio'] 
            ) / len(hedge_exchanges)
            
            strategy['hedges'].append({
                'exchange': exchange,
                'symbol': hedge_symbol,
                'side': 'buy' if risk.side  == 'long' else 'sell',
                'amount': hedge_amount,
                'order_type': 'limit',
                'reason': 'cross_exchange_hedge'
            })
 
    async def _add_correlation_hedge(self, strategy: Dict, risk: PositionRisk):
        """添加相关性对冲订单"""
        symbol = risk.symbol.split('-')[0].split('USDT')[0] 
        
        # 获取高相关性品种 
        if symbol not in self.correlations: 
            self.correlations[symbol]  = await get_symbol_correlation(
                symbol, 
                list(self.symbol_info.keys()) 
            )
            
        correlated_symbols = [
            s for s, corr in self.correlations[symbol].items() 
            if corr > self.config['correlation_hedge']['min_correlation']  
        ]
        
        for sym in correlated_symbols[:3]:  # 最多选3个相关品种 
            exchange = self._select_best_exchange(sym)
            hedge_symbol = self._get_hedge_symbol(sym, exchange)
            
            if not hedge_symbol:
                continue
                
            hedge_amount = abs(risk.size)  * Decimal(
                self.config['correlation_hedge']['ratio'] 
            ) / len(correlated_symbols)
            
            strategy['hedges'].append({
                'exchange': exchange,
                'symbol': hedge_symbol,
                'side': 'buy' if risk.side  == 'long' else 'sell',
                'amount': hedge_amount,
                'order_type': 'limit',
                'reason': f'correlation_hedge({sym})'
            })
 
    async def _execute_strategy(self, strategy: Dict):
        """执行风险处置策略"""
        # 优先处理主仓位
        primary_task = asyncio.create_task( 
            self._execute_order(strategy['primary'])
        )
        
        # 并行执行对冲订单 
        hedge_tasks = [
            asyncio.create_task(self._execute_order(hedge)) 
            for hedge in strategy['hedges']
        ]
        
        # 等待所有订单完成
        results = await asyncio.gather( 
            primary_task, 
            *hedge_tasks,
            return_exceptions=True
        )
        
        # 记录结果 
        self._record_execution(strategy, results)
 
    async def _execute_order(self, order: Dict) -> Dict:
        """执行单个订单"""
        try:
            # 获取最新市场数据
            ticker = await self.apis[order['exchange']].fetch_ticker(order['symbol']) 
            
            # 计算订单价格
            if order['order_type'] == 'market':
                price = ticker['ask'] if order['side'] == 'buy' else ticker['bid']
            else:
                price = self._calculate_limit_price(
                    order['side'], 
                    ticker,
                    order.get('urgency',  'MEDIUM')
                )
            
            # 发送订单
            order_result = await self.apis[order['exchange']].create_order( 
                symbol=order['symbol'],
                side=order['side'],
                type=order['order_type'],
                amount=float(order['amount']),
                price=float(price) if order['order_type'] == 'limit' else None,
                params={'reduce_only': order.get('reduce_only',  False)}
            )
            
            return {
                'status': 'filled',
                'order_id': order_result['id'],
                'price': price,
                **order
            }
            
        except Exception as e:
            self.logger.error(f" 订单执行失败 {order['exchange']} {order['symbol']}: {str(e)}")
            return {
                'status': 'failed',
                'error': str(e),
                **order
            }
 
    def _record_execution(self, strategy: Dict, results: List):
        """记录执行结果"""
        now = datetime.now() 
        records = []
        
        # 主订单记录 
        primary = results[0]
        records.append({ 
            'timestamp': now,
            'exchange': primary['exchange'],
            'symbol': primary['symbol'],
            'side': primary['side'],
            'amount': float(primary['amount']),
            'price': float(primary.get('price',  0)),
            'is_hedge': False,
            'status': primary['status']
        })
        
        # 对冲订单记录
        for res in results[1:]:
            records.append({ 
                'timestamp': now,
                'exchange': res['exchange'],
                'symbol': res['symbol'],
                'side': res['side'],
                'amount': float(res['amount']),
                'price': float(res.get('price',  0)),
                'is_hedge': True,
                'status': res['status']
            })
        
        # 更新历史记录
        self.liquidation_history  = pd.concat([ 
            self.liquidation_history, 
            pd.DataFrame(records)
        ], ignore_index=True)
 
    def _calculate_limit_price(self, side: str, ticker: Dict, urgency: str) -> Decimal:
        """计算最优限价单价格"""
        spread = ticker['ask'] - ticker['bid']
        
        if urgency == 'CRITICAL':
            # 紧急情况更激进的价格
            return ticker['ask'] * Decimal('0.998') if side == 'buy' else ticker['bid'] * Decimal('1.002')
        else:
            # 正常情况中间价
            mid = (ticker['ask'] + ticker['bid']) / 2
            return mid * Decimal('0.999') if side == 'buy' else mid * Decimal('1.001')
 
    def _classify_risk(self, risk_distance: Decimal) -> RiskLevel:
        """划分风险等级"""
        if risk_distance < Decimal('0.01'):
            return RiskLevel.CRITICAL 
        elif risk_distance < Decimal('0.03'):
            return RiskLevel.HIGH 
        elif risk_distance < Decimal('0.05'):
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
 
    def _calculate_liquidation_amount(self, size: Decimal, level: RiskLevel) -> Decimal:
        """计算清算量"""
        multiplier = {
            RiskLevel.CRITICAL: Decimal('1.0'),
            RiskLevel.HIGH: Decimal('0.7'),
            RiskLevel.MEDIUM: Decimal('0.5'),
            RiskLevel.LOW: Decimal('0.3')
        }[level]
        
        return abs(size) * multiplier 
 
    def _load_symbol_config(self) -> Dict:
        """加载品种配置"""
        return {
            'BTC': {
                'min_size': Decimal('0.001'),
                'contracts': {
                    'binance': 'BTCUSDT',
                    'okx': 'BTC-USDT-SWAP'
                }
            },
            # 其他品种配置...
        }
 
    def _get_hedge_symbol(self, symbol: str, exchange: str) -> Optional[str]:
        """获取对冲合约符号"""
        base = symbol.split('USDT')[0].split('-')[0] 
        if base not in self.symbol_info: 
            return None
        return self.symbol_info[base]['contracts'].get(exchange) 
 
    def _select_best_exchange(self, symbol: str) -> str:
        """选择流动性最好的交易所"""
        # 简化为选择第一个支持该品种的交易所 
        for exchange, contract in self.symbol_info[symbol]['contracts'].items(): 
            if exchange in self.apis: 
                return exchange 
        raise LiquidationError(f"没有可用的交易所支持 {symbol}")
 
    def get_risk_report(self) -> pd.DataFrame:
        """生成风险报告"""
        return self.liquidation_history.tail(20) 
 
if __name__ == '__main__':
    # 示例用法 
    from config import load_config 
    from exchanges import BinanceFutures, OKXFutures 
    
    async def main():
        config = load_config('risk_config.json') 
        apis = {
            'binance': BinanceFutures(config['binance']),
            'okx': OKXFutures(config['okx'])
        }
        
        liquidator = SmartLiquidator(config['liquidation'], apis)
        
        try:
            while True:
                await asyncio.sleep(1) 
        except KeyboardInterrupt:
            await liquidator.shutdown() 
            print(liquidator.get_risk_report()) 
    
    asyncio.run(main()) 