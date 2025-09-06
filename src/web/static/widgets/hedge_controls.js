/**
 * 量化对冲控制系统 
 * 功能：
 * 1. 多策略动态对冲
 * 2. 实时风险暴露监控
 * 3. 跨交易所自动平衡 
 * 4. 智能止损/止盈 
 */
 
const EventEmitter = require('events');
const Decimal = require('decimal.js'); 
const { WebSocket } = require('ws');
const { HttpsProxyAgent } = require('https-proxy-agent');
const { Spot, Futures } = require('binance-api-node');
 
// 配置Decimal精度 
Decimal.set({  precision: 8, rounding: Decimal.ROUND_DOWN });
 
class HedgeControls extends EventEmitter {
  constructor(config) {
    super();
    this.config  = {
      maxExposure: new Decimal('0.1'),  // 最大风险敞口(10%)
      rebalanceThreshold: new Decimal('0.03'), // 再平衡阈值(3%)
      stopLoss: new Decimal('0.05'),    // 止损线(5%)
      takeProfit: new Decimal('0.1'),   // 止盈线(10%)
      pollingInterval: 10000,           // 10秒轮询
      ...config
    };
 
    // 交易所适配器
    this.exchanges  = this._initExchanges();
    
    // 对冲状态
    this.positions  = {};
    this.exposure  = new Decimal('0');
    this.pnl  = {
      daily: new Decimal('0'),
      total: new Decimal('0')
    };
    
    // 风险参数 
    this.riskParams  = {
      volatility: new Decimal('0'),
      correlation: {}
    };
 
    // 控制标志 
    this.isHedgingActive  = false;
    this.emergencyStop  = false;
 
    // 初始化 
    this._init();
  }
 
  async _init() {
    // 加载历史数据 
    await this._loadHistoricalData();
    
    // 启动风险监控 
    this._startRiskEngine();
    
    // 连接交易所WebSocket 
    this._connectWebSockets();
    
    // 启动主循环 
    this._startMainLoop();
  }
 
  async _loadHistoricalData() {
    try {
      const [volatility, correlation] = await Promise.all([ 
        this._fetchHistoricalVolatility(),
        this._fetchAssetCorrelation()
      ]);
      
      this.riskParams.volatility  = new Decimal(volatility);
      this.riskParams.correlation  = correlation;
      
    } catch (err) {
      console.error(' 加载历史数据失败:', err);
      this.emit('error',  err);
    }
  }
 
  async _fetchHistoricalVolatility() {
    // 实现获取历史波动率逻辑 
    return '0.02'; // 示例值 
  }
 
  async _fetchAssetCorrelation() {
    // 实现获取资产相关性逻辑 
    return { 'BTC-USDT': 0.9, 'ETH-USDT': 0.8 }; // 示例值 
  }
 
  _initExchanges() {
    const exchanges = {};
    
    // 初始化各交易所适配器
    if (this.config.binance)  {
      exchanges.binance  = new BinanceHedgeAdapter(this.config.binance); 
    }
    
    if (this.config.ftx)  {
      exchanges.ftx  = new FTXHedgeAdapter(this.config.ftx); 
    }
    
    if (this.config.bybit)  {
      exchanges.bybit  = new BybitHedgeAdapter(this.config.bybit); 
    }
    
    return exchanges;
  }
 
  _startRiskEngine() {
    this.riskInterval  = setInterval(() => {
      this._calculateRiskExposure();
    }, this.config.pollingInterval); 
  }
 
  async _calculateRiskExposure() {
    try {
      // 获取所有仓位 
      const allPositions = await this._getAllPositions();
      
      // 计算净风险敞口 
      const exposure = this._calculateNetExposure(allPositions);
      this.exposure  = exposure;
      
      // 触发风险事件
      if (exposure.abs().gt(this.config.maxExposure))  {
        this.emit('riskExceeded',  exposure.toNumber()); 
        await this._reduceExposure();
      }
      
      // 更新波动率 
      await this._updateVolatility();
      
    } catch (err) {
      this.emit('error',  err);
    }
  }
 
  async _getAllPositions() {
    const positions = {};
    
    await Promise.all(Object.entries(this.exchanges).map(async  ([name, exchange]) => {
      positions[name] = await exchange.getPositions(); 
    }));
    
    return positions;
  }
 
