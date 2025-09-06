/**
 * 资金费率监控预警系统
 * 功能：
 * 1. 多交易所资金费率实时监控
 * 2. 动态阈值预警 
 * 3. 套利机会识别 
 * 4. 自动对冲执行 
 */
 
const axios = require('axios');
const WebSocket = require('ws');
const EventEmitter = require('events');
const Decimal = require('decimal.js'); 
const { Spot, Futures } = require('binance-api-node');
const { HttpsProxyAgent } = require('https-proxy-agent');
 
// 配置Decimal精度 
Decimal.set({  precision: 8, rounding: Decimal.ROUND_DOWN });
 
class FundingRateMonitor extends EventEmitter {
  constructor(config) {
    super();
    this.config  = {
      thresholds: {
        warning: new Decimal('0.0005'),  // 0.05%
        critical: new Decimal('0.001'),  // 0.1%
        arbitrage: new Decimal('0.002')  // 0.2%
      },
      pollingInterval: 30000, // 30秒 
      ...config 
    };
 
    // 交易所客户端 
    this.exchanges  = {
      binance: new BinanceAdapter(this.config.binance), 
      bybit: new BybitAdapter(this.config.bybit), 
      okx: new OKXAdapter(this.config.okx) 
    };
 
    // 状态存储 
    this.rates  = {};
    this.history  = [];
    this.alertCounts  = {};
    this.positionStatus  = {};
 
    // 初始化 
    this._init();
  }
 
  async _init() {
    // 加载历史数据
    await this._loadHistory();
    
    // 启动轮询任务 
    this._startPolling();
    
    // 连接WebSocket 
    this._connectWebSockets();
  }
 
  async _loadHistory() {
    try {
      // 从数据库或API加载历史资金费率
      const response = await axios.get(`${this.config.apiBase}/funding/history`); 
      this.history  = response.data.map(item  => ({
        ...item,
        rate: new Decimal(item.rate), 
        timestamp: new Date(item.timestamp) 
      }));
    } catch (err) {
      console.error(' 加载历史数据失败:', err.message); 
    }
  }
 
  _startPolling() {
    this.pollInterval  = setInterval(async () => {
      await this._checkAllExchanges();
    }, this.config.pollingInterval); 
 
    // 初始立即执行一次 
    setImmediate(() => this._checkAllExchanges());
  }
 
  async _checkAllExchanges() {
    const results = await Promise.allSettled( 
      Object.entries(this.exchanges).map(async  ([name, exchange]) => {
        const rates = await exchange.fetchFundingRates(); 
        return { exchange: name, rates };
      })
    );
 
    // 处理结果
    results.forEach(result  => {
      if (result.status  === 'fulfilled') {
        this._processRates(result.value.exchange,  result.value.rates); 
      } else {
        console.error(`[${new  Date().toISOString()}] 获取资金费率失败:`, result.reason); 
      }
    });
 
    // 检查套利机会
    this._checkArbitrageOpportunities();
  }
 
  _processRates(exchange, rates) {
    const now = new Date();
    let hasCritical = false;
 
    rates.forEach(rateInfo  => {
      const symbol = rateInfo.symbol; 
      const rate = new Decimal(rateInfo.rate); 
      const nextTime = new Date(rateInfo.nextTime); 
 
      // 存储当前费率
      if (!this.rates[exchange])  this.rates[exchange]  = {};
      this.rates[exchange][symbol]  = {
        rate,
        nextTime,
        timestamp: now 
      };
 
      // 添加到历史记录
      this.history.push({ 
        exchange,
        symbol,
        rate,
        timestamp: now,
        nextTime 
      });
 
      // 检查预警条件 
      const absRate = rate.abs(); 
      if (absRate.gt(this.config.thresholds.critical))  {
        this._triggerAlert('critical', exchange, symbol, rate);
        hasCritical = true;
      } else if (absRate.gt(this.config.thresholds.warning))  {
        this._triggerAlert('warning', exchange, symbol, rate);
      }
 
      // 更新预警计数
      const alertKey = `${exchange}:${symbol}`;
      if (absRate.gt(this.config.thresholds.warning))  {
        this.alertCounts[alertKey]  = (this.alertCounts[alertKey]  || 0) + 1;
      } else {
        this.alertCounts[alertKey]  = 0;
      }
    });
 
    // 如果有严重预警，执行对冲检查 
    if (hasCritical) {
      this._checkHedging();
    }
  }
 
  _triggerAlert(level, exchange, symbol, rate) {
    const alert = {
      level,
      exchange,
      symbol,
      rate: rate.toNumber(), 
      timestamp: new Date(),
      message: `[${level.toUpperCase()}]  ${exchange} ${symbol} 资金费率: ${rate.mul(100).toFixed(4)}%` 
    };
 
    // 触发事件 
    this.emit('alert',  alert);
    
    // 发送通知 
    this._sendNotification(alert);
    
    console.log(alert.message); 
  }
 
