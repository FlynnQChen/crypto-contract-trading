import unittest
from unittest.mock  import patch, MagicMock 
import numpy as np 
from datetime import datetime
 
class SpreadCalculator:
    """价差计算核心逻辑"""
    def __init__(self, min_profit_threshold=0.001, max_spread=0.05):
        self.min_profit_threshold  = min_profit_threshold  # 最小盈利阈值 (0.1%)
        self.max_spread  = max_spread  # 最大允许价差 (5%)
        self.price_data  = {}  # 各交易所价格数据
        self.spread_history  = []  # 价差历史记录
        
    def update_prices(self, exchange, symbol, bid, ask):
        """更新交易所价格数据"""
        if exchange not in self.price_data: 
            self.price_data[exchange]  = {}
            
        self.price_data[exchange][symbol]  = {
            'bid': bid,
            'ask': ask,
            'timestamp': datetime.now() 
        }
        
    def calculate_spread(self, symbol, exchange1, exchange2):
        """计算两个交易所间的价差"""
        try:
            price1 = self.price_data[exchange1][symbol] 
            price2 = self.price_data[exchange2][symbol] 
            
            bid_diff = price2['bid'] - price1['ask']  # 交易所2买价 - 交易所1卖价
            ask_diff = price1['bid'] - price2['ask']  # 交易所1买价 - 交易所2卖价 
            
            max_diff = max(bid_diff, ask_diff)
            
            if bid_diff > 0:
                direction = "buy_at_1_sell_at_2"
                profit_pct = bid_diff / price1['ask']
            elif ask_diff > 0:
                direction = "buy_at_2_sell_at_1"
                profit_pct = ask_diff / price2['ask']
            else:
                return None
                
            return {
                'symbol': symbol,
                'exchange_pair': (exchange1, exchange2),
                'max_diff': max_diff,
                'profit_pct': profit_pct,
                'direction': direction,
                'timestamp': datetime.now() 
            }
        except KeyError:
            return None
            
    def find_arbitrage_opportunity(self, symbol, exchanges):
        """寻找最佳套利机会"""
        if len(exchanges) < 2:
            return None
            
        best_opportunity = None 
        for i in range(len(exchanges)):
            for j in range(i+1, len(exchanges)):
                spread = self.calculate_spread(symbol,  exchanges[i], exchanges[j])
                if not spread:
                    continue 
                    
                # 检查是否满足条件 
                if (spread['profit_pct'] >= self.min_profit_threshold  and 
                    spread['profit_pct'] <= self.max_spread): 
                    
                    if not best_opportunity or spread['profit_pct'] > best_opportunity['profit_pct']:
                        best_opportunity = spread 
                        
        return best_opportunity 
        
    def monitor_spread(self, symbol, exchanges, window_size=10):
        """监控价差并返回统计信息"""
        if len(self.spread_history)  < window_size:
            return None
            
        recent_spreads = [x['profit_pct'] for x in self.spread_history[-window_size:]  if x['symbol'] == symbol]
        if not recent_spreads:
            return None
            
        return {
            'symbol': symbol,
            'mean': np.mean(recent_spreads), 
            'std': np.std(recent_spreads), 
            'max': max(recent_spreads),
            'min': min(recent_spreads),
            'current': recent_spreads[-1]
        }
 
 