  _calculateNetExposure(positions) {
    let netExposure = new Decimal('0');
    
    // 汇总所有交易所所有仓位的美元价值
    Object.values(positions).forEach(exchangePositions  => {
      Object.values(exchangePositions).forEach(pos  => {
        const value = new Decimal(pos.size).mul(pos.markPrice); 
        netExposure = pos.side  === 'long' 
          ? netExposure.add(value)  
          : netExposure.sub(value); 
      });
    });
    
    // 计算占总资产的比例 
    return netExposure.div(this._getTotalPortfolioValue()); 
  }
 
  async _getTotalPortfolioValue() {
    let total = new Decimal('0');
    
    await Promise.all(Object.values(this.exchanges).map(async  exchange => {
      const balance = await exchange.getTotalBalance(); 
      total = total.add(balance); 
    }));
    
    return total;
  }
 
  async _reduceExposure() {
    if (this.emergencyStop)  return;
    
    console.log(` 风险敞口过高(${this.exposure.mul(100).toFixed(2)}%),  开始降低风险...`);
    
    try {
      // 获取需要平仓的方向 
      const reduceSide = this.exposure.gt('0')  ? 'long' : 'short';
      
      // 获取所有仓位并按盈亏排序 
      const positions = await this._getAllPositions();
      const flatPositions = this._flattenPositions(positions);
      
      // 优先平掉亏损仓位 
      const sortedPositions = flatPositions 
        .filter(p => p.side  === reduceSide)
        .sort((a, b) => a.unrealizedPnl  - b.unrealizedPnl); 
      
      // 计算需要平仓的金额
      const targetReduction = this.exposure.abs().sub(this.config.maxExposure.mul('0.8')); 
      const usdToReduce = targetReduction.mul(this._getTotalPortfolioValue()); 
      
      // 执行平仓 
      await this._closePositionsUntilTarget(sortedPositions, usdToReduce);
      
    } catch (err) {
      this.emit('error',  new Error(`降低风险失败: ${err.message}`)); 
    }
  }
 
  _flattenPositions(positionsByExchange) {
    const result = [];
    
    Object.entries(positionsByExchange).forEach(([exchange,  positions]) => {
      Object.values(positions).forEach(pos  => {
        result.push({ 
          exchange,
          symbol: pos.symbol, 
          side: pos.side, 
          size: new Decimal(pos.size), 
          markPrice: new Decimal(pos.markPrice), 
          unrealizedPnl: new Decimal(pos.unrealizedPnl) 
        });
      });
    });
    
    return result;
  }
 
  async _closePositionsUntilTarget(positions, targetUsd) {
    let remaining = new Decimal(targetUsd);
    
    for (const pos of positions) {
      if (remaining.lte('0'))  break;
      
      const positionValue = pos.size.mul(pos.markPrice); 
      const closeSize = Decimal.min(pos.size,  remaining.div(pos.markPrice)); 
      
      try {
        console.log(` 平仓 ${pos.exchange}  ${pos.symbol}  ${pos.side}  ${closeSize}`);
        await this.exchanges[pos.exchange].closePosition( 
          pos.symbol,  
          pos.side  === 'long' ? 'sell' : 'buy',
          closeSize
        );
        
        remaining = remaining.sub(closeSize.mul(pos.markPrice)); 
      } catch (err) {
        console.error(` 平仓失败: ${pos.exchange}  ${pos.symbol}`,  err);
      }
    }
  }
 
  async _updateVolatility() {
    // 实现波动率更新逻辑 
    this.riskParams.volatility  = this.riskParams.volatility.mul('0.9').add( 
      this._estimateCurrentVolatility().mul('0.1')
    );
  }
 
  _estimateCurrentVolatility() {
    // 简化实现 - 实际应从市场数据计算 
    return new Decimal('0.02');
  }
 
  _connectWebSockets() {
    Object.values(this.exchanges).forEach(exchange  => {
      exchange.connectWebSocket(data  => {
        this._handleMarketUpdate(data);
      });
    });
  }
 
  _handleMarketUpdate(data) {
    // 更新本地仓位信息 
    if (data.type  === 'position') {
      this._updatePositionData(data);
    }
    
    // 触发对冲逻辑 
    if (this.isHedgingActive  && data.type  === 'ticker') {
      this._checkHedgeOpportunity(data);
    }
  }
 
