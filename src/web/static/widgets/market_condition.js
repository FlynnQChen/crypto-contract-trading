/**
 * 市场状态分析系统 
 * 功能：
 * 1. 多时间框架趋势识别
 * 2. 波动率聚类检测 
 * 3. 市场状态机器学习分类 
 * 4. 极端事件预警
 */
 
const tf = require('@tensorflow/tfjs-node');
const PCA = require('ml-pca');
const { KMeans } = require('ml-kmeans');
const { BollingerBands, RSI, MACD } = require('technicalindicators');
const { Decimal } = require('decimal.js'); 
const EventEmitter = require('events');
 
class MarketCondition extends EventEmitter {
  constructor(config) {
    super();
    this.config  = {
      historyLength: 200,      // 分析历史长度 
      volatilityWindow: 20,    // 波动率窗口 
      trendThreshold: 0.15,    // 趋势阈值 
      clusterCount: 4,         // 市场状态聚类数
      ...config 
    };
 
    // 状态存储 
    this.marketData  = [];
    this.currentState  = null;
    this.model  = null;
    
    // 初始化机器学习模型
    this._initModel();
  }
 
  async _initModel() {
    // 加载预训练模型 
    try {
      this.model  = await tf.loadLayersModel(this.config.modelPath  || 'file://./market_state_model/model.json'); 
      console.log(' 市场状态模型加载成功');
    } catch (err) {
      console.warn(' 无法加载预训练模型，将使用动态聚类:', err);
      this.model  = null;
    }
  }
 
  addData(newData) {
    // 添加新数据点并维护固定长度
    this.marketData.push({ 
      timestamp: newData.timestamp, 
      open: new Decimal(newData.open), 
      high: new Decimal(newData.high), 
      low: new Decimal(newData.low), 
      close: new Decimal(newData.close), 
      volume: new Decimal(newData.volume  || 0)
    });
 
    // 保持数据长度 
    if (this.marketData.length  > this.config.historyLength)  {
      this.marketData.shift(); 
    }
 
    // 当有足够数据时开始分析 
    if (this.marketData.length  >= this.config.volatilityWindow)  {
      this.analyze(); 
    }
  }
 
  analyze() {
    // 1. 计算技术指标
    const indicators = this._calculateIndicators();
    
    // 2. 特征工程 
    const features = this._extractFeatures(indicators);
    
    // 3. 市场状态分类 
    this._classifyMarketState(features);
    
    // 4. 检查极端事件 
    this._checkExtremeEvents();
  }
 
  _calculateIndicators() {
    const closes = this.marketData.map(d  => d.close.toNumber()); 
    const highs = this.marketData.map(d  => d.high.toNumber()); 
    const lows = this.marketData.map(d  => d.low.toNumber()); 
    const volumes = this.marketData.map(d  => d.volume.toNumber()); 
 
    // 计算布林带 
    const bbInput = {
      period: 20,
      stdDev: 2,
      values: closes
    };
    const bollingerBands = BollingerBands.calculate(bbInput); 
 
    // 计算RSI 
    const rsi = RSI.calculate({  values: closes, period: 14 });
 
    // 计算MACD 
    const macd = MACD.calculate({ 
      values: closes,
      fastPeriod: 12,
      slowPeriod: 26,
      signalPeriod: 9,
      SimpleMAOscillator: false,
      SimpleMASignal: false 
    });
 
    // 计算波动率
    const volatilities = [];
    for (let i = this.config.volatilityWindow;  i <= closes.length;  i++) {
      const window = closes.slice(i  - this.config.volatilityWindow,  i);
      const returns = window.map((x,  idx) => 
        idx > 0 ? Math.log(x  / window[idx - 1]) : 0 
      );
      const stdDev = Math.sqrt( 
        returns.reduce((sum,  x) => sum + Math.pow(x,  2), 0) / returns.length 
      );
      volatilities.push(stdDev); 
    }
 
    return {
      bollingerBands,
      rsi,
      macd,
      volatilities,
      closes,
      highs,
      lows,
      volumes
    };
  }
 
  _extractFeatures(indicators) {
    // 1. 趋势特征 
    const priceChanges = [];
    for (let i = 1; i < indicators.closes.length;  i++) {
      priceChanges.push( 
        indicators.closes[i]  / indicators.closes[i  - 1] - 1 
      );
    }
    const avgChange = priceChanges.reduce((sum,  x) => sum + x, 0) / priceChanges.length; 
    const trendStrength = new Decimal(avgChange).abs().toNumber();
 
    // 2. 波动率特征
    const currentVolatility = indicators.volatilities[indicators.volatilities.length  - 1];
    const volatilityRatio = currentVolatility / 
      (indicators.volatilities.reduce((sum,  x) => sum + x, 0) / indicators.volatilities.length); 
 
    // 3. 动量特征
    const lastMacd = indicators.macd[indicators.macd.length  - 1];
    const macdSignalRatio = lastMacd && lastMacd.MACD ? 
      Math.abs(lastMacd.MACD  / lastMacd.signal)  : 0;
 
    // 4. 流动性特征 
    const volumeAvg = indicators.volumes.reduce((sum,  x) => sum + x, 0) / indicators.volumes.length; 
    const currentVolume = indicators.volumes[indicators.volumes.length  - 1];
    const volumeRatio = currentVolume / volumeAvg;
 
    // 5. 市场宽度特征
    const bbWidth = indicators.bollingerBands[indicators.bollingerBands.length  - 1];
    const bbPercent = bbWidth ? 
      (indicators.closes[indicators.closes.length  - 1] - bbWidth.lower)  / 
      (bbWidth.upper  - bbWidth.lower)  : 0.5;
 
    return {
      trendStrength,
      volatilityRatio,
      macdSignalRatio,
      volumeRatio,
      bbPercent,
      rsi: indicators.rsi[indicators.rsi.length  - 1] / 100 
    };
  }
 
