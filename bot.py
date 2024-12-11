import time
import ccxt
import logging
import threading
import asyncio
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Configuración de la API de Binance
API_KEY = ''
API_SECRET = ''
TESTNET_API_URL = 'https://testnet.binance.vision/api'

# Configuración de Telegram (Opcional)
TELEGRAM_API_TOKEN = ''

# Configuración del log avanzado
logging.basicConfig(
    filename="trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Conexión a Binance Testnet
exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})
exchange.set_sandbox_mode(True)  # Habilitar el modo testnet

# Cargar mercados al inicio
try:
    exchange.load_markets()
    logging.info("Mercados cargados correctamente.")
except Exception as e:
    logging.error(f"Error al cargar los mercados: {e}")

# Lista global de operaciones
operations = []
message_interval = 20  # Intervalo por defecto para los mensajes (en segundos)

# Notificación vía Telegram
async def send_telegram_message(chat_id, message):
    """Envía un mensaje a Telegram."""
    bot = Bot(token=TELEGRAM_API_TOKEN)
    try:
        await bot.send_message(chat_id=chat_id, text=message)
    except Exception as e:
        logging.error(f"Error al enviar mensaje de Telegram: {e}")

# Validar si un símbolo está disponible
def validate_symbol(symbol):
    try:
        return symbol in exchange.symbols
    except Exception as e:
        logging.error(f"Error al validar símbolo {symbol}: {e}")
        return False

# Obtener el precio real desde Binance Testnet
def get_current_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return ticker['last']
    except Exception as e:
        logging.error(f"Error al obtener el precio actual para {symbol}: {e}")
        return None

# Colocar una orden de mercado
def place_market_order(symbol, side, amount):
    """Coloca una orden de mercado en Binance Testnet."""
    try:
        order = exchange.create_order(symbol, 'market', side, amount)
        logging.info(f"Orden {side} ejecutada: {order}")
        return order
    except Exception as e:
        logging.error(f"Error al colocar la orden {side} para {symbol}: {e}")
        return None

# Colocar una orden limitada
def place_limit_order(symbol, side, amount, price):
    """Coloca una orden limitada en Binance Testnet."""
    try:
        order = exchange.create_order(symbol, 'limit', side, amount, price)
        logging.info(f"Orden limitada {side} colocada: {order}")
        return order
    except Exception as e:
        logging.error(f"Error al colocar la orden limitada {side} para {symbol} a {price}: {e}")
        return None

# Monitorear una operación específica
def monitor_operation(operation, chat_id):
    """Monitorea una operación y ajusta los niveles de Stop Loss y Trailing Stop."""
    global message_interval
    symbol = operation["symbol"]
    trade_amount = operation["trade_amount"]
    trailing_stop_percent = operation["trailing_stop_percent"]
    stop_loss_percent = operation["stop_loss_percent"]

    # Ejecución de la orden de compra
 buy_order = place_market_order(symbol, 'buy', trade_amount)
    if not buy_order:
        asyncio.run(send_telegram_message(chat_id, f"Error al ejecutar la orden de compra para {symbol}."))
        return

    # Enviar mensaje inicial de compra con detalles
    asyncio.run(
        send_telegram_message(
            chat_id,
            f"Orden de compra ejecutada: {buy_order}\n"
            f"Detalles:\n"
            f" - Symbol: {buy_order['symbol']}\n"
            f" - Order ID: {buy_order['id']}\n"
            f" - Cantidad: {buy_order['amount']}\n"
            f" - Precio: {buy_order.get('average', 'N/A')}\n"
            f" - Estado: {buy_order['status']}"
        )
    )

    # Obtener precio de entrada real desde la respuesta de la orden
    entry_price = float(buy_order['average']) if 'average' in buy_order else get_current_price(symbol)
    if not entry_price:
        asyncio.run(send_telegram_message(chat_id, f"No se pudo obtener el precio de entrada para {symbol}."))
        return

    stop_loss_price = entry_price * (1 - stop_loss_percent / 100)
    trailing_stop_price = entry_price

    asyncio.run(
        send_telegram_message(
            chat_id,
            f"Operación iniciada para {symbol}:\n"
            f"Precio de entrada: {entry_price}\n"
            f"Stop Loss: {stop_loss_price} ({stop_loss_percent}%)\n"
            f"Trailing Stop: {trailing_stop_price} ({trailing_stop_percent}%)",
        )
    )

    while True:
        try:
            current_price = get_current_price(symbol)
            if not current_price:
                continue

            # Actualizar Trailing Stop si el precio sube
            if current_price > trailing_stop_price:
                trailing_stop_price = current_price * (1 - trailing_stop_percent / 100)

            # Enviar actualizaciones según el intervalo
            if message_interval > 0:
                asyncio.run(
                    send_telegram_message(
                        chat_id,
                        f"Precio actual: {current_price}\n"
                        f"Trailing Stop: {trailing_stop_price} ({trailing_stop_percent}%)\n"
                        f"Stop Loss: {stop_loss_price} ({stop_loss_percent}%)\n"
                        f"Entrada: {entry_price}",
                    )

                )

            # Verificar Stop Loss
            if current_price < stop_loss_price:
                asyncio.run(send_telegram_message(chat_id, f"Stop Loss alcanzado para {symbol}. Vendiendo..."))
                sell_order = place_market_order(symbol, 'sell', trade_amount)
                if sell_order:
                    asyncio.run(send_telegram_message(chat_id, f"Orden de venta ejecutada: {sell_order}"))
                break

            # Verificar Trailing Stop
            if current_price < trailing_stop_price:
                asyncio.run(send_telegram_message(chat_id, f"Trailing Stop alcanzado para {symbol}. Vendiendo..."))
                sell_order = place_market_order(symbol, 'sell', trade_amount)
                if sell_order:
                    asyncio.run(send_telegram_message(chat_id, f"Orden de venta ejecutada: {sell_order}"))
                break

            time.sleep(message_interval if message_interval > 0 else 1)
        except Exception as e:
            logging.error(f"Error en el monitoreo de {symbol}: {e}")
            asyncio.run(send_telegram_message(chat_id, f"Error en el monitoreo de {symbol}: {e}"))
            break