  _updatePositionData(data) {
    const { exchange, symbol, side, size, markPrice } = data;
    
    if (!this.positions[exchange])  {
      this.positions[exchange]  = {};
    }
    
    if (new Decimal(size).eq('0')) {
      delete this.positions[exchange][symbol]; 
    } else {
      this.positions[exchange][symbol]  = {
        side,
        size: new Decimal(size),
        markPrice: new Decimal(markPrice),
        updatedAt: new Date()
      };
    }
  }
 
  async _checkHedgeOpportunity(ticker) {
    if (this.emergencyStop)  return;
    
    try {
      // 获取所有交易所的相同交易对价格 
      const allPrices = await this._getAllPrices(ticker.symbol); 
      
      // 寻找最佳对冲机会
      const opportunity = this._findBestHedgeOpportunity(ticker.symbol,  allPrices);
      
      if (opportunity) {
        await this._executeHedge(opportunity);
      }
    } catch (err) {
      this.emit('error',  err);
    }
  }
 
  async _getAllPrices(symbol) {
    const prices = {};
    
    await Promise.all(Object.entries(this.exchanges).map(async  ([name, exchange]) => {
      try {
        prices[name] = await exchange.getPrice(symbol); 
      } catch (err) {
        console.error(` 获取价格失败 ${name} ${symbol}:`, err);
      }
    }));
    
    return prices;
  }
 
  _findBestHedgeOpportunity(symbol, prices) {
    const entries = Object.entries(prices).filter(([_,  price]) => price);
    
    if (entries.length  < 2) return null;
    
    // 寻找最高价和最低价
    let maxExchange, maxPrice;
    let minExchange, minPrice;
    
    for (const [exchange, price] of entries) {
      const decimalPrice = new Decimal(price);
      
      if (!maxPrice || decimalPrice.gt(maxPrice))  {
        maxPrice = decimalPrice;
        maxExchange = exchange;
      }
      
      if (!minPrice || decimalPrice.lt(minPrice))  {
        minPrice = decimalPrice;
        minExchange = exchange;
      }
    }
    
    // 检查价差是否足够 
    const spread = maxPrice.sub(minPrice); 
    const spreadRatio = spread.div(minPrice); 
    
    if (spreadRatio.lt(this.config.minHedgeSpread))  {
      return null;
    }
    
    return {
      symbol,
      longExchange: minExchange,
      longPrice: minPrice,
      shortExchange: maxExchange,
      shortPrice: maxPrice,
      spread: spread,
      spreadRatio: spreadRatio 
    };
  }
 
  async _executeHedge(opportunity) {
    const { symbol, longExchange, shortExchange } = opportunity;
    const key = `${symbol}:${longExchange}:${shortExchange}`;
    
    // 检查是否已有对冲 
    if (this.activeHedges[key])  {
      return;
    }
    
    console.log(` 执行对冲: ${key}`);
    this.activeHedges[key]  = { ...opportunity, status: 'opening' };
    
    try {
      // 计算对冲规模 
      const size = await this._calculateHedgeSize(opportunity);
      
      // 执行对冲订单 
      const [longOrder, shortOrder] = await Promise.all([ 
        this.exchanges[longExchange].createOrder(symbol,  'buy', size),
        this.exchanges[shortExchange].createOrder(symbol,  'sell', size)
      ]);
      
      // 更新状态
      this.activeHedges[key]  = {
        ...opportunity,
        status: 'active',
        longOrder,
        shortOrder,
        size,
        entryTime: new Date()
      };
      
      this.emit('hedgeOpened',  this.activeHedges[key]); 
      
    } catch (err) {
      console.error(` 对冲执行失败: ${key}`, err);
      this.activeHedges[key].status  = 'failed';
      this.emit('hedgeFailed',  { key, error: err.message  });
    }
  }
 
  async _calculateHedgeSize(opportunity) {
    // 获取账户余额
    const [longBalance, shortBalance] = await Promise.all([ 
      this.exchanges[opportunity.longExchange].getAvailableBalance(), 
      this.exchanges[opportunity.shortExchange].getAvailableBalance() 
    ]);
    
    // 计算最大可用规模
    const maxLongSize = longBalance.div(opportunity.longPrice); 
    const maxShortSize = shortBalance.div(opportunity.shortPrice); 
    const maxSize = Decimal.min(maxLongSize,  maxShortSize);
    
    // 应用风险控制 
    const riskAdjustedSize = maxSize.mul('0.1');  // 使用10%的可用资金 
    
    return riskAdjustedSize.toDecimalPlaces(8); 
  }
 
