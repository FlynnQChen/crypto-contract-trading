# src/core/cli.py  
from src.core.strategy.base  import run_default_strategy
from src.api.binance_futures  import BinanceAPI
 
def main():
    run_default_strategy(BinanceAPI())
 
if __name__ == '__main__':
    main()