  async _sendNotification(alert) {
    try {
      if (this.config.notification?.webhook)  {
        await axios.post(this.config.notification.webhook,  {
          text: alert.message, 
          ...alert 
        });
      }
      
      if (this.config.notification?.telegram)  {
        await axios.post(`https://api.telegram.org/bot${this.config.notification.telegram.token}/sendMessage`,  {
          chat_id: this.config.notification.telegram.chatId, 
          text: alert.message 
        });
      }
    } catch (err) {
      console.error(' 发送通知失败:', err.message); 
    }
  }
 
  _checkArbitrageOpportunities() {
    // 找出所有交易所共有的交易对
    const commonSymbols = this._findCommonSymbols();
    
    commonSymbols.forEach(symbol  => {
      const rates = {};
      
      // 收集各交易所费率 
      Object.keys(this.exchanges).forEach(exchange  => {
        if (this.rates[exchange]?.[symbol])  {
          rates[exchange] = this.rates[exchange][symbol].rate; 
        }
      });
      
      // 检查套利条件 
      if (Object.keys(rates).length  >= 2) {
        const [maxExchange, maxRate] = this._findMaxRate(rates);
        const [minExchange, minRate] = this._findMinRate(rates);
        
        const spread = maxRate.sub(minRate); 
        if (spread.gt(this.config.thresholds.arbitrage))  {
          this._triggerArbitrageAlert(symbol, maxExchange, maxRate, minExchange, minRate);
        }
      }
    });
  }
 
  _findCommonSymbols() {
    const symbolSets = Object.values(this.rates).map( 
      exchangeRates => new Set(Object.keys(exchangeRates  || {}))
    );
    
    if (symbolSets.length  === 0) return [];
    
    return [...symbolSets.reduce((a,  b) => {
      return new Set([...a].filter(x => b.has(x))); 
    })];
  }
 
  _findMaxRate(rates) {
    return Object.entries(rates).reduce( 
      (max, [exchange, rate]) => 
        rate.gt(max[1])  ? [exchange, rate] : max,
      ['', new Decimal('-Infinity')]
    );
  }
 
  _findMinRate(rates) {
    return Object.entries(rates).reduce( 
      (min, [exchange, rate]) => 
        rate.lt(min[1])  ? [exchange, rate] : min,
      ['', new Decimal('Infinity')]
    );
  }
 
  _triggerArbitrageAlert(symbol, longExchange, longRate, shortExchange, shortRate) {
    const spread = longRate.sub(shortRate); 
    const message = `[ARBITRAGE] 套利机会 ${symbol}: ` +
      `做多 ${longExchange} (费率 ${longRate.mul(100).toFixed(4)}%)  / ` +
      `做空 ${shortExchange} (费率 ${shortRate.mul(100).toFixed(4)}%)  ` +
      `价差: ${spread.mul(100).toFixed(4)}%`; 
    
    const alert = {
      level: 'arbitrage',
      symbol,
      longExchange,
      longRate: longRate.toNumber(),  
      shortExchange,
      shortRate: shortRate.toNumber(), 
      spread: spread.toNumber(), 
      timestamp: new Date(),
      message
    };
    
    this.emit('arbitrage',  alert);
    console.log(message); 
    
    // 自动执行对冲 
    if (this.config.autoHedge)  {
      this._executeHedge(symbol, longExchange, shortExchange);
    }
  }
 
  async _executeHedge(symbol, longExchange, shortExchange) {
    const key = `${symbol}:${longExchange}:${shortExchange}`;
    
    // 防止重复执行 
    if (this.positionStatus[key])  {
      console.log(` 对冲已存在: ${key}`);
      return;
    }
    
    console.log(` 执行对冲: ${key}`);
    this.positionStatus[key]  = 'opening';
    
    try {
      // 获取账户余额
      const [longBal, shortBal] = await Promise.all([ 
        this.exchanges[longExchange].getBalance(), 
        this.exchanges[shortExchange].getBalance() 
      ]);
      
      // 计算头寸规模 (使用最小可用余额的50%)
      const maxSize = Decimal.min(longBal.available,  shortBal.available) 
        .mul('0.5')
        .toDecimalPlaces(8);
      
      if (maxSize.lte('0'))  {
        throw new Error('可用资金不足');
      }
      
      // 获取最新价格计算合约数量
      const [longPrice, shortPrice] = await Promise.all([ 
        this.exchanges[longExchange].getPrice(symbol), 
        this.exchanges[shortExchange].getPrice(symbol) 
      ]);
      
      const longQty = maxSize.div(longPrice).toDecimalPlaces(8); 
      const shortQty = maxSize.div(shortPrice).toDecimalPlaces(8); 
      
      // 执行对冲订单 
      const [longOrder, shortOrder] = await Promise.all([ 
        this.exchanges[longExchange].createOrder(symbol,  'buy', longQty),
        this.exchanges[shortExchange].createOrder(symbol,  'sell', shortQty)
      ]);
      
      this.positionStatus[key]  = {
        status: 'opened',
        longExchange,
        longOrder,
        shortExchange,
        shortOrder,
        openedAt: new Date()
      };
      
      console.log(` 对冲建立成功:`, {
        symbol,
        size: maxSize.toString(), 
        longOrder,
        shortOrder
      });
      
    } catch (err) {
      console.error(` 对冲执行失败: ${err.message}`); 
      this.positionStatus[key]  = 'failed';
    }
  }
 