  _startMainLoop() {
    this.mainInterval  = setInterval(async () => {
      if (this.emergencyStop)  return;
      
      try {
        // 监控对冲仓位 
        await this._monitorActiveHedges();
        
        // 检查再平衡需求 
        await this._checkRebalance();
        
        // 更新PnL 
        await this._updatePnL();
        
      } catch (err) {
        this.emit('error',  err);
      }
    }, this.config.pollingInterval); 
  }
 
  async _monitorActiveHedges() {
    await Promise.all(Object.entries(this.activeHedges).map(async  ([key, hedge]) => {
      if (hedge.status  !== 'active') return;
      
      try {
        // 获取最新价格 
        const [longPrice, shortPrice] = await Promise.all([ 
          this.exchanges[hedge.longExchange].getPrice(hedge.symbol), 
          this.exchanges[hedge.shortExchange].getPrice(hedge.symbol) 
        ]);
        
        // 计算当前价差
        const currentSpread = new Decimal(shortPrice).sub(longPrice);
        const currentRatio = currentSpread.div(longPrice); 
        
        // 检查止盈/止损 
        const entryRatio = hedge.spreadRatio; 
        const ratioChange = entryRatio.sub(currentRatio); 
        
        // 止盈逻辑 
        if (ratioChange.gte(this.config.takeProfit.mul('0.5')))  {
          console.log(` 对冲止盈: ${key}`);
          await this._closeHedge(key, 'take_profit');
          return;
        }
        
        // 止损逻辑
        if (ratioChange.lte(this.config.stopLoss.neg()))  {
          console.log(` 对冲止损: ${key}`);
          await this._closeHedge(key, 'stop_loss');
          return;
        }
        
        // 更新对冲状态 
        this.activeHedges[key]  = {
          ...hedge,
          currentSpread,
          currentRatio,
          pnl: this._calculateHedgePnl(hedge, longPrice, shortPrice)
        };
        
      } catch (err) {
        console.error(` 监控对冲失败: ${key}`, err);
      }
    }));
  }
 
  async _closeHedge(key, reason) {
    const hedge = this.activeHedges[key]; 
    if (!hedge || hedge.status  !== 'active') return;
    
    try {
      // 平仓 
      await Promise.all([ 
        this.exchanges[hedge.longExchange].closePosition(hedge.symbol,  'sell', hedge.size), 
        this.exchanges[hedge.shortExchange].closePosition(hedge.symbol,  'buy', hedge.size) 
      ]);
      
      // 计算最终PnL
      const pnl = this._calculateHedgePnl(
        hedge,
        await this.exchanges[hedge.longExchange].getPrice(hedge.symbol), 
        await this.exchanges[hedge.shortExchange].getPrice(hedge.symbol) 
      );
      
      // 更新状态 
      this.activeHedges[key].status  = 'closed';
      this.activeHedges[key].exitTime  = new Date();
      this.activeHedges[key].closeReason  = reason;
      this.activeHedges[key].finalPnl  = pnl;
      
      // 更新总PnL
      this.pnl.total  = this.pnl.total.add(pnl); 
      
      this.emit('hedgeClosed',  this.activeHedges[key]); 
      
    } catch (err) {
      console.error(` 平仓对冲失败: ${key}`, err);
      this.activeHedges[key].status  = 'close_failed';
      this.emit('hedgeCloseFailed',  { key, error: err.message  });
    }
  }
 
  _calculateHedgePnl(hedge, longPrice, shortPrice) {
    const longChange = new Decimal(longPrice).sub(hedge.longPrice); 
    const shortChange = new Decimal(hedge.shortPrice).sub(shortPrice); 
    return longChange.add(shortChange).mul(hedge.size); 
  }
 
  async _checkRebalance() {
    // 检查各交易所之间的资金平衡 
    const balances = await this._getExchangeBalances();
    const total = Object.values(balances).reduce((sum,  b) => sum.add(b),  new Decimal('0'));
    const avg = total.div(Object.keys(balances).length); 
    
    // 找出需要调整的交易所 
    const needsRebalance = Object.entries(balances) 
      .filter(([_, bal]) => bal.sub(avg).abs().div(total).gt(this.config.rebalanceThreshold)); 
    
    if (needsRebalance.length  > 0) {
      await this._performRebalance(balances, total);
    }
  }
 
