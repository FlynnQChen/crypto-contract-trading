import unittest 
from unittest.mock  import patch, MagicMock 
import numpy as np
import pandas as pd 
from datetime import datetime, timedelta 
 
class VolatilityCalculator:
    """波动率计算核心逻辑"""
    def __init__(self, window_size=20, threshold_multiplier=2.0):
        self.window_size  = window_size  # 计算窗口大小
        self.threshold_multiplier  = threshold_multiplier  # 波动率阈值乘数
        self.price_history  = {}  # 各交易对价格历史
        self.volatility_data  = {}  # 波动率计算结果 
        
    def update_price(self, symbol, price, timestamp=None):
        """更新价格数据"""
        if symbol not in self.price_history: 
            self.price_history[symbol]  = []
            
        timestamp = timestamp or datetime.now() 
        self.price_history[symbol].append({ 
            'price': price,
            'timestamp': timestamp 
        })
        
        # 保持窗口大小 
        if len(self.price_history[symbol])  > self.window_size: 
            self.price_history[symbol]  = self.price_history[symbol][-self.window_size:] 
            
    def calculate_volatility(self, symbol):
        """计算指定交易对的波动率"""
        if symbol not in self.price_history  or len(self.price_history[symbol])  < 2:
            return None
            
        prices = [x['price'] for x in self.price_history[symbol]] 
        returns = np.diff(prices)  / prices[:-1]  # 计算收益率 
        
        volatility = np.std(returns)   # 波动率(标准差)
        mean_return = np.mean(returns) 
        
        self.volatility_data[symbol]  = {
            'volatility': volatility,
            'mean_return': mean_return,
            'threshold': mean_return + self.threshold_multiplier  * volatility,
            'last_price': prices[-1],
            'timestamp': datetime.now() 
        }
        
        return self.volatility_data[symbol] 
        
    def check_volatility_alert(self, symbol):
        """检查波动率警报"""
        if symbol not in self.volatility_data: 
            self.calculate_volatility(symbol) 
            if symbol not in self.volatility_data: 
                return None
                
        last_data = self.volatility_data[symbol] 
        current_price = self.price_history[symbol][-1]['price'] 
        prev_price = self.price_history[symbol][-2]['price'] 
        current_return = (current_price - prev_price) / prev_price
        
        if abs(current_return) > last_data['threshold']:
            return {
                'symbol': symbol,
                'current_return': current_return,
                'threshold': last_data['threshold'],
                'volatility': last_data['volatility'],
                'direction': 'up' if current_return > 0 else 'down',
                'timestamp': datetime.now() 
            }
        return None 
        
    def get_volatility_ranking(self, min_window=10):
        """获取波动率排名"""
        valid_symbols = [s for s in self.price_history  
                        if len(self.price_history[s])  >= min_window]
                        
        if not valid_symbols:
            return None
            
        ranking = []
        for symbol in valid_symbols:
            vol_data = self.calculate_volatility(symbol) 
            if vol_data:
                ranking.append((symbol,  vol_data['volatility']))
                
        # 按波动率从高到低排序
        ranking.sort(key=lambda  x: x[1], reverse=True)
        return ranking 
 
 
