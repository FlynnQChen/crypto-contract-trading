import unittest 
from unittest.mock  import patch, MagicMock
import pandas as pd 
from datetime import datetime, timedelta 
 
class FundingRateArbitrage:
    """资金费率套利核心逻辑"""
    def __init__(self, min_profit_rate=0.0005, max_funding_rate=0.01):
        self.min_profit_rate  = min_profit_rate  # 最小套利利润 (0.05%)
        self.max_funding_rate  = max_funding_rate  # 最大允许资金费率 (1%)
        self.exchanges  = {}  # 连接的交易所
        self.positions  = {}  # 当前持仓
        self.history  = []  # 历史交易记录 
        
    def add_exchange(self, name, api):
        """添加交易所连接"""
        self.exchanges[name]  = api 
        
    def get_funding_rates(self, symbol):
        """获取各交易所资金费率"""
        rates = {}
        for name, api in self.exchanges.items(): 
            try:
                rate = api.get_funding_rate(symbol) 
                rates[name] = {
                    'rate': rate,
                    'timestamp': datetime.now() 
                }
            except Exception as e:
                print(f"Error getting funding rate from {name}: {str(e)}")
                continue
        return rates
        
    def find_arbitrage_opportunity(self, symbol):
        """寻找资金费率套利机会"""
        rates = self.get_funding_rates(symbol) 
        if len(rates) < 2:
            return None 
            
        # 找出最高正费率和最低负费率 
        positive_rates = {k: v for k, v in rates.items()  if v['rate'] > 0}
        negative_rates = {k: v for k, v in rates.items()  if v['rate'] < 0}
        
        if not positive_rates or not negative_rates:
            return None 
            
        highest_positive = max(positive_rates.items(),  key=lambda x: x[1]['rate'])
        lowest_negative = min(negative_rates.items(),  key=lambda x: x[1]['rate'])
        
        # 计算套利利润
        spread = highest_positive[1]['rate'] - lowest_negative[1]['rate']
        
        if (spread >= self.min_profit_rate  and 
            abs(highest_positive[1]['rate']) <= self.max_funding_rate  and 
            abs(lowest_negative[1]['rate']) <= self.max_funding_rate): 
            return {
                'symbol': symbol,
                'long_exchange': lowest_negative[0],  # 负费率交易所做多 
                'short_exchange': highest_positive[0],  # 正费率交易所做空
                'funding_spread': spread,
                'long_rate': lowest_negative[1]['rate'],
                'short_rate': highest_positive[1]['rate'],
                'timestamp': datetime.now() 
            }
        return None
        
    def execute_arbitrage(self, symbol, amount):
        """执行资金费率套利"""
        opportunity = self.find_arbitrage_opportunity(symbol) 
        if not opportunity:
            return False
            
        long_exchange = self.exchanges[opportunity['long_exchange']] 
        short_exchange = self.exchanges[opportunity['short_exchange']] 
        
        try:
            # 在负费率交易所做多
            long_order = long_exchange.create_order( 
                symbol=symbol,
                side='buy',
                amount=amount,
                type='market'
            )
            
            # 在正费率交易所做空 
            short_order = short_exchange.create_order( 
                symbol=symbol,
                side='sell',
                amount=amount,
                type='market'
            )
            
            # 记录交易 
            trade_id = f"FR-ARB-{datetime.now().strftime('%Y%m%d-%H%M%S')}" 
            self.positions[trade_id]  = {
                'symbol': symbol,
                'long_order': long_order,
                'short_order': short_order,
                'amount': amount,
                'expected_funding': amount * opportunity['funding_spread'],
                'entry_time': datetime.now(), 
                'funding_rates': {
                    'long': opportunity['long_rate'],
                    'short': opportunity['short_rate']
                }
            }
            
            return trade_id
        except Exception as e:
            print(f"Funding arbitrage execution failed: {str(e)}")
            # 尝试平仓
            self.close_positions(trade_id) 
            return False 
            
    def close_positions(self, trade_id):
        """平仓套利头寸"""
        if trade_id not in self.positions: 
            return False
            
        position = self.positions[trade_id] 
        long_exchange = self.exchanges[position['long_order']['exchange']] 
        short_exchange = self.exchanges[position['short_order']['exchange']] 
        
        try:
            # 平多仓
            long_close = long_exchange.create_order( 
                symbol=position['symbol'],
                side='sell',
                amount=position['amount'],
                type='market'
            )
            
            # 平空仓
            short_close = short_exchange.create_order( 
                symbol=position['symbol'],
                side='buy',
                amount=position['amount'],
                type='market'
            )
            
            # 记录平仓
            position['exit_time'] = datetime.now() 
            position['closed'] = True 
            self.history.append(position) 
            del self.positions[trade_id] 
            
            return True
        except Exception as e:
            print(f"Position close failed: {str(e)}")
            return False 
 
 
class MockExchangeAPI:
    """模拟交易所API"""
    def __init__(self, name, funding_rate):
        self.name  = name
        self.funding_rate  = funding_rate 
        self.orders  = {}
        self.order_id  = 1
        
    def get_funding_rate(self, symbol):
        return self.funding_rate  
        
    def create_order(self, symbol, side, amount, type):
        order = {
            'id': f"{self.name}-{self.order_id}", 
            'symbol': symbol,
            'side': side,
            'amount': amount,
            'type': type,
            'exchange': self.name, 
            'status': 'filled',
            'timestamp': datetime.now() 
        }
        self.orders[order['id']]  = order
        self.order_id  += 1
        return order
 
 
