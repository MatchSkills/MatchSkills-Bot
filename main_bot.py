import logging
import os
import httpx
import jwt
import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

load_dotenv()


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "S3GR3D0_D3_35T4D0")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:67")# Quero ver sair SIX da manhã e só voltar SEVEN da noite capinando lote
VERIFICADOR_SECRETO = os.getenv("JWT_SECRET")


AGUARDANDO_CONFIRMACAO, EM_ENTREVISTA = range(2)


def gerar_jwt() -> str:
    """Vou tentar gerar um JWT assinado no proprio bot, 
    com um tempo limite pra n virar bagunça"""
    agora = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": "bot-service",
        "role": "SYSTEM",
        "iat": agora,
        "exp": agora + datetime.timedelta(seconds=30) 
    }
    return jwt.encode(payload, VERIFICADOR_SECRETO, algorithm="HS256")

def obter_headers() -> dict:
    """Monta os cabeçalhos"""
    token = gerar_jwt()
    return {
        "X-Internal-Token": token,
        "Content-Type": "application/json"
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    args = context.args
    if args:
        vaga_id = args[0]
        context.user_data["vaga_id"] = vaga_id
    else:
        await update.message.reply_text("Por favor, informe o ID da vaga para iniciar.")
        return ConversationHandler.END

    keyboard = [
        [
            InlineKeyboardButton("Sim", callback_data="SIM"),
            InlineKeyboardButton("Não", callback_data="NAO"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Olá! Identificamos sua candidatura para a vaga `{vaga_id}`.\n\n"
        "Você quer começar a entrevista agora?",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )

    return AGUARDANDO_CONFIRMACAO


async def tratar_decisao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query

    if query:
        await query.answer()
        resposta = query.data
        message = query.message
    else:
        resposta = update.message.text.strip().upper()
        message = update.message

    if resposta in ["SIM", "VAMOS LÁ", "VAMOS LA", "BRASIL"]:
        return await iniciar_e_pedir_primeira_pergunta(message, context)
    else:
        await message.reply_text("Quando puder, só mandar um 'Vamos lá'!")
        return AGUARDANDO_CONFIRMACAO


async def iniciar_e_pedir_primeira_pergunta(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    vaga_id = context.user_data.get("vaga_id")
    candidato_id = message.chat.id #pega o do chat msm

    await message.reply_text("Carregando suas perguntas, só um instante...")

    headers = obter_headers(vaga_id, candidato_id)

    async with httpx.AsyncClient(headers=headers) as client:
        try:
            caminho_perguntas_API = f"{API_BASE_URL}/job-posting/{vaga_id}/interview/questions"
            response = await client.get(
                caminho_perguntas_API,
                timeout=10.0,
            )
            
            if response.status_code == 200:
                data = response.json()
                
                perguntas = data.get("perguntas", [])

                if not perguntas:
                    await message.reply_text("Não foram encontradas perguntas para esta vaga.")
                    return ConversationHandler.END

                context.user_data["perguntas"] = perguntas
                context.user_data["respostas"] = []
                context.user_data["indice_atual"] = 0

                primeira_pergunta = perguntas[0]["texto"]
                total = len(perguntas)
                
                await message.reply_text(f"Pergunta 1/{total}:\n\n{primeira_pergunta}")
                return EM_ENTREVISTA
            else:
                await message.reply_text("Houve um erro ao buscar as perguntas. Tente novamente mais tarde.")
                return ConversationHandler.END

        except Exception as e:
            logging.error(f"Erro na integração com API: {e}")
            await message.reply_text("Serviço indisponível no momento.")
            return ConversationHandler.END


async def processar_resposta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Acumula a resposta localmente. Se for a última pergunta, envia o JSON final."""
    resposta_usuario = update.message.text
    
    perguntas = context.user_data.get("perguntas", [])
    indice = context.user_data.get("indice_atual", 0)
    pergunta_atual = perguntas[indice]
    context.user_data["respostas"].append({
        "pergunta_id": pergunta_atual.get("id"),
        "pergunta": pergunta_atual.get("texto"),
        "resposta": resposta_usuario
    })
    indice += 1
    context.user_data["indice_atual"] = indice

    if indice < len(perguntas):
        proxima_pergunta = perguntas[indice]["texto"]
        total = len(perguntas)
        await update.message.reply_text(f"Pergunta {indice + 1}/{total}:\n\n{proxima_pergunta}")
        return EM_ENTREVISTA

    await update.message.reply_text("Obrigado! Enviando suas respostas...")

    vaga_id = context.user_data.get("vaga_id")
    candidato_id = update.message.chat.id
    
    payload_final = {
        "candidato_id": str(candidato_id),
        "vaga_id": vaga_id,
        "respostas": context.user_data["respostas"]
    }

    headers = obter_headers(vaga_id, candidato_id)

    async with httpx.AsyncClient(headers=headers) as client:
        try:
            caminho_das_resposta = f"{API_BASE_URL}/job-posting/{vaga_id}/interview/answers"
            response = await client.post(
                caminho_das_resposta,
                json=payload_final,
                timeout=15.0,
            )

            if response.status_code in [200, 201]:
                await update.message.reply_text(
                    "Entrevista concluída! Suas informações já foram enviadas ao recrutador. Boa sorte!"
                )
            else:
                await update.message.reply_text(
                    "Erro ao salvar suas respostas. Entre em contato com o suporte."
                )
        except Exception as e:
            logging.error(f"Erro ao enviar JSON final à API: {e}")
            await update.message.reply_text("Falha de conexão ao enviar suas respostas.")

    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Entrevista cancelada.")
    return ConversationHandler.END


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AGUARDANDO_CONFIRMACAO: [
                CallbackQueryHandler(tratar_decisao),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tratar_decisao),
            ],
            EM_ENTREVISTA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, processar_resposta)
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )

    app.add_handler(conv_handler)

    print("Bot rodando...")
    app.run_polling()


if __name__ == "__main__":
    main()