class TestVolatilityCalculator(unittest.TestCase):
    """波动率计算测试用例"""
    
    def setUp(self):
        self.vc  = VolatilityCalculator(window_size=5)
        
    def test_update_price(self):
        """测试价格更新"""
        self.vc.update_price("BTC/USDT",  30000)
        self.vc.update_price("ETH/USDT",  2000)
        
        self.assertIn("BTC/USDT",  self.vc.price_history) 
        self.assertEqual(len(self.vc.price_history["BTC/USDT"]),  1)
        
    def test_window_size_limit(self):
        """测试窗口大小限制"""
        for i in range(10):
            self.vc.update_price("BTC/USDT",  30000 + i*100)
            
        self.assertEqual(len(self.vc.price_history["BTC/USDT"]),  5)
        self.assertEqual(self.vc.price_history["BTC/USDT"][0]['price'],  30050)
        
    def test_calculate_volatility(self):
        """测试波动率计算"""
        prices = [100, 102, 101, 105, 103]
        for p in prices:
            self.vc.update_price("TEST",  p)
            
        result = self.vc.calculate_volatility("TEST") 
        self.assertIsNotNone(result) 
        self.assertAlmostEqual(result['volatility'],  0.018708, places=6)
        
    def test_check_volatility_alert_no_alert(self):
        """测试无波动率警报的情况"""
        prices = [100, 101, 102, 101, 100]
        for p in prices:
            self.vc.update_price("TEST",  p)
            
        alert = self.vc.check_volatility_alert("TEST") 
        self.assertIsNone(alert) 
        
    def test_check_volatility_alert_with_alert(self):
        """测试触发波动率警报"""
        # 设置波动率数据 (阈值 ~0.02)
        prices = [100, 101, 102, 101, 100]
        for p in prices:
            self.vc.update_price("TEST",  p)
        self.vc.calculate_volatility("TEST") 
        
        # 添加一个大幅波动 (5%)
        self.vc.update_price("TEST",  105)
        alert = self.vc.check_volatility_alert("TEST") 
        
        self.assertIsNotNone(alert) 
        self.assertEqual(alert['direction'],  'up')
        self.assertAlmostEqual(alert['current_return'],  0.05, places=4)
        
    def test_get_volatility_ranking(self):
        """测试波动率排名"""
        # 添加多个交易对数据
        pairs = {
            "BTC/USDT": [30000, 30100, 30200, 30300, 30400],  # 低波动
            "ETH/USDT": [2000, 2050, 1950, 2100, 1900],      # 高波动
            "SOL/USDT": [50, 52, 51, 53, 49]                 # 中波动
        }
        
        for symbol in pairs:
            for p in pairs[symbol]:
                self.vc.update_price(symbol,  p)
                
        ranking = self.vc.get_volatility_ranking() 
        self.assertEqual(len(ranking),  3)
        self.assertEqual(ranking[0][0],  "ETH/USDT")  # 波动率最高 
        self.assertEqual(ranking[-1][0],  "BTC/USDT") # 波动率最低
 
 
class TestIntegration(unittest.TestCase):
    """集成测试波动率监控全流程"""
    
    def setUp(self):
        self.vc  = VolatilityCalculator(window_size=10)
        
    def test_full_cycle(self):
        """测试完整波动率监控周期"""
        # 1. 模拟价格更新 
        test_prices = [
            100, 101, 102, 101, 100, 
            99, 98, 99, 101, 105  # 最后一步大幅上涨 
        ]
        
        for i, p in enumerate(test_prices):
            self.vc.update_price("TEST",  p)
            
            # 2. 定期计算波动率
            if i % 3 == 0:
                self.vc.calculate_volatility("TEST") 
                
            # 3. 检查警报 
            alert = self.vc.check_volatility_alert("TEST") 
            if i == len(test_prices)-1:  # 最后一步应触发警报 
                self.assertIsNotNone(alert) 
                self.assertEqual(alert['direction'],  'up')
            elif alert:
                self.fail(" 过早触发波动率警报")
                
        # 4. 获取波动率排名 
        ranking = self.vc.get_volatility_ranking() 
        self.assertEqual(ranking[0][0],  "TEST")
 
 
class MockMarketData:
    """模拟市场数据源"""
    def __init__(self):
        self.prices  = {
            "BTC/USDT": 30000,
            "ETH/USDT": 2000,
            "SOL/USDT": 50 
        }
        
    def get_current_price(self, symbol):
        return self.prices.get(symbol) 
        
    def generate_random_move(self, symbol, max_change=0.1):
        """模拟随机价格变动"""
        change = (np.random.random()  - 0.5) * 2 * max_change 
        self.prices[symbol]  *= (1 + change)
        return self.prices[symbol] 
 
 
class TestWithMockMarket(unittest.TestCase):
    """使用模拟市场数据的测试"""
    
    def setUp(self):
        self.vc  = VolatilityCalculator(window_size=20)
        self.market  = MockMarketData()
        
    def test_with_random_data(self):
        """测试随机市场数据下的波动率计算"""
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        
        # 模拟100次价格更新 
        for _ in range(100):
            for symbol in symbols:
                price = self.market.generate_random_move(symbol,  max_change=0.05)
                self.vc.update_price(symbol,  price)
                
            # 每10步计算一次波动率
            if _ % 10 == 0:
                for symbol in symbols:
                    self.vc.calculate_volatility(symbol) 
                    
        # 验证波动率排名合理性 
        ranking = self.vc.get_volatility_ranking() 
        self.assertEqual(len(ranking),  3)
        
        # ETH通常比BTC波动更大 
        eth_rank = [x[0] for x in ranking].index("ETH/USDT")
        btc_rank = [x[0] for x in ranking].index("BTC/USDT")
        self.assertLess(eth_rank,  btc_rank)
 
 
if __name__ == '__main__':
    unittest.main(verbosity=2) 