class TestFundingRateArbitrage(unittest.TestCase):
    """资金费率套利测试用例"""
    
    def setUp(self):
        self.arb  = FundingRateArbitrage()
        self.binance  = MockExchangeAPI("binance", 0.0005)  # 正费率 
        self.ftx  = MockExchangeAPI("ftx", -0.0003)         # 负费率 
        self.okx  = MockExchangeAPI("okx", 0.0002)          # 低正费率 
        
    def test_add_exchange(self):
        """测试添加交易所"""
        self.arb.add_exchange("binance",  self.binance) 
        self.arb.add_exchange("ftx",  self.ftx) 
        self.assertEqual(len(self.arb.exchanges),  2)
        
    def test_get_funding_rates(self):
        """测试获取资金费率"""
        self.arb.add_exchange("binance",  self.binance) 
        self.arb.add_exchange("ftx",  self.ftx) 
        
        rates = self.arb.get_funding_rates("BTC/USDT") 
        self.assertEqual(len(rates),  2)
        self.assertEqual(rates["binance"]["rate"],  0.0005)
        self.assertEqual(rates["ftx"]["rate"],  -0.0003)
        
    def test_find_arbitrage_opportunity(self):
        """测试寻找套利机会"""
        self.arb.add_exchange("binance",  self.binance) 
        self.arb.add_exchange("ftx",  self.ftx) 
        self.arb.add_exchange("okx",  self.okx) 
        
        # 计算套利空间: 0.0005 - (-0.0003) = 0.0008
        opportunity = self.arb.find_arbitrage_opportunity("BTC/USDT") 
        self.assertIsNotNone(opportunity) 
        self.assertEqual(opportunity["long_exchange"],  "ftx")
        self.assertEqual(opportunity["short_exchange"],  "binance")
        self.assertAlmostEqual(opportunity["funding_spread"],  0.0008, places=6)
        
    def test_min_profit_threshold(self):
        """测试最小利润阈值"""
        self.arb.min_profit_rate  = 0.001  # 设置更高阈值 
        
        self.arb.add_exchange("binance",  self.binance) 
        self.arb.add_exchange("ftx",  self.ftx) 
        
        opportunity = self.arb.find_arbitrage_opportunity("BTC/USDT") 
        self.assertIsNone(opportunity)   # 0.0008 < 0.001 
        
    def test_max_funding_rate(self):
        """测试最大资金费率限制"""
        # 设置极端资金费率
        high_rate_exchange = MockExchangeAPI("high", 0.02)  # 2%
        self.arb.add_exchange("high",  high_rate_exchange)
        self.arb.add_exchange("ftx",  self.ftx) 
        
        opportunity = self.arb.find_arbitrage_opportunity("BTC/USDT") 
        self.assertIsNone(opportunity)   # 2% > max_funding_rate
        
    def test_execute_arbitrage(self):
        """测试执行套利"""
        self.arb.add_exchange("binance",  self.binance) 
        self.arb.add_exchange("ftx",  self.ftx) 
        
        trade_id = self.arb.execute_arbitrage("BTC/USDT",  1)
        self.assertIsNotNone(trade_id) 
        self.assertEqual(len(self.arb.positions),  1)
        
        position = self.arb.positions[trade_id] 
        self.assertEqual(position["amount"],  1)
        self.assertAlmostEqual(position["expected_funding"],  0.0008, places=6)
        
    def test_close_positions(self):
        """测试平仓"""
        self.arb.add_exchange("binance",  self.binance) 
        self.arb.add_exchange("ftx",  self.ftx) 
        
        trade_id = self.arb.execute_arbitrage("BTC/USDT",  1)
        result = self.arb.close_positions(trade_id) 
        self.assertTrue(result) 
        self.assertEqual(len(self.arb.positions),  0)
        self.assertEqual(len(self.arb.history),  1)
 
 
class TestIntegration(unittest.TestCase):
    """集成测试资金费率套利全流程"""
    
    def setUp(self):
        self.arb  = FundingRateArbitrage()
        self.binance  = MockExchangeAPI("binance", 0.0005)
        self.ftx  = MockExchangeAPI("ftx", -0.0003)
        self.arb.add_exchange("binance",  self.binance) 
        self.arb.add_exchange("ftx",  self.ftx) 
        
    def test_full_cycle(self):
        """测试完整套利周期"""
        # 1. 寻找套利机会
        opportunity = self.arb.find_arbitrage_opportunity("BTC/USDT") 
        self.assertIsNotNone(opportunity) 
        
        # 2. 执行套利
        trade_id = self.arb.execute_arbitrage("BTC/USDT",  1)
        self.assertIsNotNone(trade_id) 
        
        # 3. 验证持仓 
        self.assertEqual(len(self.arb.positions),  1)
        position = self.arb.positions[trade_id] 
        self.assertEqual(position["symbol"],  "BTC/USDT")
        
        # 4. 平仓 
        result = self.arb.close_positions(trade_id) 
        self.assertTrue(result) 
        self.assertEqual(len(self.arb.history),  1)
 
 
if __name__ == '__main__':
    unittest.main(verbosity=2) 