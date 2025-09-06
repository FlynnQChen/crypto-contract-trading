import unittest
from unittest.mock  import patch, MagicMock 
import pandas as pd
from datetime import datetime, timedelta
 
class Arbitrator:
    """套利策略核心逻辑"""
    def __init__(self, min_profit=0.005, max_spread=0.02, max_position=10000):
        self.min_profit  = min_profit  # 最小套利利润 (0.5%)
        self.max_spread  = max_spread  # 最大允许价差 (2%)
        self.max_position  = max_position  # 最大头寸限制 
        self.active_arbitrages  = {}  # 活跃套利交易
        self.history  = []  # 历史交易记录 
        self.exchanges  = {}  # 连接的交易所
        
    def add_exchange(self, name, api): 
        """添加交易所连接"""
        self.exchanges[name]  = api
        
    def get_spread(self, symbol):
        """计算交易所间价差"""
        if len(self.exchanges)  < 2:
            return None
            
        # 获取所有交易所的买卖价格
        prices = {}
        for name, api in self.exchanges.items(): 
            try:
                order_book = api.get_order_book(symbol) 
                prices[name] = {
                    'bid': order_book['bids'][0][0],
                    'ask': order_book['asks'][0][0]
                }
            except Exception as e:
                print(f"Error getting {symbol} price from {name}: {str(e)}")
                continue
                
        if len(prices) < 2:
            return None 
            
        # 找出最佳买卖价格 
        best_bid = max(prices.values(),  key=lambda x: x['bid'])
        best_ask = min(prices.values(),  key=lambda x: x['ask'])
        
        if best_bid['bid'] >= best_ask['ask']:
            spread = best_bid['bid'] - best_ask['ask']
            spread_pct = spread / best_ask['ask']
            return {
                'buy_exchange': best_ask['exchange'],
                'sell_exchange': best_bid['exchange'],
                'buy_price': best_ask['ask'],
                'sell_price': best_bid['bid'],
                'spread': spread,
                'spread_pct': spread_pct
            }
        return None
        
    def check_arbitrage(self, symbol):
        """检查套利机会"""
        spread_info = self.get_spread(symbol) 
        if not spread_info:
            return False 
            
        # 检查是否满足条件 
        if (spread_info['spread_pct'] >= self.min_profit  and 
            spread_info['spread_pct'] <= self.max_spread): 
            return spread_info
        return False
        
    def execute_arbitrage(self, symbol, amount):
        """执行套利交易"""
        spread_info = self.check_arbitrage(symbol) 
        if not spread_info:
            return False 
            
        buy_exchange = self.exchanges[spread_info['buy_exchange']] 
        sell_exchange = self.exchanges[spread_info['sell_exchange']] 
        
        try:
            # 买入
            buy_order = buy_exchange.create_order( 
                symbol=symbol,
                side='buy',
                amount=amount,
                price=spread_info['buy_price']
            )
            
            # 卖出 
            sell_order = sell_exchange.create_order( 
                symbol=symbol,
                side='sell',
                amount=amount,
                price=spread_info['sell_price']
            )
            
            # 记录交易 
            trade_id = f"ARB-{datetime.now().strftime('%Y%m%d-%H%M%S')}" 
            self.active_arbitrages[trade_id]  = {
                'symbol': symbol,
                'buy_order': buy_order,
                'sell_order': sell_order,
                'amount': amount,
                'expected_profit': amount * spread_info['spread'],
                'timestamp': datetime.now() 
            }
            
            return trade_id
        except Exception as e:
            print(f"Arbitrage execution failed: {str(e)}")
            # 尝试撤销已成交的部分 
            self.cancel_orders(buy_exchange,  sell_exchange, buy_order, sell_order)
            return False 
            
    def cancel_orders(self, buy_exchange, sell_exchange, buy_order, sell_order):
        """撤销订单"""
        if buy_order:
            try:
                buy_exchange.cancel_order(buy_order['id']) 
            except:
                pass
        if sell_order:
            try:
                sell_exchange.cancel_order(sell_order['id']) 
            except:
                pass 
 
 
class MockExchange:
    """模拟交易所"""
    def __init__(self, name, bid, ask):
        self.name  = name
        self.bid  = bid 
        self.ask  = ask
        self.orders  = {}
        self.order_id  = 1
        
    def get_order_book(self, symbol):
        return {
            'bids': [[self.bid, 10]],
            'asks': [[self.ask, 10]]
        }
        
    def create_order(self, symbol, side, amount, price):
        order = {
            'id': f"{self.name}-{self.order_id}", 
            'symbol': symbol,
            'side': side,
            'amount': amount,
            'price': price,
            'status': 'filled',
            'timestamp': datetime.now() 
        }
        self.orders[order['id']]  = order
        self.order_id  += 1
        return order
        
    def cancel_order(self, order_id):
        if order_id in self.orders: 
            self.orders[order_id]['status']  = 'canceled'
            return True 
        return False 
 
 
