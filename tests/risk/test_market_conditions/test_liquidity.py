import unittest 
from unittest.mock  import patch, MagicMock
import numpy as np 
from datetime import datetime 
 
class LiquidityAnalyzer:
    """流动性分析核心逻辑"""
    def __init__(self, min_liquidity=10000, slippage_threshold=0.005):
        self.min_liquidity  = min_liquidity  # 最小流动性要求 (USD)
        self.slippage_threshold  = slippage_threshold  # 最大允许滑点 (0.5%)
        self.orderbook_data  = {}  # 各交易对订单簿数据 
        self.liquidity_history  = {}  # 流动性历史记录
        
    def update_orderbook(self, symbol, bids, asks, timestamp=None):
        """更新订单簿数据"""
        timestamp = timestamp or datetime.now() 
        self.orderbook_data[symbol]  = {
            'bids': sorted(bids, key=lambda x: -x[0]),  # 价格降序排列 
            'asks': sorted(asks, key=lambda x: x[0]),   # 价格升序排列
            'timestamp': timestamp
        }
        
    def calculate_liquidity(self, symbol, amount, side='both'):
        """计算指定交易量下的流动性指标"""
        if symbol not in self.orderbook_data: 
            return None 
            
        orderbook = self.orderbook_data[symbol] 
        results = {}
        
        if side in ['buy', 'both']:
            # 计算卖出流动性 (吃单买)
            cumulative_amount = 0
            cumulative_value = 0
            slippage = 0 
            best_ask = orderbook['asks'][0][0]
            
            for price, qty in orderbook['asks']:
                if cumulative_amount >= amount:
                    break
                    
                fill_qty = min(qty, amount - cumulative_amount)
                cumulative_amount += fill_qty 
                cumulative_value += fill_qty * price 
                
            if cumulative_amount > 0:
                avg_price = cumulative_value / cumulative_amount
                slippage = (avg_price - best_ask) / best_ask 
                
            results['buy'] = {
                'executable_amount': cumulative_amount,
                'average_price': avg_price if cumulative_amount > 0 else None,
                'slippage': slippage if cumulative_amount > 0 else None,
                'depth_reached': cumulative_value 
            }
            
        if side in ['sell', 'both']:
            # 计算买入流动性 (吃单卖)
            cumulative_amount = 0 
            cumulative_value = 0 
            slippage = 0
            best_bid = orderbook['bids'][0][0]
            
            for price, qty in orderbook['bids']:
                if cumulative_amount >= amount:
                    break
                    
                fill_qty = min(qty, amount - cumulative_amount)
                cumulative_amount += fill_qty 
                cumulative_value += fill_qty * price 
                
            if cumulative_amount > 0:
                avg_price = cumulative_value / cumulative_amount
                slippage = (best_bid - avg_price) / best_bid 
                
            results['sell'] = {
                'executable_amount': cumulative_amount,
                'average_price': avg_price if cumulative_amount > 0 else None,
                'slippage': slippage if cumulative_amount > 0 else None,
                'depth_reached': cumulative_value 
            }
            
        return results
        
    def check_liquidity(self, symbol, amount):
        """检查流动性是否充足"""
        liquidity = self.calculate_liquidity(symbol,  amount)
        if not liquidity:
            return False 
            
        alerts = []
        
        # 检查买入流动性
        if 'buy' in liquidity:
            buy_data = liquidity['buy']
            if buy_data['executable_amount'] < amount:
                alerts.append(f"Insufficient  buy liquidity ({buy_data['executable_amount']}/{amount})")
            elif buy_data['slippage'] > self.slippage_threshold: 
                alerts.append(f"High  buy slippage ({buy_data['slippage']*100:.2f}%)")
                
        # 检查卖出流动性 
        if 'sell' in liquidity:
            sell_data = liquidity['sell']
            if sell_data['executable_amount'] < amount:
                alerts.append(f"Insufficient  sell liquidity ({sell_data['executable_amount']}/{amount})")
            elif sell_data['slippage'] > self.slippage_threshold: 
                alerts.append(f"High  sell slippage ({sell_data['slippage']*100:.2f}%)")
                
        return alerts if alerts else True
        
    def get_liquidity_ranking(self, amount=1000):
        """获取流动性排名"""
        symbols = list(self.orderbook_data.keys()) 
        if not symbols:
            return None
            
        ranking = []
        for symbol in symbols:
            liquidity = self.calculate_liquidity(symbol,  amount)
            if not liquidity:
                continue
                
            # 计算综合流动性得分 (深度/滑点)
            buy_score = liquidity.get('buy',  {}).get('depth_reached', 0)
            sell_score = liquidity.get('sell',  {}).get('depth_reached', 0)
            total_score = buy_score + sell_score 
            
            ranking.append((symbol,  total_score))
            
        # 按流动性从高到低排序
        ranking.sort(key=lambda  x: x[1], reverse=True)
        return ranking
 
 