  async _getExchangeBalances() {
    const balances = {};
    
    await Promise.all(Object.entries(this.exchanges).map(async  ([name, exchange]) => {
      balances[name] = await exchange.getTotalBalance(); 
    }));
    
    return balances;
  }
 
  async _performRebalance(balances, total) {
    console.log(' 执行跨交易所再平衡...');
    
    // 计算目标余额 
    const target = total.div(Object.keys(balances).length); 
    
    // 创建转账任务 
    const transfers = [];
    
    Object.entries(balances).forEach(([name,  balance]) => {
      const diff = balance.sub(target); 
      if (diff.abs().div(total).lt(this.config.rebalanceThreshold))  return;
      
      // 找出最适合接收资金的交易所 
      const recipient = Object.keys(balances).find( 
        other => name !== other && balances[other].lt(target)
      );
      
      if (recipient) {
        const amount = Decimal.min(diff,  target.sub(balances[recipient])); 
        transfers.push({  from: name, to: recipient, amount });
      }
    });
    
    // 执行转账 
    await Promise.all(transfers.map(async  ({ from, to, amount }) => {
      try {
        console.log(` 转账 ${amount} from ${from} to ${to}`);
        await this.exchanges[from].transferTo( 
          this.exchanges[to], 
          amount,
          this.config.transferAsset  || 'USDT'
        );
      } catch (err) {
        console.error(` 转账失败 ${from} -> ${to}:`, err);
      }
    }));
  }
 
  async _updatePnL() {
    // 实现每日PnL计算和重置 
    const now = new Date();
    if (now.getHours()  === 0 && now.getMinutes()  < 10) {
      if (!this.dailyPnlReset)  {
        this.emit('dailyPnl',  this.pnl.daily.toNumber()); 
        this.pnl.daily  = new Decimal('0');
        this.dailyPnlReset  = true;
      }
    } else {
      this.dailyPnlReset  = false;
    }
  }
 
  startHedging() {
    this.isHedgingActive  = true;
    this.emit('hedgingStarted'); 
  }
 
  stopHedging() {
    this.isHedgingActive  = false;
    this.emit('hedgingStopped'); 
  }
 
  emergencyShutdown() {
    this.emergencyStop  = true;
    this.stopHedging(); 
    
    // 开始平掉所有仓位 
    this._closeAllPositions();
    
    this.emit('emergencyShutdown'); 
  }
 
  async _closeAllPositions() {
    console.log(' 紧急平仓所有仓位...');
    
    const positions = await this._getAllPositions();
    const flatPositions = this._flattenPositions(positions);
    
    await Promise.all(flatPositions.map(pos  => {
      return this.exchanges[pos.exchange].closePosition( 
        pos.symbol, 
        pos.side  === 'long' ? 'sell' : 'buy',
        pos.size  
      ).catch(err => {
        console.error(` 平仓失败 ${pos.exchange}  ${pos.symbol}:`,  err);
      });
    }));
  }
 
  getStatus() {
    return {
      active: this.isHedgingActive, 
      emergencyStop: this.emergencyStop, 
      exposure: this.exposure.toNumber(), 
      pnl: {
        daily: this.pnl.daily.toNumber(), 
        total: this.pnl.total.toNumber() 
      },
      activeHedges: Object.keys(this.activeHedges).length, 
      riskParams: {
        volatility: this.riskParams.volatility.toNumber() 
      }
    };
  }
}
 
// 交易所对冲适配器基类
class HedgeExchangeAdapter {
  constructor(config) {
    this.config  = config;
    this.ws  = null;
  }
 
  async getPositions() {
    throw new Error('Not implemented');
  }
 
  async getPrice(symbol) {
    throw new Error('Not implemented');
  }
 
  async createOrder(symbol, side, quantity) {
    throw new Error('Not implemented');
  }
 
  async closePosition(symbol, side, quantity) {
    throw new Error('Not implemented');
  }
 
  async getTotalBalance() {
    throw new Error('Not implemented');
  }
 
  async getAvailableBalance() {
    throw new Error('Not implemented');
  }
 
