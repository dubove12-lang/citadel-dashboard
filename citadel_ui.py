import streamlit as st
import requests
from web3 import Web3
import math
import pandas as pd
import datetime
from streamlit_autorefresh import st_autorefresh

# =========================
# KONFIGURÁCIA
# =========================
ARB_RPC = "https://arb1.arbitrum.io/rpc"
w3 = Web3(Web3.HTTPProvider(ARB_RPC))

POSITION_MANAGER = w3.to_checksum_address("0xC36442b4a4522E871399CD717aBDD847Ab11FE88")
FACTORY_ADDRESS = w3.to_checksum_address("0x1F98431c8aD98523631AE4a59f267346ea31F984")
POOL_ID = 4931983  # tvoje LP NFT ID

HL_WALLET = "0x689fEBfd1EA5Af9E70B86d8a29362eC119C289B0"
HL_API = "https://api.hyperliquid.xyz/info"

# =========================
# ABI
# =========================
position_manager_abi = [
    {
        "inputs": [{"internalType": "uint256","name":"tokenId","type":"uint256"}],
        "name": "positions",
        "outputs": [
            {"internalType":"uint96","name":"nonce","type":"uint96"},
            {"internalType":"address","name":"operator","type":"address"},
            {"internalType":"address","name":"token0","type":"address"},
            {"internalType":"address","name":"token1","type":"address"},
            {"internalType":"uint24","name":"fee","type":"uint24"},
            {"internalType":"int24","name":"tickLower","type":"int24"},
            {"internalType":"int24","name":"tickUpper","type":"int24"},
            {"internalType":"uint128","name":"liquidity","type":"uint128"},
            {"internalType":"uint256","name":"feeGrowthInside0LastX128","type":"uint256"},
            {"internalType":"uint256","name":"feeGrowthInside1LastX128","type":"uint256"},
            {"internalType":"uint128","name":"tokensOwed0","type":"uint128"},
            {"internalType":"uint128","name":"tokensOwed1","type":"uint128"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

factory_abi = [
    {
        "inputs": [
            {"internalType": "address","name":"tokenA","type":"address"},
            {"internalType": "address","name":"tokenB","type":"address"},
            {"internalType": "uint24","name":"fee","type":"uint24"}
        ],
        "name": "getPool",
        "outputs": [{"internalType":"address","name":"pool","type":"address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

pool_abi = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType":"uint160","name":"sqrtPriceX96","type":"uint160"},
            {"internalType":"int24","name":"tick","type":"int24"},
            {"internalType":"uint16","name":"observationIndex","type":"uint16"},
            {"internalType":"uint16","name":"observationCardinality","type":"uint16"},
            {"internalType":"uint16","name":"observationCardinalityNext","type":"uint16"},
            {"internalType":"uint8","name":"feeProtocol","type":"uint8"},
            {"internalType":"bool","name":"unlocked","type":"bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

erc20_abi = [
    {"constant": True,"inputs": [],"name": "decimals","outputs": [{"name": "","type": "uint8"}],"type": "function"},
    {"constant": True,"inputs": [],"name": "symbol","outputs": [{"name": "","type": "string"}],"type": "function"}
]

collect_abi = [{
    "inputs": [
        {"internalType": "tuple", "name": "params", "type": "tuple", "components": [
            {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
            {"internalType": "address", "name": "recipient", "type": "address"},
            {"internalType": "uint128", "name": "amount0Max", "type": "uint128"},
            {"internalType": "uint128", "name": "amount1Max", "type": "uint128"},
        ]}
    ],
    "name": "collect",
    "outputs": [
        {"internalType": "uint256", "name": "amount0", "type": "uint256"},
        {"internalType": "uint256", "name": "amount1", "type": "uint256"}
    ],
    "stateMutability": "nonpayable",
    "type": "function"
}]

# =========================
# FUNKCIE
# =========================
def get_lp_amounts_and_value(token_id):
    pos_contract = w3.eth.contract(address=POSITION_MANAGER, abi=position_manager_abi)
    factory = w3.eth.contract(address=FACTORY_ADDRESS, abi=factory_abi)

    pos = pos_contract.functions.positions(token_id).call()
    token0, token1, fee = pos[2], pos[3], pos[4]
    tickLower, tickUpper, liquidity = pos[5], pos[6], pos[7]

    pool_address = factory.functions.getPool(token0, token1, fee).call()
    pool = w3.eth.contract(address=pool_address, abi=pool_abi)

    sqrtPriceX96, tick, *_ = pool.functions.slot0().call()
    sqrtPrice = sqrtPriceX96 / (1 << 96)
    sqrtPriceA = math.sqrt(1.0001 ** tickLower)
    sqrtPriceB = math.sqrt(1.0001 ** tickUpper)

    if sqrtPrice <= sqrtPriceA:
        amount0 = liquidity * (sqrtPriceB - sqrtPriceA) / (sqrtPriceA * sqrtPriceB)
        amount1 = 0
    elif sqrtPrice < sqrtPriceB:
        amount0 = liquidity * (sqrtPriceB - sqrtPrice) / (sqrtPrice * sqrtPriceB)
        amount1 = liquidity * (sqrtPrice - sqrtPriceA)
    else:
        amount0 = 0
        amount1 = liquidity * (sqrtPriceB - sqrtPriceA)

    # decimals + symboly
    t0 = w3.eth.contract(address=token0, abi=erc20_abi)
    t1 = w3.eth.contract(address=token1, abi=erc20_abi)
    dec0 = t0.functions.decimals().call()
    dec1 = t1.functions.decimals().call()
    sym0 = t0.functions.symbol().call()
    sym1 = t1.functions.symbol().call()

    amount0 /= 10 ** dec0
    amount1 /= 10 ** dec1

    # cena medzi token0 a token1
    price0_1 = (sqrtPriceX96 / (1 << 96)) ** 2 * 10**(dec0 - dec1)

    if sym0.upper() in ["WETH", "ETH"]:
        eth_amt, usdc_amt = amount0, amount1
        eth_price = price0_1
    else:
        eth_amt, usdc_amt = amount1, amount0
        eth_price = 1 / price0_1

    eth_value_usd = eth_amt * eth_price
    total_value_usd = eth_value_usd + usdc_amt

    # === unclaimed fees cez simulovaný collect ===
    pos_collect = w3.eth.contract(address=POSITION_MANAGER, abi=collect_abi)
    try:
        fees0, fees1 = pos_collect.functions.collect((
            token_id,
            "0x0000000000000000000000000000000000000000",
            2**128-1,
            2**128-1
        )).call()

        fees0 /= 10**dec0
        fees1 /= 10**dec1

        if sym0.upper() in ["WETH", "ETH"]:
            fees_value_usd = fees0 * eth_price + fees1
        else:
            fees_value_usd = fees1 * eth_price + fees0
    except Exception:
        fees_value_usd = 0.0

    return eth_amt, usdc_amt, eth_value_usd, total_value_usd, fees_value_usd


def get_hl_account_value(wallet):
    body = {"type": "clearinghouseState", "user": wallet}
    resp = requests.post(HL_API, json=body).json()
    return float(resp["marginSummary"]["accountValue"])


# =========================
# STREAMLIT APP
# =========================
st.set_page_config(page_title="Krypto Dashboard", layout="wide")
st.title("📊 LP + HL Value Tracker")

if "data" not in st.session_state:
    st.session_state["data"] = pd.DataFrame(columns=["time", "lp_value", "hl_value", "total_value", "apr"])

# dáta
eth_amt, usdc_amt, eth_value_usd, lp_val, fee_val = get_lp_amounts_and_value(POOL_ID)
hl_val = get_hl_account_value(HL_WALLET)

lp_val_total = lp_val + fee_val
total_val = lp_val_total + hl_val

# APR z posledného kroku
apr = None
if len(st.session_state["data"]) > 0:
    prev_val = st.session_state["data"]["total_value"].iloc[-1]
    if prev_val > 0:
        change = (total_val - prev_val) / prev_val
        apr = change * 525600 * 100  # % p.a.

new_row = pd.DataFrame([{
    "time": datetime.datetime.now(),
    "lp_value": lp_val_total,
    "hl_value": hl_val,
    "total_value": total_val,
    "apr": apr
}])

st.session_state["data"] = pd.concat(
    [st.session_state["data"], new_row],
    ignore_index=True
)

# graf
st.line_chart(
    st.session_state["data"].set_index("time")[["lp_value", "hl_value", "total_value"]],
    height=500
)

# metriky
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("ETH v LP", f"{eth_amt:.6f} ETH", f"≈ ${eth_value_usd:.2f}")
col2.metric("USDC v LP", f"{usdc_amt:.2f} USDC")
col3.metric("ETH hodnota v USD", f"${eth_value_usd:.2f}")
col4.metric("LP celkom (s fees)", f"${lp_val_total:.2f}")
col5.metric("Unclaimed Fees", f"${fee_val:.2f}")

# HL + Total
col6, col7 = st.columns(2)
col6.metric("HL účet", f"${hl_val:.2f}")
col7.metric("Portfólio celkom", f"${total_val:.2f}")

# priemerné APR
if st.session_state["data"]["apr"].notna().any():
    avg_apr = st.session_state["data"]["apr"].mean()
    st.metric("Odhadovaný APR", f"{avg_apr:.2f}%")

# Auto-refresh každú minútu
st_autorefresh(interval=60 * 1000, key="datarefresh")