# Comandos de Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra un mensaje de bienvenida."""
    await update.message.reply_text(
        "Bienvenido al bot de trading. Usa /help para ver los comandos disponibles."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista los comandos disponibles."""
    commands = (
        "/start - Iniciar el bot\n"
        "/add_operation <symbol> <amount> <trailing_stop> <stop_loss> - Agregar una operación al precio actual\n"
        "/add_limit_operation <symbol> <amount> <price> <trailing_stop> <stop_loss> - Agregar operación limitada\n"
        "/view_operations - Ver operaciones activas\n"
        "/del_operation <operation_number> - Eliminar una operación activa\n"
        "/interval_msg <seconds> - Configurar el intervalo para mensajes de estado\n"
    )
    await update.message.reply_text(commands)

async def add_operation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Agrega una operación al precio actual."""
    chat_id = update.message.chat_id
    try:
        args = context.args
        symbol = args[0].upper()
        if not validate_symbol(symbol):
            raise ValueError(f"Símbolo {symbol} no válido.")
        trade_amount = float(args[1])
        trailing_stop_percent = float(args[2])
        stop_loss_percent = float(args[3])

        operation = {
            "symbol": symbol,
            "trade_amount": trade_amount,
            "trailing_stop_percent": trailing_stop_percent,
            "stop_loss_percent": stop_loss_percent,
        }
        operations.append(operation)
        threading.Thread(target=monitor_operation, args=(operation, chat_id)).start()
        await send_telegram_message(chat_id, f"Operación añadida: {operation}")
    except Exception as e:
        await send_telegram_message(chat_id, f"Error al agregar operación: {e}")

async def add_limit_operation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Agrega una operación limitada."""
    chat_id = update.message.chat_id
    try:
        args = context.args
        symbol = args[0].upper()
        if not validate_symbol(symbol):
            raise ValueError(f"Símbolo {symbol} no válido.")
        trade_amount = float(args[1])
        price = float(args[2])
        trailing_stop_percent = float(args[3])
        stop_loss_percent = float(args[4])

        limit_order = place_limit_order(symbol, 'buy', trade_amount, price)

        if limit_order:
            operation = {
                "symbol": symbol,
                "trade_amount": trade_amount,
                "trailing_stop_percent": trailing_stop_percent,
                "stop_loss_percent": stop_loss_percent,
                "entry_price": price,
            }
            operations.append(operation)
            await send_telegram_message(chat_id, f"Operación limitada añadida: {operation}")
        else:
            await send_telegram_message(chat_id, f"No se pudo agregar la operación limitada para {symbol}.")
    except Exception as e:
        await send_telegram_message(chat_id, f"Error al agregar operación limitada: {e}")

async def del_operation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina una operación activa por su número."""
    chat_id = update.message.chat_id
    try:
        operation_number = int(context.args[0]) - 1
        if 0 <= operation_number < len(operations):
            removed_operation = operations.pop(operation_number)
            await send_telegram_message(chat_id, f"Operación eliminada: {removed_operation}")
        else:
            await send_telegram_message(chat_id, f"Número de operación inválido. Hay {len(operations)} operaciones activas.")
    except Exception as e:
        await send_telegram_message(chat_id, f"Error al eliminar operación: {e}")

async def interval_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configura el intervalo de mensajes de estado."""
    global message_interval
    chat_id = update.message.chat_id
    try:
        interval = int(context.args[0])
        message_interval = max(0, interval)
        if interval == 0:
            await send_telegram_message(chat_id, "Mensajes de estado desactivados.")
        else:
            await send_telegram_message(chat_id, f"Intervalo de mensajes configurado a {interval} segundos.")
    except Exception as e:
        await send_telegram_message(chat_id, f"Error al configurar el intervalo: {e}")

async def view_operations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra las operaciones activas."""
    if operations:
        message = "\n".join(
            [
                f"{idx + 1}. {op['symbol']} - {op['trade_amount']} BTC - Entrada: {op.get('entry_price', 'Precio actual')} "
                f"- Trailing Stop: {op['trailing_stop_percent']}% - Stop Loss: {op['stop_loss_percent']}%"
                for idx, op in enumerate(operations)
            ]
        )
    else:
        message = "No hay operaciones activas."
    await update.message.reply_text(message)

# Configuración del bot de Telegram
def main():
    """Inicia el bot de Telegram."""
    app = ApplicationBuilder().token(TELEGRAM_API_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("add_operation", add_operation))
    app.add_handler(CommandHandler("add_limit_operation", add_limit_operation))
    app.add_handler(CommandHandler("view_operations", view_operations))
    app.add_handler(CommandHandler("del_operation", del_operation))
    app.add_handler(CommandHandler("interval_msg", interval_msg))

    app.run_polling()

if __name__ == "__main__":
    main()
