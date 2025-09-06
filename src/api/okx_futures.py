import hmac 
import base64
import hashlib 
import time 
import json
from typing import Dict, List, Optional 
import requests
from requests.exceptions  import RequestException
from ..core.risk.exceptions  import APIError, RateLimitError 
 
class OKXFuturesAPI:
    """
    OKX USDT本位合约API完整封装 
    文档参考: https://www.okx.com/docs-v5/zh/ 
    """
 
    def __init__(self, api_key: str = "", api_secret: str = "", passphrase: str = "", testnet: bool = False):
        self.base_url  = "https://www.okx.com"   # 测试网与主网同域名
        self.ws_url  = "wss://ws.okx.com:8443/ws/v5/private" 
        self.api_key  = api_key 
        self.api_secret  = api_secret 
        self.passphrase  = passphrase  # OKX特有参数
        self.demo_trading  = testnet  # 通过header标记测试环境
        self.session  = requests.Session()
        self.session.headers.update({ 
            "OK-ACCESS-KEY": self.api_key, 
            "Content-Type": "application/json"
        })
 
    def _sign_request(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """生成OKX签名 (SHA256 + Base64)"""
        message = timestamp + method.upper()  + path + body 
        signature = base64.b64encode(
            hmac.new( 
                self.api_secret.encode('utf-8'), 
                message.encode('utf-8'), 
                hashlib.sha256 
            ).digest()
        ).decode('utf-8')
        return signature 
 
    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, signed: bool = False) -> Dict:
        """统一请求处理"""
        url = f"{self.base_url}{endpoint}" 
        body = json.dumps(params)  if params and method != "GET" else ""
        headers = {}
 
        if signed:
            timestamp = self._get_timestamp()
            headers.update({ 
                "OK-ACCESS-SIGN": self._sign_request(timestamp, method, endpoint, body),
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.passphrase, 
                "x-simulated-trading": "1" if self.demo_trading  else "0"  # 模拟交易标记
            })
 
        try:
            response = self.session.request( 
                method,
                url,
                params=params if method == "GET" else None,
                data=body,
                headers=headers
            )
            data = response.json() 
 
            if response.status_code  == 429:
                retry_after = int(response.headers.get("Retry-After",  60))
                raise RateLimitError(f"请求过于频繁 请等待 {retry_after}秒")
            if data.get("code")  != "0":
                raise APIError(data.get("msg",  "Unknown error"), code=data.get("code")) 
 
            return data["data"] if "data" in data else data 
 
        except RequestException as e:
            raise APIError(f"网络请求失败: {str(e)}")
 
    def _get_timestamp(self) -> str:
        """获取ISO格式时间戳 (OKX要求精确到毫秒)"""
        return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]  + 'Z'
 
    # ---------- 账户相关 ----------
    def get_account_balance(self, ccy: str = "USDT") -> List[Dict]:
        """获取账户余额"""
        return self._request("GET", "/api/v5/account/balance", {"ccy": ccy}, signed=True)
 
    def set_leverage(
        self,
        symbol: str,
        leverage: int,
        margin_mode: str = "cross"
    ) -> Dict:
        """调整杠杆 (全仓/逐仓)"""
        return self._request(
            "POST", "/api/v5/account/set-leverage",
            {
                "instId": symbol,
                "lever": str(leverage),
                "mgnMode": margin_mode
            },
            signed=True 
        )
 
    # ---------- 交易相关 ----------
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        size: float,
        price: Optional[float] = None,
        reduce_only: bool = False,
        **kwargs 
    ) -> Dict:
        """下单接口"""
        params = {
            "instId": symbol,
            "tdMode": "cross" if not reduce_only else "isolated",
            "side": side.lower(), 
            "ordType": order_type.lower(), 
            "sz": str(size),  # OKX要求字符串格式
            **kwargs 
        }
        if price is not None:
            params["px"] = str(round(price, 2))
        if reduce_only:
            params["reduceOnly"] = "true"
 
        return self._request("POST", "/api/v5/trade/order", params, signed=True)
 
    def batch_orders(self, orders: List[Dict]) -> List[Dict]:
        """批量下单 (最多20个)"""
        return self._request(
            "POST", "/api/v5/trade/batch-orders",
            {"data": orders},
            signed=True 
        )
 
    # ---------- 行情数据 ----------
    def get_mark_price(self, symbol: str) -> Dict:
        """获取标记价格"""
        return self._request("GET", "/api/v5/public/mark-price", {"instId": symbol})[0]
 
    def get_funding_rate_history(
        self,
        symbol: str,
        limit: int = 100
    ) -> List[Dict]:
        """获取历史资金费率"""
        return self._request(
            "GET", "/api/v5/public/funding-rate-history",
            {"instId": symbol, "limit": str(limit)}
        )
 
    # ---------- 风险接口 ----------
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """获取持仓风险"""
        params = {"instType": "SWAP"}
        if symbol:
            params["instId"] = symbol
        return self._request("GET", "/api/v5/account/positions", params, signed=True)
 
    def get_bills(
        self,
        bill_type: str = "8",  # 8=资金费率 
        limit: int = 100 
    ) -> List[Dict]:
        """获取账单流水"""
        return self._request(
            "GET", "/api/v5/account/bills",
            {"type": bill_type, "limit": str(limit)},
            signed=True 
        )