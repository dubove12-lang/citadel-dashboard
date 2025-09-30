import streamlit as st
import requests
from web3 import Web3
import math
import pandas as pd
import datetime
from streamlit_autorefresh import st_autorefresh
import os

# =========================
# KONFIGURÁCIA
# =========================
ARB_RPC = "https://arb1.arbitrum.io/rpc"
w3 = Web3(Web3.HTTPProvider(ARB_RPC))

POSITION_MANAGER = w3.to_checksum_address("0xC36442b4a4522E871399CD717aBDD847Ab11FE88")
FACTORY_ADDRESS = w3.to_checksum_address("0x1F98431c8aD98523631AE4a59f267346ea31F984")

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

    # decimals + symboly
    t0 = w3.eth.contract(address=token0, abi=erc20_abi)
    t1 = w3.eth.contract(address=token1, abi=erc20_abi)
    dec0 = t0.functions.decimals().call()
    dec1 = t1.functions.decimals().call()
    sym0 = t0.functions.symbol().call()
    sym1 = t1.functions.symbol().call()

    # aktuálna cena ETH
    price = 1.0001 ** tick
    price *= 10 ** (dec0 - dec1)
    if sym0.upper() in ["WETH", "ETH"]:
        eth_price = price
    else:
        eth_price = 1 / price

    # spodný a horný range
    lower = 1.0001 ** tickLower
    upper = 1.0001 ** tickUpper
    lower_price = lower * 10 ** (dec0 - dec1)
    upper_price = upper * 10 ** (dec0 - dec1)
    if sym0.upper() not in ["WETH", "ETH"]:
        lower_price, upper_price = 1/lower_price, 1/upper_price

    # amounts v pooli
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

    amount0 /= 10 ** dec0
    amount1 /= 10 ** dec1

    if sym0.upper() in ["WETH", "ETH"]:
        eth_amt, usdc_amt = amount0, amount1
    else:
        eth_amt, usdc_amt = amount1, amount0

    eth_value_usd = eth_amt * eth_price
    total_value_usd = eth_value_usd + usdc_amt

    # fees
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

    return eth_amt, usdc_amt, eth_value_usd, total_value_usd, fees_value_usd, lower_price, upper_price, eth_price

def get_hl_account_value(wallet):
    body = {"type": "clearinghouseState", "user": wallet}
    resp = requests.post(HL_API, json=body).json()
    return float(resp["marginSummary"]["accountValue"])

# 🔥 NOVÁ FUNKCIA: výpočet celkových fees
def get_hl_fees(wallet):
    body = {"type": "userFills", "user": wallet}
    resp = requests.post(HL_API, json=body).json()

    # API vracia list fillov
    if isinstance(resp, list):
        fills = resp
    else:
        fills = resp.get("fills", [])

    total_fees = sum(float(f.get("fee", 0)) for f in fills)
    return total_fees


# =========================
# DASHBOARD RENDER
# =========================
def render_dashboard(title, csv_file, pool_id, hl_wallet):
    st.markdown(
        """
        <div style="border: 4px solid black; border-radius: 10px; padding: 15px; margin: 10px;">
        """,
        unsafe_allow_html=True
    )

    st.subheader(title)

    # načítaj CSV ak existuje
    if os.path.exists(csv_file):
        st.session_state[csv_file] = pd.read_csv(csv_file, parse_dates=["time"])
    else:
        st.session_state[csv_file] = pd.DataFrame(columns=["time", "lp_value", "hl_value", "total_value", "apr"])

    # dáta
    eth_amt, usdc_amt, eth_value_usd, lp_val, fee_val, lower_price, upper_price, eth_price = get_lp_amounts_and_value(pool_id)
    hl_val = get_hl_account_value(hl_wallet)
    hl_fees = get_hl_fees(hl_wallet)

    lp_val_total = lp_val + fee_val
    total_val = lp_val_total + hl_val

    # priemerný APR z priemerných 5-min zmien
    df = st.session_state[csv_file].copy()
    apr = None
    if len(df) > 1:
        df["change"] = df["total_value"].pct_change()
        avg_change = df["change"].mean()
        apr = avg_change * 12 * 24 * 365 * 100

    new_row = pd.DataFrame([{
        "time": datetime.datetime.now(),
        "lp_value": lp_val_total,
        "hl_value": hl_val,
        "total_value": total_val,
        "apr": apr
    }])

    st.session_state[csv_file] = pd.concat(
        [st.session_state[csv_file], new_row],
        ignore_index=True
    )

    st.session_state[csv_file].to_csv(csv_file, index=False)

    # graf
    st.line_chart(
        st.session_state[csv_file].set_index("time")[["lp_value", "hl_value", "total_value"]],
        height=300
    )

    # tabuľka
    metrics = [
        ["ETH v LP", f"{eth_amt:.6f} ETH (≈ ${eth_value_usd:.2f})"],
        ["USDC v LP", f"{usdc_amt:.2f} USDC"],
        ["Unclaimed Fees", f"${fee_val:.2f}"],
        ["Spodný range (USD za 1 ETH)", f"${lower_price:.2f}"],
        ["Horný range (USD za 1 ETH)", f"${upper_price:.2f}"],
        ["ETH cena (USD)", f"${eth_price:.2f}"],
        ["LP celkom (s fees)", f"${lp_val_total:.2f}"],
        ["HL účet", f"${hl_val:.2f}"],
        ["HL fees (spálené)", f"${hl_fees:.2f}"],
        ["Portfólio celkom", f"${total_val:.2f}"],
        ["Odhadovaný APR", f"{apr:.2f}%" if apr else "N/A"]
    ]

    st.markdown("📋 **Prehľad portfólia**")
    for i, (m, v) in enumerate(metrics):
        if i in [3, 6, 9]:
            st.markdown("---")
        if i == 10:
            st.markdown(f"**{m}: {v}**")
        else:
            st.write(f"{m}: {v}")

    st.markdown("</div>", unsafe_allow_html=True)

# =========================
# STREAMLIT APP
# =========================
st.set_page_config(page_title="Krypto Dashboard", layout="wide")
st.title("📊 Citadel MVP Strategies")

# tu si vieš nastaviť 4 rôzne pooly a HL peňaženky
col1, col3 = st.columns(2)
with col1:
    render_dashboard("📈 S1 +-10%, 2% order step", "data1.csv", pool_id=4942551, hl_wallet="0x37945bd99Be0D58CdD79aA6C760aA69062917442")

with col3:
    render_dashboard("📈 S3 +-5%, 1% order step", "data3.csv", pool_id=4942575, hl_wallet="0x78067440372b4d37982a9F38D2c27a7cBB09a981")

# Auto-refresh každých 5 minút
st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")