class TestArbitrator(unittest.TestCase):
    """套利策略测试用例"""
    
    def setUp(self):
        self.arb  = Arbitrator()
        self.exchange1  = MockExchange("Binance", bid=30000, ask=30010)
        self.exchange2  = MockExchange("FTX", bid=30020, ask=30025)
        
    def test_add_exchange(self):
        """测试添加交易所"""
        self.arb.add_exchange("Binance",  self.exchange1) 
        self.arb.add_exchange("FTX",  self.exchange2) 
        self.assertEqual(len(self.arb.exchanges),  2)
        
    def test_get_spread_no_arbitrage(self):
        """测试无套利机会的情况"""
        self.arb.add_exchange("Binance",  self.exchange1) 
        self.arb.add_exchange("FTX",  self.exchange2) 
        
        # 设置无套利空间 (卖价 > 买价)
        self.exchange1.bid  = 30000
        self.exchange1.ask  = 30010
        self.exchange2.bid  = 30005  # 最高买价 < 最低卖价 
        self.exchange2.ask  = 30015 
        
        spread = self.arb.get_spread("BTC/USDT") 
        self.assertIsNone(spread) 
        
    def test_get_spread_with_arbitrage(self):
        """测试有套利机会的情况"""
        self.arb.add_exchange("Binance",  self.exchange1) 
        self.arb.add_exchange("FTX",  self.exchange2) 
        
        # 设置套利空间 (卖价 < 买价)
        self.exchange1.bid  = 30000
        self.exchange1.ask  = 30010
        self.exchange2.bid  = 30020  # 最高买价 > 最低卖价 
        self.exchange2.ask  = 30015 
        
        spread = self.arb.get_spread("BTC/USDT") 
        self.assertIsNotNone(spread) 
        self.assertEqual(spread['buy_exchange'],  "Binance")
        self.assertEqual(spread['sell_exchange'],  "FTX")
        self.assertEqual(spread['buy_price'],  30010)
        self.assertEqual(spread['sell_price'],  30020)
        self.assertAlmostEqual(spread['spread_pct'],  (30020-30010)/30010, places=4)
        
    def test_check_arbitrage_profit_too_small(self):
        """测试利润不足的情况"""
        self.arb.min_profit  = 0.01  # 1%
        self.arb.add_exchange("Binance",  self.exchange1) 
        self.arb.add_exchange("FTX",  self.exchange2) 
        
        # 设置小套利空间 (0.33%)
        self.exchange1.ask  = 30000
        self.exchange2.bid  = 30100
        spread_info = self.arb.check_arbitrage("BTC/USDT") 
        self.assertFalse(spread_info) 
        
    def test_check_arbitrage_spread_too_wide(self):
        """测试价差过大的情况"""
        self.arb.max_spread  = 0.01  # 1%
        self.arb.add_exchange("Binance",  self.exchange1) 
        self.arb.add_exchange("FTX",  self.exchange2) 
        
        # 设置大价差 (5%)
        self.exchange1.ask  = 30000 
        self.exchange2.bid  = 31500 
        spread_info = self.arb.check_arbitrage("BTC/USDT") 
        self.assertFalse(spread_info) 
        
    def test_execute_arbitrage_success(self):
        """测试成功执行套利"""
        self.arb.add_exchange("Binance",  self.exchange1) 
        self.arb.add_exchange("FTX",  self.exchange2) 
        
        # 设置有效套利空间 
        self.exchange1.ask  = 30000 
        self.exchange2.bid  = 30100 
        
        trade_id = self.arb.execute_arbitrage("BTC/USDT",  1)
        self.assertIsNotNone(trade_id) 
        self.assertEqual(len(self.arb.active_arbitrages),  1)
        
        trade = self.arb.active_arbitrages[trade_id] 
        self.assertEqual(trade['symbol'],  "BTC/USDT")
        self.assertEqual(trade['amount'],  1)
        self.assertEqual(trade['expected_profit'],  100)  # (30100-30000)*1 
        
    def test_execute_arbitrage_failure(self):
        """测试套利执行失败"""
        # 模拟交易所API失败 
        broken_exchange = MagicMock()
        broken_exchange.get_order_book.return_value  = {
            'bids': [[30000, 10]],
            'asks': [[30010, 10]]
        }
        broken_exchange.create_order.side_effect  = Exception("API error")
        
        self.arb.add_exchange("Binance",  self.exchange1) 
        self.arb.add_exchange("Broken",  broken_exchange)
        
        # 设置套利空间
        self.exchange1.ask  = 30000
        broken_exchange.get_order_book.return_value  = {
            'bids': [[30100, 10]],
            'asks': [[30050, 10]]
        }
        
        # 执行应失败
        with patch.object(self.arb,  'cancel_orders') as mock_cancel:
            trade_id = self.arb.execute_arbitrage("BTC/USDT",  1)
            self.assertFalse(trade_id) 
            mock_cancel.assert_called_once() 
 
 
class TestIntegration(unittest.TestCase):
    """集成测试套利策略全流程"""
    
    def setUp(self):
        self.arb  = Arbitrator(min_profit=0.005)
        self.binance  = MockExchange("Binance", bid=29900, ask=30000)
        self.ftx  = MockExchange("FTX", bid=30100, ask=30200)
        self.arb.add_exchange("Binance",  self.binance) 
        self.arb.add_exchange("FTX",  self.ftx) 
        
    def test_full_arbitrage_cycle(self):
        """测试完整套利周期"""
        # 1. 检查套利机会
        spread_info = self.arb.check_arbitrage("BTC/USDT") 
        self.assertIsNotNone(spread_info) 
        
        # 2. 执行套利
        trade_id = self.arb.execute_arbitrage("BTC/USDT",  0.5)
        self.assertIsNotNone(trade_id) 
        
        # 3. 验证活跃交易 
        self.assertEqual(len(self.arb.active_arbitrages),  1)
        trade = self.arb.active_arbitrages[trade_id] 
        self.assertEqual(trade['expected_profit'],  50)  # (30100-30000)*0.5
        
        # 4. 验证交易所订单
        self.assertEqual(len(self.binance.orders),  1)
        self.assertEqual(len(self.ftx.orders),  1)
        binance_order = list(self.binance.orders.values())[0] 
        ftx_order = list(self.ftx.orders.values())[0] 
        self.assertEqual(binance_order['side'],  'buy')
        self.assertEqual(ftx_order['side'],  'sell')
 
 
if __name__ == '__main__':
    unittest.main(verbosity=2) 