  _classifyMarketState(features) {
    // 特征向量
    const featureVector = [
      features.trendStrength, 
      features.volatilityRatio, 
      features.macdSignalRatio, 
      features.volumeRatio, 
      features.bbPercent, 
      features.rsi 
    ];
 
    let state;
    
    if (this.model)  {
      // 使用神经网络分类 
      const inputTensor = tf.tensor2d([featureVector]); 
      const prediction = this.model.predict(inputTensor); 
      const predictedClass = prediction.argMax(1).dataSync()[0]; 
      state = this._interpretState(predictedClass);
    } else {
      // 使用动态聚类
      const pca = new PCA([featureVector]);
      const reducedFeatures = pca.predict([featureVector],  { nComponents: 2 });
      
      const kmeans = new KMeans({ 
        k: this.config.clusterCount, 
        initialization: 'kmeans++'
      });
      
      const clusters = kmeans.cluster(reducedFeatures); 
      state = this._interpretState(clusters[0]);
    }
 
    // 状态变化检测
    if (this.currentState  && this.currentState.type  !== state.type)  {
      this.emit('stateChange',  {
        from: this.currentState, 
        to: state,
        timestamp: Date.now() 
      });
    }
 
    this.currentState  = {
      ...state,
      features,
      timestamp: Date.now() 
    };
  }
 
  _interpretState(clusterId) {
    const states = [
      { type: 'trend_up', volatility: 'low', description: '平稳上涨' },
      { type: 'trend_down', volatility: 'low', description: '平稳下跌' },
      { type: 'volatile', volatility: 'high', description: '高波动震荡' },
      { type: 'ranging', volatility: 'low', description: '低波动横盘' }
    ];
    
    return states[clusterId % states.length]; 
  }
 
  _checkExtremeEvents() {
    const lastClose = this.marketData[this.marketData.length  - 1].close;
    const prevClose = this.marketData[this.marketData.length  - 2].close;
    const change = lastClose.sub(prevClose).div(prevClose).abs(); 
 
    // 1. 闪崩/暴涨检测
    if (change.gt('0.05'))  { // 5%以上单根K线变动 
      const direction = lastClose.gt(prevClose)  ? 'surge' : 'crash';
      this.emit('extremeEvent',  {
        type: 'price_' + direction,
        change: change.toNumber(), 
        timestamp: Date.now(), 
        data: {
          lastClose: lastClose.toNumber(), 
          prevClose: prevClose.toNumber() 
        }
      });
    }
 
    // 2. 流动性枯竭检测 
    const volumeAvg = this.marketData  
      .slice(-this.config.volatilityWindow) 
      .reduce((sum, d) => sum.add(d.volume),  new Decimal(0))
      .div(this.config.volatilityWindow); 
    
    const lastVolume = this.marketData[this.marketData.length  - 1].volume;
    if (lastVolume.div(volumeAvg).lt('0.3'))  {
      this.emit('extremeEvent',  {
        type: 'liquidity_drop',
        ratio: lastVolume.div(volumeAvg).toNumber(), 
        timestamp: Date.now() 
      });
    }
 
    // 3. 波动率激增检测 
    const volatilities = this._calculateVolatilities();
    const currentVol = volatilities[volatilities.length - 1];
    const volAvg = volatilities.reduce((sum,  x) => sum + x, 0) / volatilities.length; 
    
    if (currentVol > volAvg * 3) {
      this.emit('extremeEvent',  {
        type: 'volatility_spike',
        ratio: currentVol / volAvg,
        timestamp: Date.now() 
      });
    }
  }
 
  _calculateVolatilities() {
    const closes = this.marketData.map(d  => d.close.toNumber()); 
    const volatilities = [];
    
    for (let i = this.config.volatilityWindow;  i <= closes.length;  i++) {
      const window = closes.slice(i  - this.config.volatilityWindow,  i);
      const returns = window.map((x,  idx) => 
        idx > 0 ? Math.log(x  / window[idx - 1]) : 0 
      );
      const stdDev = Math.sqrt( 
        returns.reduce((sum,  x) => sum + Math.pow(x,  2), 0) / returns.length 
      );
      volatilities.push(stdDev); 
    }
    
    return volatilities;
  }
 
  getCurrentState() {
    return this.currentState; 
  }
 
  getMarketData() {
    return this.marketData; 
  }
 
  async saveModel(path) {
    if (this.model)  {
      await this.model.save(`file://${path}`); 
    }
  }
}
 
// 使用示例
const marketAnalyzer = new MarketCondition({
  historyLength: 500,
  clusterCount: 5
});
 
// 模拟数据输入 
const mockData = Array.from({  length: 200 }, (_, i) => ({
  timestamp: Date.now()  - (200 - i) * 60000,
  open: 100 + Math.sin(i  / 10) * 10,
  high: 100 + Math.sin(i  / 10) * 10 + Math.random()  * 5,
  low: 100 + Math.sin(i  / 10) * 10 - Math.random()  * 5,
  close: 100 + Math.sin(i  / 10) * 10 + (Math.random()  - 0.5) * 2,
  volume: 1000 + Math.random()  * 500
}));
 
mockData.forEach(data  => marketAnalyzer.addData(data)); 
 
// 监听市场状态变化
marketAnalyzer.on('stateChange',  ({ from, to }) => {
  console.log(` 市场状态变化: ${from.description}  → ${to.description}`); 
});
 
marketAnalyzer.on('extremeEvent',  event => {
  console.warn(` 极端事件: ${event.type}`,  event);
});
 
// 获取当前状态
console.log(' 当前市场状态:', marketAnalyzer.getCurrentState()); 