# src/core/cli.py  
from src.core.strategy.base  import run_default_strategy 
from src.api.binance_futures  import BinanceAPI 
 
def main():
    exchange = BinanceAPI(config={})  # 传入您的实际配置
    run_default_strategy(exchange)
 
if __name__ == '__main__':
    main()