class TestSpreadCalculator(unittest.TestCase):
    """价差计算测试用例"""
    
    def setUp(self):
        self.calculator  = SpreadCalculator()
        
    def test_update_prices(self):
        """测试价格更新"""
        self.calculator.update_prices("binance",  "BTC/USDT", 30000, 30010)
        self.calculator.update_prices("ftx",  "BTC/USDT", 30005, 30015)
        
        self.assertIn("binance",  self.calculator.price_data) 
        self.assertIn("ftx",  self.calculator.price_data) 
        self.assertEqual(self.calculator.price_data["binance"]["BTC/USDT"]["bid"],  30000)
        
    def test_calculate_spread_no_arbitrage(self):
        """测试无套利空间的价差计算"""
        self.calculator.update_prices("binance",  "BTC/USDT", 30000, 30010)
        self.calculator.update_prices("ftx",  "BTC/USDT", 30005, 30015)
        
        spread = self.calculator.calculate_spread("BTC/USDT",  "binance", "ftx")
        self.assertIsNone(spread) 
        
    def test_calculate_spread_with_arbitrage(self):
        """测试有套利空间的价差计算"""
        self.calculator.update_prices("binance",  "BTC/USDT", 30000, 30010)
        self.calculator.update_prices("ftx",  "BTC/USDT", 30020, 30030)
        
        spread = self.calculator.calculate_spread("BTC/USDT",  "binance", "ftx")
        self.assertIsNotNone(spread) 
        self.assertEqual(spread['direction'],  "buy_at_1_sell_at_2")
        self.assertAlmostEqual(spread['profit_pct'],  (30020-30010)/30010, places=4)
        
    def test_find_arbitrage_opportunity(self):
        """测试套利机会发现"""
        # 设置3个交易所的价格
        self.calculator.update_prices("binance",  "BTC/USDT", 30000, 30010)
        self.calculator.update_prices("ftx",  "BTC/USDT", 30020, 30030)  # 最佳套利 
        self.calculator.update_prices("okx",  "BTC/USDT", 30015, 30025)
        
        opportunity = self.calculator.find_arbitrage_opportunity( 
            "BTC/USDT", ["binance", "ftx", "okx"]
        )
        
        self.assertIsNotNone(opportunity) 
        self.assertEqual(opportunity['exchange_pair'],  ("binance", "ftx"))
        self.assertAlmostEqual(opportunity['profit_pct'],  0.003332, places=6)
        
    def test_min_profit_threshold(self):
        """测试最小盈利阈值"""
        self.calculator.min_profit_threshold  = 0.005  # 0.5%
        
        # 设置低于阈值的套利空间 (0.33%)
        self.calculator.update_prices("binance",  "BTC/USDT", 30000, 30010)
        self.calculator.update_prices("ftx",  "BTC/USDT", 30020, 30030)
        
        opportunity = self.calculator.find_arbitrage_opportunity( 
            "BTC/USDT", ["binance", "ftx"]
        )
        self.assertIsNone(opportunity) 
        
    def test_max_spread_limit(self):
        """测试最大价差限制"""
        self.calculator.max_spread  = 0.01  # 1%
        
        # 设置超过限制的价差 (3.33%)
        self.calculator.update_prices("binance",  "BTC/USDT", 30000, 30010)
        self.calculator.update_prices("ftx",  "BTC/USDT", 31000, 31010)
        
        opportunity = self.calculator.find_arbitrage_opportunity( 
            "BTC/USDT", ["binance", "ftx"]
        )
        self.assertIsNone(opportunity) 
        
    def test_monitor_spread(self):
        """测试价差监控"""
        # 添加历史数据 
        for i in range(10):
            spread = {
                'symbol': "BTC/USDT",
                'profit_pct': 0.001 + i*0.0001,
                'timestamp': datetime.now() 
            }
            self.calculator.spread_history.append(spread) 
            
        stats = self.calculator.monitor_spread("BTC/USDT",  [], window_size=10)
        self.assertIsNotNone(stats) 
        self.assertAlmostEqual(stats['mean'],  0.00145, places=5)
        self.assertAlmostEqual(stats['max'],  0.0019, places=5)
        self.assertAlmostEqual(stats['current'],  0.0019, places=5)
 
 
class TestIntegration(unittest.TestCase):
    """集成测试价差监控全流程"""
    
    def setUp(self):
        self.calculator  = SpreadCalculator()
        self.exchanges  = ["binance", "ftx", "okx"]
        
    def test_full_cycle(self):
        """测试完整价差监控周期"""
        # 1. 更新价格数据 
        self.calculator.update_prices("binance",  "BTC/USDT", 30000, 30010)
        self.calculator.update_prices("ftx",  "BTC/USDT", 30020, 30030)
        self.calculator.update_prices("okx",  "BTC/USDT", 30015, 30025)
        
        # 2. 计算价差
        spread = self.calculator.calculate_spread("BTC/USDT",  "binance", "ftx")
        self.assertIsNotNone(spread) 
        
        # 3. 记录价差
        self.calculator.spread_history.append(spread) 
        
        # 4. 寻找套利机会
        opportunity = self.calculator.find_arbitrage_opportunity("BTC/USDT",  self.exchanges) 
        self.assertIsNotNone(opportunity) 
        
        # 5. 监控价差统计
        for _ in range(9):  # 补足10个数据点
            self.calculator.spread_history.append(spread) 
            
        stats = self.calculator.monitor_spread("BTC/USDT",  self.exchanges) 
        self.assertIsNotNone(stats) 
        self.assertAlmostEqual(stats['mean'],  spread['profit_pct'], places=6)
 
 
if __name__ == '__main__':
    unittest.main(verbosity=2) 