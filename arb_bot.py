import asyncio
import os
import time
from dotenv import load_dotenv
import ccxt.async_support as ccxt
import aiohttp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ===========================
# CONFIG
# ===========================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "3"))
PROFIT_THRESHOLD_PCT = float(os.getenv("PROFIT_THRESHOLD_PCT", "0.7"))
TRADE_FEE_PCT = float(os.getenv("TRADE_FEE_PCT", "0.2"))

WATCH = {
    "BTC/USDT": ["binance", "kucoin", "bybit"],
    "ETH/USDT": ["binance", "kucoin", "bybit"],
}

EXCHANGE_CLASSES = {
    "binance": ccxt.binance,
    "kucoin": ccxt.kucoin,
    "bybit": ccxt.bybit,
}

scanning = False  # global flag to control scanning
task = None       # background task handle


# ===========================
# CORE FUNCTIONS
# ===========================

async def create_exchanges():
    clients = {}
    for name, cls in EXCHANGE_CLASSES.items():
        try:
            client = cls({"enableRateLimit": True})
            clients[name] = client
        except Exception as e:
            print(f"[ERROR] Failed to init {name}: {e}")
    return clients


async def fetch_best(client, exchange_name, symbol):
    try:
        limit = 20 if exchange_name == "kucoin" else 5
        ob = await client.fetch_order_book(symbol, limit=limit)
        best_bid = ob["bids"][0][0] if ob["bids"] else None
        best_ask = ob["asks"][0][0] if ob["asks"] else None
        return {"exchange": exchange_name, "symbol": symbol, "bid": best_bid, "ask": best_ask}
    except Exception as e:
        print(f"[ERROR] {exchange_name} {symbol}: {e}")
        return {"exchange": exchange_name, "symbol": symbol, "bid": None, "ask": None}


def analyze_opportunities(best_prices):
    alerts = []
    for s in best_prices:
        for b in best_prices:
            if s["exchange"] == b["exchange"]:
                continue
            buy = b["ask"]
            sell = s["bid"]
            if buy is None or sell is None or buy <= 0:
                continue
            gross_pct = ((sell - buy) / buy) * 100.0
            net_pct = gross_pct - (TRADE_FEE_PCT * 2)
            if net_pct >= PROFIT_THRESHOLD_PCT:
                alerts.append({
                    "buy_ex": b["exchange"],
                    "sell_ex": s["exchange"],
                    "buy_price": buy,
                    "sell_price": sell,
                    "gross_pct": round(gross_pct, 4),
                    "net_pct": round(net_pct, 4),
                })
    return alerts


async def scan_loop(context: ContextTypes.DEFAULT_TYPE):
    global scanning
    clients = await create_exchanges()
    async with aiohttp.ClientSession() as session:
        while scanning:
            start = time.time()
            for symbol, ex_list in WATCH.items():
                tasks = [fetch_best(clients[ex], ex, symbol) for ex in ex_list if ex in clients]
                results = await asyncio.gather(*tasks)
                opps = analyze_opportunities(results)
                if opps:
                    msg = [f"<b>💰 Arbitrage Alert for {symbol}</b>"]
                    for o in opps:
                        msg.append(
                            f"Buy on <code>{o['buy_ex']}</code> @ {o['buy_price']:.4f} → Sell on <code>{o['sell_ex']}</code> @ {o['sell_price']:.4f} | gross {o['gross_pct']}% net ~{o['net_pct']}%"
                        )
                    msg.append(f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC")
                    await context.bot.send_message(chat_id=context.job.chat_id, text="\n".join(msg), parse_mode="HTML")
            elapsed = time.time() - start
            await asyncio.sleep(max(0, SCAN_INTERVAL - elapsed))
    for c in clients.values():
        try:
            await c.close()
        except Exception:
            pass


# ===========================
# TELEGRAM COMMANDS
# ===========================

async def start_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scanning, task
    if scanning:
        await update.message.reply_text("🔁 Scanner already running.")
        return

    scanning = True
    chat_id = update.effective_chat.id
    await update.message.reply_text("✅ Arbitrage scanner started.")

    # ✅ Properly schedule background task
    job_queue = context.application.job_queue
    job_queue.run_once(lambda ctx: asyncio.create_task(scan_loop(ctx)), when=1, chat_id=chat_id)


async def stop_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scanning
    scanning = False
    await update.message.reply_text("🛑 Scanner stopped.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "✅ Running" if scanning else "⏸️ Stopped"
    await update.message.reply_text(f"Bot status: {text}")


# ===========================
# MAIN
# ===========================

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_scan))
    app.add_handler(CommandHandler("stop", stop_scan))
    app.add_handler(CommandHandler("status", status))
    print("🚀 Bot is running... Use /start in Telegram to begin scanning.")
    app.run_polling()


if __name__ == "__main__":
    main()
