import unittest
from unittest.mock  import patch, MagicMock
import pandas as pd 
from datetime import datetime, timedelta 
 
class FundingProtection:
    """资金保护核心逻辑"""
    def __init__(self, max_drawdown=0.2, daily_loss_limit=0.1, min_balance=1000):
        self.max_drawdown  = max_drawdown  # 最大回撤限制 (20%)
        self.daily_loss_limit  = daily_loss_limit  # 单日亏损限制 (10%)
        self.min_balance  = min_balance  # 最低账户余额
        self.today_pnl  = 0 
        self.history_pnl  = []
        self.equity_curve  = []
        self.last_check_date  = datetime.now().date() 
        
    def update_pnl(self, amount):
        """更新当日盈亏"""
        current_date = datetime.now().date() 
        
        # 如果是新的一天，重置当日盈亏记录 
        if current_date != self.last_check_date: 
            self.history_pnl.append(self.today_pnl) 
            self.today_pnl  = 0
            self.last_check_date  = current_date 
            
        self.today_pnl  += amount
        self.equity_curve.append(self.get_current_equity()) 
        
    def get_current_equity(self):
        """计算当前权益 (模拟)"""
        return 10000 + sum(self.history_pnl)  + self.today_pnl  
        
    def check_risk_rules(self):
        """检查所有风险规则"""
        violations = []
        current_equity = self.get_current_equity() 
        
        # 1. 检查最大回撤
        if len(self.equity_curve)  >= 5:  # 至少有5个数据点 
            peak = max(self.equity_curve) 
            drawdown = (peak - current_equity) / peak 
            if drawdown > self.max_drawdown: 
                violations.append(f"Max  drawdown violation: {drawdown*100:.2f}%")
        
        # 2. 检查单日亏损 
        if self.today_pnl  < 0 and abs(self.today_pnl)  > self.daily_loss_limit  * current_equity:
            violations.append(f"Daily  loss limit violation: {-self.today_pnl:.2f}") 
            
        # 3. 检查最低余额
        if current_equity < self.min_balance: 
            violations.append(f"Min  balance violation: {current_equity:.2f}")
            
        return violations 
    
    def should_stop_trading(self):
        """是否应该停止交易"""
        return len(self.check_risk_rules())  > 0 
 
 
