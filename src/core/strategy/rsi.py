#!/usr/bin/env python3 
# -*- coding: utf-8 -*-
"""
RSI指标交易系统
功能：
1. 多时间框架RSI计算
2. 自适应超买超卖阈值 
3. 动态仓位管理 
4. 回测引擎集成 
"""
 
import numpy as np 
import pandas as pd
from typing import List, Dict, Optional, Tuple 
from dataclasses import dataclass
from decimal import Decimal 
from datetime import datetime
import matplotlib.pyplot  as plt
from .base import BaseStrategy, Bar, Direction, Order 
 
class RSICalculator:
    """RSI指标计算引擎"""
    
    def __init__(self, period: int = 14, warmup_bars: int = 50):
        """
        Args:
            period: RSI计算周期
            warmup_bars: 预热所需K线数量 
        """
        self.period  = period
        self.warmup_bars  = max(warmup_bars, period * 2)
        self._price_buffer = []
        self._rsi_values = []
        
    def update(self, new_bar: Bar) -> Optional[float]:
        """
        更新RSI值 
        Returns:
            当前RSI值(未完成计算时返回None)
        """
        self._price_buffer.append(float(new_bar.close)) 
        
        if len(self._price_buffer) < self.period  + 1:
            return None 
            
        deltas = np.diff(self._price_buffer[-self.period-1:]) 
        gains = np.where(deltas  > 0, deltas, 0)
        losses = np.where(deltas  < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[:self.period]) 
        avg_loss = np.mean(losses[:self.period]) 
        
        for i in range(self.period,  len(deltas)):
            avg_gain = (avg_gain * (self.period  - 1) + gains[i]) / self.period 
            avg_loss = (avg_loss * (self.period  - 1) + losses[i]) / self.period  
        
        rs = avg_gain / avg_loss if avg_loss != 0 else np.inf 
        current_rsi = 100 - (100 / (1 + rs))
        self._rsi_values.append(current_rsi) 
        return current_rsi 
    
    @property
    def is_ready(self) -> bool:
        """是否完成预热"""
        return len(self._price_buffer) >= self.warmup_bars 
    
    @property
    def current_rsi(self) -> float:
        """当前RSI值"""
        return self._rsi_values[-1] if self._rsi_values else 50.0
        
    def get_series(self) -> pd.Series:
        """获取RSI序列"""
        return pd.Series(
            self._rsi_values,
            index=pd.to_datetime( 
                [b.timestamp for b in self._price_buffer[len(self._price_buffer)-len(self._rsi_values):]],
                unit='s'
            )
        )
 
