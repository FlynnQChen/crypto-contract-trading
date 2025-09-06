def main():
    from .strategy.base  import run_default_strategy 
    from src.api.binance_futures  import BinanceAPI
    run_default_strategy(BinanceAPI())

if __name__ == "__main__":
    main()