  _connectWebSockets() {
    // 连接各交易所的WebSocket获取实时资金费率
    Object.entries(this.exchanges).forEach(([name,  exchange]) => {
      exchange.connectWebSocket((data)  => {
        if (data.symbol  && data.rate  !== undefined) {
          this._processRates(name, [{
            symbol: data.symbol, 
            rate: new Decimal(data.rate), 
            nextTime: new Date(data.nextTime) 
          }]);
        }
      });
    });
  }
 
  _checkHedging() {
    // 检查现有对冲头寸是否需要调整 
    Object.entries(this.positionStatus).forEach(([key,  status]) => {
      if (status === 'opened') {
        this._monitorHedgePosition(key);
      }
    });
  }
 
  async _monitorHedgePosition(key) {
    const [symbol, longEx, shortEx] = key.split(':'); 
    const position = this.positionStatus[key]; 
    
    try {
      // 获取最新资金费率
      const [longRate, shortRate] = await Promise.all([ 
        this.exchanges[longEx].getFundingRate(symbol), 
        this.exchanges[shortEx].getFundingRate(symbol) 
      ]);
      
      const spread = longRate.sub(shortRate); 
      
      // 如果价差缩小到阈值以下，平仓 
      if (spread.abs().lt(this.config.thresholds.warning))  {
        console.log(` 价差缩小，平仓对冲: ${key}`);
        await this._closeHedgePosition(key);
      }
    } catch (err) {
      console.error(` 监控对冲头寸失败: ${key}`, err.message); 
    }
  }
 
  async _closeHedgePosition(key) {
    const position = this.positionStatus[key]; 
    if (!position || position.status  !== 'opened') return;
    
    try {
      const { longExchange, longOrder, shortExchange, shortOrder } = position;
      
      // 平仓订单 
      await Promise.all([ 
        this.exchanges[longExchange].closePosition(longOrder.symbol), 
        this.exchanges[shortExchange].closePosition(shortOrder.symbol) 
      ]);
      
      // 计算盈亏
      const pnl = await this._calculateHedgePnl(position);
      console.log(` 对冲平仓完成: ${key} PnL: ${pnl.toFixed(4)}`); 
      
      this.positionStatus[key]  = {
        ...position,
        status: 'closed',
        closedAt: new Date(),
        pnl: pnl.toNumber() 
      };
      
    } catch (err) {
      console.error(` 平仓失败: ${key}`, err.message); 
      this.positionStatus[key].status  = 'close_failed';
    }
  }
 
  async _calculateHedgePnl(position) {
    // 简化计算：仅考虑资金费率差异 
    const durationHours = (new Date() - position.openedAt)  / (1000 * 60 * 60);
    const [longRate, shortRate] = await Promise.all([ 
      this.exchanges[position.longExchange].getAvgFundingRate( 
        position.longOrder.symbol,  
        position.openedAt  
      ),
      this.exchanges[position.shortExchange].getAvgFundingRate( 
        position.shortOrder.symbol, 
        position.openedAt  
      )
    ]);
    
    // PnL = (空头费率 - 多头费率) * 头寸规模 * 持续时间
    return shortRate.sub(longRate) 
      .mul(position.longOrder.quantity) 
      .mul(durationHours);
  }
 
  stop() {
    clearInterval(this.pollInterval); 
    Object.values(this.exchanges).forEach(ex  => ex.disconnect()); 
  }
}
 
// 交易所适配器抽象类
class ExchangeAdapter {
  constructor(config) {
    this.config  = config;
    this.ws  = null;
  }
 
  async fetchFundingRates() {
    throw new Error('Not implemented');
  }
 
  async getFundingRate(symbol) {
    throw new Error('Not implemented');
  }
 
  async getAvgFundingRate(symbol, since) {
    throw new Error('Not implemented');
  }
 
  async getBalance() {
    throw new Error('Not implemented');
  }
 
  async getPrice(symbol) {
    throw new Error('Not implemented');
  }
 