  async transferTo(exchange, amount, asset) {
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
 
// Binance对冲适配器 
class BinanceHedgeAdapter extends HedgeExchangeAdapter {
  constructor(config) {
    super(config);
    this.client  = Futures({
      apiKey: config.apiKey, 
      apiSecret: config.apiSecret, 
      proxy: config.proxy  ? new HttpsProxyAgent(config.proxy)  : undefined 
    });
  }
 
  async getPositions() {
    const positions = await this.client.positionRisk(); 
    return positions.reduce((acc,  pos) => {
      if (Math.abs(parseFloat(pos.positionAmt))  > 0) {
        acc[pos.symbol] = {
          side: parseFloat(pos.positionAmt)  > 0 ? 'long' : 'short',
          size: new Decimal(pos.positionAmt).abs(), 
          entryPrice: new Decimal(pos.entryPrice), 
          markPrice: new Decimal(pos.markPrice), 
          unrealizedPnl: new Decimal(pos.unRealizedProfit) 
        };
      }
      return acc;
    }, {});
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
      orderId: order.orderId, 
      symbol,
      side,
      quantity: new Decimal(order.executedQty), 
      price: new Decimal(order.price) 
    };
  }
 
  async closePosition(symbol, side, quantity) {
    return this.createOrder(symbol,  side, quantity);
  }
 
  async getTotalBalance() {
    const balance = await this.client.balance(); 
    const asset = balance.find(item  => item.asset  === this.config.tradeAsset  || 'USDT');
    return new Decimal(asset.balance); 
  }
 
  async getAvailableBalance() {
    const balance = await this.client.balance(); 
    const asset = balance.find(item  => item.asset  === this.config.tradeAsset  || 'USDT');
    return new Decimal(asset.availableBalance); 
  }
 
  async transferTo(exchange, amount, asset) {
    // 实现跨交易所转账逻辑 
    throw new Error('Binance转账功能未实现');
  }
 
  connectWebSocket(callback) {
    this.ws  = new WebSocket('wss://fstream.binance.com/ws'); 
    
    this.ws.on('open',  () => {
      // 订阅仓位和价格更新
      this.ws.send(JSON.stringify({ 
        method: 'SUBSCRIBE',
        params: ['!markPrice@arr@1s', '!userData'],
        id: Date.now() 
      }));
    });
    
    this.ws.on('message',  (data) => {
      try {
        const msg = JSON.parse(data); 
        
        // 处理仓位更新 
        if (msg.e === 'ACCOUNT_UPDATE') {
          msg.a.P.forEach(pos  => {
            callback({
              type: 'position',
              exchange: 'binance',
              symbol: pos.s,
              side: parseFloat(pos.pa)  > 0 ? 'long' : 'short',
              size: Math.abs(parseFloat(pos.pa)), 
              markPrice: parseFloat(pos.mp) 
            });
          });
        }
        
        // 处理价格更新 
        if (Array.isArray(msg)  && msg[0]?.s) {
          msg.forEach(ticker  => {
            callback({
              type: 'ticker',
              exchange: 'binance',
              symbol: ticker.s,
              price: parseFloat(ticker.p),
              timestamp: ticker.E 
            });
          });
        }
      } catch (err) {
        console.error('WebSocket 消息解析失败:', err);
      }
    });
  }
}
 
// FTX对冲适配器 (类似实现)
class FTXHedgeAdapter extends HedgeExchangeAdapter {
  // 实现类似Binance的接口
}
 
// Bybit对冲适配器 (类似实现)
class BybitHedgeAdapter extends HedgeExchangeAdapter {
  // 实现类似Binance的接口 
}
 
// 使用示例 
const hedgeControl = new HedgeControls({
  binance: {
    apiKey: 'your_api_key',
    apiSecret: 'your_api_secret',
    tradeAsset: 'USDT'
  },
  maxExposure: '0.15', // 15%
  stopLoss: '0.03',    // 3%
  takeProfit: '0.08'   // 8%
});
 
// 启动对冲 
hedgeControl.startHedging(); 
 
// 监听事件 
hedgeControl.on('hedgeOpened',  hedge => {
  console.log(` 对冲开仓: ${hedge.symbol}`); 
});
 
hedgeControl.on('hedgeClosed',  hedge => {
  console.log(` 对冲平仓: ${hedge.symbol}  原因: ${hedge.closeReason}  PnL: ${hedge.finalPnl}`); 
});
 
hedgeControl.on('riskExceeded',  exposure => {
  console.log(` 风险敞口过高: ${(exposure * 100).toFixed(2)}%`);
});
 
// 获取状态 
setInterval(() => {
  console.log(' 当前状态:', hedgeControl.getStatus()); 
}, 60000);