class TestFundingProtection(unittest.TestCase):
    """资金保护测试用例"""
    
    def setUp(self):
        self.fp  = FundingProtection()
        
    def test_initial_state(self):
        """测试初始化状态"""
        self.assertEqual(self.fp.max_drawdown,  0.2)
        self.assertEqual(self.fp.daily_loss_limit,  0.1)
        self.assertEqual(self.fp.min_balance,  1000)
        self.assertEqual(self.fp.today_pnl,  0)
        self.assertEqual(len(self.fp.history_pnl),  0)
        
    def test_update_pnl(self):
        """测试更新盈亏"""
        self.fp.update_pnl(500) 
        self.assertEqual(self.fp.today_pnl,  500)
        
        self.fp.update_pnl(-300) 
        self.assertEqual(self.fp.today_pnl,  200)
        
    def test_daily_pnl_reset(self):
        """测试每日盈亏重置"""
        # 模拟日期变更
        with patch('datetime.date')  as mock_date:
            mock_date.today.return_value  = datetime.now().date() 
            mock_date.side_effect  = lambda *args, **kw: datetime(*args, **kw).date()
            
            # 第一天
            self.fp.update_pnl(500) 
            self.assertEqual(len(self.fp.history_pnl),  0)
            
            # 第二天 
            mock_date.today.return_value  = (datetime.now()  + timedelta(days=1)).date()
            self.fp.update_pnl(300) 
            self.assertEqual(len(self.fp.history_pnl),  1)
            self.assertEqual(self.fp.history_pnl[0],  500)
            self.assertEqual(self.fp.today_pnl,  300)
    
    def test_max_drawdown_violation(self):
        """测试最大回撤违规"""
        # 模拟资金曲线
        self.fp.equity_curve  = [10000, 11000, 12000, 9000]  # 从12000->9000 回撤25%
        violations = self.fp.check_risk_rules() 
        self.assertIn("Max  drawdown violation: 25.00%", violations)
        
    def test_daily_loss_violation(self):
        """测试单日亏损违规"""
        # 初始权益10000，日亏损限制10%=1000 
        self.fp.update_pnl(-1500)   # 当日亏损1500
        violations = self.fp.check_risk_rules() 
        self.assertIn("Daily  loss limit violation: 1500.00", violations)
        
    def test_min_balance_violation(self):
        """测试最低余额违规"""
        # 模拟大额亏损使余额低于最小值 
        self.fp.history_pnl  = [-9500]  # 10000 - 9500 = 500 < 1000 
        violations = self.fp.check_risk_rules() 
        self.assertIn("Min  balance violation: 500.00", violations)
        
    def test_should_stop_trading(self):
        """测试停止交易条件"""
        # 正常情况 
        self.assertFalse(self.fp.should_stop_trading()) 
        
        # 触发违规
        self.fp.update_pnl(-1500) 
        self.assertTrue(self.fp.should_stop_trading()) 
        
    def test_multiple_violations(self):
        """测试多规则同时违规"""
        # 设置同时触发多个违规的情况 
        self.fp.equity_curve  = [10000, 12000, 8000]  # 回撤33.33%
        self.fp.update_pnl(-1500)   # 日亏损超限
        self.fp.history_pnl  = [-5000]  # 余额5000 (低于10000但高于min_balance)
        
        violations = self.fp.check_risk_rules() 
        self.assertEqual(len(violations),  2)
        self.assertIn("Max  drawdown violation: 33.33%", violations)
        self.assertIn("Daily  loss limit violation: 1500.00", violations)
        
    def test_edge_cases(self):
        """测试边界情况"""
        # 刚好达到限制但不违规 
        self.fp.equity_curve  = [10000, 12000, 9600]  # 回撤20%
        violations = self.fp.check_risk_rules() 
        self.assertEqual(len(violations),  0)
        
        # 刚好达到日亏损限制 
        self.fp.today_pnl  = -1000  # 10000 * 10% = 1000
        violations = self.fp.check_risk_rules() 
        self.assertEqual(len(violations),  0)
        
        # 刚好达到最低余额 
        self.fp.history_pnl  = [-9000]  # 余额1000
        violations = self.fp.check_risk_rules() 
        self.assertEqual(len(violations),  0)
 
 
class MockExchangeAPI:
    """模拟交易所API"""
    def __init__(self):
        self.balance  = 10000
        self.positions  = {}
        
    def get_balance(self):
        return {'total': self.balance} 
    
    def get_positions(self):
        return self.positions  
    
    def close_all_positions(self):
        self.positions  = {}
        return True
 
 
class TestIntegrationWithExchange(unittest.TestCase):
    """与交易所API集成测试"""
    
    def setUp(self):
        self.exchange  = MockExchangeAPI()
        self.fp  = FundingProtection()
        
    def test_balance_monitoring(self):
        """测试余额监控"""
        # 初始余额检查 
        self.fp.update_pnl(0) 
        self.assertEqual(self.fp.get_current_equity(),  10000)
        
        # 模拟盈利 
        self.exchange.balance  = 12000
        self.fp.update_pnl(2000) 
        self.assertEqual(self.fp.get_current_equity(),  12000)
        
        # 模拟亏损 
        self.exchange.balance  = 8000
        self.fp.update_pnl(-4000) 
        self.assertEqual(self.fp.get_current_equity(),  8000)
        
    def test_auto_liquidation(self):
        """测试自动平仓逻辑"""
        # 设置违规条件 
        self.exchange.balance  = 500  # 低于最低余额 
        self.fp.update_pnl(-9500) 
        
        # 验证应该停止交易 
        self.assertTrue(self.fp.should_stop_trading()) 
        
        # 模拟平仓操作 
        with patch.object(self.exchange,  'close_all_positions') as mock_close:
            if self.fp.should_stop_trading(): 
                self.exchange.close_all_positions() 
            mock_close.assert_called_once() 
 
 
if __name__ == '__main__':
    unittest.main(verbosity=2) 