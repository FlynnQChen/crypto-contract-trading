#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD量化交易系统 
功能：
1. 标准MACD指标计算 
2. 自适应信号线阈值
3. 多时间框架分析
4. 集成风险管理 
"""
 
import numpy as np 
import pandas as pd
from typing import List, Dict, Optional, Tuple 
from dataclasses import dataclass, field
from decimal import Decimal 
from datetime import datetime
import matplotlib.pyplot  as plt
from .base import BaseStrategy, Bar, Direction, Order, Position 
 
class MACDCalculator:
    """MACD指标计算引擎"""
    
    def __init__(self, 
                 fast_period: int = 12,
                 slow_period: int = 26,
                 signal_period: int = 9,
                 warmup_bars: int = 100):
        """
        Args:
            fast_period: 快线周期 
            slow_period: 慢线周期
            signal_period: 信号线周期
            warmup_bars: 预热所需K线数量 
        """
        self.fast_period  = fast_period
        self.slow_period  = slow_period 
        self.signal_period  = signal_period
        self.warmup_bars  = max(warmup_bars, slow_period + signal_period)
        self._price_buffer = []
        self._macd_values = []
        self._signal_values = []
        self._hist_values = []
        
    def update(self, new_bar: Bar) -> Optional[Tuple[float, float, float]]:
        """
        更新MACD值 
        Returns:
            (macd, signal, hist) 或 None(未完成预热时)
        """
        self._price_buffer.append(float(new_bar.close)) 
        
        if len(self._price_buffer) < self.slow_period  + self.signal_period: 
            return None 
            
        # 计算EMA 
        fast_ema = self._calculate_ema(self.fast_period) 
        slow_ema = self._calculate_ema(self.slow_period) 
        macd = fast_ema - slow_ema 
        
        # 计算信号线
        if len(self._macd_values) < self.signal_period  - 1:
            signal = np.nan 
        else:
            signal = np.mean(self._macd_values[-(self.signal_period-1):]  + [macd])
        
        hist = macd - signal
        
        # 更新状态 
        self._macd_values.append(macd) 
        self._signal_values.append(signal) 
        self._hist_values.append(hist) 
        
        return macd, signal, hist
    
    def _calculate_ema(self, period: int) -> float:
        """计算指数移动平均"""
        prices = np.array(self._price_buffer[-period:]) 
        weights = np.exp(np.linspace(-1,  0, period))
        weights /= weights.sum() 
        return np.dot(prices,  weights)
    
    @property 
    def is_ready(self) -> bool:
        """是否完成预热"""
        return len(self._price_buffer) >= self.warmup_bars  
    
    @property 
    def current_macd(self) -> Tuple[float, float, float]:
        """当前MACD值"""
        if not self._macd_values:
            return 0.0, 0.0, 0.0 
        return (
            self._macd_values[-1], 
            self._signal_values[-1] if self._signal_values else 0.0,
            self._hist_values[-1] if self._hist_values else 0.0 
        )
        
    def get_dataframe(self) -> pd.DataFrame:
        """获取MACD数据DataFrame"""
        return pd.DataFrame({
            'timestamp': [b.timestamp for b in self._price_buffer[len(self._price_buffer)-len(self._macd_values):]],
            'price': [b.close for b in self._price_buffer[len(self._price_buffer)-len(self._macd_values):]],
            'macd': self._macd_values,
            'signal': self._signal_values,
            'hist': self._hist_values 
        })
 
class AdaptiveMACDParams:
    """自适应MACD参数"""
    
    def __init__(self,
                 base_fast: int = 12,
                 base_slow: int = 26,
                 volatility_lookback: int = 30,
                 atr_multiplier: float = 1.5):
        """
        Args:
            base_fast: 基础快线周期
            base_slow: 基础慢线周期 
            volatility_lookback: 波动率观察窗口 
            atr_multiplier: ATR乘数阈值 
        """
        self.base_fast  = base_fast 
        self.base_slow  = base_slow 
        self.volatility_lookback  = volatility_lookback 
        self.atr_multiplier  = atr_multiplier 
        self._atr_values = []
        
    def update_params(self, new_bar: Bar) -> Tuple[int, int, float]:
        """
        根据市场波动率调整参数
        Returns:
            (fast_period, slow_period, signal_threshold)
        """
        # 计算ATR
        if len(self._atr_values) >= 1:
            prev_close = self._atr_values[-1].close 
            tr = max(
                new_bar.high  - new_bar.low, 
                abs(new_bar.high  - prev_close),
                abs(new_bar.low  - prev_close)
            )
            self._atr_values.append(new_bar) 
            atr = np.mean([b.atr  for b in self._atr_values[-self.volatility_lookback:]]) 
        else:
            self._atr_values.append(new_bar) 
            atr = 0.0 
        
        # 参数调整逻辑
        if atr > (np.mean([b.close  for b in self._atr_values[-30:]]) * self.atr_multiplier  / 100):
            # 高波动率市场使用更敏感的短周期
            fast = max(8, int(self.base_fast  * 0.7))
            slow = max(20, int(self.base_slow  * 0.8))
            threshold = 0.5  # 降低信号阈值
        else:
            fast = self.base_fast  
            slow = self.base_slow  
            threshold = 1.0  # 标准信号阈值
            
        return fast, slow, threshold
 
@dataclass 
class MACDSignal:
    """MACD交易信号"""
    timestamp: float
    macd_value: float 
    signal_value: float 
    hist_value: float 
    direction: Direction
    strength: float  # 信号强度 0-1
    fast_period: int
    slow_period: int 
 
class MACDStrategy(BaseStrategy):
    """MACD交易策略"""
    
    def __init__(self, 
                 exchange: BaseExchange,
                 config: Dict[str, any]):
        super().__init__(exchange, config)
        self.macd_calculator  = MACDCalculator(
            fast_period=config.get('fast_period',  12),
            slow_period=config.get('slow_period',  26),
            signal_period=config.get('signal_period',  9),
            warmup_bars=config.get('warmup_bars',  100)
        )
        self.param_adjuster  = AdaptiveMACDParams(
            base_fast=config.get('base_fast',  12),
            base_slow=config.get('base_slow',  26),
            volatility_lookback=config.get('volatility_lookback',  30),
            atr_multiplier=config.get('atr_multiplier',  1.5)
        )
        self.signals:  List[MACDSignal] = []
        self.position_size  = Decimal('0')
        
        # 状态变量 
        self._current_fast = config.get('fast_period',  12)
        self._current_slow = config.get('slow_period',  26)
        self._signal_threshold = 1.0 
        
    async def on_bar(self, bar: Bar) -> None:
        """K线回调"""
        # 更新参数 
        self._current_fast, self._current_slow, self._signal_threshold = \
            self.param_adjuster.update_params(bar) 
            
        # 更新MACD计算器参数
        if (self.macd_calculator.fast_period  != self._current_fast or 
            self.macd_calculator.slow_period  != self._current_slow):
            self.macd_calculator.fast_period  = self._current_fast 
            self.macd_calculator.slow_period  = self._current_slow
            
        # 计算MACD 
        macd_values = self.macd_calculator.update(bar) 
        if macd_values is None:
            return 
            
        macd, signal, hist = macd_values 
        
        # 生成信号 
        signal = self._generate_signal(bar, macd, signal, hist)
        if signal:
            self.signals.append(signal) 
            await self._execute_trade(signal, bar)
            
    def _generate_signal(self, 
                        bar: Bar,
                        macd: float, 
                        signal: float, 
                        hist: float) -> Optional[MACDSignal]:
        """生成交易信号"""
        if not self.macd_calculator.is_ready  or np.isnan(signal): 
            return None
            
        direction = None 
        strength = 0.0 
        
        # MACD上穿信号线 (买入信号)
        if (macd > signal and 
            hist > abs(hist) * self._signal_threshold and 
            (len(self._hist_values) < 2 or self._hist_values[-2] <= 0)):
            direction = Direction.LONG 
            strength = min(hist / (abs(hist) + 1), 1.0)  # 标准化到0-1
            
        # MACD下穿信号线 (卖出信号)
        elif (macd < signal and 
              hist < -abs(hist) * self._signal_threshold and 
              (len(self._hist_values) < 2 or self._hist_values[-2] >= 0)):
            direction = Direction.SHORT 
            strength = min(-hist / (abs(hist) + 1), 1.0)
            
        if direction:
            return MACDSignal(
                timestamp=bar.timestamp, 
                macd_value=macd,
                signal_value=signal,
                hist_value=hist,
                direction=direction,
                strength=strength,
                fast_period=self._current_fast,
                slow_period=self._current_slow 
            )
        return None 
        
    async def _execute_trade(self, signal: MACDSignal, bar: Bar) -> None:
        """执行交易"""
        # 计算动态仓位 
        base_size = Decimal(str(self.config['base_order_size'])) 
        size = base_size * Decimal(str(signal.strength)) 
        
        # 平反向仓位 
        if self.position_size  * signal.direction.sign  < 0:
            close_order = await self.exchange.create_order( 
                symbol=self.config['symbol'], 
                direction=Direction.SHORT if self.position_size  > 0 else Direction.LONG,
                order_type=OrderType.MARKET,
                size=abs(self.position_size) 
            )
            self.position_size  = Decimal('0')
            
        # 开新仓 
        new_order = await self.exchange.create_order( 
            symbol=self.config['symbol'], 
            direction=signal.direction, 
            order_type=OrderType.MARKET,
            size=size 
        )
        self.position_size  += size * signal.direction.sign  
        
    def plot_signals(self) -> plt.Figure:
        """可视化信号"""
        if not self.signals: 
            raise ValueError("No signals to plot")
            
        df = self.macd_calculator.get_dataframe() 
        timestamps = [datetime.fromtimestamp(ts) for ts in df['timestamp']]
        
        fig, (ax1, ax2) = plt.subplots(2,  1, figsize=(12, 8), sharex=True)
        
        # 价格和信号 
        ax1.plot(timestamps,  df['price'], label='Price', color='black')
        
        long_signals = [s for s in self.signals  if s.direction  == Direction.LONG]
        short_signals = [s for s in self.signals  if s.direction  == Direction.SHORT]
        
        ax1.scatter( 
            [datetime.fromtimestamp(s.timestamp) for s in long_signals],
            [df['price'].iloc[self._find_nearest_index(df, s.timestamp)]  for s in long_signals],
            color='green', marker='^', label='Buy'
        )
        ax1.scatter( 
            [datetime.fromtimestamp(s.timestamp) for s in short_signals],
            [df['price'].iloc[self._find_nearest_index(df, s.timestamp)]  for s in short_signals],
            color='red', marker='v', label='Sell'
        )
        ax1.set_title('Price  with MACD Signals')
        ax1.legend() 
        
        # MACD曲线
        ax2.plot(timestamps,  df['macd'], label='MACD', color='blue')
        ax2.plot(timestamps,  df['signal'], label='Signal', color='orange')
        ax2.bar(timestamps,  df['hist'], 
               color=np.where(df['hist']  > 0, 'green', 'red'),
               alpha=0.3, label='Histogram')
        ax2.axhline(0,  color='gray', linestyle='--')
        ax2.set_title('MACD  Indicator')
        ax2.legend() 
        
        plt.tight_layout() 
        return fig 
        
    def _find_nearest_index(self, df: pd.DataFrame, timestamp: float) -> int:
        """找到最近的价格索引"""
        return (df['timestamp'] - timestamp).abs().idxmin()
 
class MACDBacktester:
    """MACD策略回测引擎"""
    
    def __init__(self, data: pd.DataFrame):
        """
        Args:
            data: 包含OHLCV的DataFrame, 索引为时间戳 
        """
        self.data  = data 
        self.results  = None
        
    def run_backtest(self, 
                    fast_period: int = 12,
                    slow_period: int = 26,
                    signal_period: int = 9,
                    commission: float = 0.0005) -> Dict[str, any]:
        """
        运行回测
        Returns:
            回测结果字典 
        """
        close_prices = self.data['close'].values  
        macd, signal, hist = self._calculate_macd(close_prices, fast_period, slow_period, signal_period)
        
        positions = np.zeros(len(close_prices)) 
        returns = np.zeros(len(close_prices)) 
        equity = np.ones(len(close_prices)) 
        
        in_position = False 
        entry_price = 0.0
        
        for i in range(slow_period + signal_period, len(close_prices)):
            # 平仓条件 
            if in_position:
                returns[i] = (close_prices[i] - entry_price) / entry_price * (-1 if positions[i-1] == -1 else 1)
                equity[i] = equity[i-1] * (1 + returns[i] - commission)
                
                # MACD反向交叉平仓 
                if (positions[i-1] == 1 and macd[i] < signal[i]) or \
                   (positions[i-1] == -1 and macd[i] > signal[i]):
                    positions[i] = 0 
                    in_position = False 
                else:
                    positions[i] = positions[i-1]
            # 开仓条件        
            else:
                equity[i] = equity[i-1]
                # MACD上穿信号线
                if macd[i] > signal[i] and macd[i-1] <= signal[i-1]:
                    positions[i] = 1 
                    entry_price = close_prices[i]
                    in_position = True
                # MACD下穿信号线 
                elif macd[i] < signal[i] and macd[i-1] >= signal[i-1]:
                    positions[i] = -1
                    entry_price = close_prices[i]
                    in_position = True 
        
        # 计算绩效指标
        total_return = equity[-1] - 1
        sharpe_ratio = self._calculate_sharpe(returns[slow_period + signal_period:])
        max_drawdown = self._calculate_max_drawdown(equity)
        win_rate = self._calculate_win_rate(returns[slow_period + signal_period:])
        
        self.results  = {
            'positions': positions,
            'returns': returns,
            'equity': equity,
            'macd': macd,
            'signal': signal,
            'hist': hist,
            'total_return': total_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'parameters': {
                'fast_period': fast_period,
                'slow_period': slow_period,
                'signal_period': signal_period
            }
        }
        return self.results  
        
    def plot_results(self) -> plt.Figure:
        """可视化回测结果"""
        if self.results  is None:
            raise ValueError("Run backtest first")
            
        fig, (ax1, ax2, ax3) = plt.subplots(3,  1, figsize=(12, 10), sharex=True)
        
        # 价格和仓位 
        ax1.plot(self.data.index,  self.data['close'],  label='Price', color='black')
        ax1.plot(self.data.index[self.results['positions']  == 1], 
                self.data['close'][self.results['positions']  == 1],
                '^', markersize=8, color='green', label='Long')
        ax1.plot(self.data.index[self.results['positions']  == -1], 
                self.data['close'][self.results['positions']  == -1],
                'v', markersize=8, color='red', label='Short')
        ax1.set_title('Price  and Positions')
        ax1.legend() 
        
        # MACD指标
        ax2.plot(self.data.index,  self.results['macd'],  label='MACD', color='blue')
        ax2.plot(self.data.index,  self.results['signal'],  label='Signal', color='orange')
        ax2.bar(self.data.index,  self.results['hist'],  
               color=np.where(self.results['hist']  > 0, 'green', 'red'),
               alpha=0.3)
        ax2.axhline(0,  color='gray', linestyle='--')
        ax2.set_title('MACD  Indicator')
        ax2.legend() 
        
        # 资金曲线
        ax3.plot(self.data.index,  self.results['equity'],  label='Equity', color='purple')
        ax3.set_title('Equity  Curve')
        ax3.legend() 
        
        plt.tight_layout() 
        return fig 
        
    def _calculate_macd(self, 
                       prices: np.ndarray,  
                       fast: int, 
                       slow: int, 
                       signal: int) -> Tuple[np.ndarray, np.ndarray,  np.ndarray]: 
        """计算MACD指标"""
        # 计算EMA
        fast_ema = self._calculate_ema(prices, fast)
        slow_ema = self._calculate_ema(prices, slow)
        macd = fast_ema - slow_ema
        
        # 计算信号线 
        signal_line = np.zeros_like(macd) 
        for i in range(slow, len(macd)):
            start_idx = max(0, i - signal + 1)
            signal_line[i] = np.mean(macd[start_idx:i+1]) 
        
        hist = macd - signal_line
        return macd, signal_line, hist
        
    def _calculate_ema(self, prices: np.ndarray,  period: int) -> np.ndarray: 
        """计算指数移动平均"""
        ema = np.zeros_like(prices) 
        ema[period-1] = np.mean(prices[:period]) 
        
        multiplier = 2 / (period + 1)
        for i in range(period, len(prices)):
            ema[i] = (prices[i] - ema[i-1]) * multiplier + ema[i-1]
        return ema
        
    def _calculate_sharpe(self, returns: np.ndarray,  risk_free_rate: float = 0.0) -> float:
        """计算夏普比率"""
        excess_returns = returns - risk_free_rate
        return np.mean(excess_returns)  / np.std(excess_returns)  * np.sqrt(252) 
        
    def _calculate_max_drawdown(self, equity: np.ndarray)  -> float:
        """计算最大回撤"""
        peak = equity[0]
        max_dd = 0.0 
        
        for value in equity:
            if value > peak:
                peak = value
            dd = (peak - value) / peak 
            if dd > max_dd:
                max_dd = dd
        return max_dd
        
    def _calculate_win_rate(self, returns: np.ndarray)  -> float:
        """计算胜率"""
        winning_trades = returns[returns > 0]
        return len(winning_trades) / len(returns[returns != 0]) if len(returns[returns != 0]) > 0 else 0.0 
 
# 示例用法 
if __name__ == '__main__':
    # 生成测试数据
    np.random.seed(42) 
    dates = pd.date_range(start='2020-01-01',  end='2021-12-31')
    prices = np.cumprod(1  + np.random.normal(0.001,  0.01, len(dates)))
    df = pd.DataFrame({
        'open': prices,
        'high': prices * 1.005,
        'low': prices * 0.995,
        'close': prices,
        'volume': np.random.randint(100,  1000, len(dates))
    }, index=dates)
    
    # 回测示例
    backtester = MACDBacktester(df)
    results = backtester.run_backtest( 
        fast_period=12,
        slow_period=26,
        signal_period=9,
        commission=0.0005 
    )
    print(f"总收益率: {results['total_return']:.2%}")
    print(f"夏普比率: {results['sharpe_ratio']:.2f}")
    print(f"最大回撤: {results['max_drawdown']:.2%}")
    print(f"胜率: {results['win_rate']:.2%}")
    
    # 可视化 
    fig = backtester.plot_results() 
    plt.show() 