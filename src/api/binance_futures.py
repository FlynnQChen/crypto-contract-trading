import hmac
import hashlib 
import urllib.parse  
from typing import Dict, List, Optional, Union
import requests 
from requests.exceptions  import RequestException
from ..core.risk.exceptions  import APIError, RateLimitError
 
class BinanceFuturesAPI:
    """
    Binance USDT本位合约API完整封装
    文档参考: https://binance-docs.github.io/apidocs/futures/cn/ 
    """
 
    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False):
        self.base_url  = "https://testnet.binancefuture.com"  if testnet else "https://fapi.binance.com" 
        self.ws_url  = "wss://stream.binancefuture.com/ws" 
        self.api_key  = api_key
        self.api_secret  = api_secret
        self.session  = requests.Session()
        self.session.headers.update({ 
            "X-MBX-APIKEY": self.api_key, 
            "Content-Type": "application/json"
        })
 
    def _sign_request(self, params: Dict) -> str:
        """生成签名"""
        query_string = urllib.parse.urlencode(params) 
        return hmac.new( 
            self.api_secret.encode('utf-8'), 
            query_string.encode('utf-8'), 
            hashlib.sha256 
        ).hexdigest()
 
    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, signed: bool = False) -> Dict:
        """统一请求处理"""
        url = f"{self.base_url}{endpoint}" 
        params = params or {}
 
        if signed:
            params['timestamp'] = int(time.time()  * 1000)
            params['signature'] = self._sign_request(params)
 
        try:
            response = self.session.request( 
                method,
                url,
                params=params if method == 'GET' else None,
                json=params if method != 'GET' else None 
            )
            data = response.json() 
 
            if response.status_code  == 429:
                raise RateLimitError(f"API频率限制 请等待 {data.get('retryAfter',  60)}秒")
            if response.status_code  >= 400:
                raise APIError(data.get('msg',  'Unknown error'), code=response.status_code) 
 
            return data 
 
        except RequestException as e:
            raise APIError(f"网络请求失败: {str(e)}")
 
    # ---------- 账户相关 ----------
    def get_account_balance(self) -> List[Dict]:
        """获取USDT余额和持仓"""
        return self._request("GET", "/fapi/v2/balance", signed=True)
 
    def change_leverage(self, symbol: str, leverage: int) -> Dict:
        """调整杠杆"""
        return self._request(
            "POST", "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
            signed=True
        )
 
    # ---------- 交易相关 ----------
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        reduce_only: bool = False,
        **kwargs
    ) -> Dict:
        """下单接口"""
        params = {
            "symbol": symbol,
            "side": side.upper(), 
            "type": order_type.upper(), 
            "quantity": round(quantity, 6),  # 避免小数位过多
            "reduceOnly": str(reduce_only).lower(),
            **kwargs
        }
        if price is not None:
            params["price"] = round(price, 2)  # 价格精度处理 
        
        return self._request("POST", "/fapi/v1/order", params, signed=True)
 
    def batch_orders(self, orders: List[Dict]) -> List[Dict]:
        """批量下单 (最多5个)"""
        return self._request(
            "POST", "/fapi/v1/batchOrders",
            {"batchOrders": json.dumps(orders)}, 
            signed=True
        )
 
    # ---------- 行情数据 ----------
    def get_mark_price(self, symbol: str) -> Dict:
        """获取标记价格"""
        return self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
 
    def get_funding_rate_history(self, symbol: str, limit: int = 100) -> List[Dict]:
        """获取历史资金费率"""
        return self._request(
            "GET", "/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": limit}
        )
 
    # ---------- WebSocket ----------
    def get_listen_key(self) -> str:
        """获取WebSocket监听密钥"""
        return self._request("POST", "/fapi/v1/listenKey", signed=True)['listenKey']
 
    # ---------- 风险控制扩展 ----------
    def get_position_risk(self) -> List[Dict]:
        """获取持仓风险指标 (强平价格/保证金率等)"""
        return self._request("GET", "/fapi/v2/positionRisk", signed=True)
 
    def get_income_history(
        self,
        income_type: str = "REALIZED_PNL",
        limit: int = 100 
    ) -> List[Dict]:
        """获取资金流水 (包含资金费率支付记录)"""
        return self._request(
            "GET", "/fapi/v1/income",
            {"incomeType": income_type, "limit": limit},
            signed=True 
        )