class TestLiquidityAnalyzer(unittest.TestCase):
    """流动性分析测试用例"""
    
    def setUp(self):
        self.analyzer  = LiquidityAnalyzer()
        
    def test_update_orderbook(self):
        """测试订单簿更新"""
        bids = [(30000, 2), (29900, 3)]
        asks = [(30100, 2), (30200, 3)]
        self.analyzer.update_orderbook("BTC/USDT",  bids, asks)
        
        self.assertIn("BTC/USDT",  self.analyzer.orderbook_data) 
        self.assertEqual(len(self.analyzer.orderbook_data["BTC/USDT"]['bids']),  2)
        self.assertEqual(self.analyzer.orderbook_data["BTC/USDT"]['asks'][0][0],  30100)
        
    def test_calculate_liquidity_sufficient(self):
        """测试充足流动性计算"""
        # 设置深度足够的订单簿 
        bids = [(30000, 5), (29900, 5)]  # 5个@30000, 5个@29900
        asks = [(30100, 5), (30200, 5)]  # 5个@30100, 5个@30200 
        self.analyzer.update_orderbook("BTC/USDT",  bids, asks)
        
        # 计算买入3个BTC的流动性
        result = self.analyzer.calculate_liquidity("BTC/USDT",  3, 'buy')
        self.assertIsNotNone(result) 
        buy_data = result['buy']
        self.assertEqual(buy_data['executable_amount'],  3)
        self.assertEqual(buy_data['average_price'],  30100)
        self.assertEqual(buy_data['slippage'],  0)  # 全部吃最优价 
        
    def test_calculate_liquidity_insufficient(self):
        """测试不足流动性计算"""
        # 设置深度不足的订单簿 
        bids = [(30000, 1), (29900, 1)]  # 总共只有2个BTC流动性 
        asks = [(30100, 1), (30200, 1)]
        self.analyzer.update_orderbook("BTC/USDT",  bids, asks)
        
        # 尝试卖出3个BTC
        result = self.analyzer.calculate_liquidity("BTC/USDT",  3, 'sell')
        sell_data = result['sell']
        self.assertEqual(sell_data['executable_amount'],  2)  # 只能成交2个 
        self.assertAlmostEqual(sell_data['average_price'],  29966.666, places=2)
        
    def test_check_liquidity(self):
        """测试流动性检查"""
        # 设置订单簿
        bids = [(30000, 2), (29900, 2)]
        asks = [(30100, 2), (30200, 2)]
        self.analyzer.update_orderbook("BTC/USDT",  bids, asks)
        
        # 检查充足流动性
        self.assertTrue(self.analyzer.check_liquidity("BTC/USDT",  1))
        
        # 检查不足流动性 
        alerts = self.analyzer.check_liquidity("BTC/USDT",  3)
        self.assertEqual(len(alerts),  2)  # 买卖双方都不足
        self.assertIn("Insufficient  buy liquidity (2/3)", alerts)
        
    def test_slippage_check(self):
        """测试滑点检查"""
        # 设置浅订单簿 (大单会产生高滑点)
        bids = [(30000, 0.1), (29900, 0.1)]
        asks = [(30100, 0.1), (30200, 0.1)]
        self.analyzer.update_orderbook("BTC/USDT",  bids, asks)
        
        # 检查滑点 (买入1BTC)
        alerts = self.analyzer.check_liquidity("BTC/USDT",  1)
        self.assertIn("High  buy slippage", alerts[0])
        
    def test_get_liquidity_ranking(self):
        """测试流动性排名"""
        # 添加多个交易对 
        self.analyzer.update_orderbook("BTC/USDT",  
            [(30000, 10), (29900, 10)], 
            [(30100, 10), (30200, 10)])
        self.analyzer.update_orderbook("ETH/USDT",  
            [(2000, 20), (1990, 20)], 
            [(2010, 20), (2020, 20)])
            
        ranking = self.analyzer.get_liquidity_ranking(amount=15) 
        self.assertEqual(len(ranking),  2)
        self.assertEqual(ranking[0][0],  "ETH/USDT")  # ETH流动性更好 
 
 
class MockExchange:
    """模拟交易所API"""
    def __init__(self):
        self.orderbooks  = {
            "BTC/USDT": {
                'bids': [(30000, 1), (29900, 2)],
                'asks': [(30100, 1), (30200, 2)]
            },
            "ETH/USDT": {
                'bids': [(2000, 10), (1990, 10)],
                'asks': [(2010, 10), (2020, 10)]
            }
        }
        
    def get_orderbook(self, symbol):
        return self.orderbooks.get(symbol) 
 
 
class TestIntegration(unittest.TestCase):
    """集成测试流动性分析全流程"""
    
    def setUp(self):
        self.analyzer  = LiquidityAnalyzer()
        self.exchange  = MockExchange()
        
    def test_full_cycle(self):
        """测试完整流动性分析周期"""
        # 1. 从交易所获取订单簿 
        for symbol in ["BTC/USDT", "ETH/USDT"]:
            orderbook = self.exchange.get_orderbook(symbol) 
            self.analyzer.update_orderbook( 
                symbol, 
                orderbook['bids'], 
                orderbook['asks']
            )
        
        # 2. 计算流动性 
        btc_liquidity = self.analyzer.calculate_liquidity("BTC/USDT",  2)
        self.assertIsNotNone(btc_liquidity) 
        
        # 3. 检查流动性
        self.assertTrue(self.analyzer.check_liquidity("ETH/USDT",  5))
        alerts = self.analyzer.check_liquidity("BTC/USDT",  2)
        self.assertIn("Insufficient  buy liquidity (1/2)", alerts)
        
        # 4. 获取流动性排名 
        ranking = self.analyzer.get_liquidity_ranking() 
        self.assertEqual(ranking[0][0],  "ETH/USDT")
 
 
if __name__ == '__main__':
    unittest.main(verbosity=2) 