  async createOrder(symbol, side, quantity) {
    throw new Error('Not implemented');
  }
 
  async closePosition(symbol) {
    throw new Error('Not implemented');
  }
 
  connectWebSocket(callback) {
    throw new Error('Not implemented');
  }
 
  disconnect() {
    if (this.ws)  {
      this.ws.close(); 
      this.ws  = null;
    }
  }
}
 
// Binance适配器
class BinanceAdapter extends ExchangeAdapter {
  constructor(config) {
    super(config);
    this.client  = Futures({
      apiKey: config.apiKey, 
      apiSecret: config.apiSecret, 
      proxy: config.proxy  ? new HttpsProxyAgent(config.proxy)  : undefined 
    });
  }
 
  async fetchFundingRates() {
    const rates = await this.client.fundingRate(); 
    return rates.map(item  => ({
      symbol: item.symbol, 
      rate: new Decimal(item.fundingRate), 
      nextTime: new Date(item.fundingTime) 
    }));
  }
 
  async getFundingRate(symbol) {
    const rate = await this.client.fundingRate({  symbol });
    return new Decimal(rate.fundingRate); 
  }
 
  async getAvgFundingRate(symbol, since) {
    const rates = await this.client.fundingRateHistory({  symbol, startTime: since.getTime()  });
    if (rates.length  === 0) return new Decimal(0);
    
    const sum = rates.reduce((acc,  item) => acc.add(new  Decimal(item.fundingRate)),  new Decimal(0));
    return sum.div(rates.length); 
  }
 
  async getBalance() {
    const balance = await this.client.balance(); 
    const asset = balance.find(item  => item.asset  === this.config.tradeAsset  || 'USDT');
    return {
      total: new Decimal(asset.balance), 
      available: new Decimal(asset.availableBalance) 
    };
  }
 
  async getPrice(symbol) {
    const ticker = await this.client.prices({  symbol });
    return new Decimal(ticker[symbol]);
  }
 
  async createOrder(symbol, side, quantity) {
    const order = await this.client.order({ 
      symbol,
      side: side.toUpperCase(), 
      type: 'MARKET',
      quantity: quantity.toString() 
    });
    return {
      symbol,
      orderId: order.orderId, 
      quantity: new Decimal(order.executedQty), 
      price: new Decimal(order.price) 
    };
  }
 
  async closePosition(symbol) {
    const position = await this.client.positionRisk({  symbol });
    if (!position || position.length  === 0) {
      throw new Error(`未找到仓位: ${symbol}`);
    }
    
    const posAmt = new Decimal(position[0].positionAmt);
    if (posAmt.eq(0))  {
      throw new Error(`仓位已平: ${symbol}`);
    }
    
    const side = posAmt.gt(0)  ? 'sell' : 'buy';
    return this.createOrder(symbol,  side, posAmt.abs()); 
  }
 
  connectWebSocket(callback) {
    this.ws  = new WebSocket('wss://fstream.binance.com/ws/!markPrice@arr@1s'); 
    
    this.ws.on('message',  (data) => {
      try {
        const updates = JSON.parse(data); 
        updates.forEach(item  => {
          if (item.s && item.r && item.T) {
            callback({
              symbol: item.s,
              rate: item.r,
              nextTime: new Date(item.T)
            });
          }
        });
      } catch (err) {
        console.error('WebSocket 消息解析失败:', err);
      }
    });
    
    this.ws.on('error',  (err) => {
      console.error('Binance  WebSocket错误:', err);
    });
    
    this.ws.on('close',  () => {
      console.log('Binance  WebSocket连接关闭');
      setTimeout(() => this.connectWebSocket(callback),  5000);
    });
  }
}
 
// Bybit适配器 (类似实现)
class BybitAdapter extends ExchangeAdapter {
  // 实现类似BinanceAdapter的接口
}
 
// OKX适配器 (类似实现)
class OKXAdapter extends ExchangeAdapter {
  // 实现类似BinanceAdapter的接口
}
 
// 使用示例
const config = {
  binance: {
    apiKey: 'your_api_key',
    apiSecret: 'your_api_secret',
    tradeAsset: 'USDT'
  },
  bybit: {
    apiKey: 'your_api_key',
    apiSecret: 'your_api_secret'
  },
  notification: {
    telegram: {
      token: 'your_bot_token',
      chatId: 'your_chat_id'
    }
  },
  autoHedge: true
};
 
const monitor = new FundingRateMonitor(config);
 
// 监听预警事件 
monitor.on('alert',  alert => {
  // 处理预警 (发送邮件/短信等)
});
 
monitor.on('arbitrage',  opportunity => {
  // 处理套利机会
});
 
// 停止监控 
process.on('SIGINT',  () => {
  monitor.stop(); 
  process.exit(); 
});