class AdaptiveRSIParams:
    """自适应RSI参数"""
    
    def __init__(self, 
                 base_period: int = 14,
                 volatility_lookback: int = 30,
                 atr_threshold: float = 0.02):
        """
        Args:
            base_period: 基础计算周期
            volatility_lookback: 波动率观察窗口
            atr_threshold: 高波动率ATR阈值(百分比)
        """
        self.base_period  = base_period
        self.volatility_lookback  = volatility_lookback
        self.atr_threshold  = atr_threshold
        self._atr_values = []
        
    def update_params(self, new_bar: Bar) -> Tuple[int, float, float]:
        """
        根据市场波动率调整参数 
        Returns:
            (adjusted_period, overbought, oversold)
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
        if atr > self.atr_threshold: 
            period = int(self.base_period  * 0.7)  # 高波动率缩短周期 
            overbought = 70 
            oversold = 30 
        else:
            period = self.base_period 
            overbought = 65  # 低波动率收紧阈值
            oversold = 35 
            
        return period, overbought, oversold
 
@dataclass 
class RSISignal:
    """RSI交易信号"""
    timestamp: float 
    rsi_value: float 
    direction: Direction
    strength: float  # 信号强度 0-1
    overbought: float 
    oversold: float 
 
class RSIStrategy(BaseStrategy):
    """RSI交易策略"""
    
    def __init__(self, 
                 exchange: BaseExchange,
                 config: Dict[str, any]):
        super().__init__(exchange, config)
        self.rsi_calculator  = RSICalculator(
            period=config.get('rsi_period',  14),
            warmup_bars=config.get('warmup_bars',  100)
        )
        self.param_adjuster  = AdaptiveRSIParams(
            base_period=config.get('base_period',  14),
            volatility_lookback=config.get('volatility_lookback',  30),
            atr_threshold=config.get('atr_threshold',  0.02)
        )
        self.signals:  List[RSISignal] = []
        self.position_size  = Decimal('0')
        
        # 状态变量 
        self._current_period = config.get('rsi_period',  14)
        self._overbought = 70
        self._oversold = 30 
        
    async def on_bar(self, bar: Bar) -> None:
        """K线回调"""
        # 更新参数 
        self._current_period, self._overbought, self._oversold = \
            self.param_adjuster.update_params(bar) 
            
        # 计算RSI
        current_rsi = self.rsi_calculator.update(bar) 
        if current_rsi is None:
            return
            
        # 生成信号
        signal = self._generate_signal(bar, current_rsi)
        if signal:
            self.signals.append(signal) 
            await self._execute_trade(signal, bar)
            
    def _generate_signal(self, bar: Bar, rsi_value: float) -> Optional[RSISignal]:
        """生成交易信号"""
        if not self.rsi_calculator.is_ready: 
            return None 
            
        direction = None 
        strength = 0.0 
        
        # 超卖信号
        if rsi_value < self._oversold:
            direction = Direction.LONG
            strength = (self._oversold - rsi_value) / self._oversold
            
        # 超买信号    
        elif rsi_value > self._overbought:
            direction = Direction.SHORT 
            strength = (rsi_value - self._overbought) / (100 - self._overbought)
            
        if direction:
            return RSISignal(
                timestamp=bar.timestamp, 
                rsi_value=rsi_value,
                direction=direction,
                strength=min(strength, 1.0),
                overbought=self._overbought,
                oversold=self._oversold 
            )
        return None 
        
    async def _execute_trade(self, signal: RSISignal, bar: Bar) -> None:
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
            
        fig, (ax1, ax2) = plt.subplots(2,  1, figsize=(12, 8), sharex=True)
        
        # 价格和信号 
        prices = [b.close for b in self.rsi_calculator._price_buffer] 
        timestamps = [datetime.fromtimestamp(b.timestamp) for b in self.rsi_calculator._price_buffer] 
        ax1.plot(timestamps,  prices, label='Price', color='black')
        
        long_signals = [s for s in self.signals  if s.direction  == Direction.LONG]
        short_signals = [s for s in self.signals  if s.direction  == Direction.SHORT]
        
        ax1.scatter( 
            [datetime.fromtimestamp(s.timestamp) for s in long_signals],
            [prices[self._find_nearest_index(s.timestamp)]  for s in long_signals],
            color='green', marker='^', label='Buy'
        )
        ax1.scatter( 
            [datetime.fromtimestamp(s.timestamp) for s in short_signals],
            [prices[self._find_nearest_index(s.timestamp)]  for s in short_signals],
            color='red', marker='v', label='Sell'
        )
        ax1.set_title('Price  with RSI Signals')
        ax1.legend() 
        
        # RSI曲线 
        rsi_series = self.rsi_calculator.get_series() 
        ax2.plot(rsi_series.index,  rsi_series.values,  label='RSI', color='blue')
        
        # 动态阈值线 
        ob_levels = [s.overbought for s in self.signals] 
        os_levels = [s.oversold for s in self.signals] 
        signal_times = [datetime.fromtimestamp(s.timestamp) for s in self.signals] 
        
        ax2.plot(signal_times,  ob_levels, linestyle='--', color='red', alpha=0.3)
        ax2.plot(signal_times,  os_levels, linestyle='--', color='green', alpha=0.3)
        ax2.axhline(70,  linestyle=':', color='red')
        ax2.axhline(30,  linestyle=':', color='green')
        ax2.set_ylim(0,  100)
        ax2.set_title('RSI  with Dynamic Thresholds')
        
        plt.tight_layout() 
        return fig 
        
    def _find_nearest_index(self, timestamp: float) -> int:
        """找到最近的价格索引"""
        timestamps = [b.timestamp for b in self.rsi_calculator._price_buffer] 
        return min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - timestamp))
 
class RSIBacktester:
    """RSI策略回测引擎"""
    
    def __init__(self, data: pd.DataFrame):
        """
        Args:
            data: 包含OHLCV的DataFrame, 索引为时间戳 
        """
        self.data  = data 
        self.results  = None
        
    def run_backtest(self, 
                    rsi_period: int = 14,
                    overbought: int = 70,
                    oversold: int = 30,
                    commission: float = 0.0005) -> Dict[str, any]:
        """
        运行回测
        Returns:
            回测结果字典 
        """
        close_prices = self.data['close'].values 
        rsi = self._calculate_rsi(close_prices, rsi_period)
        
        positions = np.zeros(len(close_prices)) 
        returns = np.zeros(len(close_prices)) 
        equity = np.ones(len(close_prices)) 
        
        in_position = False
        entry_price = 0.0 
        
        for i in range(rsi_period, len(close_prices)):
            # 平仓条件 
            if in_position:
                returns[i] = (close_prices[i] - entry_price) / entry_price * (-1 if positions[i-1] == -1 else 1)
                equity[i] = equity[i-1] * (1 + returns[i] - commission)
                
                # 反向信号平仓
                if (positions[i-1] == 1 and rsi[i] > overbought) or \
                   (positions[i-1] == -1 and rsi[i] < oversold):
                    positions[i] = 0
                    in_position = False 
                else:
                    positions[i] = positions[i-1]
            # 开仓条件        
            else:
                equity[i] = equity[i-1]
                if rsi[i] < oversold:
                    positions[i] = 1 
                    entry_price = close_prices[i]
                    in_position = True 
                elif rsi[i] > overbought:
                    positions[i] = -1 
                    entry_price = close_prices[i]
                    in_position = True
        
        # 计算绩效指标
        total_return = equity[-1] - 1
        sharpe_ratio = self._calculate_sharpe(returns[rsi_period:])
        max_drawdown = self._calculate_max_drawdown(equity)
        
        self.results  = {
            'positions': positions,
            'returns': returns,
            'equity': equity,
            'rsi': rsi,
            'total_return': total_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'parameters': {
                'rsi_period': rsi_period,
                'overbought': overbought,
                'oversold': oversold
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
        
        # RSI 
        ax2.plot(self.data.index,  self.results['rsi'],  label='RSI', color='blue')
        ax2.axhline(self.results['parameters']['overbought'],  linestyle='--', color='red')
        ax2.axhline(self.results['parameters']['oversold'],  linestyle='--', color='green')
        ax2.set_ylim(0,  100)
        ax2.set_title('RSI  Indicator')
        
        # 资金曲线
        ax3.plot(self.data.index,  self.results['equity'],  label='Equity', color='purple')
        ax3.set_title('Equity  Curve')
        ax3.legend() 
        
        plt.tight_layout() 
        return fig 
        
    def _calculate_rsi(self, prices: np.ndarray,  period: int) -> np.ndarray: 
        """计算RSI"""
        deltas = np.diff(prices) 
        seed = deltas[:period]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / down
        rsi = np.zeros_like(prices) 
        rsi[:period] = 100. - (100. / (1. + rs))
        
        for i in range(period, len(prices)-1):
            delta = deltas[i]
            up = (up * (period - 1) + max(delta, 0)) / period
            down = (down * (period - 1) + max(-delta, 0)) / period 
            rs = up / down if down != 0 else np.inf 
            rsi[i+1] = 100. - (100. / (1. + rs))
            
        return rsi
        
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
    backtester = RSIBacktester(df)
    results = backtester.run_backtest( 
        rsi_period=14,
        overbought=70,
        oversold=30,
        commission=0.0005
    )
    print(f"总收益率: {results['total_return']:.2%}")
    print(f"夏普比率: {results['sharpe_ratio']:.2f}")
    print(f"最大回撤: {results['max_drawdown']:.2%}")
    
    # 可视化 
    fig = backtester.plot_results() 
    plt.show() 